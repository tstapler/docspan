"""Google Docs backend."""

from __future__ import annotations

import difflib
import logging
import os
import pathlib
import re
import shutil
import tempfile
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple, Union

from googleapiclient.errors import HttpError

from docspan.backends.base import Backend, CreateResult, PullResult, PushResult
from docspan.backends.google_docs import cross_doc_links
from docspan.backends.google_docs.auth import (
    DualAccountAuth,
    GoogleAuthenticator,
    OAuthAuthenticator,
    default_token_path,
)
from docspan.backends.google_docs.checkbox_state import (
    extract_checkbox_states,
    patch_checkbox_lines,
)
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.backends.google_docs.comments import (
    RespondResult,
    bucket_comments_by_section,
    format_comments_markdown,
    parse_reply_directives,
)
from docspan.backends.google_docs.converter import DocumentConverter
from docspan.backends.google_docs.docs_request_builder import DiffTooExpensive, DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsImageNode,
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
)
from docspan.backends.google_docs.heading_anchors import (
    available_anchor_slugs,
    heading_id_to_slug,
    unresolved_anchors,
    upgrade_heading_id_anchors,
)
from docspan.backends.google_docs.image_source import (
    LocalPathSource,
    UrlSource,
    build_source,
    resolve_document_images,
)
from docspan.backends.google_docs.manifest import (
    MANIFEST_FILENAME,
    ManifestError,
    ManifestStore,
    SectionManifestEntry,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.onboarding import (
    OAUTH_HELP,
    autodetect_client_secret,
    confirm,
    is_interactive,
    persist_google_docs_config,
    validate_client_secret,
    validate_service_account,
)
from docspan.backends.google_docs.projection import (
    describe_residue,
    describe_target_residue,
    project,
)
from docspan.backends.google_docs.push_preview import (
    PushPlan,
    PushPreview,
    find_high_risk_paragraphs,
    render_available_anchors,
    render_high_risk,
)
from docspan.backends.google_docs.section_splitter import SectionSplitError, split_nodes
from docspan.backends.google_docs.tabs import (
    TabNotFoundError,
    heading_ids_by_tab,
    resolve_document_tab,
)
from docspan.core.atomic_dir import atomic_replace_dir
from docspan.core.paths import COMMENTS_SUFFIX

if TYPE_CHECKING:
    from docspan.config import GoogleDocsConfig, MarkgateConfig

logger = logging.getLogger(__name__)


class GoogleDocsBackend(Backend):
    name = "google_docs"

    def __init__(self, config: "GoogleDocsConfig") -> None:
        self.config = config
        self._client: GoogleDocsClient | None = None

    @classmethod
    def from_config(cls, markgate_config: "MarkgateConfig") -> "GoogleDocsBackend":
        from docspan.config import GoogleDocsConfig
        return cls(markgate_config.backends.google_docs or GoogleDocsConfig())

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        # 1. Explicit service-account file in config.
        if self.config.credentials_path:
            auth = GoogleAuthenticator(credentials_path=self.config.credentials_path)
            self._client = GoogleDocsClient(auth.get_credentials())
            return

        # 2. Service-account via environment (Railway / CI).
        if os.getenv("ACCOUNT_A_CREDENTIALS") or os.getenv("ACCOUNT_A_CREDENTIALS_PATH"):
            dual = DualAccountAuth()
            self._client = GoogleDocsClient(dual.get_account_a_credentials())
            return

        # 3. Per-user OAuth (client secret configured, or a token is already cached).
        oauth = OAuthAuthenticator(
            client_secret_path=self.config.oauth_client_secret_path,
            token_path=self.config.token_path,
        )
        if self.config.oauth_client_secret_path or oauth.has_valid_credentials():
            self._client = GoogleDocsClient(oauth.get_credentials())
            return

        raise RuntimeError(
            "Google Docs credentials not found. Configure one of:\n"
            "  • service account: credentials_path in markgate.yaml (or ACCOUNT_A_CREDENTIALS_PATH)\n"
            "  • per-user OAuth: oauth_client_secret_path in markgate.yaml\n"
            "Run: docspan auth setup google_docs"
        )

    def _cleanup_temp_uploads(self, file_ids: List[str]) -> List[str]:
        """Best-effort delete of temp Drive uploads; returns ids that could not be deleted.

        Called from push()'s error paths so `retryable_temp_drive_file_ids`
        actually reflects files still orphaned on Drive, rather than the
        full upload list regardless of whether cleanup was ever attempted.
        """
        assert self._client is not None
        still_orphaned = []
        for file_id in file_ids:
            try:
                self._client.delete_temp_upload(file_id)
            except Exception:
                still_orphaned.append(file_id)
        return still_orphaned

    def _cleanup_temp_uploads_logged(self, file_ids: List[str]) -> List[str]:
        """Same as `_cleanup_temp_uploads`, but logs when cleanup fails.

        The four exception-handling paths in push() surface a cleanup
        failure via `PushResult.retryable_temp_drive_file_ids` — the
        caller sees it. The success/skipped paths used to discard the
        return value entirely, so a cleanup failure there was completely
        silent: no log, no signal, and the temp file stayed public on
        Drive indefinitely. This wraps the same best-effort cleanup with a
        warning log so that invisibility doesn't depend on which push
        outcome triggered it.
        """
        still_orphaned = self._cleanup_temp_uploads(file_ids)
        if still_orphaned:
            logger.warning(
                "Failed to delete %d temp Drive upload(s) after push; "
                "still public and orphaned: %s",
                len(still_orphaned),
                still_orphaned,
            )
        return still_orphaned

    def _cleanup_plan_temp_uploads(self, plan: Optional[PushPlan]) -> List[str]:
        """`_cleanup_temp_uploads_logged`, tolerating a plan that never got built.

        Every exception path in push() reaches its cleanup call with `plan`
        possibly still None (the exception fired before `_build_push_plan`
        returned) — this is the one line each of those four `except`
        clauses would otherwise duplicate.
        """
        return self._cleanup_temp_uploads_logged(plan.temp_drive_file_ids if plan else [])

    def _build_push_plan(
        self,
        local_path: str,
        doc_id: str,
        tab_id: Optional[str] = None,
        content: Optional[str] = None,
    ) -> PushPlan:
        """Fetch the doc + open comments exactly once and compute the diff/risk plan.

        Performs exactly one get_document() call and exactly one
        list_comments() call, then computes current_nodes/target_nodes/
        requests (DocsRequestBuilder.build), entries/unchanged_count
        (DocsRequestBuilder.diff_summary), and high_risk
        (find_high_risk_paragraphs) — all from that single fetch. Never
        calls batch_update. push() and preview_push() each call this
        independently — they never share a plan computed by the other, so
        push()'s write is always gated by data it fetched itself (see
        plan.md Story 1.2.3, architecture-review.md Blocker 1).

        `tab_id` (from Mapping.tab_id) selects which tab of a multi-tab doc
        to diff/write against (tabs.resolve_document_tab); None keeps the
        pre-tabs-support default of the first tab. If the doc has more than
        one tab, plan.tab_warning is set so callers can surface that the
        choice was implicit. Raises TabNotFoundError if tab_id doesn't match
        any tab.

        `content`: when given, this markdown string is parsed directly and
        `local_path` is never read from disk — used by push_sectioned(),
        which has already reassembled the section files into one markdown
        string and only needs `local_path` as the anchor file path for
        image resolution (resolve_document_images/build_source only ever
        take `Path(markdown_path).resolve().parent`, so `local_path` must
        stay a real file path, never the sectioned directory itself).
        push() and preview_push() never pass this — they keep reading
        `local_path` themselves, unchanged.
        """
        assert self._client is not None
        if content is None:
            content = pathlib.Path(local_path).read_text()

        target_nodes = MarkdownToParagraphParser().parse(content)

        # Local images must be uploaded to Drive (and unresolved ones dropped
        # with a warning) before the diff/build below ever sees them --
        # DocsRequestBuilder emits insertInlineImage from node.src verbatim,
        # so a bare relative markdown path reaching it would produce a
        # request the Docs API rejects.
        image_nodes = [n for n in target_nodes if isinstance(n, DocsImageNode)]
        image_warnings: list[str] = []
        temp_drive_file_ids: list[str] = []
        if image_nodes:
            resolved_images, image_warnings, temp_drive_file_ids = resolve_document_images(
                image_nodes, local_path, self._client.upload_temp_image
            )
            # A failed resolution (renamed/unreadable/oversized file, a
            # transient upload error) must NOT drop the node from
            # target_nodes: DocsRequestBuilder.build() treats "present in
            # current, absent in target" as a delete, so dropping it here
            # would silently delete a previously-synced image from the live
            # doc on the very push that failed to re-resolve it. Substitute
            # the original (unresolved) DocsImageNode instead — its
            # alt/width_pt/height_pt are untouched, which is exactly the
            # identity _node_key/_content_key use for images (src is
            # deliberately excluded from both, see their docstrings), so the
            # diff sees "same image, no change" rather than "removed".
            substituted_images = [
                resolved if resolved is not None else original
                for resolved, original in zip(resolved_images, image_nodes)
            ]
            subst_iter = iter(substituted_images)
            target_nodes = [
                next(subst_iter) if isinstance(n, DocsImageNode) else n
                for n in target_nodes
            ]

        try:
            whole_doc = self._client.get_document(doc_id)
            doc, resolved_tab_id, tab_warning = resolve_document_tab(whole_doc, tab_id)
            current_nodes = DocsStructureParser().parse(doc)

            # Both sides of the diff pass through the same projection, so the diff
            # only ever sees what markdown can faithfully represent. Without this,
            # an empty paragraph in the document has no counterpart on the markdown
            # side (blank lines are separators, so the parser never emits one) and
            # the diff read that asymmetry as "the user deleted this" — issue #17.
            # See projection.project for the rule and the trade it makes.
            current_nodes, current_residue = project(current_nodes)
            # Projecting the target is NOT a no-op any more. This comment used to say
            # "MarkdownToParagraphParser cannot emit an empty-text node, so there is
            # nothing on this side to drop" — splitting fenced code blocks per line
            # falsified that: a blank line inside a block, an empty fence and a
            # blank-only fence all produce `text=""`.
            #
            # So this drops author content — not the #17 trade, which *preserves* a
            # blank paragraph the Doc already has. A blank code line exists only in the
            # markdown and is never written.
            #
            # Reported rather than fixed, deliberately. Writing it needs projection to
            # tell a blank *code* line (content) from a stray empty *prose* paragraph
            # (not content), and the node model cannot express that distinction — a
            # design change, not a patch. Until then the author is told, which is the
            # difference between a known limitation and silent data loss.
            target_nodes, target_residue = project(target_nodes)

            body_content = doc.get("body", {}).get("content", [])
            doc_end_index = body_content[-1].get("endIndex", 1) if body_content else 1

            request_builder = DocsRequestBuilder()
            requests = request_builder.build(
                current_nodes, target_nodes, doc_end_index, tab_id=resolved_tab_id
            )
            entries, unchanged_count = request_builder.diff_summary(current_nodes, target_nodes)

            comments = self._client.list_comments(doc_id)
            high_risk = find_high_risk_paragraphs(entries, comments)
        except Exception:
            # Images (if any) were already uploaded to Drive above -- anything
            # that fails from here on must not leave them orphaned. Best-effort:
            # a cleanup failure must not mask the original exception.
            for file_id in temp_drive_file_ids:
                try:
                    self._client.delete_temp_upload(file_id)
                except Exception:
                    pass
            raise

        return PushPlan(
            current_nodes=current_nodes,
            target_nodes=target_nodes,
            requests=requests,
            doc=doc,
            entries=entries,
            unchanged_count=unchanged_count,
            comments=comments,
            high_risk=high_risk,
            target_residue=target_residue,
            tab_warning=tab_warning,
            resolved_tab_id=resolved_tab_id,
            residue=current_residue,
            whole_doc=whole_doc,
            temp_drive_file_ids=temp_drive_file_ids,
            image_warnings=image_warnings,
        )

    def preview_push(
        self, local_path: str, doc_id: str, tab_id: Optional[str] = None
    ) -> PushPreview:
        """Build a read-only, cosmetic preview of what push() would do.

        Calls _build_push_plan() with its own, independent fetch — never a
        plan passed in from elsewhere. Never calls batch_update. This method
        is for --dry-run rendering ONLY — its result must never be consulted
        by push() to decide whether a real write proceeds (that gate is
        enforced entirely inside push() itself, from push()'s own fetch).

        Wraps _build_push_plan() in the same try/except HttpError/except
        Exception pattern push() uses, so a transient failure (expired auth,
        network error, malformed doc) during --dry-run returns a
        PushPreview(error=...) instead of letting the raw exception
        propagate and crash the CLI with a traceback.
        """
        self._ensure_client()
        try:
            plan = self._build_push_plan(local_path, doc_id, tab_id=tab_id)
            # Inside the try/except deliberately: the docstring above promises a
            # PushPreview(error=…) rather than a traceback, and evaluating these
            # in the return expression put them outside that promise.
            #
            # Parsed unprojected, NOT reused from plan.current_nodes, even though
            # that would save a parse. project() drops empty paragraphs — and an
            # empty *heading* (press Enter at the end of a heading) carries a real
            # `headingId`. Dropping it takes that id out of the resolvable set,
            # while pass 2 parses unprojected and resolves it fine. So the cheap
            # version made --dry-run invent a broken cross-reference that the push
            # then wrote correctly, which is the one direction this advisory must
            # never fail in.
            document_nodes = DocsStructureParser().parse(plan.doc)
            # Same foreign-tab map push() gives align(), or the dry-run reports a
            # cross-tab anchor the push then resolves — an over-report, which is
            # the direction unresolved_anchors' own contract forbids.
            unresolved = unresolved_anchors(
                plan.target_nodes, document_nodes, heading_ids_by_tab(plan.whole_doc)
            )
            target_residue_note = describe_target_residue(plan.target_residue)
            available = available_anchor_slugs(plan.target_nodes, document_nodes)
            # --dry-run never inserts anything, so any image just uploaded to
            # build this preview must be deleted now -- PushPreview has no
            # field to carry temp_drive_file_ids forward for a later cleanup.
            # Routed through the same best-effort helper push() uses, so a
            # cleanup failure never turns a successful preview into a
            # reported error.
            self._cleanup_temp_uploads_logged(plan.temp_drive_file_ids)
        except HttpError as exc:
            return PushPreview(
                entries=[], unchanged_count=0, high_risk=[], request_count=0, error=str(exc)
            )
        except DiffTooExpensive as exc:
            return PushPreview(
                entries=[], unchanged_count=0, high_risk=[], request_count=0, error=str(exc)
            )
        except Exception as exc:
            return PushPreview(
                entries=[], unchanged_count=0, high_risk=[], request_count=0, error=str(exc)
            )
        return PushPreview(
            entries=plan.entries,
            unchanged_count=plan.unchanged_count,
            high_risk=plan.high_risk,
            request_count=len(plan.requests),
            tab_warning=plan.tab_warning,
            # Anchors are resolved by pass 2, against a document this dry-run
            # never writes, so this is the markdown-and-current-document
            # approximation of what push() will report: it under-reports and
            # never over-reports. unresolved_anchors' docstring names the three
            # causes it cannot see.
            unresolved_anchors=unresolved,
            available_anchors=available,
            target_residue_note=target_residue_note,
        )

    def push(
        self,
        local_path: str,
        doc_id: str,
        force: bool = False,
        tab_id: Optional[str] = None,
        **kwargs: object,
    ) -> PushResult:
        """Convert local markdown to Google Docs format and batch-update the doc.

        Gates on a PushPlan built from THIS call's own single fetch (see
        _build_push_plan) — never on a plan computed by preview_push() or
        any other caller. A HighRiskParagraph (open comment or native
        checkbox glyph, found live on this fetch) blocks the write unless
        force=True. After a successful write, the CommentCountBackstop
        re-checks the open-comment count and escalates status to "warning"
        (never leaves it "ok") if the count dropped.

        `tab_id` (from Mapping.tab_id) targets a specific tab of a multi-tab
        doc; None keeps the pre-tabs-support default of the first tab. If the
        doc has multiple tabs and tab_id is None, the successful result's
        status is escalated to "warning" (message explains the doc is
        multi-tab and the choice was implicit) rather than silently writing
        to whichever tab happens to be first.
        """
        self._ensure_client()
        resolver = self._build_cross_doc_resolver(kwargs, caller="push")
        return self._execute_push(
            local_path, doc_id, force=force, tab_id=tab_id, resolver=resolver
        )

    def _build_cross_doc_resolver(
        self, kwargs: dict, *, caller: str
    ) -> Optional["cross_doc_links.CrossDocLinkResolver"]:
        """Shared `mappings`/`cross_doc_cache` kwarg validation for push()/push_sectioned()."""
        mappings = kwargs.get("mappings")
        cross_doc_cache = kwargs.get("cross_doc_cache")
        if not mappings:
            return None
        assert isinstance(mappings, list), (
            f"{caller}() 'mappings' kwarg must be a list, got {type(mappings).__name__}"
        )
        assert cross_doc_cache is None or isinstance(cross_doc_cache, dict), (
            f"{caller}() 'cross_doc_cache' kwarg must be a dict, got "
            f"{type(cross_doc_cache).__name__}"
        )
        return cross_doc_links.CrossDocLinkResolver(
            mappings, self._fetch_target_headings, cache=cross_doc_cache
        )

    def _execute_push(
        self,
        local_path: str,
        doc_id: str,
        *,
        force: bool,
        tab_id: Optional[str],
        resolver: Optional["cross_doc_links.CrossDocLinkResolver"],
        content: Optional[str] = None,
        diff_too_expensive_status: Literal["error", "blocked"] = "error",
    ) -> PushResult:
        """Shared diff/request-emission tail for push() and push_sectioned().

        Everything from `_build_push_plan` through the pass-1/pass-2
        batch_update calls and PushResult assembly lives here, unchanged
        from push()'s own former body — push() calls this with
        `content=None` (read `local_path` itself, exactly as before);
        push_sectioned() calls it with the reassembled section markdown as
        `content` and a section file's own path as `local_path` (the
        anchor path `_build_push_plan`/resolve_document_images need).

        `diff_too_expensive_status` lets push_sectioned() report the guard
        trip as "blocked" (refused to write, no partial batch_update ever
        sent) instead of push()'s historical "error", without touching the
        guard itself (_MAX_COMPARISON_CELLS/_MAX_DUPLICATE_RUN in
        docs_request_builder.py) or adding any bypass/fallback around it —
        the guard fires identically either way, only the reported status
        string differs.
        """
        assert self._client is not None
        plan: Optional[PushPlan] = None
        try:
            plan = self._build_push_plan(local_path, doc_id, tab_id=tab_id, content=content)

            if plan.requests and plan.high_risk and not force:
                # A blocked push is not going to be auto-retried, so cleanup
                # is unconditional and best-effort here, same as the
                # skipped/success paths below -- not "retryable" reporting.
                return PushResult(
                    status="blocked",
                    doc_id=doc_id,
                    message=render_high_risk(plan.high_risk),
                    retryable_temp_drive_file_ids=self._cleanup_temp_uploads_logged(
                        plan.temp_drive_file_ids
                    ),
                )

            if plan.requests:
                self._client.batch_update(
                    doc_id, plan.requests, required_revision_id=plan.doc["revisionId"]
                )

            # Pass 2 — tables are inserted empty and inline styling
            # (links/bold/italic/monospace) is deferred, so it needs real
            # post-insert indices.
            #
            # Pass 1 being empty does NOT mean there is nothing to do, and this
            # used to be checked in the wrong order. The diff key covers style,
            # bullet, text and nesting level — it does not include marks — so
            # adding a link to text that is otherwise unchanged produces zero
            # diffs and zero pass-1 requests. When the "nothing to do" return
            # sat above this block, that push wrote nothing and reported
            # `status="skipped"` / "No changes detected", rendered as a green ✓.
            # Bold, italic, monospace and an indentation-only change all fail
            # the same way.
            #
            # `needs_pass2` still gates the work — a target with no tables and
            # no styled spans has nothing for pass 2 to do, and re-reading the
            # document to discover that would add a GET to every text-only push.
            needs_pass2 = any(
                isinstance(n, DocsTableNode)
                or (isinstance(n, DocsParagraphNode) and n.spans)
                for n in plan.target_nodes
            )
            second: list[dict] = []
            unstyled: list[DocsParagraphNode] = []
            unplaced_cells: list[str] = []
            dead_anchors: list[str] = []
            cross_doc_issues: list[str] = []
            # Residue from the second, post-pass-1 parse `align()` does inside
            # `_align_for_styling` — distinct from plan.residue (the *first*
            # parse's residue) and reported the same way for the same reason:
            # an `ambiguous_code_prefix` here is pass 1's own edits, not the
            # original document, and is just as unsafe to drop silently.
            pass2_residue: list = []
            if needs_pass2:
                # When pass 1 wrote nothing the already-fetched plan.doc is
                # still current, so pass 2 runs against it and skips a
                # redundant GET; otherwise the document has moved and must be
                # re-read. plan.doc is already narrowed to the resolved tab,
                # which is why PushPlan carries resolved_tab_id.
                if plan.requests:
                    whole_doc = self._client.get_document(doc_id)
                    pass2_doc, pass2_tab_id, _ = resolve_document_tab(whole_doc, tab_id)
                else:
                    whole_doc = plan.whole_doc
                    pass2_doc, pass2_tab_id = plan.doc, plan.resolved_tab_id

                builder = DocsRequestBuilder()
                # Parsed, paired and anchor-resolved once, then shared by all
                # three consumers below. Each would otherwise re-parse the
                # document and re-run the whole SequenceMatcher — three times per
                # push, two results discarded — and that recomputation sits
                # *inside* the window between the get_document above and the
                # batch_update below, where a concurrent edit costs a conflict on
                # a document pass 1 has already changed. Measured at +43% on that
                # window for a 5000-paragraph document.
                # Heading ids from *every* tab, so an anchor into a sibling tab
                # resolves instead of being reported dead on every push forever —
                # on a file that is exactly what `pull` wrote, against a Doc whose
                # link is fine. Needs the unresolved document, since
                # resolve_document_tab narrows away the other tabs.
                alignment = builder.align(
                    pass2_doc, plan.target_nodes, heading_ids_by_tab(whole_doc)
                )
                pass2_residue = alignment.residue
                second = builder.build_second_pass_requests(
                    pass2_doc, plan.target_nodes, tab_id=pass2_tab_id, alignment=alignment,
                    resolver=resolver, local_path=local_path,
                )
                # Pass 2 aligns by content and refuses to guess (see
                # DocsRequestBuilder._align_for_styling). Anything it couldn't
                # place got no styling at all rather than styling aimed at the
                # wrong paragraph — surface that instead of returning a clean
                # "ok" over a doc whose links didn't land.
                unstyled = builder.unaligned_span_targets(
                    pass2_doc, plan.target_nodes, alignment
                )
                # An internal anchor pass 2 could not point at a heading — a
                # typo, a renamed or deleted heading, or a heading the document
                # reports without an id. It wrote no link at all rather than a
                # `url` link holding a "#fragment" the Doc cannot follow, so this
                # is the only thing standing between that and a green ✓.
                #
                # Reported rather than blocking the push: the content changes are
                # correct and wanted, and refusing the whole document over one bad
                # anchor would discard every other paragraph's styling too. Note
                # this does *not* make such a push converge — it exits non-zero
                # every time, with nothing to suppress it. That is a known open
                # decision, not a solved problem.
                dead_anchors = builder.unresolved_anchor_links(
                    pass2_doc, plan.target_nodes, alignment
                )
                # Cross-document links (a relative path to another mapped file)
                # that resolve() could not turn into a URL: an ambiguous mapping,
                # a target that isn't a pushed Google Doc yet, a fetch failure, or
                # a fragment naming no heading in the target. Reported the same
                # way as dead_anchors and for the same reason — this is the only
                # thing standing between such a link and a written `url` holding
                # the original (broken) relative path.
                #
                # This is also why an already-broken link never gets reported as
                # "fixed": the message-assembly path below only ever surfaces
                # failures (dead_anchors, cross_doc_issues, unstyled/unplaced
                # content, residue) — there is no success/fix-claim branch to
                # begin with. `grep -rn '"fixed"\|auto-fixed' src/docspan/`
                # (excluding tests/) returns nothing. See
                # tests/test_cross_doc_links_backend.py::
                # test_untouched_broken_link_is_never_claimed_as_fixed for the
                # pinned scenario: a paragraph pass 1 finds no diff for still
                # goes through pass 2 (because *some* paragraph in the document
                # needs it), and the pre-existing broken link in it is reported
                # as still-unresolved, never as fixed.
                cross_doc_issues = builder.cross_doc_link_issues(
                    pass2_doc, plan.target_nodes, alignment,
                    resolver=resolver, local_path=local_path,
                )
                # Styled table cells pass 2 could not place. Same trade as
                # `unstyled` above and the same reason it has to be said out loud:
                # the cell got no styling rather than styling aimed at whatever sat
                # at that ordinal, and a silent drop is indistinguishable from
                # success. Reachable when a table is created by this very push (its
                # cells are still empty when the ranges are computed), when a
                # concurrent edit changed a cell, or when the cell holds an inline
                # object whose index width the text search cannot see.
                unplaced_cells = builder.unplaced_table_cells(
                    pass2_doc, plan.target_nodes, alignment
                )
                if second:
                    # The document's own revisionId guards this batch the same
                    # way pass 1 is guarded, so pass 2 can't silently overwrite
                    # an edit that landed since that document was read.
                    self._client.batch_update(
                        doc_id, second, required_revision_id=pass2_doc["revisionId"]
                    )

            if (not plan.requests and not second and not unstyled
                    and not dead_anchors and not unplaced_cells and not cross_doc_issues):
                # Nothing was applied by either pass. That is now a true
                # statement about the document rather than an inference from an
                # empty request list: projection.project() removes the one class
                # of node whose delete request used to be silently dropped, so
                # the diff cannot ask for an edit the builder then declines to
                # emit.
                #
                # Residue is reported here rather than warned about, because it
                # is not a failure — it is state markdown does not describe, and
                # the document is as close to the local file as markdown can
                # express.
                skipped_retryable = self._cleanup_temp_uploads_logged(plan.temp_drive_file_ids)
                return PushResult(
                    status="skipped",
                    doc_id=doc_id,
                    message=(f"⚠ {plan.tab_warning}" if plan.tab_warning else None)
                    or describe_residue(plan.residue)
                    or describe_residue(pass2_residue)
                    or ("\n".join(plan.image_warnings) if plan.image_warnings else None)
                    or "No changes detected",
                    retryable_temp_drive_file_ids=skipped_retryable,
                )

            url = f"https://docs.google.com/document/d/{doc_id}/edit"

            # The write(s) succeeded, so any Drive files uploaded for images in
            # this push are now referenced by the document -- nothing left to
            # retry, so they're cleaned up rather than left as orphaned temp
            # files (criterion 5/7). Best-effort: a transient delete failure
            # here must not turn a successful push into a reported error, but
            # it must not be silent either -- _cleanup_temp_uploads_logged logs
            # a warning and its return value is threaded into both PushResults
            # below so a failure here is visible the same way it is on every
            # exception path.
            success_retryable = self._cleanup_temp_uploads_logged(plan.temp_drive_file_ids)

            # Every warning signal is collected, not raced. Returning on the
            # first one meant whichever fired earliest hid the rest: a single
            # typo'd anchor suppressed the multi-tab warning entirely, so
            # docspan kept writing to a possibly-wrong tab while the user read a
            # message about a link. These are independent facts about one push
            # and the user needs all of them.
            messages = [
                message
                for message in (
                    # Only after a write. Its own contract is "re-check the count
                    # after a successful batch_update", and a push that reports a
                    # dead anchor without writing anything no longer short-circuits
                    # to "skipped" above — so without this gate a no-op push could
                    # blame docspan for a comment a human resolved mid-run, and pay
                    # an extra list_comments call to do it.
                    self._comment_backstop_message(doc_id, len(plan.comments))
                    if (plan.requests or second)
                    else None,
                    self._render_unstyled(unstyled) if unstyled else None,
                    self._render_unplaced_cells(unplaced_cells) if unplaced_cells else None,
                    # Offer the keys resolution actually consulted, so the list
                    # cannot name the anchor it just called dead.
                    self._render_dead_anchors(
                        dead_anchors, sorted(s for s in alignment.slug_to_id if s)
                    )
                    if dead_anchors
                    else None,
                    self._render_cross_doc_issues(cross_doc_issues)
                    if cross_doc_issues
                    else None,
                    describe_target_residue(plan.target_residue) or None,
                    # Doc-side residue (e.g. an ambiguous code-block prefix) is only
                    # reported unconditionally above when the push is a no-op. A push
                    # that writes something else must still surface it here, or the
                    # warning this residue exists to give is silently dropped on the
                    # common case — the exact failure mode `project()`'s docstring
                    # says residue exists to avoid.
                    describe_residue(plan.residue) or None,
                    # Same reporting, for residue from pass 2's own re-parse of
                    # the document (see `pass2_residue`'s definition above) —
                    # a distinct occurrence of the same residue kind that
                    # `plan.residue` cannot see, since it comes from a parse
                    # `_build_push_plan` never runs.
                    describe_residue(pass2_residue) or None,
                    # Per-image resolution failures (missing file, oversized,
                    # unsupported format) from the pre-pass in
                    # _build_push_plan -- the image node was dropped from the
                    # write rather than crashing the whole push, so this is
                    # the only place that failure is visible.
                    ("\n".join(f"⚠ {w}" for w in plan.image_warnings))
                    if plan.image_warnings
                    else None,
                    # ⚠-prefixed here as well. Every other collected message
                    # carries one, and PushPreview.render() adds one to this same
                    # string — without it the tab warning read as a continuation
                    # of the bulleted anchor list above it, and push and dry-run
                    # rendered the same warning differently.
                    f"⚠ {plan.tab_warning}" if plan.tab_warning else None,
                )
                if message
            ]
            if messages:
                return PushResult(
                    status="warning",
                    doc_id=doc_id,
                    url=url,
                    message="\n".join(messages),
                    retryable_temp_drive_file_ids=success_retryable,
                )
            return PushResult(
                status="ok",
                doc_id=doc_id,
                url=url,
                retryable_temp_drive_file_ids=success_retryable,
            )
        except TabNotFoundError as exc:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=str(exc),
                retryable_temp_drive_file_ids=self._cleanup_plan_temp_uploads(plan),
            )
        except HttpError as exc:
            retryable = self._cleanup_plan_temp_uploads(plan)
            if exc.resp.status == 400 and "requiredRevisionId" in str(exc):
                return PushResult(
                    status="conflict",
                    doc_id=doc_id,
                    message="The doc changed since your last pull — run `docspan pull` again",
                    retryable_temp_drive_file_ids=retryable,
                )
            return PushResult(
                status="error", doc_id=doc_id, message=str(exc),
                retryable_temp_drive_file_ids=retryable,
            )
        except DiffTooExpensive as exc:
            # A clear, actionable error rather than a multi-minute hang or an
            # uncaught traceback — see DiffTooExpensive's docstring. The guard
            # itself already raised before any batch_update was ever issued,
            # so there is no partial-write state to clean up here beyond the
            # usual temp-upload cleanup.
            return PushResult(
                status=diff_too_expensive_status,
                doc_id=doc_id,
                message=str(exc),
                retryable_temp_drive_file_ids=self._cleanup_plan_temp_uploads(plan),
            )
        except Exception as exc:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=str(exc),
                retryable_temp_drive_file_ids=self._cleanup_plan_temp_uploads(plan),
            )

    def _resolve_sectioned_push_order(
        self, local_dir: str, doc_id: str, manifest_path: str
    ) -> Union[PushResult, Tuple[List[str], List[SectionManifestEntry], Optional[str]]]:
        """Load a sectioned mapping's manifest and compute push ordering.

        Extracted out of `push_sectioned` (pure extraction, no behavior
        change) — everything up to reading section file *content*.

        On any validation failure, returns the `PushResult(status="error",
        ...)` `push_sectioned` should return as-is. On success, returns
        `(ordered_filenames, present_entries, reorder_warning)`: manifest
        order followed by any filesystem-only additions, the manifest
        entries whose files still exist (for reorder classification), and
        the reorder warning string (or `None`).
        """
        try:
            entries = ManifestStore.load(manifest_path)
        except ManifestError as exc:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=f"cannot push sectioned mapping: {exc}",
            )

        if not entries:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=f"cannot push sectioned mapping: manifest {manifest_path!r} has no sections",
            )

        # A manifest entry whose file no longer exists is a *deleted* section
        # (drop it — the reused diff tail sees its content missing from the
        # target and emits the delete itself), not an error: only a wholesale
        # missing/unreadable manifest is an error condition here.
        present_files = (
            {f for f in os.listdir(local_dir) if f.endswith(".md")}
            if os.path.isdir(local_dir)
            else set()
        )
        known_filenames = {e.filename for e in entries}
        present_entries = [e for e in entries if e.filename in present_files]
        # New local .md files with no manifest entry are *added* sections —
        # appended after every known section, in filename order. Their
        # position within the reassembled document is therefore always "at
        # the end" rather than wherever their NN- prefix might suggest; a
        # section meant to land elsewhere needs a subsequent pull to
        # re-derive its manifest position. Documented simplification, not an
        # attempt to infer mid-document placement from filename ordinals.
        added_filenames = sorted(present_files - known_filenames)

        ordered_filenames = [e.filename for e in present_entries] + added_filenames
        if not ordered_filenames:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=f"cannot push sectioned mapping: no section files present in {local_dir!r}",
            )

        reorder_warning = self._classify_section_reorder(present_entries)
        return ordered_filenames, present_entries, reorder_warning

    def push_sectioned(
        self,
        local_dir: str,
        doc_id: str,
        force: bool = False,
        tab_id: Optional[str] = None,
        **kwargs: object,
    ) -> PushResult:
        """Reassemble a sectioned mapping's section files and push() the result.

        Reads `_manifest.yaml` (ManifestStore.load) for the ordered list of
        (heading_id, filename) entries, reads each section file's markdown
        in **manifest order** (not filesystem/glob order — a user can
        reorder sections by editing the manifest without renaming files)
        and concatenates them into one markdown string, then hands off to
        `_execute_push` exactly as push() does — the diff/request-emission
        tail (SequenceMatcher-based node diff -> DocsRequestBuilder.build()
        -> pass 1/pass 2 batch_update) is entirely unchanged and untouched
        by this method; only content acquisition differs.

        `local_path` passed into `_execute_push`/`_build_push_plan` is the
        *anchor* section file's own path (the manifest's first entry) —
        never `local_dir` itself. resolve_document_images/build_source only
        ever take `Path(markdown_path).resolve().parent`, so passing the
        bare directory would resolve relative image paths one level too
        high (plan.md Task 3.1.3).

        Section-level add/delete/reorder is classified (via
        difflib.SequenceMatcher keyed on heading_id, comparing the
        manifest's on-disk entry order against the section files actually
        present, sorted the way pull_sectioned names them) purely for the
        warning surfaced in a PushResult — it does not steer request
        emission. Google Docs' batchUpdate API has no "move paragraph"
        primitive (confirmed in docs_request_builder.py's `build()`: only
        equal/delete/insert/replace opcodes exist), so in general a section
        reorder can only be written as an ordinary delete-old-range +
        insert-at-new-range pair by the reused diff tail, exactly like any
        other content reorder — which would mean the reordered heading's
        `heading_id` does NOT survive the round trip (fallback rung 3 of
        plan.md's in-place-move ladder: accept heading_id churn as a
        documented limitation, by necessity since there is no rung-1 API
        primitive and rung-2 copy-then-delete would mean new request-shape
        code beyond what's demonstrated necessary here).
        In practice, for a *pure* reorder (manifest order changed, section
        content itself untouched), `docs_request_builder.py`'s `_repair`
        step recognizes the swapped run's content pairs as literally
        unchanged and folds them back to `equal` opcodes rather than
        delete+insert — so heading_ids are in fact preserved and zero
        batch_update requests are emitted; the live document's paragraph
        order is left as-is (Docs has no notion of order independent of
        content identity here). Rung-3 heading_id churn only actually
        occurs when the reordered run also carries a genuine content edit
        that the differ cannot fold back to `equal` — the next
        `pull_sectioned` re-derives the manifest fresh in that case.

        The reorder warning is only ever attached to a push that already
        writes something (`ok`/`warning`), never to a true `skipped`
        no-op: `_classify_section_reorder` compares manifest order against
        *filename* order, a static property of how the mapping is laid
        out, not evidence that anything changed since the last push — a
        mapping deliberately kept in a manifest order that differs from
        its filenames (as in the byte-for-content-identical case) would
        otherwise warn on every single push forever, which is churn, not
        signal.

        DiffTooExpensive still fires from the same single guard point
        (`_bounded_opcodes` in docs_request_builder.py) with the same
        thresholds — this method adds no per-sectioned override or
        fallback around it. It is reported as `status="blocked"` (a
        refused write, no partial batch_update ever sent) rather than
        push()'s historical `status="error"` for the same exception,
        because a sectioned push failing the guard is a decision not to
        write, not an unexpected fault — the guard itself is bit-for-bit
        identical to the non-sectioned path either way.
        """
        self._ensure_client()
        manifest_path = os.path.join(local_dir, MANIFEST_FILENAME)
        prepared = self._resolve_sectioned_push_order(local_dir, doc_id, manifest_path)
        if isinstance(prepared, PushResult):
            return prepared
        ordered_filenames, present_entries, reorder_warning = prepared

        section_paths = [os.path.join(local_dir, name) for name in ordered_filenames]
        try:
            contents = [pathlib.Path(p).read_text() for p in section_paths]
        except OSError as exc:
            return PushResult(
                status="error",
                doc_id=doc_id,
                message=f"cannot push sectioned mapping: {exc}",
            )
        combined_content = "".join(
            c if c.endswith("\n") or c == "" else c + "\n" for c in contents
        )
        anchor_path = section_paths[0]

        duplicate_image_warning = self._classify_duplicate_images(
            ordered_filenames, section_paths, contents
        )

        resolver = self._build_cross_doc_resolver(kwargs, caller="push_sectioned")
        result = self._execute_push(
            anchor_path,
            doc_id,
            force=force,
            tab_id=tab_id,
            resolver=resolver,
            content=combined_content,
            diff_too_expensive_status="blocked",
        )
        warnings = [w for w in (reorder_warning, duplicate_image_warning) if w]
        if warnings and result.status in ("ok", "warning"):
            result.status = "warning"
            result.message = (
                "\n".join([result.message, *warnings]) if result.message else "\n".join(warnings)
            )
        return result

    @staticmethod
    def _classify_section_reorder(
        entries: List[SectionManifestEntry],
    ) -> Optional[str]:
        """Detect a manifest-order reorder relative to filesystem (filename) order.

        `entries` is already filtered to manifest entries whose section file
        is still present (added/deleted sections are handled separately by
        the caller and never reach here — they have no prior manifest
        position to compare against).

        `pull_sectioned` names section files `NN-slug.md` so filesystem order
        and manifest order agree immediately after a pull. A push where they
        differ means the manifest itself was hand-edited to reorder sections
        since the last pull. Uses difflib.SequenceMatcher keyed on
        heading_id (never on slug/filename/title, which can be renamed
        without the section moving) to report *which* sections moved,
        matching the identity key `manifest.py`'s own docstring calls the
        stable one across pulls. Returns None when order matches (the
        common case — no warning needed).
        """
        manifest_order = [e.heading_id for e in entries]
        filesystem_order = [
            e.heading_id for e in sorted(entries, key=lambda e: e.filename)
        ]
        if manifest_order == filesystem_order:
            return None

        matcher = difflib.SequenceMatcher(a=filesystem_order, b=manifest_order, autojunk=False)
        moved_ids: List[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                moved_ids.extend(filesystem_order[i1:i2])
                moved_ids.extend(
                    hid for hid in manifest_order[j1:j2] if hid not in moved_ids
                )
        if not moved_ids:
            return None
        return (
            "Section order in _manifest.yaml differs from the section files' own "
            f"names ({len(moved_ids)} section(s) reordered: {', '.join(sorted(set(moved_ids)))}). "
            "Google Docs has no move primitive: if the reordered section(s) also "
            "carry a content edit, they are written as delete+insert and their "
            "heading_id will change (the manifest is regenerated fresh on the "
            "next pull); if the reorder is the only change, the differ folds the "
            "swapped content back to a no-op and nothing is written — the live "
            "document's order is left as-is."
        )

    # Matches markdown image syntax `![alt](ref "optional title")`. Deliberately
    # does not attempt to parse mermaid fences (```mermaid blocks) — those are
    # rendered fresh per section on every push (image_source.py's
    # `MermaidSource`) rather than referencing a shared on-disk/URL asset, so
    # they can never collide the way a reused filename can.
    _IMAGE_REF_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')

    @classmethod
    def _classify_duplicate_images(
        cls,
        filenames: List[str],
        section_paths: List[str],
        contents: List[str],
    ) -> Optional[str]:
        """Detect two sections referencing an image with the same filename but a different source.

        A sectioned push reassembles every section's markdown into one
        document, but each section's relative image references are still
        resolved independently (`build_source`, keyed off that section's own
        path). If two different sections happen to reference an image with
        the same final filename — e.g. both a `diagram.png` — but the refs
        actually resolve to different content (different local paths, or a
        local path in one section and a URL in another), only whichever one
        `resolve_document_images` happens to process second effectively
        "wins" any place the filename alone is used to key an upload. This is
        surfaced as a push warning rather than silently letting one section's
        image shadow the other's, naming both originating sections and the
        shared filename so the ambiguity is visible instead of guessed at.
        """
        # basename -> list of (section_filename, identity)
        seen: dict = {}
        for section_filename, section_path, content in zip(filenames, section_paths, contents):
            for ref in cls._IMAGE_REF_RE.findall(content):
                basename = os.path.basename(ref.split("?", 1)[0].split("#", 1)[0])
                if not basename:
                    continue
                source = build_source(section_path, ref)
                if isinstance(source, UrlSource):
                    identity = source.url
                elif isinstance(source, LocalPathSource):
                    identity = source.path
                else:
                    # InMemorySource / MermaidSource have no stable on-disk or
                    # URL identity; their repr (which includes all fields)
                    # is enough to distinguish genuinely different sources.
                    identity = repr(source)
                seen.setdefault(basename, []).append((section_filename, identity))

        warnings = []
        for basename, occurrences in seen.items():
            distinct_identities = {identity for _, identity in occurrences}
            if len(distinct_identities) <= 1:
                continue
            origins = sorted({section_filename for section_filename, _ in occurrences})
            warnings.append(
                f"Image {basename!r} is referenced by {len(origins)} sections "
                f"({', '.join(origins)}) with different underlying sources — "
                "only one will be used; rename one of the images to disambiguate."
            )
        if not warnings:
            return None
        return "\n".join(warnings)

    @staticmethod
    def _render_dead_anchors(anchors: list[str], available: list[str]) -> str:
        """Report internal anchors pass 2 could not point at a heading.

        Distinct from _render_unstyled: those paragraphs got no styling because
        pass 2 could not place them, whereas these were placed and styled and
        only the link was left off. Everything else about the paragraph landed,
        so saying "styling was not applied" would be wrong.

        The wording stays cause-neutral, and matches PushPreview's. Three
        different causes reach here — the author typo'd the anchor, the heading
        was renamed or deleted, or the document reports the heading with no
        `headingId` — and an earlier wording asserted the third ("the heading
        each names has no id"), which is simply false for the first, the most
        common one. It also avoids "were written": these anchors may be reported
        by a push that made no API write at all.
        """
        shown = anchors[:5]
        more = len(anchors) - len(shown)
        lines = [
            f"⚠ {len(anchors)} internal anchor(s) have no link — nothing in the "
            f"document matches what they name:",
        ]
        lines += [f"    • {anchor}" for anchor in shown]
        if more:
            lines.append(f"    • … and {more} more")
        lines.append(render_available_anchors(available))
        return "\n".join(lines)

    @staticmethod
    def _render_cross_doc_issues(issues: list[str]) -> str:
        """Report cross-document links pass 2 could not resolve to a target URL.

        Distinct from _render_dead_anchors: a same-document anchor names no
        heading in this document, whereas these name another mapped file whose
        mapping is ambiguous, isn't a pushed Google Doc yet, could not be
        fetched, or whose fragment names no heading in *that* document. Each
        detail string already explains its own cause (see
        cross_doc_links.CrossDocResolution), so this only formats the list.
        """
        shown = issues[:5]
        more = len(issues) - len(shown)
        lines = [
            f"⚠ {len(issues)} cross-document link(s) have no link — docspan could "
            f"not resolve what they point at:",
        ]
        lines += [f"    • {issue}" for issue in shown]
        if more:
            lines.append(f"    • … and {more} more")
        return "\n".join(lines)

    @staticmethod
    def _render_unplaced_cells(cells: list[str]) -> str:
        """Styled table cells whose styling was not written.

        Distinct from _render_unstyled, which is about paragraphs.

        Two causes with opposite remedies, so the message names both rather than
        promising the wrong one. A table this push *created* is still empty when pass 2
        computes its ranges, and pushing again places the styling. But a cell holding a
        smart chip, an inline object, or more than one paragraph can never be placed,
        so "push again" is false there — and because a warning exits non-zero, such a
        document would exit 1 on every push forever while being told to retry.
        Non-convergence is the same known open decision `unresolved_anchor_links`
        documents; misdescribing the remedy is not, so that part is fixed here.
        """
        preview = [(text[:40] or "(empty)") for text in cells[:5]]
        more = len(cells) - len(preview)
        listed = "; ".join(preview) + (f"; +{more} more" if more > 0 else "")
        return (
            f"⚠ {len(cells)} table cell(s) kept their text but not their formatting "
            f"— docspan could not locate them in the written document, so it wrote no "
            f"styling rather than styling aimed at the wrong cell: {listed}. "
            f"If this push created the table, pushing again places the styling. "
            f"Otherwise the cell holds something docspan cannot measure around — a "
            f"smart chip, an image, or more than one paragraph — and pushing again "
            f"will report this same warning; style that cell in the document instead."
        )

    @staticmethod
    def _render_unstyled(unstyled: list[DocsParagraphNode]) -> str:
        """One-line-per-paragraph report of inline styling pass 2 declined to apply."""
        preview = [(node.text[:60] or "(empty)") for node in unstyled[:5]]
        more = len(unstyled) - len(preview)
        lines = [
            f"⚠ inline styling (links/bold/italic/code) was not applied to "
            f"{len(unstyled)} paragraph(s) — docspan could not match them in the "
            f"written doc and would not guess:",
        ]
        lines += [f"    • {text}" for text in preview]
        if more > 0:
            lines.append(f"    • …and {more} more")
        return "\n".join(lines)

    @staticmethod
    def _render_unreadable_links(kinds: list[str]) -> Optional[str]:
        """Report link kinds a tab-scoped pull could not express in markdown.

        These are dropped from the written file — until now dropped in
        silence, the same failure heading anchors were fixed for on sibling
        members of the same `Link` union, plus table cells. The Doc still has
        them, so nothing is lost yet; the warning buys the author finding out
        now rather than after a later push rewrites the paragraph and takes
        the link with it.
        """
        if not kinds:
            return None
        lines = [
            f"⚠ {len(kinds)} kind(s) of link could not be represented in markdown "
            f"and are absent from the pulled file (the Doc still has them):"
        ]
        lines += [f"    • {kind}" for kind in kinds]
        return "\n".join(lines)

    def _fetch_target_headings(self, doc_id: str, tab_id: Optional[str]) -> list:
        """CrossDocLinkResolver's FetchHeadings callback for this backend.

        Fetches and parses a *target* document — not the one being pushed —
        purely to read its headings for fragment resolution. Raises on
        failure; CrossDocLinkResolver.resolve() catches that and reports
        "fetch_failed" rather than this bubbling up and failing the source
        document's own push (criterion 5).
        """
        assert self._client is not None
        raw = self._client.get_document(doc_id)
        resolved_doc, _resolved_tab_id, _ = resolve_document_tab(raw, tab_id)
        return DocsStructureParser().parse(resolved_doc)

    def _comment_backstop_message(self, doc_id: str, before_count: int) -> Optional[str]:
        """CommentCountBackstop — orthogonal, exact check independent of the
        substring heuristic in find_high_risk_paragraphs(). Re-checks the
        open-comment count after a successful batch_update(); a drop
        escalates status to "warning", never leaves it "ok" with only a
        message appended (see plan.md Task 1.2.3c / ADR-002). Returns None
        when the count didn't drop.

        Returns the message rather than a whole PushResult so push() can report
        it *alongside* its other warnings instead of instead of them.
        """
        assert self._client is not None
        after_count = len(self._client.list_comments(doc_id))
        if after_count < before_count:
            return (
                f"⚠ open comment count dropped ({before_count}→{after_count}) — "
                "a comment may have been lost even though it wasn't flagged"
            )
        return None

    def pull(
        self, doc_id: str, local_path: str, tab_id: Optional[str] = None, **kwargs: object
    ) -> PullResult:
        """Fetch the Google Doc, convert to markdown, write locally.

        Default (tab_id=None): unchanged pre-tabs-support behavior — export
        via Drive's HTML export (files.export) and run it through
        DocumentConverter.html_to_markdown(). Drive export always returns the
        doc's first/default tab and cannot target a specific tab; if the doc
        has more than one tab, status is escalated to "warning" (not "ok")
        so a silent wrong-tab pull (the bug this parameter exists to fix)
        is surfaced instead of hidden.

        Explicit tab_id: Drive export can't select a tab, so this instead
        re-fetches structurally (get_document + resolve_document_tab +
        DocsStructureParser.parse) and renders back to markdown with
        render_nodes_to_markdown() — the same structural machinery push()
        uses, run in reverse.
        """
        self._ensure_client()
        assert self._client is not None
        try:
            if tab_id is not None:
                doc = self._client.get_document(doc_id)
                doc, _resolved_tab_id, _warning = resolve_document_tab(doc, tab_id)
                parser = DocsStructureParser()
                nodes = parser.parse(doc)
                # Render what markdown can represent, and nothing else. The
                # renderer had no way to express a TITLE, so it emitted the bare
                # text, which re-parsed as NORMAL_TEXT and made the next push
                # demote the title. project() maps it to the nearest style
                # markdown *does* have, so pull/push is a fixpoint.
                nodes, residue = project(nodes)
                markdown_content = render_nodes_to_markdown(nodes)
                pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(local_path).write_text(markdown_content)
                self._write_comment_sidecar(doc_id, local_path)
                # Only the two kinds that can hide real authored content are
                # surfaced here. `paragraph_style` (e.g. TITLE) is mapped to
                # the closest markdown heading, not dropped — pull/push stays
                # a fixpoint and nothing is lost. `empty_paragraph` is blank
                # whitespace, already low-stakes on the push side. But
                # `private_use_glyph`/`ambiguous_code_prefix` paragraphs are
                # elided from the markdown entirely with nothing left in
                # their place, which is exactly the silent-drop failure mode
                # project()'s docstring exists to avoid, and push() already
                # surfaces it unconditionally.
                residue_note = describe_residue(
                    [r for r in residue if r.kind in ("private_use_glyph", "ambiguous_code_prefix")]
                )
                # Collected, not raced — one warning must not hide the other.
                messages = [
                    message
                    for message in (
                        f"⚠ {residue_note}" if residue_note else None,
                        self._render_unreadable_links(parser.unreadable_links),
                    )
                    if message
                ]
                if messages:
                    return PullResult(
                        status="warning",
                        doc_id=doc_id,
                        local_path=local_path,
                        message="\n".join(messages),
                    )
                return PullResult(status="ok", doc_id=doc_id, local_path=local_path)

            doc = self._client.get_document(doc_id)
            resolved_doc, _resolved_tab_id, warning = resolve_document_tab(doc, None)

            html_content = self._client.get_doc_content(doc_id)
            markdown_content = DocumentConverter().html_to_markdown(html_content)
            # Drive's HTML export carries a heading link through as the Doc's own
            # opaque fragment — verified live, `[A1](#h.70l3py5ob5tg)`. That
            # pushes back correctly (ids resolve before slugs) but it is a dead
            # link in GitHub or any other markdown renderer, and it overwrites
            # the readable anchor the author wrote. The structural pull already
            # emits the slug; this gives the default path the same upgrade, using
            # the document fetched just above for the tab check. Ids the document
            # does not know are left exactly as they are.
            structural_nodes, _residue = project(DocsStructureParser().parse(resolved_doc))
            markdown_content = upgrade_heading_id_anchors(
                markdown_content, heading_id_to_slug(structural_nodes)
            )

            markdown_content, checkbox_warning = self._recover_checkbox_state(
                doc_id, structural_nodes, markdown_content
            )

            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown_content)
            self._write_comment_sidecar(doc_id, local_path)

            # NOT reporting DocsStructureParser().unreadable_links here (see #38).
            # This path's markdown comes from Drive's HTML export, which does
            # write a bookmark href and a table-cell link — the structural parse
            # above serves only the id->slug map. Reporting it would claim those
            # links are "absent from the pulled file" while they sit in the file
            # just written. Only the tab-scoped path above can report what the
            # file lacks, because there the parser's output *is* the file.
            messages = [w for w in (warning, checkbox_warning) if w]
            if messages:
                return PullResult(
                    status="warning",
                    doc_id=doc_id,
                    local_path=local_path,
                    message="⚠ " + "\n⚠ ".join(messages),
                )
            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        except TabNotFoundError as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))
        except DiffTooExpensive as exc:
            # pull() does not currently run DocsRequestBuilder's diff, but this
            # keeps the two entry points symmetric and future-proofs pull()
            # against ever gaining a diff-based path without a clear error
            # silently degrading into the generic Exception branch below.
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))
        except Exception as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))

    def pull_sectioned(
        self,
        doc_id: str,
        local_dir: str,
        split_level: Optional[str] = None,
        tab_id: Optional[str] = None,
        **kwargs: object,
    ) -> PullResult:
        """Fetch the Google Doc, split it at `split_level`, write one file per section.

        Always uses the structural path (DocsStructureParser + project()),
        never Drive's HTML export — HTML export always returns the whole
        document and cannot be scoped to a heading range (plan.md Task
        2.1.3). `local_dir` ends up holding `NN-slug.md` per section plus a
        `_manifest.yaml` sidecar (manifest.py) recording each section's
        `heading_id`/slug/filename in order.

        Every section file and the manifest are written to a temp directory
        first, then swapped into place with two `os.replace` calls (Task
        6.1.1, Epic 6's atomicity requirement folded in here): a crash
        during the write loop leaves `local_dir` completely untouched, and a
        crash between the two replaces is recoverable by hand from the
        `.<name>.old.*` sibling directory left behind — never a half-written
        set of section files.
        """
        if split_level is None:
            raise ValueError("pull_sectioned requires split_level")
        self._ensure_client()
        assert self._client is not None
        target_dir = pathlib.Path(local_dir)
        try:
            doc = self._client.get_document(doc_id)
            doc, _resolved_tab_id, _warning = resolve_document_tab(doc, tab_id)
            nodes = DocsStructureParser().parse(doc)
            nodes, residue = project(nodes)

            existing_entries: List[SectionManifestEntry] = []
            manifest_path = target_dir / MANIFEST_FILENAME
            if manifest_path.exists():
                try:
                    existing_entries = ManifestStore.load(str(manifest_path))
                except ManifestError:
                    existing_entries = []

            sections = split_nodes(nodes, split_level, existing_entries=existing_entries)
            width = max(2, len(str(len(sections) - 1)))

            tmp_parent = target_dir.parent if str(target_dir.parent) else pathlib.Path(".")
            tmp_parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = pathlib.Path(
                tempfile.mkdtemp(dir=str(tmp_parent), prefix=f".{target_dir.name}.", suffix=".tmp")
            )
            try:
                entries: List[SectionManifestEntry] = []
                section_texts: List[Tuple[str, str]] = []
                for index, section in enumerate(sections):
                    filename = f"{str(index).zfill(width)}-{section.slug}.md"
                    content = render_nodes_to_markdown(section.nodes) if section.nodes else ""
                    (tmp_dir / filename).write_text(content)
                    section_texts.append((filename, content))
                    entries.append(
                        SectionManifestEntry(
                            heading_id=section.heading_id,
                            slug=section.slug,
                            filename=filename,
                            title=section.title or None,
                        )
                    )
                ManifestStore.save(str(tmp_dir / MANIFEST_FILENAME), entries)
                self._write_sectioned_comment_sidecars(doc_id, tmp_dir, section_texts)
                self._atomic_replace_dir(tmp_dir, target_dir)
            except Exception:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise

            residue_note = describe_residue(
                [r for r in residue if r.kind in ("private_use_glyph", "ambiguous_code_prefix")]
            )
            if residue_note:
                return PullResult(
                    status="warning", doc_id=doc_id, local_path=local_dir, message=f"⚠ {residue_note}"
                )
            return PullResult(status="ok", doc_id=doc_id, local_path=local_dir)
        except TabNotFoundError as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_dir, message=str(exc))
        except SectionSplitError as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_dir, message=str(exc))
        except DiffTooExpensive as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_dir, message=str(exc))
        except Exception as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_dir, message=str(exc))

    @staticmethod
    def _atomic_replace_dir(tmp_dir: pathlib.Path, target_dir: pathlib.Path) -> None:
        """Swap a fully-populated `tmp_dir` into place as `target_dir`.

        Delegates to `core.atomic_dir.atomic_replace_dir` (shared with
        `core/orchestrator.py`'s string-path callers) — kept as a method
        here since callers in this class already invoke it as
        `self._atomic_replace_dir(...)`.
        """
        atomic_replace_dir(tmp_dir, target_dir)

    def _recover_checkbox_state(
        self, doc_id: str, structural_nodes: list, markdown_content: str
    ) -> tuple:
        """Recover native-checkbox checked/unchecked state for the default pull path.

        `documents.get()` never exposes a checkbox's checked bit (see ADR-001),
        but Drive's markdown export does. This pairs the ordered
        is_native_checkbox paragraphs from `structural_nodes` (already parsed
        just above, for the heading-slug map) against the ordered checklist
        lines from that export, and patches them into `markdown_content`.

        Fails closed: a doc with no native checkboxes, an export transport
        failure, a checkbox-count mismatch, or any unmatched line all leave
        `markdown_content` byte-identical to today's rendering (the glyph text,
        with no checked/unchecked signal) — never a partial or guessed patch.
        Returns (markdown_content, warning_or_None).
        """
        checkbox_nodes = [
            n
            for n in structural_nodes
            if isinstance(n, DocsParagraphNode) and n.is_native_checkbox
        ]
        if not checkbox_nodes:
            return markdown_content, None

        assert self._client is not None
        try:
            export_text = self._client.fetch_markdown_export(doc_id)
        except Exception:
            return (
                markdown_content,
                "Could not recover native checkbox checked/unchecked state "
                "(markdown export failed) — all checkboxes pulled as unchecked.",
            )

        states = extract_checkbox_states(export_text)
        if len(states) != len(checkbox_nodes):
            return (
                markdown_content,
                "Native checkbox count from the markdown export "
                f"({len(states)}) didn't match the document's checkbox "
                f"paragraphs ({len(checkbox_nodes)}) — all checkboxes pulled "
                "as unchecked rather than risk a wrong match.",
            )

        patched, all_found = patch_checkbox_lines(
            markdown_content, [(n.text, checked) for n, checked in zip(checkbox_nodes, states)]
        )
        if not all_found:
            return (
                markdown_content,
                "Could not confidently locate every native checkbox's line in "
                "the pulled markdown — all checkboxes pulled as unchecked "
                "rather than risk a wrong match.",
            )
        return patched, None

    def _write_sectioned_comment_sidecars(
        self, doc_id: str, tmp_dir: pathlib.Path, section_texts: List[Tuple[str, str]]
    ) -> None:
        """Bucket the doc's comments into one `{file}.comments.md` sidecar per
        section, written into `tmp_dir` alongside that section's `.md` file so
        they land inside the same atomic temp-dir-then-swap `pull_sectioned`
        already uses (Epic 6).

        Best-effort, matching `_write_comment_sidecar`'s single-file
        semantics: any failure fetching comments or doc metadata is logged
        and swallowed rather than failing the whole sectioned pull. Only
        sections with at least one bucketed comment get a sidecar file — no
        empty `{file}.comments.md` files are written (plan.md Story 4.1).
        Comments whose quoted text matches no section are logged as residue
        by `bucket_comments_by_section` and otherwise dropped, consistent
        with the Observability Plan's warning-only residue treatment.
        """
        if not self.config.pull_comments:
            return
        assert self._client is not None
        try:
            comments = self._client.get_comments(doc_id)
        except Exception:
            logger.warning("Could not fetch comments for %s — skipping comment sidecars.", doc_id)
            return
        if not comments:
            return
        try:
            title = self._client.get_doc_info(doc_id).get("name", doc_id)
        except Exception:
            title = doc_id

        try:
            bucketed = bucket_comments_by_section(list(comments), section_texts)
        except Exception:
            logger.warning(
                "Failed to bucket comments for %s by section — skipping comment sidecars.", doc_id
            )
            return

        for filename, section_comments in bucketed.by_section.items():
            if not section_comments:
                continue
            sidecar = tmp_dir / (filename + COMMENTS_SUFFIX)
            sidecar.write_text(format_comments_markdown(title, section_comments))

    def _write_comment_sidecar(self, doc_id: str, local_path: str) -> None:
        """Write a {file}.comments.md sidecar of the doc's comments (best-effort)."""
        if not self.config.pull_comments:
            return
        assert self._client is not None
        sidecar = pathlib.Path(str(local_path) + COMMENTS_SUFFIX)
        try:
            comments = self._client.get_comments(doc_id)
        except Exception:
            return  # comments are best-effort; never fail a pull over them
        if comments:
            try:
                title = self._client.get_doc_info(doc_id).get("name", doc_id)
            except Exception:
                title = doc_id
            sidecar.write_text(format_comments_markdown(title, comments))
        elif sidecar.exists():
            sidecar.unlink()  # no comments anymore — drop a stale sidecar

    def respond_to_comments(self, doc_id: str, local_path: str) -> RespondResult:
        """
        Post Reply:/Resolve: directives written into a `.comments.md` sidecar
        back to the live Google Doc, then refresh the sidecar so posted
        replies show up in-thread and resolved comments move to ## Resolved.

        Directives are matched to comments by the `<!-- id:... -->` marker
        format_comments_markdown() writes — editing that marker breaks the
        match, so a stale/hand-written id silently posts nothing.
        """
        self._ensure_client()
        assert self._client is not None
        sidecar = pathlib.Path(str(local_path) + COMMENTS_SUFFIX)
        if not sidecar.exists():
            return RespondResult(posted=0, resolved=0)

        directives = parse_reply_directives(sidecar.read_text())
        posted = resolved = 0
        for directive in directives:
            self._client.create_reply(
                doc_id, directive.comment_id, directive.reply, resolve=directive.resolve
            )
            if directive.reply:
                posted += 1
            if directive.resolve:
                resolved += 1

        self._write_comment_sidecar(doc_id, local_path)
        return RespondResult(posted=posted, resolved=resolved)

    def get_remote_version(self, doc_id: str) -> str:
        """Return the revisionId of the Google Doc (opaque, non-empty string)."""
        self._ensure_client()
        assert self._client is not None
        doc = self._client.get_document(doc_id)
        return doc["revisionId"]

    def create(self, title: str, **kwargs: object) -> CreateResult:
        """Create a new, empty Google Doc and return its id/title/url."""
        self._ensure_client()
        assert self._client is not None
        doc = self._client.create_document(title)
        doc_id = doc["documentId"]
        return CreateResult(
            doc_id=doc_id,
            title=doc.get("title", title),
            url=f"https://docs.google.com/document/d/{doc_id}/edit",
        )

    def _has_any_credentials(self) -> bool:
        token = self.config.token_path or default_token_path()
        token_exists = bool(pathlib.Path(os.path.expanduser(token)).exists())
        return bool(
            self.config.credentials_path
            or self.config.oauth_client_secret_path
            or os.getenv("ACCOUNT_A_CREDENTIALS_PATH")
            or os.getenv("ACCOUNT_A_CREDENTIALS")
            or token_exists
        )

    def auth_setup(self, config_path: "Optional[str]" = None) -> None:
        """Guided, interactive Google Docs auth setup (falls back to instructions with no TTY)."""
        # Already configured → verify and stop.
        if self._has_any_credentials():
            print("Google Docs is already configured.")
            try:
                self._ensure_client()
                print("✔ Connection verified.")
            except Exception as exc:
                print(f"✖ Connection failed: {exc}\n  Re-run to reconfigure.")
            return

        # Non-interactive (CI, piped) → print instructions, never block.
        if not is_interactive():
            self._print_setup_instructions()
            return

        print("\nLet's connect docspan to Google Docs.\n")
        print("How should docspan sign in?")
        print("  1) Personal (OAuth)  — sign in as yourself in the browser. [recommended]")
        print("  2) Service account   — a robot key, no browser. Best for CI / automation.")
        choice = input("Method [1]: ").strip() or "1"
        if choice.startswith("2"):
            self._setup_service_account_interactive(config_path)
        else:
            self._setup_oauth_interactive(config_path)

    def _setup_oauth_interactive(self, config_path: "Optional[str]") -> None:
        path = self.config.oauth_client_secret_path
        if path and not validate_client_secret(path)[0]:
            path = None
        if not path:
            found = autodetect_client_secret()
            if found and confirm(f"Found a client secret: {found}\nUse this file? [Y/n]: ", True):
                path = found
        attempts = 0
        while not path:
            entered = input("Path to client_secret.json (Enter for help creating one): ").strip()
            if not entered:
                print(OAUTH_HELP)
                continue
            ok, msg = validate_client_secret(entered)
            if ok:
                path = os.path.expanduser(entered)
            else:
                print(f"✖ {msg}")
                attempts += 1
                if attempts >= 3:
                    print("Giving up after 3 tries. Re-run when you have the file.")
                    return

        self.config.oauth_client_secret_path = path
        oauth = OAuthAuthenticator(client_secret_path=path, token_path=self.config.token_path)
        try:
            print("\nOpening your browser to sign in… (waiting for approval)")
            oauth.get_credentials(allow_interactive=True)
        except Exception as exc:
            print(f"✖ Sign-in didn't finish: {exc}")
            return
        print(f"✔ Signed in. Token cached at {oauth.token_path}")

        self._client = None
        try:
            self._ensure_client()
            print("✔ Connection OK — docspan can read and write your Google Docs.")
        except Exception as exc:
            print(f"✖ Connection verify failed: {exc}")
            return

        if confirm("\nSave this to markgate.yaml so you won't set it up again? [Y/n]: ", True):
            saved = persist_google_docs_config(
                config_path,
                {"oauth_client_secret_path": path, "token_path": self.config.token_path},
            )
            print(f"✔ Saved to {saved}")
            print(f"  (token stored at {oauth.token_path}, outside your repo)")
        print("\n✔ Done — docspan is connected to Google Docs.")
        print("→ Next:  docspan push   |   docspan pull")

    def _setup_service_account_interactive(self, config_path: "Optional[str]") -> None:
        print("\nService accounts act as a robot (not you) and need no browser.")
        attempts = 0
        key_path = None
        while not key_path:
            entered = input("Path to the service-account key JSON: ").strip()
            ok, msg = validate_service_account(entered) if entered else (False, "no path given.")
            if ok:
                key_path = os.path.expanduser(entered)
                if msg:
                    print(f"✔ Loaded service account: {msg}")
                    print(f"→ Share the Docs/folders you want to sync with {msg} (Editor access).")
            else:
                print(f"✖ {msg}")
                attempts += 1
                if attempts >= 3:
                    print("Giving up after 3 tries.")
                    return

        self.config.credentials_path = key_path
        self._client = None
        try:
            self._ensure_client()
            print("✔ Connection OK.")
        except Exception as exc:
            print(f"✖ Connection verify failed: {exc}")
            return
        if confirm("\nSave this to markgate.yaml? [Y/n]: ", True):
            saved = persist_google_docs_config(config_path, {"credentials_path": key_path})
            print(f"✔ Saved to {saved}")
        print("\n✔ Done. → Next:  docspan push   |   docspan pull")

    def _print_setup_instructions(self) -> None:
        print("\nGoogle Docs Auth Setup")
        print("=" * 40)
        print("Run this in an interactive terminal for a guided setup, or configure manually:")
        print("\n  Per-user OAuth (recommended — acts as you, like gws):")
        print("    1. Create an OAuth client (Desktop app); download client_secret.json")
        print("    2. docspan auth setup google_docs --oauth --client-secret /path/to/client_secret.json")
        print("       (or set backends.google_docs.oauth_client_secret_path in markgate.yaml)")
        print("\n  Service account (automation):")
        print("    1. Create a service account + JSON key; enable the Docs & Drive APIs")
        print("    2. Share your docs with the service-account email")
        print("    3. Set credentials_path in markgate.yaml (or ACCOUNT_A_CREDENTIALS_PATH env)")

    def validate_config(self) -> None:
        if not self._has_any_credentials():
            raise ValueError(
                "Missing Google Docs credentials. Configure a service account "
                "(credentials_path / ACCOUNT_A_CREDENTIALS_PATH) or per-user OAuth "
                "(oauth_client_secret_path). Run: docspan auth setup google_docs"
            )
