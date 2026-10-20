"""
Wikilinks parser extension.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from docspan.backends.confluence.markdown.ast import LinkNode, MarkdownNode, TextNode, WikiLinkNode


class WikilinksParser:
    """
    Parser for wiki-style links ([[page]] or [[page|title]]).
    """

    PATTERN = r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"

    @classmethod
    def parse(cls, content: str) -> List[Tuple[str, MarkdownNode]]:
        """
        Parse wiki links in content and return list of replacements.

        Args:
            content: Text content to parse

        Returns:
            List of tuples (original text, WikiLinkNode)
        """
        pattern = cls.PATTERN
        matches = re.finditer(pattern, content)

        replacements = []
        for match in matches:
            original = match.group(0)
            target = match.group(1).strip()
            display = match.group(2).strip() if match.group(2) else None

            node = WikiLinkNode(target=target, display=display)
            replacements.append((original, node))

        return replacements

    @classmethod
    def replace_wikilinks(
        cls, text_node: TextNode, page_map: Dict[str, Any] = None
    ) -> List[MarkdownNode]:
        """
        Replace wiki links in a text node with link nodes.

        Args:
            text_node: Text node to process
            page_map: Optional mapping of page names to page IDs

        Returns:
            List of nodes with wiki links replaced
        """
        if not text_node.content:
            return [text_node]

        content = text_node.content
        pattern = cls.PATTERN

        # Find all wikilink matches
        matches = list(re.finditer(pattern, content))
        if not matches:
            return [text_node]

        # Create result nodes
        result = []
        last_end = 0

        for match in matches:
            start, end = match.span()

            # Add text before the link
            if start > last_end:
                result.append(TextNode(content=content[last_end:start]))

            # Process the link
            target = match.group(1).strip()
            display = match.group(2).strip() if match.group(2) else target

            # Resolve link target if page map provided
            page_id = None
            if page_map:
                page_id = cls.resolve_link(target, page_map)

            # Create a wiki link node or standard link node
            if page_id:
                # Create regular link to Confluence page
                result.append(LinkNode(url=f"#page-{page_id}", title=target))
                link_node = result[-1]
                link_node.children.append(TextNode(content=display))
            else:
                # Create wiki link node
                result.append(WikiLinkNode(target=target, display=display))

            last_end = end

        # Add remaining text
        if last_end < len(content):
            result.append(TextNode(content=content[last_end:]))

        return result

    @classmethod
    def resolve_link(cls, target: str, page_map: Dict[str, Any]) -> Optional[str]:
        """
        Resolve wiki link target to a Confluence page ID.

        Args:
            target: Link target (page name)
            page_map: Dictionary mapping page names to page IDs

        Returns:
            Page ID or None if not found
        """
        # Check direct match
        if target in page_map:
            return page_map[target]

        # Try case-insensitive match
        target_lower = target.lower()
        for name, page_id in page_map.items():
            if name.lower() == target_lower:
                return page_id

        # Try with different separators
        normalized_target = cls._normalize_page_name(target)
        for name, page_id in page_map.items():
            normalized_name = cls._normalize_page_name(name)
            if normalized_name == normalized_target:
                return page_id

        return None

    @staticmethod
    def _normalize_page_name(name: str) -> str:
        """
        Normalize a page name for comparison.

        Args:
            name: Page name to normalize

        Returns:
            Normalized page name
        """
        # Replace spaces, hyphens, and underscores with a common separator
        result = re.sub(r"[ \-_]+", "-", name.lower())
        # Remove any non-alphanumeric chars (except dashes)
        result = re.sub(r"[^\w\-]", "", result, flags=re.ASCII)
        return result

    @classmethod
    def build_page_map(cls, pages: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Build a mapping of page titles to page IDs.

        Args:
            pages: List of page data dictionaries

        Returns:
            Dictionary mapping page titles to page IDs
        """
        result = {}

        for page in pages:
            title = page.get("title")
            page_id = page.get("id")

            if title and page_id:
                result[title] = page_id

                # Also add normalized version
                normalized = cls._normalize_page_name(title)
                if normalized != title.lower():
                    result[normalized] = page_id

        return result
