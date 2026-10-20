"""
Inline element parser for Markdown.
"""

import re
from typing import Any, Dict, List, Match

from docspan.backends.confluence.markdown.ast import (
    ColoredTextNode,
    DateNode,
    EmojiNode,
    HighlightedTextNode,
    ImageNode,
    InlineCodeNode,
    LinkNode,
    MarkdownNode,
    MentionNode,
    StatusBadgeNode,
    StatusMacroNode,
    TextNode,
    TocNode,
)


class InlineParser:
    """
    Parser for inline Markdown elements.

    This parser handles inline formatting like bold, italic, links,
    inline code, and other elements that appear within block-level elements.
    """

    # Regex patterns for inline elements
    PATTERNS = {
        "link": r'\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)',  # [text](url "title")
        "image": r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?(?:\s+=(\d+)(?:x(\d+))?)?\)',  # ![alt](url "title" =WxH)
        "bold": r"\*\*([^*]+)\*\*|\b__([^_]+)__\b",  # **text** or __text__
        "italic": r"\*([^*]+)\*|\b_([^_]+)_\b",  # *text* or _text_
        "bold_italic": r"\*\*\*([^*]+)\*\*\*|\b___([^_]+)___\b",  # ***text*** or ___text___
        "inline_code": r"`([^`]+)`",  # `code`
        "strikethrough": r"~~([^~]+)~~",  # ~~text~~
        "emoji": r":([a-z0-9_+-]+):",  # :emoji_name:
        "mention": r"@([a-zA-Z0-9._-]+)",  # @username
        "status_badge": r"\[!([A-Z_]+)\]",  # [!STATUS]
        "highlighted": r"==([^=]+)==",  # ==text==
        "colored_text": r'<mark[^>]*style="color:([^"]+)"[^>]*>([^<]+)</mark>',  # <mark style="color:#ff0000">text</mark>
        "iso_date": r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?",  # 2025-10-29 or 2025-10-29T14:00:00
        "inline_macro": r"\{([a-z][a-z0-9-]*)(?::([^}]+))?\}",  # {toc} or {toc:maxLevel=3} or {status:colour=Green|title=Active}
    }

    def __init__(self):
        """Initialize the parser."""
        self.compiled_patterns = {
            name: re.compile(pattern) for name, pattern in self.PATTERNS.items()
        }

    def parse(self, text: str) -> List[MarkdownNode]:
        """
        Parse inline elements in text.

        Args:
            text: Text content to parse

        Returns:
            List of MarkdownNode objects
        """
        if not text:
            return []

        # Find all inline element matches
        matches = []
        for elem_type, pattern in self.compiled_patterns.items():
            for match in pattern.finditer(text):
                start, end = match.span()
                matches.append((start, end, elem_type, match))

        # If no matches, return a single text node
        if not matches:
            return [TextNode(type="text", content=text)]

        # Sort matches by start position
        matches.sort(key=lambda x: x[0])

        # Create result nodes
        result = []
        last_end = 0

        for start, end, elem_type, match in matches:
            # Handle overlapping matches by checking if this match starts
            # after the end of the previous match
            if start < last_end:
                continue

            # Add text before the match
            if start > last_end:
                result.append(TextNode(type="text", content=text[last_end:start]))

            # Process the match based on its type
            if elem_type == "link":
                node = self._create_link_node(match)
                result.append(node)
            elif elem_type == "image":
                node = self._create_image_node(match)
                result.append(node)
            elif elem_type == "inline_code":
                node = self._create_inline_code_node(match)
                result.append(node)
            elif elem_type == "emoji":
                node = self._create_emoji_node(match)
                result.append(node)
            elif elem_type == "mention":
                node = self._create_mention_node(match)
                result.append(node)
            elif elem_type == "status_badge":
                node = self._create_status_badge_node(match)
                result.append(node)
            elif elem_type == "highlighted":
                node = self._create_highlighted_text_node(match)
                result.append(node)
            elif elem_type == "colored_text":
                node = self._create_colored_text_node(match)
                result.append(node)
            elif elem_type == "iso_date":
                node = self._create_date_node(match)
                result.append(node)
            elif elem_type == "inline_macro":
                node = self._create_inline_macro_node(match)
                result.append(node)
            else:
                # Handle formatting marks (bold, italic, etc.)
                # This returns a list of nodes (to support nested elements like links in bold)
                nodes = self._create_formatted_text_node(match, elem_type)
                result.extend(nodes)

            last_end = end

        # Add any remaining text
        if last_end < len(text):
            result.append(TextNode(type="text", content=text[last_end:]))

        return result

    def _create_link_node(self, match: Match) -> LinkNode:
        """
        Create a link node from a match.

        Args:
            match: Regex match for a link

        Returns:
            LinkNode
        """
        text = match.group(1)
        url = match.group(2)
        title = match.group(3) if match.lastindex >= 3 else None

        link = LinkNode(type="link", url=url, title=title)
        link.children.append(TextNode(type="text", content=text))

        return link

    def _create_image_node(self, match: Match) -> ImageNode:
        """
        Create an image node from a match.

        Supports extended syntax for image dimensions:
        - ![alt](url) - basic image
        - ![alt](url "title") - with title
        - ![alt](url =W) - with width only
        - ![alt](url =WxH) - with width and height
        - ![alt](url "title" =WxH) - with title and dimensions

        Args:
            match: Regex match for an image

        Returns:
            ImageNode with optional width and height
        """
        alt = match.group(1) or ""
        src = match.group(2)
        title = match.group(3) if match.lastindex >= 3 and match.group(3) else None

        # Parse dimensions if present (groups 4 and 5)
        width = None
        height = None

        if match.lastindex >= 4 and match.group(4):
            try:
                width = int(match.group(4))
            except (ValueError, TypeError):
                pass  # Ignore invalid width values

        if match.lastindex >= 5 and match.group(5):
            try:
                height = int(match.group(5))
            except (ValueError, TypeError):
                pass  # Ignore invalid height values

        return ImageNode(
            type="image",
            src=src,
            alt=alt,
            title=title,
            width=width,
            height=height
        )

    def _create_inline_code_node(self, match: Match) -> InlineCodeNode:
        """
        Create an inline code node from a match.

        Args:
            match: Regex match for inline code

        Returns:
            InlineCodeNode
        """
        code = match.group(1)
        node = InlineCodeNode(type="inlineCode", content=code)
        return node

    def _create_emoji_node(self, match: Match) -> EmojiNode:
        """
        Create an emoji node from a match.

        Args:
            match: Regex match for emoji (:emoji_name:)

        Returns:
            EmojiNode
        """
        short_name = match.group(1)
        # EmojiNode will auto-initialize text with :short_name: format
        node = EmojiNode(short_name=short_name)
        return node

    def _create_mention_node(self, match: Match) -> MentionNode:
        """
        Create a mention node from a match.

        Args:
            match: Regex match for mention (@username)

        Returns:
            MentionNode
        """
        username = match.group(1)
        # MentionNode will auto-initialize text with @username format
        node = MentionNode(username=username)
        return node

    def _create_status_badge_node(self, match: Match) -> StatusBadgeNode:
        """
        Create a status badge node from a match.

        Args:
            match: Regex match for status badge ([!STATUS])

        Returns:
            StatusBadgeNode
        """
        status_text = match.group(1)

        # Status color mapping
        STATUS_COLOR_MAP = {
            # Green - Success states
            "DONE": "green",
            "COMPLETE": "green",
            "SUCCESS": "green",
            "PASSED": "green",
            "APPROVED": "green",
            # Red - Error states
            "ERROR": "red",
            "FAILED": "red",
            "BLOCKED": "red",
            "REJECTED": "red",
            # Yellow - Warning states
            "WARNING": "yellow",
            "PENDING": "yellow",
            "REVIEW": "yellow",
            # Blue - Info states
            "INFO": "blue",
            "IN_PROGRESS": "blue",
            "RUNNING": "blue",
            # Purple - Special states
            "OPTIONAL": "purple",
            # Neutral - Default
            "TODO": "neutral",
        }

        color = STATUS_COLOR_MAP.get(status_text, "neutral")
        node = StatusBadgeNode(text=status_text, color=color)
        return node

    def _create_highlighted_text_node(self, match: Match) -> HighlightedTextNode:
        """
        Create a highlighted text node from a match.

        Args:
            match: Regex match for highlighted text (==text==)

        Returns:
            HighlightedTextNode
        """
        text = match.group(1)
        node = HighlightedTextNode(content=text, bg_color="#ffff00")
        return node

    def _create_colored_text_node(self, match: Match) -> ColoredTextNode:
        """
        Create a colored text node from a match.

        Args:
            match: Regex match for colored text (<mark style="color:...">text</mark>)

        Returns:
            ColoredTextNode
        """
        color = match.group(1)
        text = match.group(2)
        node = ColoredTextNode(content=text, color=color)
        return node

    def _create_date_node(self, match: Match) -> DateNode:
        """
        Create a date node from a match.

        Args:
            match: Regex match for ISO date (2025-10-29 or 2025-10-29T14:00:00)

        Returns:
            DateNode
        """
        from datetime import datetime

        date_string = match.group(0)

        try:
            # Try parsing as datetime first
            if 'T' in date_string:
                dt = datetime.fromisoformat(date_string)
            else:
                # Parse as date only and set time to midnight
                dt = datetime.strptime(date_string, "%Y-%m-%d")

            # Convert to milliseconds timestamp
            timestamp = int(dt.timestamp() * 1000)
            node = DateNode(timestamp=timestamp, date_string=date_string)
        except (ValueError, AttributeError):
            # If parsing fails, create a date node with timestamp 0
            node = DateNode(timestamp=0, date_string=date_string)

        return node

    def _create_inline_macro_node(self, match: Match) -> MarkdownNode:
        """
        Create a macro node from a match.

        Supports curly brace macro syntax:
        - {toc} - simple macro
        - {toc:maxLevel=3} - with parameters
        - {status:colour=Green|title=Active} - status with pipe separator

        Args:
            match: Regex match for macro ({macro-name:params})

        Returns:
            Appropriate extension node (TocNode, StatusMacroNode, etc.)
        """
        macro_name = match.group(1)
        params_str = match.group(2) if match.lastindex >= 2 and match.group(2) else None

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}

        # Create appropriate node based on macro name
        if macro_name == "toc":
            # Parse TOC-specific parameters
            max_level = int(params.get("maxLevel", "7"))
            min_level = int(params.get("minLevel", "1"))
            include = params.get("include")
            exclude = params.get("exclude")
            toc_type = params.get("type", "list")
            printable = params.get("printable", "true").lower() == "true"
            separator = params.get("separator", "dots")

            return TocNode(
                max_level=max_level,
                min_level=min_level,
                include=include,
                exclude=exclude,
                toc_type=toc_type,
                printable=printable,
                separator=separator
            )

        elif macro_name == "status":
            # Parse status-specific parameters
            # Support both 'title' and direct text
            status_text = params.get("title", "")
            color = params.get("colour", params.get("color", "Grey"))  # Support both spellings
            subtle = params.get("subtle", "false").lower() == "true"

            return StatusMacroNode(
                status_text=status_text,
                color=color,
                subtle=subtle
            )

        else:
            # For other macros, create a generic extension node placeholder
            # This will be handled by block parser if it's a block macro
            # For now, treat as text node
            return TextNode(type="text", content=match.group(0))

    def _parse_macro_parameters(self, params_str: str) -> Dict[str, str]:
        """
        Parse macro parameters from parameter string.

        Supports both comma-separated and pipe-separated parameters:
        - maxLevel=3,minLevel=1
        - colour=Green|title=Active

        Args:
            params_str: Parameter string (e.g., "maxLevel=3,minLevel=1")

        Returns:
            Dictionary of parameter key-value pairs
        """
        params = {}

        # Determine separator (pipe for status, comma for others)
        if "|" in params_str:
            separator = "|"
        else:
            separator = ","

        # Split by separator and parse key=value pairs
        for pair in params_str.split(separator):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key.strip()] = value.strip()

        return params

    def _create_formatted_text_node(self, match: Match, format_type: str) -> List[MarkdownNode]:
        """
        Create formatted text nodes from a match, recursively parsing nested inline elements.

        Args:
            match: Regex match for formatted text
            format_type: Type of formatting (bold, italic, etc.)

        Returns:
            List of MarkdownNode objects with appropriate marks applied
        """
        # Get the captured text content from the match
        text = match.group(1) if match.group(1) else match.group(2)

        # Determine which marks to apply
        marks = []
        if format_type == "bold":
            marks.append({"type": "strong"})
        elif format_type == "italic":
            marks.append({"type": "em"})
        elif format_type == "bold_italic":
            marks.append({"type": "strong"})
            marks.append({"type": "em"})
        elif format_type == "strikethrough":
            marks.append({"type": "strike"})

        # Recursively parse the content for nested inline elements (like links)
        nested_nodes = self.parse(text)

        # Apply marks to all text nodes in the result
        self._apply_marks_to_nodes(nested_nodes, marks)

        return nested_nodes

    def _apply_marks_to_nodes(self, nodes: List[MarkdownNode], marks: List[Dict[str, Any]]) -> None:
        """
        Recursively apply marks to all TextNode objects in a list of nodes.

        Args:
            nodes: List of nodes to process
            marks: Marks to apply to text nodes
        """
        for node in nodes:
            if isinstance(node, TextNode):
                # Add marks to this text node
                node.marks.extend(marks)
            elif hasattr(node, 'children') and node.children:
                # Recursively apply to children (e.g., text inside links)
                self._apply_marks_to_nodes(node.children, marks)
