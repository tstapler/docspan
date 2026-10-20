"""
Confluence Comment Client — full v2 API coverage.

Endpoints implemented (all under /wiki/api/v2/):

Footer comments:
  GET  /footer-comments                        get_all_footer_comments()
  POST /footer-comments                        create_footer_comment()  [replaces v1]
  GET  /footer-comments/{id}                   get_footer_comment()
  PUT  /footer-comments/{id}                   update_footer_comment()
  DEL  /footer-comments/{id}                   delete_footer_comment()
  GET  /footer-comments/{id}/children          get_footer_comment_children()
  GET  /pages/{id}/footer-comments             get_page_footer_comments()
  GET  /blogposts/{id}/footer-comments         get_blogpost_footer_comments()
  GET  /attachments/{id}/footer-comments       get_attachment_footer_comments()
  GET  /custom-content/{id}/footer-comments    get_custom_content_footer_comments()

Inline comments:
  GET  /inline-comments                        get_all_inline_comments()
  POST /inline-comments                        create_inline_comment()  [replaces v1]
  GET  /inline-comments/{id}                   get_inline_comment()
  PUT  /inline-comments/{id}                   update_inline_comment()  (also resolves)
  DEL  /inline-comments/{id}                   delete_inline_comment()
  GET  /pages/{id}/inline-comments             get_page_inline_comments()
  GET  /blogposts/{id}/inline-comments         get_blogpost_inline_comments()

Legacy v1 (retained for rendered-HTML access):
  GET  /wiki/rest/api/content/{id}/child/comment   get_comments()
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.services.confluence.base_client import (
    ConfluenceApiError,
    PageNotFoundError,
)

logger = logging.getLogger(__name__)


class CommentNotFoundError(ConfluenceApiError):
    """Raised when a comment cannot be found."""
    pass


class InlineCommentNotSupportedError(ConfluenceApiError):
    """
    Raised when an inline comment operation is not supported.

    Kept for backwards compatibility. The v2 API now supports creating and
    replying to inline comments, so this should rarely be raised.
    """
    pass


class ConfluenceCommentClient:
    """
    Full v2 API client for Confluence comments.

    Footer comments:
      ✅ get_all_footer_comments()          GET  /footer-comments
      ✅ create_footer_comment()            POST /footer-comments
      ✅ get_footer_comment()               GET  /footer-comments/{id}
      ✅ update_footer_comment()            PUT  /footer-comments/{id}
      ✅ delete_footer_comment()            DEL  /footer-comments/{id}
      ✅ get_footer_comment_children()      GET  /footer-comments/{id}/children
      ✅ get_page_footer_comments()         GET  /pages/{id}/footer-comments
      ✅ get_blogpost_footer_comments()     GET  /blogposts/{id}/footer-comments
      ✅ get_attachment_footer_comments()   GET  /attachments/{id}/footer-comments
      ✅ get_custom_content_footer_comments() GET /custom-content/{id}/footer-comments

    Inline comments:
      ✅ get_all_inline_comments()          GET  /inline-comments
      ✅ create_inline_comment()            POST /inline-comments
      ✅ get_inline_comment()               GET  /inline-comments/{id}
      ✅ update_inline_comment()            PUT  /inline-comments/{id}
      ✅ delete_inline_comment()            DEL  /inline-comments/{id}
      ✅ get_page_inline_comments()         GET  /pages/{id}/inline-comments
      ✅ get_blogpost_inline_comments()     GET  /blogposts/{id}/inline-comments

    Reply helpers:
      ✅ reply_to_footer_comment()  → create_footer_comment(parent_comment_id=...)
      ✅ reply_to_inline_comment()  → create_inline_comment(parent_comment_id=...)

    Legacy v1 (for rendered body.view HTML):
      ✅ get_comments()                     GET  /wiki/rest/api/content/{id}/child/comment
    """

    def __init__(self, config: ConfluenceConfig):
        self.config = config
        self.base_url = config.base_url.rstrip('/')
        self._v2 = f"{self.base_url}/wiki/api/v2"
        self._v1 = f"{self.base_url}/wiki/rest/api"
        self.auth = HTTPBasicAuth(config.username, config.api_token)
        self.session = requests.Session()
        self.session.auth = self.auth

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _get(self, url: str, params: Optional[Dict] = None) -> Dict:
        """GET with standard error handling."""
        try:
            response = self.session.get(url, params=params or {})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 404:
                raise PageNotFoundError(f"Not found: {url}", status_code=404, response=e.response)
            raise ConfluenceApiError(f"GET {url} failed: {e}", status_code=status, response=e.response)

    def _post(self, url: str, payload: Dict) -> Dict:
        """POST with standard error handling."""
        try:
            response = self.session.post(url, json=payload, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 404:
                raise PageNotFoundError(f"Not found: {url}", status_code=404, response=e.response)
            raise ConfluenceApiError(f"POST {url} failed: {e}", status_code=status, response=e.response)

    def _put(self, url: str, payload: Dict) -> Dict:
        """PUT with standard error handling."""
        try:
            response = self.session.put(url, json=payload, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 404:
                raise CommentNotFoundError(f"Comment not found: {url}", status_code=404, response=e.response)
            raise ConfluenceApiError(f"PUT {url} failed: {e}", status_code=status, response=e.response)

    def _delete(self, url: str) -> None:
        """DELETE with standard error handling."""
        try:
            response = self.session.delete(url)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 404:
                raise CommentNotFoundError(f"Comment not found: {url}", status_code=404, response=e.response)
            raise ConfluenceApiError(f"DELETE {url} failed: {e}", status_code=status, response=e.response)

    def _collect_pages(self, url: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Fetch all results across cursor-based pagination.

        Follows _links.next until exhausted. Returns flat list of all result objects.
        """
        params = dict(params or {})
        all_results: List[Dict] = []

        while True:
            data = self._get(url, params)
            all_results.extend(data.get("results", []))

            next_cursor = data.get("_links", {}).get("next")
            if not next_cursor:
                break

            # next_cursor is a relative URL like /wiki/api/v2/...?cursor=xxx&limit=yyy
            # Extract the cursor value from it
            import urllib.parse as _up
            parsed = _up.urlparse(next_cursor)
            qs = _up.parse_qs(parsed.query)
            cursor = qs.get("cursor", [None])[0]
            if not cursor:
                break
            params["cursor"] = cursor

        return all_results

    @staticmethod
    def _body_payload(body_value: str, representation: str = "storage") -> Dict:
        """Build body dict for create/update payloads."""
        if representation == "storage" and not body_value.strip().startswith("<"):
            body_value = f"<p>{body_value}</p>"
        return {"representation": representation, "value": body_value}

    # ──────────────────────────────────────────────
    # Footer comments — READ
    # ──────────────────────────────────────────────

    def get_all_footer_comments(
        self,
        body_format: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """
        GET /footer-comments — all footer comments (global, paginated).

        Returns a flat list of all FooterCommentModel objects.
        """
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/footer-comments", params)

    def get_footer_comment(
        self,
        comment_id: str,
        body_format: Optional[str] = None,
        version: Optional[int] = None,
        include_properties: bool = False,
        include_operations: bool = False,
        include_likes: bool = False,
        include_versions: bool = False,
    ) -> Dict:
        """GET /footer-comments/{comment-id} — retrieve a single footer comment."""
        params: Dict[str, Any] = {}
        if body_format:
            params["body-format"] = body_format
        if version is not None:
            params["version"] = version
        if include_properties:
            params["include-properties"] = "true"
        if include_operations:
            params["include-operations"] = "true"
        if include_likes:
            params["include-likes"] = "true"
        if include_versions:
            params["include-versions"] = "true"
        return self._get(f"{self._v2}/footer-comments/{comment_id}", params)

    def get_footer_comment_children(
        self,
        comment_id: str,
        body_format: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /footer-comments/{id}/children — replies to a footer comment (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/footer-comments/{comment_id}/children", params)

    def get_page_footer_comments(
        self,
        page_id: str,
        body_format: Optional[str] = None,
        status: Optional[List[str]] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /pages/{id}/footer-comments — root footer comments on a page (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if status:
            params["status"] = status
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/pages/{page_id}/footer-comments", params)

    def get_blogpost_footer_comments(
        self,
        blogpost_id: str,
        body_format: Optional[str] = None,
        status: Optional[List[str]] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /blogposts/{id}/footer-comments — root footer comments on a blog post (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if status:
            params["status"] = status
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/blogposts/{blogpost_id}/footer-comments", params)

    def get_attachment_footer_comments(
        self,
        attachment_id: str,
        body_format: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /attachments/{id}/footer-comments — footer comments on an attachment (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/attachments/{attachment_id}/footer-comments", params)

    def get_custom_content_footer_comments(
        self,
        content_id: str,
        body_format: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /custom-content/{id}/footer-comments — footer comments on custom content (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/custom-content/{content_id}/footer-comments", params)

    # ──────────────────────────────────────────────
    # Footer comments — WRITE
    # ──────────────────────────────────────────────

    def create_footer_comment(
        self,
        body_value: str,
        *,
        page_id: Optional[str] = None,
        blogpost_id: Optional[str] = None,
        parent_comment_id: Optional[str] = None,
        attachment_id: Optional[str] = None,
        custom_content_id: Optional[str] = None,
        body_representation: str = "storage",
    ) -> Dict:
        """
        POST /footer-comments — create a footer comment.

        Exactly one of page_id, blogpost_id, attachment_id, or custom_content_id must be
        provided (or parent_comment_id for a reply).

        Args:
            body_value: Comment body text (HTML for storage, plain text auto-wrapped).
            page_id: Target page ID.
            blogpost_id: Target blog post ID.
            parent_comment_id: Parent comment ID for replies.
            attachment_id: Target attachment ID.
            custom_content_id: Target custom content ID.
            body_representation: Body format — "storage" (default) or "atlas_doc_format".

        Example:
            >>> comment = client.create_footer_comment("Great doc!", page_id="123456")
            >>> reply = client.create_footer_comment("I agree!", parent_comment_id="789")
        """
        payload: Dict[str, Any] = {"body": self._body_payload(body_value, body_representation)}
        if page_id:
            payload["pageId"] = page_id
        if blogpost_id:
            payload["blogPostId"] = blogpost_id
        if parent_comment_id:
            payload["parentCommentId"] = parent_comment_id
        if attachment_id:
            payload["attachmentId"] = attachment_id
        if custom_content_id:
            payload["customContentId"] = custom_content_id
        return self._post(f"{self._v2}/footer-comments", payload)

    def reply_to_footer_comment(self, parent_comment_id: str, body_value: str) -> Dict:
        """Convenience wrapper: create_footer_comment(parent_comment_id=...)."""
        return self.create_footer_comment(body_value, parent_comment_id=parent_comment_id)

    def update_footer_comment(
        self,
        comment_id: str,
        body_value: str,
        version_number: int,
        version_message: str = "",
        body_representation: str = "storage",
    ) -> Dict:
        """
        PUT /footer-comments/{comment-id} — update a footer comment body.

        Args:
            comment_id: ID of the comment to update.
            body_value: New body text.
            version_number: Current version number + 1 (Confluence increments versions).
            version_message: Optional change message.
            body_representation: "storage" or "atlas_doc_format".
        """
        payload: Dict[str, Any] = {
            "version": {"number": version_number, "message": version_message},
            "body": self._body_payload(body_value, body_representation),
        }
        return self._put(f"{self._v2}/footer-comments/{comment_id}", payload)

    def delete_footer_comment(self, comment_id: str) -> None:
        """DELETE /footer-comments/{comment-id} — permanently delete a footer comment."""
        self._delete(f"{self._v2}/footer-comments/{comment_id}")

    # ──────────────────────────────────────────────
    # Inline comments — READ
    # ──────────────────────────────────────────────

    def get_all_inline_comments(
        self,
        body_format: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """
        GET /inline-comments — all inline comments (global, paginated).

        Returns a flat list of all InlineCommentModel objects including
        resolutionStatus and inlineOriginalSelection.
        """
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/inline-comments", params)

    def get_inline_comment(
        self,
        comment_id: str,
        body_format: Optional[str] = None,
        version: Optional[int] = None,
        include_properties: bool = False,
        include_operations: bool = False,
        include_likes: bool = False,
        include_versions: bool = False,
    ) -> Dict:
        """GET /inline-comments/{comment-id} — retrieve a single inline comment."""
        params: Dict[str, Any] = {}
        if body_format:
            params["body-format"] = body_format
        if version is not None:
            params["version"] = version
        if include_properties:
            params["include-properties"] = "true"
        if include_operations:
            params["include-operations"] = "true"
        if include_likes:
            params["include-likes"] = "true"
        if include_versions:
            params["include-versions"] = "true"
        return self._get(f"{self._v2}/inline-comments/{comment_id}", params)

    def get_page_inline_comments(
        self,
        page_id: str,
        body_format: Optional[str] = None,
        status: Optional[List[str]] = None,
        resolution_status: Optional[List[str]] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """
        GET /pages/{id}/inline-comments — root inline comments on a page (paginated).

        Each result includes:
          - resolutionStatus: "open" | "resolved"
          - properties.inlineOriginalSelection: the highlighted text
          - properties.inlineMarkerRef: marker reference for the highlight position
        """
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if status:
            params["status"] = status
        if resolution_status:
            params["resolution-status"] = resolution_status
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/pages/{page_id}/inline-comments", params)

    def get_blogpost_inline_comments(
        self,
        blogpost_id: str,
        body_format: Optional[str] = None,
        status: Optional[List[str]] = None,
        resolution_status: Optional[List[str]] = None,
        sort: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict]:
        """GET /blogposts/{id}/inline-comments — root inline comments on a blog post (paginated)."""
        params: Dict[str, Any] = {"limit": limit}
        if body_format:
            params["body-format"] = body_format
        if status:
            params["status"] = status
        if resolution_status:
            params["resolution-status"] = resolution_status
        if sort:
            params["sort"] = sort
        return self._collect_pages(f"{self._v2}/blogposts/{blogpost_id}/inline-comments", params)

    # ──────────────────────────────────────────────
    # Inline comments — WRITE
    # ──────────────────────────────────────────────

    def create_inline_comment(
        self,
        body_value: str,
        *,
        page_id: Optional[str] = None,
        blogpost_id: Optional[str] = None,
        parent_comment_id: Optional[str] = None,
        text_selection: Optional[str] = None,
        text_selection_match_count: int = 1,
        text_selection_match_index: int = 0,
        body_representation: str = "storage",
    ) -> Dict:
        """
        POST /inline-comments — create an inline comment (v2 API).

        For new inline comments, provide text_selection to anchor the highlight.
        For replies to existing inline comments, provide parent_comment_id.

        Args:
            body_value: Comment text.
            page_id: Target page ID.
            blogpost_id: Target blog post ID.
            parent_comment_id: Parent inline comment ID for replies.
            text_selection: Exact text to highlight. Required for new (non-reply) comments.
            text_selection_match_count: Total occurrences of text_selection in the page.
            text_selection_match_index: Which occurrence to anchor (0-indexed).
            body_representation: "storage" or "atlas_doc_format".

        Example:
            # New inline comment on highlighted text
            >>> client.create_inline_comment(
            ...     "This needs clarification", page_id="123", text_selection="SLA targets"
            ... )
            # Reply to existing inline comment
            >>> client.create_inline_comment(
            ...     "Agreed", parent_comment_id="456"
            ... )
        """
        payload: Dict[str, Any] = {"body": self._body_payload(body_value, body_representation)}
        if page_id:
            payload["pageId"] = page_id
        if blogpost_id:
            payload["blogPostId"] = blogpost_id
        if parent_comment_id:
            payload["parentCommentId"] = parent_comment_id
        if text_selection and not parent_comment_id:
            payload["inlineCommentProperties"] = {
                "textSelection": text_selection,
                "textSelectionMatchCount": text_selection_match_count,
                "textSelectionMatchIndex": text_selection_match_index,
            }
        return self._post(f"{self._v2}/inline-comments", payload)

    def reply_to_inline_comment(self, parent_comment_id: str, body_value: str) -> Dict:
        """
        Reply to an inline comment (v2 API).

        Previously unsupported via v1. Now fully supported via v2
        POST /inline-comments with parentCommentId.
        """
        return self.create_inline_comment(body_value, parent_comment_id=parent_comment_id)

    def update_inline_comment(
        self,
        comment_id: str,
        version_number: int,
        body_value: Optional[str] = None,
        resolved: Optional[bool] = None,
        version_message: str = "",
        body_representation: str = "storage",
    ) -> Dict:
        """
        PUT /inline-comments/{comment-id} — update body and/or resolve an inline comment.

        Args:
            comment_id: ID of the inline comment.
            version_number: New version number (current + 1).
            body_value: Updated body text (omit to keep unchanged, but version still increments).
            resolved: True to resolve the comment thread, False to re-open.
            version_message: Optional change message.
            body_representation: "storage" or "atlas_doc_format".

        Example:
            # Resolve an inline comment
            >>> client.update_inline_comment("789", version_number=2, resolved=True)
        """
        payload: Dict[str, Any] = {
            "version": {"number": version_number, "message": version_message},
        }
        if body_value is not None:
            payload["body"] = self._body_payload(body_value, body_representation)
        if resolved is not None:
            payload["resolved"] = resolved
        return self._put(f"{self._v2}/inline-comments/{comment_id}", payload)

    def resolve_inline_comment(self, comment_id: str, version_number: int) -> Dict:
        """Convenience wrapper: update_inline_comment(..., resolved=True)."""
        return self.update_inline_comment(comment_id, version_number, resolved=True)

    def delete_inline_comment(self, comment_id: str) -> None:
        """DELETE /inline-comments/{comment-id} — permanently delete an inline comment."""
        self._delete(f"{self._v2}/inline-comments/{comment_id}")

    # ──────────────────────────────────────────────
    # Legacy v1 — retained for rendered body.view
    # ──────────────────────────────────────────────

    def get_comments(
        self,
        page_id: str,
        expand: str = "body.view,version,extensions",
    ) -> Dict:
        """
        GET /wiki/rest/api/content/{id}/child/comment (v1) — all comments with rendered HTML.

        Use this when you need body.view.value (rendered HTML for text extraction).
        The v2 list endpoints do not return body.view.

        Returns:
            Dict with 'results' list. Each comment has body.view.value (rendered HTML).

        Example:
            >>> comments = client.get_comments("1145569739")
            >>> for c in comments["results"]:
            ...     print(c["body"]["view"]["value"])
        """
        return self._get(
            f"{self._v1}/content/{page_id}/child/comment",
            {"expand": expand},
        )

    # Aliases kept for backwards compatibility
    def get_footer_comments(self, page_id: str) -> Dict:
        """
        Alias for get_page_footer_comments() returning raw API dict (not flat list).

        Kept for backwards compatibility with crawler._save_comments().
        """
        return self._get(f"{self._v2}/pages/{page_id}/footer-comments")

    def get_inline_comments(self, page_id: str) -> Dict:
        """
        Alias for get_page_inline_comments() returning raw API dict (not flat list).

        Kept for backwards compatibility with crawler._save_comments().
        """
        return self._get(f"{self._v2}/pages/{page_id}/inline-comments")

    # ──────────────────────────────────────────────
    # Formatting helpers
    # ──────────────────────────────────────────────

    def format_comment_summary(self, comment: Dict) -> str:
        """
        Format a v1 API comment as a human-readable string.

        Works with get_comments() results (has body.view.value for rendered HTML).
        For v2 comments, use crawler._format_comment_md() instead.
        """
        comment_id = comment.get("id", "unknown")
        author = comment.get("version", {}).get("by", {}).get("displayName", "Unknown")
        date = comment.get("version", {}).get("friendlyWhen", "Unknown date")
        location = comment.get("extensions", {}).get("location", "footer")

        body_html = comment.get("body", {}).get("view", {}).get("value", "")
        body_text = re.sub("<[^<]+?>", "", body_html)
        body_preview = body_text[:100] + "..." if len(body_text) > 100 else body_text

        return (
            f"ID: {comment_id}\n"
            f"Author: {author}\n"
            f"Date: {date}\n"
            f"Type: {location}\n"
            f"Content: {body_preview}\n"
        )

    def save_comments_to_json(self, comments: Dict, output_path: str) -> None:
        """Save a comments response dict to a JSON file."""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(comments, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(comments.get('results', []))} comments to {output_path}")
