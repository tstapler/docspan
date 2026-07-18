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
from docspan.backends.google_docs.comments import format_comments_markdown
from docspan.backends.google_docs.converter import DocumentConverter
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.onboarding import (
    OAUTH_HELP,
    autodetect_client_secret,
    confirm,
    is_interactive,
    persist_google_docs_config,
    validate_client_secret,
    validate_service_account,
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

    def push(self, local_path: str, doc_id: str, **kwargs: object) -> PushResult:
        """Convert local markdown to Google Docs format using structural diff and batch update."""
        self._ensure_client()
        assert self._client is not None
        try:
            content = pathlib.Path(local_path).read_text()

            target_nodes = MarkdownToParagraphParser().parse(content)
            doc = self._client.get_document(doc_id)
            current_nodes = DocsStructureParser().parse(doc)

            if "tabs" in doc and doc["tabs"]:
                body_content = doc["tabs"][0].get("documentTab", doc).get("body", {}).get("content", [])
            else:
                body_content = doc.get("body", {}).get("content", [])
            doc_end_index = body_content[-1].get("endIndex", 1) if body_content else 1

            builder = DocsRequestBuilder()
            requests = builder.build(current_nodes, target_nodes, doc_end_index)
            if not requests:
                return PushResult(status="skipped", doc_id=doc_id, message="No changes detected")

            self._client.batch_update(
                doc_id, requests, required_revision_id=doc["revisionId"]
            )

            # Pass 2: tables are inserted empty and inline styling is deferred above; re-fetch
            # to read real indices, then fill cells + apply link/bold/italic/monospace styling.
            # The re-fetch's own revisionId guards this second batch_update the same way the
            # first one is guarded above, so pass 2 can't silently overwrite an edit that landed
            # in the (small) window between pass 1 and this re-fetch.
            needs_pass2 = any(
                isinstance(n, DocsTableNode)
                or (isinstance(n, DocsParagraphNode) and n.spans)
                for n in target_nodes
            )
            if needs_pass2:
                refreshed = self._client.get_document(doc_id)
                second = builder.build_second_pass_requests(refreshed, target_nodes)
                if second:
                    self._client.batch_update(
                        doc_id, second, required_revision_id=refreshed["revisionId"]
                    )

            url = f"https://docs.google.com/document/d/{doc_id}/edit"
            return PushResult(status="ok", doc_id=doc_id, url=url)
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

    def pull(self, doc_id: str, local_path: str, **kwargs: object) -> PullResult:
        """Export Google Doc as HTML, convert to markdown, write locally."""
        self._ensure_client()
        assert self._client is not None
        try:
            html_content = self._client.get_doc_content(doc_id)
            markdown_content = DocumentConverter().html_to_markdown(html_content)
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown_content)
            self._write_comment_sidecar(doc_id, local_path)
            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
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
