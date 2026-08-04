"""Google Docs backend."""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Optional

from googleapiclient.errors import HttpError

from docspan.backends.base import Backend, PullResult, PushResult
from docspan.backends.google_docs.auth import (
    DualAccountAuth,
    GoogleAuthenticator,
    OAuthAuthenticator,
    default_token_path,
)
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.backends.google_docs.comments import (
    RespondResult,
    format_comments_markdown,
    parse_reply_directives,
)
from docspan.backends.google_docs.converter import DocumentConverter
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
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
from docspan.backends.google_docs.projection import describe_residue, project
from docspan.backends.google_docs.push_preview import (
    PushPlan,
    PushPreview,
    find_high_risk_paragraphs,
    render_available_anchors,
    render_high_risk,
)
from docspan.backends.google_docs.tabs import (
    TabNotFoundError,
    heading_ids_by_tab,
    resolve_document_tab,
)
from docspan.core.paths import COMMENTS_SUFFIX

if TYPE_CHECKING:
    from docspan.config import GoogleDocsConfig, MarkgateConfig


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

    def _build_push_plan(
        self, local_path: str, doc_id: str, tab_id: Optional[str] = None
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
        """
        assert self._client is not None
        content = pathlib.Path(local_path).read_text()

        target_nodes = MarkdownToParagraphParser().parse(content)
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
        # Projecting the target is a no-op today and no test can make it fail:
        # MarkdownToParagraphParser cannot emit an empty-text node, so there is
        # nothing on this side to drop. It is here so the two sides cannot drift
        # apart if that ever changes — the whole bug was an asymmetry between
        # these two parsers, and applying the projection to only one of them
        # would be the same shape of mistake.
        target_nodes, _target_residue = project(target_nodes)

        body_content = doc.get("body", {}).get("content", [])
        doc_end_index = body_content[-1].get("endIndex", 1) if body_content else 1

        request_builder = DocsRequestBuilder()
        requests = request_builder.build(
            current_nodes, target_nodes, doc_end_index, tab_id=resolved_tab_id
        )
        entries, unchanged_count = request_builder.diff_summary(current_nodes, target_nodes)

        comments = self._client.list_comments(doc_id)
        high_risk = find_high_risk_paragraphs(entries, comments)

        return PushPlan(
            current_nodes=current_nodes,
            target_nodes=target_nodes,
            requests=requests,
            doc=doc,
            entries=entries,
            unchanged_count=unchanged_count,
            comments=comments,
            high_risk=high_risk,
            tab_warning=tab_warning,
            resolved_tab_id=resolved_tab_id,
            residue=current_residue,
            whole_doc=whole_doc,
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
            unresolved = unresolved_anchors(plan.target_nodes, document_nodes)
            available = available_anchor_slugs(plan.target_nodes, document_nodes)
        except HttpError as exc:
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
        assert self._client is not None
        try:
            plan = self._build_push_plan(local_path, doc_id, tab_id=tab_id)

            if plan.requests and plan.high_risk and not force:
                return PushResult(
                    status="blocked",
                    doc_id=doc_id,
                    message=render_high_risk(plan.high_risk),
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
            # used to be checked in the wrong order. The diff key is
            # (style, text, is_list_item) — it does not include marks — so
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
            dead_anchors: list[str] = []
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
                second = builder.build_second_pass_requests(
                    pass2_doc, plan.target_nodes, tab_id=pass2_tab_id, alignment=alignment
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
                if second:
                    # The document's own revisionId guards this batch the same
                    # way pass 1 is guarded, so pass 2 can't silently overwrite
                    # an edit that landed since that document was read.
                    self._client.batch_update(
                        doc_id, second, required_revision_id=pass2_doc["revisionId"]
                    )

            if not plan.requests and not second and not unstyled and not dead_anchors:
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
                return PushResult(
                    status="skipped",
                    doc_id=doc_id,
                    message=(f"⚠ {plan.tab_warning}" if plan.tab_warning else None)
                    or describe_residue(plan.residue)
                    or "No changes detected",
                )

            url = f"https://docs.google.com/document/d/{doc_id}/edit"

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
                    # Offer the keys resolution actually consulted, so the list
                    # cannot name the anchor it just called dead.
                    self._render_dead_anchors(
                        dead_anchors, sorted(s for s in alignment.slug_to_id if s)
                    )
                    if dead_anchors
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
                    status="warning", doc_id=doc_id, url=url, message="\n".join(messages)
                )
            return PushResult(status="ok", doc_id=doc_id, url=url)
        except TabNotFoundError as exc:
            return PushResult(status="error", doc_id=doc_id, message=str(exc))
        except HttpError as exc:
            if exc.resp.status == 400 and "requiredRevisionId" in str(exc):
                return PushResult(
                    status="conflict",
                    doc_id=doc_id,
                    message="The doc changed since your last pull — run `docspan pull` again",
                )
            return PushResult(status="error", doc_id=doc_id, message=str(exc))
        except Exception as exc:
            return PushResult(status="error", doc_id=doc_id, message=str(exc))

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
    def _render_unreadable_links(kinds: list[str]) -> Optional[str]:
        """Report link kinds a pull could not express in markdown.

        These are dropped from the file, and until now dropped in silence — the
        same failure heading anchors were fixed for, on sibling members of the
        same `Link` union plus table cells. The Doc keeps them, so nothing is lost
        yet; what the warning buys is that the author finds out *now* rather than
        after a later push rewrites the paragraph and takes the link with it.
        """
        if not kinds:
            return None
        lines = [
            f"⚠ {len(kinds)} kind(s) of link could not be represented in markdown and "
            f"are absent from the pulled file (the Doc still has them):"
        ]
        lines += [f"    • {kind}" for kind in kinds]
        return "\n".join(lines)

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
                nodes, _residue = project(nodes)
                markdown_content = render_nodes_to_markdown(nodes)
                pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(local_path).write_text(markdown_content)
                self._write_comment_sidecar(doc_id, local_path)
                dropped = self._render_unreadable_links(parser.unreadable_links)
                if dropped:
                    return PullResult(
                        status="warning",
                        doc_id=doc_id,
                        local_path=local_path,
                        message=dropped,
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
            parser = DocsStructureParser()
            markdown_content = upgrade_heading_id_anchors(
                markdown_content,
                heading_id_to_slug(project(parser.parse(resolved_doc))[0]),
            )
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown_content)
            self._write_comment_sidecar(doc_id, local_path)

            # Collected, not raced — same reason as push()'s warnings.
            messages = [
                message
                for message in (warning, self._render_unreadable_links(parser.unreadable_links))
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
        except TabNotFoundError as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))
        except Exception as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))

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
