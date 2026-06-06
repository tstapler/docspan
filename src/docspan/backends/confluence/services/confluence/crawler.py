"""
Confluence space crawler for extracting pages and ADF content.

This module provides functionality to crawl Confluence spaces and extract
page content in ADF (Atlassian Document Format) for analysis and comparison.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from docspan.backends.confluence.services.confluence.base_client import BaseConfluenceClient, ConfluenceApiError


@dataclass
class PageMetadata:
    """Metadata for a crawled Confluence page."""

    id: str
    title: str
    space_key: str
    version: int
    status: str
    created_date: str
    modified_date: str
    creator: Optional[str] = None
    last_modifier: Optional[str] = None
    parent_id: Optional[str] = None
    url: Optional[str] = None
    labels: List[str] = field(default_factory=list)


@dataclass
class CrawledPage:
    """A crawled Confluence page with metadata and content."""

    metadata: PageMetadata
    adf_content: Dict[str, Any]
    storage_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "metadata": {
                "id": self.metadata.id,
                "title": self.metadata.title,
                "space_key": self.metadata.space_key,
                "version": self.metadata.version,
                "status": self.metadata.status,
                "created_date": self.metadata.created_date,
                "modified_date": self.metadata.modified_date,
                "creator": self.metadata.creator,
                "last_modifier": self.metadata.last_modifier,
                "parent_id": self.metadata.parent_id,
                "url": self.metadata.url,
                "labels": self.metadata.labels,
            },
            "adf_content": self.adf_content,
            "storage_content": self.storage_content,
        }


class SpaceCrawler:
    """
    Crawl Confluence spaces to extract pages and ADF content.

    This class provides methods to recursively crawl Confluence spaces,
    extract page content in ADF format, and save it for analysis.
    """

    def __init__(self, client: BaseConfluenceClient, comment_client=None):
        """
        Initialize the space crawler.

        Args:
            client: Confluence API client
            comment_client: Optional ConfluenceCommentClient for fetching page comments
        """
        self.client = client
        self.comment_client = comment_client
        self.logger = logging.getLogger(__name__)

    def crawl_space(
        self,
        space_key: str,
        max_pages: Optional[int] = None,
        include_archived: bool = False,
    ) -> List[CrawledPage]:
        """
        Crawl all pages in a Confluence space.

        Args:
            space_key: Confluence space key to crawl
            max_pages: Maximum number of pages to crawl (None for all)
            include_archived: Whether to include archived pages

        Returns:
            List of crawled pages with metadata and content

        Raises:
            ConfluenceApiError: If API calls fail
        """
        self.logger.info(f"Starting crawl of space '{space_key}'")
        crawled_pages = []

        # Get all pages in the space
        pages = self._get_all_pages_in_space(space_key, include_archived)

        if max_pages:
            pages = pages[:max_pages]

        self.logger.info(f"Found {len(pages)} pages to crawl")

        for i, page_info in enumerate(pages, 1):
            try:
                self.logger.info(f"Crawling page {i}/{len(pages)}: {page_info['title']} (ID: {page_info['id']})")
                crawled_page = self._crawl_page(page_info['id'])
                crawled_pages.append(crawled_page)
            except Exception as e:
                self.logger.error(f"Failed to crawl page {page_info['id']}: {e}")
                continue

        self.logger.info(f"Successfully crawled {len(crawled_pages)} pages")
        return crawled_pages

    def crawl_page_tree(
        self,
        root_page_id: str,
        max_depth: Optional[int] = None,
    ) -> List[CrawledPage]:
        """
        Crawl a page and all its descendants recursively.

        Args:
            root_page_id: Root page ID to start crawling from
            max_depth: Maximum depth to crawl (None for unlimited)

        Returns:
            List of crawled pages in breadth-first order
        """
        self.logger.info(f"Starting tree crawl from page ID '{root_page_id}'")
        crawled_pages = []

        def crawl_recursive(page_id: str, current_depth: int = 0) -> None:
            if max_depth is not None and current_depth > max_depth:
                return

            try:
                # Crawl current page
                crawled_page = self._crawl_page(page_id)
                crawled_pages.append(crawled_page)

                # Get child pages
                children = self._get_child_pages(page_id)
                for child in children:
                    crawl_recursive(child['id'], current_depth + 1)

            except Exception as e:
                self.logger.error(f"Failed to crawl page tree at {page_id}: {e}")

        crawl_recursive(root_page_id)
        self.logger.info(f"Successfully crawled {len(crawled_pages)} pages in tree")
        return crawled_pages

    def save_crawl_results(
        self,
        pages: List[CrawledPage],
        output_dir: Path,
        create_index: bool = True,
        include_attachments: bool = False,
        include_comments: bool = True,
    ) -> None:
        """
        Save crawled pages to disk.

        Args:
            pages: List of crawled pages
            output_dir: Directory to save pages to
            create_index: Whether to create an index file
            include_attachments: Whether to download and save page attachments
            include_comments: Whether to fetch and save footer and inline comments
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save each page
        for page in pages:
            page_dir = output_dir / f"{page.metadata.id}_{self._sanitize_filename(page.metadata.title)}"
            page_dir.mkdir(exist_ok=True)

            # Save metadata
            metadata_file = page_dir / "metadata.json"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(page.metadata.__dict__, f, indent=2, ensure_ascii=False)

            # Save ADF content
            adf_file = page_dir / "content.adf.json"
            with open(adf_file, 'w', encoding='utf-8') as f:
                json.dump(page.adf_content, f, indent=2, ensure_ascii=False)

            # Save storage format if available
            if page.storage_content:
                storage_file = page_dir / "content.storage.html"
                with open(storage_file, 'w', encoding='utf-8') as f:
                    f.write(page.storage_content)

            # Download attachments if requested
            if include_attachments:
                self._save_attachments(page.metadata.id, page_dir)

            # Fetch and save comments if requested and client is available
            if include_comments and self.comment_client:
                self._save_comments(page.metadata.id, page_dir)

        # Create index file
        if create_index:
            index_data = [page.to_dict() for page in pages]
            index_file = output_dir / "index.json"
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Saved {len(pages)} pages to {output_dir}")

    def _save_comments(self, page_id: str, page_dir: Path) -> None:
        """
        Fetch and save all comments for a page.

        Saves footer comments, inline comments, and a human-readable summary:
        - comments.footer.json: Raw footer comments from v2 API
        - comments.inline.json: Raw inline comments from v2 API (includes anchor text)
        - comments.md: Human-readable Markdown summary of all comments

        Args:
            page_id: Confluence page ID
            page_dir: Directory where page files are saved
        """
        footer_data = {}
        inline_data = {}

        try:
            footer_data = self.comment_client.get_footer_comments(page_id)
            footer_count = len(footer_data.get("results", []))
            if footer_count:
                footer_file = page_dir / "comments.footer.json"
                with open(footer_file, 'w', encoding='utf-8') as f:
                    json.dump(footer_data, f, indent=2, ensure_ascii=False)
                self.logger.info(f"Saved {footer_count} footer comments for page {page_id}")
        except Exception as e:
            self.logger.warning(f"Failed to fetch footer comments for page {page_id}: {e}")

        try:
            inline_data = self.comment_client.get_inline_comments(page_id)
            inline_count = len(inline_data.get("results", []))
            if inline_count:
                inline_file = page_dir / "comments.inline.json"
                with open(inline_file, 'w', encoding='utf-8') as f:
                    json.dump(inline_data, f, indent=2, ensure_ascii=False)
                self.logger.info(f"Saved {inline_count} inline comments for page {page_id}")
        except Exception as e:
            self.logger.warning(f"Failed to fetch inline comments for page {page_id}: {e}")

        # Write human-readable summary if any comments exist
        footer_results = footer_data.get("results", [])
        inline_results = inline_data.get("results", [])

        if not footer_results and not inline_results:
            return

        lines = [f"# Comments\n"]

        if footer_results:
            lines.append(f"## Footer Comments ({len(footer_results)})\n")
            for comment in footer_results:
                lines.append(self._format_comment_md(comment, comment_type="footer"))

        if inline_results:
            lines.append(f"## Inline Comments ({len(inline_results)})\n")
            for comment in inline_results:
                lines.append(self._format_comment_md(comment, comment_type="inline"))

        comments_md = page_dir / "comments.md"
        with open(comments_md, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

    def _format_comment_md(self, comment: Dict[str, Any], comment_type: str = "footer") -> str:
        """
        Format a v2 API comment as a Markdown block.

        Args:
            comment: Comment dict from v2 API response
            comment_type: 'footer' or 'inline'

        Returns:
            Markdown-formatted string
        """
        comment_id = comment.get("id", "unknown")
        version = comment.get("version", {})
        author_id = version.get("authorId", "unknown")
        created_at = version.get("createdAt", "")

        # Extract body text (storage format is HTML-ish; strip tags for readability)
        body_storage = comment.get("body", {}).get("storage", {})
        body_html = body_storage.get("value", "") if isinstance(body_storage, dict) else ""
        body_text = re.sub(r'<[^>]+>', '', body_html).strip()

        lines = [f"### Comment {comment_id}"]
        lines.append(f"- **Author**: {author_id}")
        lines.append(f"- **Date**: {created_at}")
        lines.append(f"- **Type**: {comment_type}")

        if comment_type == "inline":
            props = comment.get("properties", {})
            selection = props.get("inlineOriginalSelection", "")
            if selection:
                lines.append(f"- **Highlighted**: `{selection[:120]}`")
            resolution = comment.get("resolutionStatus", "")
            if resolution:
                lines.append(f"- **Resolution**: {resolution}")

        if body_text:
            lines.append(f"\n{body_text}")

        return "\n".join(lines) + "\n"

    def _save_attachments(self, page_id: str, page_dir: Path) -> None:
        """
        Download and save all attachments for a page.

        Creates an attachments/ subdirectory containing each attachment file
        and an attachments.json manifest.

        Args:
            page_id: Confluence page ID
            page_dir: Directory where page files are saved
        """
        attachments = self._get_page_attachments(page_id)
        if not attachments:
            return

        attachments_dir = page_dir / "attachments"
        attachments_dir.mkdir(exist_ok=True)

        manifest = []
        for attachment in attachments:
            filename = attachment.get("title", attachment.get("id", "unknown"))
            download_path = attachment.get("_links", {}).get("download")
            attachment_id = attachment.get("id", "")

            manifest.append({
                "id": attachment_id,
                "filename": filename,
                "media_type": attachment.get("metadata", {}).get("mediaType", ""),
                "file_size": attachment.get("extensions", {}).get("fileSize"),
                "comment": attachment.get("metadata", {}).get("comment", ""),
            })

            if not download_path:
                self.logger.warning(f"No download link for attachment '{filename}' on page {page_id}")
                continue

            try:
                content = self._download_attachment_content(download_path)
                safe_filename = self._sanitize_filename(filename)
                out_path = attachments_dir / safe_filename
                # Avoid collisions if sanitization produces duplicate names
                if out_path.exists() and out_path.read_bytes() != content:
                    stem = out_path.stem
                    suffix = out_path.suffix
                    out_path = attachments_dir / f"{stem}_{attachment_id}{suffix}"
                out_path.write_bytes(content)
                self.logger.info(f"Saved attachment: {safe_filename} ({len(content):,} bytes)")
            except Exception as e:
                self.logger.error(f"Failed to download attachment '{filename}': {e}")

        manifest_file = page_dir / "attachments.json"
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def _get_page_attachments(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Fetch the list of attachments for a page.

        Args:
            page_id: Confluence page ID

        Returns:
            List of attachment metadata dicts from the Confluence API
        """
        attachments = []
        start = 0
        limit = 100

        while True:
            params = {"limit": limit, "start": start, "expand": "metadata,extensions"}
            response = self.client.session.get(
                f"{self.client.rest_api_url}/content/{page_id}/child/attachment",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            attachments.extend(results)

            if len(results) < limit:
                break
            start += limit

        return attachments

    def _download_attachment_content(self, download_path: str) -> bytes:
        """
        Download the binary content of an attachment.

        Args:
            download_path: Relative download path from the Confluence API response

        Returns:
            Binary content of the attachment
        """
        # download_path is relative to the wiki base (e.g. /wiki/download/attachments/...)
        wiki_base = self.client.rest_api_url.replace("/rest/api", "")
        url = f"{wiki_base}{download_path}"
        response = self.client.session.get(url)
        response.raise_for_status()
        return response.content

    def _crawl_page(self, page_id: str) -> CrawledPage:
        """
        Crawl a single page and extract its content.

        Args:
            page_id: Confluence page ID

        Returns:
            Crawled page with metadata and content
        """
        # Request page with all necessary expansions
        params = {
            'expand': 'body.atlas_doc_format,body.storage,version,space,history,metadata.labels'
        }

        response = self.client.session.get(
            f"{self.client.rest_api_url}/content/{page_id}",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        # Extract metadata
        metadata = self._extract_metadata(data)

        # Extract ADF content
        adf_content = {}
        if 'body' in data and 'atlas_doc_format' in data['body']:
            adf_content = data['body']['atlas_doc_format'].get('value', {})
            if isinstance(adf_content, str):
                adf_content = json.loads(adf_content)

        # Extract storage format
        storage_content = None
        if 'body' in data and 'storage' in data['body']:
            storage_content = data['body']['storage'].get('value')

        return CrawledPage(
            metadata=metadata,
            adf_content=adf_content,
            storage_content=storage_content,
        )

    def _extract_metadata(self, page_data: Dict[str, Any]) -> PageMetadata:
        """Extract page metadata from API response."""
        version_info = page_data.get('version', {})
        history_info = page_data.get('history', {})
        space_info = page_data.get('space', {})

        # Extract labels
        labels = []
        if 'metadata' in page_data and 'labels' in page_data['metadata']:
            labels = [
                label.get('name', '')
                for label in page_data['metadata']['labels'].get('results', [])
            ]

        return PageMetadata(
            id=page_data['id'],
            title=page_data['title'],
            space_key=space_info.get('key', ''),
            version=version_info.get('number', 0),
            status=page_data.get('status', 'current'),
            created_date=history_info.get('createdDate', ''),
            modified_date=version_info.get('when', ''),
            creator=history_info.get('createdBy', {}).get('displayName'),
            last_modifier=version_info.get('by', {}).get('displayName'),
            parent_id=page_data.get('ancestors', [{}])[-1].get('id') if page_data.get('ancestors') else None,
            url=page_data.get('_links', {}).get('webui'),
            labels=labels,
        )

    def _get_all_pages_in_space(
        self,
        space_key: str,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get all pages in a space using pagination.

        Args:
            space_key: Confluence space key
            include_archived: Whether to include archived pages

        Returns:
            List of page information dictionaries
        """
        pages = []
        start = 0
        limit = 100

        while True:
            params = {
                'spaceKey': space_key,
                'limit': limit,
                'start': start,
                'expand': 'version,space',
            }

            if include_archived:
                params['status'] = 'current,archived'

            response = self.client.session.get(
                f"{self.client.rest_api_url}/content",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get('results', [])
            pages.extend(results)

            # Check if there are more pages
            if len(results) < limit:
                break

            start += limit

        return pages

    def _get_child_pages(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Get child pages of a parent page.

        Args:
            page_id: Parent page ID

        Returns:
            List of child page information
        """
        params = {
            'expand': 'version',
        }

        response = self.client.session.get(
            f"{self.client.rest_api_url}/content/{page_id}/child/page",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        return data.get('results', [])

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize a string for use as a filename."""
        # Replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        # Limit length
        if len(filename) > 100:
            filename = filename[:100]

        return filename.strip()
