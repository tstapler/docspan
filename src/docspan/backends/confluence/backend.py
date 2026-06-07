"""Confluence backend."""

from __future__ import annotations

import logging
import os
import pathlib
import re
from typing import TYPE_CHECKING, Optional

import markdownify as md_lib

from docspan.backends.base import Backend, PullResult, PushResult
from docspan.backends.confluence.adf.converter import AdfConverter
from docspan.backends.confluence.config.models import ConfluenceConfig as InternalConfluenceConfig
from docspan.backends.confluence.markdown.parser import MarkdownParser
from docspan.backends.confluence.models.page import ConfluencePage
from docspan.backends.confluence.services.confluence.client import ConfluenceClient
from docspan.backends.confluence.services.confluence.comment_client import ConfluenceCommentClient
from docspan.config import ConfluenceConfig
from docspan.core.paths import COMMENTS_SUFFIX

if TYPE_CHECKING:
    from docspan.config import MarkgateConfig

logger = logging.getLogger(__name__)


class ConfluenceBackend(Backend):
    name = "confluence"

    def __init__(self, config: ConfluenceConfig) -> None:
        self.config = config
        self._client: Optional[ConfluenceClient] = None
        self._comment_client: Optional[ConfluenceCommentClient] = None
        self._internal_cfg: Optional[InternalConfluenceConfig] = None

    @classmethod
    def from_config(cls, markgate_config: "MarkgateConfig") -> "ConfluenceBackend":
        return cls(markgate_config.backends.confluence or ConfluenceConfig())

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        base_url = self.config.base_url or os.getenv("CONFLUENCE_BASE_URL")
        username = self.config.username or os.getenv("ATLASSIAN_USER_NAME")
        api_token = self.config.api_token or os.getenv("CONFLUENCE_API_TOKEN")
        if not all([base_url, username, api_token]):
            raise RuntimeError(
                "Confluence credentials incomplete. Run: docspan auth setup confluence\n"
                "Or set CONFLUENCE_BASE_URL, ATLASSIAN_USER_NAME, CONFLUENCE_API_TOKEN."
            )
        self._internal_cfg = InternalConfluenceConfig(
            base_url=base_url,
            username=username,
            api_token=api_token,
        )
        self._client = ConfluenceClient(self._internal_cfg)

    def _ensure_comment_client(self) -> None:
        if self._comment_client is not None:
            return
        self._ensure_client()
        assert self._internal_cfg is not None
        self._comment_client = ConfluenceCommentClient(self._internal_cfg)

    # ── Comment sidecar ────────────────────────────────────────────────────

    @staticmethod
    def _format_comment(comment: dict) -> list[str]:
        comment_id = comment.get("id", "unknown")
        author = (
            comment.get("createdBy", {}).get("displayName")
            or comment.get("version", {}).get("by", {}).get("displayName", "Unknown")
        )
        date = (
            comment.get("version", {}).get("createdAt", "")
            or comment.get("version", {}).get("friendlyWhen", "")
        )
        lines: list[str] = [f"### [{comment_id}] {author} — {date}", ""]
        selection = (
            comment.get("properties", {}).get("inlineOriginalSelection", "")
            or comment.get("inlineCommentProperties", {}).get("textSelection", "")
        )
        if selection:
            lines += [f'> Selection: "{selection}"', ""]
        body_html = (
            comment.get("body", {}).get("storage", {}).get("value", "")
            or comment.get("body", {}).get("view", {}).get("value", "")
        )
        body_text = re.sub(r"<[^<]+?>", "", body_html).strip()
        if body_text:
            lines.append(body_text)
        lines += ["", "---", ""]
        return lines

    def _write_comment_sidecar(
        self,
        local_path: str,
        page_title: str,
        inline_comments: list,
        footer_comments: list,
    ) -> None:
        lines = [f"# Comments: {page_title}", ""]
        if inline_comments:
            lines += ["## Inline comments", ""]
            for comment in inline_comments:
                lines += self._format_comment(comment)
        if footer_comments:
            lines += ["## Footer comments", ""]
            for comment in footer_comments:
                lines += self._format_comment(comment)
        sidecar_path = str(local_path) + COMMENTS_SUFFIX
        pathlib.Path(sidecar_path).write_text("\n".join(lines))

    # ── Backend interface ──────────────────────────────────────────────────

    def push(self, local_path: str, doc_id: str, **kwargs: object) -> PushResult:
        """Convert local markdown to ADF and update the Confluence page."""
        self._ensure_client()
        assert self._client is not None
        try:
            content = pathlib.Path(local_path).read_text()
            ast = MarkdownParser().parse(content)
            adf_doc = AdfConverter().convert(ast)

            page = self._client.get_page(doc_id)
            title = page.get("title", "Untitled")
            version = page.get("version", {}).get("number", 1)
            parent_id = page.get("parentId") or (page.get("ancestors") or [{}])[-1].get("id", "")

            confluence_page = ConfluencePage(
                id=doc_id,
                title=title,
                content=adf_doc,
                parent_id=parent_id,
                version=version,
            )
            self._client.update_page(confluence_page)
            base_url = self.config.base_url or ""
            return PushResult(status="ok", doc_id=doc_id, url=f"{base_url}/pages/{doc_id}")
        except Exception as exc:
            return PushResult(status="error", doc_id=doc_id, message=str(exc))

    def pull(self, doc_id: str, local_path: str, **kwargs: object) -> PullResult:
        """Fetch Confluence page storage content and convert to markdown."""
        self._ensure_client()
        assert self._client is not None
        try:
            page = self._client.get_page(doc_id)
            storage_html = page.get("body", {}).get("storage", {}).get("value", "")
            markdown = md_lib.markdownify(storage_html, heading_style="ATX", strip=["script", "style"])
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown)

            page_title = page.get("title", "Untitled")
            try:
                self._ensure_comment_client()
            except Exception:
                logger.warning("Could not initialise comment client for %s; skipping comments", doc_id, exc_info=True)

            inline_comments: list = []
            footer_comments: list = []

            try:
                inline_comments = self._comment_client.get_page_inline_comments(doc_id)  # type: ignore[union-attr]
            except Exception as exc:
                try:
                    v1_result = self._comment_client.get_comments(doc_id)  # type: ignore[union-attr]
                    inline_comments = v1_result.get("results", [])
                except Exception as fallback_exc:
                    logger.warning(
                        "Could not fetch inline comments for %s (v2: %s, v1 fallback: %s)",
                        doc_id, exc, fallback_exc,
                    )

            try:
                footer_comments = self._comment_client.get_page_footer_comments(doc_id)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("Could not fetch footer comments for %s: %s", doc_id, exc)

            if inline_comments or footer_comments:
                self._write_comment_sidecar(local_path, page_title, inline_comments, footer_comments)

            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        except Exception as exc:
            return PullResult(status="error", doc_id=doc_id, local_path=local_path, message=str(exc))

    def get_remote_version(self, doc_id: str) -> str:
        """Return the current Confluence page version number as a string."""
        self._ensure_client()
        assert self._client is not None
        page = self._client.get_page(doc_id)
        return str(page["version"]["number"])

    def auth_setup(self) -> None:
        """Interactive Confluence auth setup — prompts for credentials and prints YAML snippet."""
        print("\nConfluence auth setup")
        print("=" * 40)
        base_url = input("Confluence base URL (e.g. https://yourorg.atlassian.net): ").strip()
        username = input("Atlassian username (email): ").strip()
        print(
            f"\nAdd to markgate.yaml:\n\n"
            f"backends:\n"
            f"  confluence:\n"
            f"    base_url: {base_url}\n"
            f"    username: {username}\n"
            f"    api_token: <your-token>  # from id.atlassian.com/manage-profile/security/api-tokens\n"
        )
        print("Done. Test with: docspan status")

    def validate_config(self) -> None:
        base_url = self.config.base_url or os.getenv("CONFLUENCE_BASE_URL")
        username = self.config.username or os.getenv("ATLASSIAN_USER_NAME")
        api_token = self.config.api_token or os.getenv("CONFLUENCE_API_TOKEN")
        missing = []
        if not base_url:
            missing.append("base_url / CONFLUENCE_BASE_URL")
        if not username:
            missing.append("username / ATLASSIAN_USER_NAME")
        if not api_token:
            missing.append("api_token / CONFLUENCE_API_TOKEN")
        if missing:
            raise ValueError(
                f"Missing Confluence config: {', '.join(missing)}. "
                "Run: docspan auth setup confluence"
            )
