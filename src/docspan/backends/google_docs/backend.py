"""Google Docs backend."""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from docspan.backends.base import Backend, PullResult, PushResult
from docspan.backends.google_docs.auth import DualAccountAuth, GoogleAuthenticator
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.backends.google_docs.converter import DocumentConverter
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsStructureParser
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser

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
        if self.config.credentials_path:
            auth = GoogleAuthenticator(credentials_path=self.config.credentials_path)
            self._client = GoogleDocsClient(auth.get_credentials())
        else:
            dual = DualAccountAuth()
            if not dual.is_authenticated():
                raise RuntimeError(
                    "Google Docs credentials not found. "
                    "Set credentials_path in markgate.yaml or ACCOUNT_A_CREDENTIALS_PATH env var. "
                    "Run: markgate auth setup google_docs"
                )
            self._client = GoogleDocsClient(dual.get_account_a_credentials())

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

            requests = DocsRequestBuilder().build(current_nodes, target_nodes, doc_end_index)
            if not requests:
                return PushResult(status="skipped", doc_id=doc_id, message="No changes detected")

            self._client.batch_update(doc_id, requests)
            url = f"https://docs.google.com/document/d/{doc_id}/edit"
            return PushResult(status="ok", doc_id=doc_id, url=url)
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
            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        except Exception as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))

    def get_remote_version(self, doc_id: str) -> str:
        """Return the revisionId of the Google Doc (opaque, non-empty string)."""
        self._ensure_client()
        assert self._client is not None
        doc = self._client.get_document(doc_id)
        return doc["revisionId"]

    def auth_setup(self) -> None:
        """Print setup instructions for Google Docs service account credentials."""
        has_path = self.config.credentials_path or os.getenv("ACCOUNT_A_CREDENTIALS_PATH")
        has_json = os.getenv("ACCOUNT_A_CREDENTIALS")

        if has_path or has_json:
            print("Google Docs credentials are already configured.")
            try:
                self._ensure_client()
                print("✓ Connection verified successfully.")
            except Exception as exc:
                print(f"✗ Connection test failed: {exc}")
            return

        print("\nGoogle Docs Auth Setup")
        print("=" * 40)
        print("Markgate uses Google service account credentials for Google Docs access.")
        print("\nSetup steps:")
        print("  1. Create a service account at:")
        print("     https://console.cloud.google.com/iam-admin/serviceaccounts")
        print("  2. Enable Google Docs API and Google Drive API in your project")
        print("  3. Download the service account JSON key file")
        print("  4. Share your Google Docs with the service account email")
        print("\nConfigure credentials via one of:")
        print("  Option A — YAML config:")
        print("    backends:")
        print("      google_docs:")
        print("        credentials_path: /path/to/service-account.json")
        print("  Option B — environment variable (path):")
        print("    export ACCOUNT_A_CREDENTIALS_PATH=/path/to/service-account.json")
        print("  Option C — environment variable (inline JSON):")
        print("    export ACCOUNT_A_CREDENTIALS='{ ... service account JSON ... }'")

    def validate_config(self) -> None:
        has_credentials = (
            self.config.credentials_path
            or os.getenv("ACCOUNT_A_CREDENTIALS_PATH")
            or os.getenv("ACCOUNT_A_CREDENTIALS")
        )
        if not has_credentials:
            raise ValueError(
                "Missing Google Docs credentials. "
                "Set credentials_path in markgate.yaml or ACCOUNT_A_CREDENTIALS_PATH env var. "
                "Run: markgate auth setup google_docs"
            )
