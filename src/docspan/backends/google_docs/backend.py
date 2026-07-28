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
from docspan.backends.google_docs.push_preview import (
    PushPlan,
    PushPreview,
    find_high_risk_paragraphs,
    render_high_risk,
)
from docspan.backends.google_docs.tabs import TabNotFoundError, resolve_document_tab
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
        doc = self._client.get_document(doc_id)
        doc, resolved_tab_id, tab_warning = resolve_document_tab(doc, tab_id)
        current_nodes = DocsStructureParser().parse(doc)

        body_content = doc.get("body", {}).get("content", [])
        doc_end_index = body_content[-1].get("endIndex", 1) if body_content else 1

        request_builder = DocsRequestBuilder()
        requests = request_builder.build(
            current_nodes, target_nodes, doc_end_index, tab_id=resolved_tab_id
        )
        entries, unchanged_count = request_builder.diff_summary(current_nodes, target_nodes)
        unappliable = request_builder.unappliable_removals(
            current_nodes, target_nodes, doc_end_index
        )

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
            unappliable_removals=unappliable,
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

            if not plan.requests:
                # An empty request list does NOT always mean "the doc already
                # matches". A delete whose range trims to nothing is dropped
                # (DocsRequestBuilder._make_delete_requests) — that happens to
                # an already-empty paragraph pinned by the newline anchoring a
                # Table/TableOfContents/SectionBreak. diff_summary() still
                # reports it as a removal, and it really is still in the doc,
                # so "No changes detected" here would be a false parity claim.
                if plan.unappliable_removals:
                    count = len(plan.unappliable_removals)
                    return PushResult(
                        status="warning",
                        doc_id=doc_id,
                        message=(
                            f"{count} blank paragraph(s) can't be deleted through the "
                            "Google Docs API — each one holds open a table, table of "
                            "contents or section break — so the doc still differs from "
                            "the local file. Remove them by hand in Google Docs "
                            "(backspace at the start of the blank line) to match."
                        ),
                    )
                return PushResult(
                    status="skipped",
                    doc_id=doc_id,
                    message=plan.tab_warning or "No changes detected",
                )

            if plan.high_risk and not force:
                return PushResult(
                    status="blocked",
                    doc_id=doc_id,
                    message=render_high_risk(plan.high_risk),
                )

            self._client.batch_update(
                doc_id, plan.requests, required_revision_id=plan.doc["revisionId"]
            )

            # Pass 2: tables are inserted empty and inline styling is deferred above; re-fetch
            # to read real indices, then fill cells + apply link/bold/italic/monospace styling.
            # The re-fetch's own revisionId guards this second batch_update the same way the
            # first one is guarded above, so pass 2 can't silently overwrite an edit that landed
            # in the (small) window between pass 1 and this re-fetch.
            needs_pass2 = any(
                isinstance(n, DocsTableNode)
                or (isinstance(n, DocsParagraphNode) and n.spans)
                for n in plan.target_nodes
            )
            unstyled: list[DocsParagraphNode] = []
            if needs_pass2:
                refreshed = self._client.get_document(doc_id)
                refreshed, resolved_tab_id, _ = resolve_document_tab(refreshed, tab_id)
                builder = DocsRequestBuilder()
                second = builder.build_second_pass_requests(
                    refreshed, plan.target_nodes, tab_id=resolved_tab_id
                )
                # Pass 2 aligns by content and refuses to guess (see
                # DocsRequestBuilder._align_for_styling). Anything it couldn't
                # place got no styling at all rather than styling aimed at the
                # wrong paragraph — surface that instead of returning a clean
                # "ok" over a doc whose links didn't land.
                unstyled = builder.unaligned_span_targets(refreshed, plan.target_nodes)
                if second:
                    self._client.batch_update(
                        doc_id, second, required_revision_id=refreshed["revisionId"]
                    )

            url = f"https://docs.google.com/document/d/{doc_id}/edit"

            backstop_result = self._comment_backstop_result(doc_id, len(plan.comments), url)
            if backstop_result is not None:
                return backstop_result
            if unstyled:
                return PushResult(
                    status="warning",
                    doc_id=doc_id,
                    url=url,
                    message=self._render_unstyled(unstyled),
                )
            if plan.tab_warning:
                return PushResult(status="warning", doc_id=doc_id, url=url, message=plan.tab_warning)
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

    def _comment_backstop_result(
        self, doc_id: str, before_count: int, url: str
    ) -> PushResult | None:
        """CommentCountBackstop — orthogonal, exact check independent of the
        substring heuristic in find_high_risk_paragraphs(). Re-checks the
        open-comment count after a successful batch_update(); a drop
        escalates status to "warning", never leaves it "ok" with only a
        message appended (see plan.md Task 1.2.3c / ADR-002). Returns None
        when the count didn't drop.
        """
        assert self._client is not None
        after_count = len(self._client.list_comments(doc_id))
        if after_count < before_count:
            return PushResult(
                status="warning",
                doc_id=doc_id,
                url=url,
                message=(
                    f"⚠ open comment count dropped ({before_count}→{after_count}) — "
                    "a comment may have been lost even though it wasn't flagged"
                ),
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
                nodes = DocsStructureParser().parse(doc)
                markdown_content = render_nodes_to_markdown(nodes)
                pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(local_path).write_text(markdown_content)
                self._write_comment_sidecar(doc_id, local_path)
                return PullResult(status="ok", doc_id=doc_id, local_path=local_path)

            doc = self._client.get_document(doc_id)
            _doc, _resolved_tab_id, warning = resolve_document_tab(doc, None)

            html_content = self._client.get_doc_content(doc_id)
            markdown_content = DocumentConverter().html_to_markdown(html_content)
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown_content)
            self._write_comment_sidecar(doc_id, local_path)

            if warning:
                return PullResult(
                    status="warning", doc_id=doc_id, local_path=local_path, message=warning
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
