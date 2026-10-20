"""
Markdown parser for converting Markdown content to an AST.
"""

import re
from typing import Any, Callable, Dict, List, Match, Optional, Pattern, Tuple

from docspan.backends.confluence.markdown.ast import (
    BlockquoteNode,
    BulletListNode,
    CodeBlockMacroNode,
    CodeBlockNode,
    ExcerptNode,
    ExpandMacroNode,
    ExpandNode,
    HeadingNode,
    HorizontalRuleNode,
    InfoNode,
    LayoutColumnNode,
    LayoutSectionNode,
    ListItemNode,
    MarkdownNode,
    MermaidNode,
    NoteNode,
    OrderedListNode,
    ParagraphNode,
    TableNode,
    TaskItemNode,
    TaskListNode,
    URLEmbedNode,
    WarningNode,
)
from docspan.backends.confluence.markdown.extensions.frontmatter import FrontmatterParser
from docspan.backends.confluence.markdown.inline_parser import InlineParser


class MarkdownParser:
    """
    Parser for Markdown content.

    Parses Markdown into a tree of MarkdownNode objects that can be
    converted to Atlassian Document Format (ADF).
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self._extensions = []
        self._block_parsers: Dict[str, Tuple[Pattern, Callable[[Match, str], MarkdownNode]]] = {}
        self._inline_parser = InlineParser()

        # Register default block parsers
        self._register_block_parsers()

    def register_extension(self, extension: Any) -> None:
        """
        Register an extension to modify the parsing behavior.

        Args:
            extension: Extension instance
        """
        self._extensions.append(extension)

    def parse(self, content: str) -> List[MarkdownNode]:
        """
        Parse Markdown content into an AST.

        Args:
            content: Markdown content to parse

        Returns:
            List of MarkdownNode objects representing the AST
        """
        # Extract frontmatter if present
        _, content = FrontmatterParser.extract(content)

        # Split content into blocks
        blocks = self._split_blocks(content)

        # Parse blocks
        nodes = []
        for block in blocks:
            if not block.strip():
                continue

            node = self._parse_block(block)
            if node:
                nodes.append(node)

        return nodes

    def _dedent_lines(self, lines: List[str]) -> List[str]:
        """
        Remove common leading whitespace from lines.

        This is used to normalize indented content before recursive parsing,
        allowing indented list markers to match list patterns that expect
        content at the start of the line.

        For list items, the first line typically has no indentation (it's the
        item text after the bullet), while continuation lines are indented.
        We want to remove the common indentation from continuation lines.

        Args:
            lines: List of strings with potentially common indentation

        Returns:
            List of strings with common indentation removed

        Example:
            >>> parser._dedent_lines(["First main", "   - Sub 1a", "   - Sub 1b"])
            ["First main", "- Sub 1a", "- Sub 1b"]
        """
        # Find minimum indentation (excluding blank lines AND lines with no indentation)
        indented_lines = [line for line in lines if line.strip() and line[0] in (' ', '\t')]

        if not indented_lines:
            # No indented lines - return as is
            return lines

        # Calculate minimum indentation level from indented lines only
        min_indent = min(len(line) - len(line.lstrip()) for line in indented_lines)

        # Remove that much indentation from all lines that have it
        dedented = []
        for line in lines:
            indent_level = len(line) - len(line.lstrip())
            if indent_level >= min_indent:
                # Remove the minimum indentation
                dedented.append(line[min_indent:])
            else:
                # Line has less indentation than minimum - keep as is
                dedented.append(line)

        return dedented

    def _split_blocks(self, content: str) -> List[str]:
        """
        Split content into blocks separated by blank lines or heading boundaries.

        Headings are treated as block boundaries - they start new blocks even
        without blank lines before/after them. This allows proper parsing of
        markdown where headings are immediately followed by content.

        Args:
            content: Markdown content

        Returns:
            List of block strings
        """
        # Special handling for code blocks, details blocks, and headings
        blocks = []
        in_code_block = False
        in_details_block = False
        in_list = False  # Track if we're accumulating a list block
        current_block = []

        lines = content.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if we're starting/continuing a list
            is_list_marker = re.match(r"^\d+\.\s+", line) or re.match(r"^[*\-+]\s+", line)
            if is_list_marker:
                # If we're not already in a list and have content, flush it first
                if not in_list and current_block and not in_code_block and not in_details_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                in_list = True

            # Check for code block delimiter
            is_code_fence = line.strip().startswith("```")
            is_indented = line.startswith((" ", "\t"))

            if is_code_fence:
                # If it's an indented code fence and we're in a list, don't split
                if is_indented and in_list:
                    current_block.append(line)
                    i += 1
                    continue
                elif not in_code_block:
                    # Start of a top-level code block
                    # If we have a current block, add it first
                    if current_block:
                        blocks.append("\n".join(current_block))
                        current_block = []
                        in_list = False  # Reset list tracking

                    # Start collecting the code block
                    in_code_block = True
                    current_block.append(line)
                else:
                    # End of a code block
                    current_block.append(line)
                    blocks.append("\n".join(current_block))
                    current_block = []
                    in_code_block = False
            # Check for details block start
            elif line.strip().lower().startswith("<details"):
                # Start of a details block
                # If we have a current block, add it first
                if current_block:
                    blocks.append("\n".join(current_block))
                    current_block = []

                # Start collecting the details block
                in_details_block = True
                current_block.append(line)
            # Check for details block end
            elif line.strip().lower() == "</details>":
                # End of a details block
                current_block.append(line)
                blocks.append("\n".join(current_block))
                current_block = []
                in_details_block = False
            elif not in_code_block and not in_details_block and line.startswith("#") and " " in line:
                # This is a heading line (outside code/details blocks)
                # Flush the current block if it exists
                if current_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                    in_list = False  # Reset list tracking

                # Add the heading as its own block
                blocks.append(line)
            else:
                # Handle blank lines
                if not in_code_block and not in_details_block and not line.strip():
                    if current_block:
                        # Check if the blank line should end the block
                        # Only end if we're not in a list
                        if in_list:
                            # Look ahead to see if the list continues
                            # Find the next non-blank line
                            next_line_idx = i + 1
                            next_line = None
                            while next_line_idx < len(lines):
                                if lines[next_line_idx].strip():
                                    next_line = lines[next_line_idx]
                                    break
                                next_line_idx += 1

                            # Check if next line continues the list
                            list_continues = False
                            if next_line:
                                # List continues if next line is a list marker or indented
                                is_list_marker = re.match(r"^\d+\.\s+", next_line) or re.match(r"^[*\-+]\s+", next_line)
                                is_indented = next_line.startswith((" ", "\t"))
                                list_continues = is_list_marker or is_indented

                            if list_continues:
                                # Keep accumulating - blank lines within lists are OK
                                current_block.append(line)
                            else:
                                # List has ended - flush the block
                                blocks.append("\n".join(current_block))
                                current_block = []
                                in_list = False
                        else:
                            blocks.append("\n".join(current_block))
                            current_block = []
                    # else: Skip leading blank lines
                else:
                    current_block.append(line)

            i += 1

        # Add any remaining content
        if current_block:
            blocks.append("\n".join(current_block))

        return blocks

    def _register_block_parsers(self) -> None:
        """Register default block parsers with their regex patterns."""
        # Heading - # Heading
        self._block_parsers["heading"] = (
            re.compile(r"^(#+)\s+(.+)$"),
            lambda m, content: self._parse_heading(m, content),
        )

        # Mermaid diagram - needs to be before generic code block to take precedence
        # Allow optional leading whitespace for indented mermaid diagrams in lists
        self._block_parsers["mermaid"] = (
            re.compile(r"^\s*```mermaid\n([\s\S]+?)\n\s*```$"),
            lambda m, content: self._parse_mermaid(m, content),
        )

        # Code block - ```language\ncode\n```
        # Allow optional leading whitespace for indented code blocks in lists
        self._block_parsers["code_block"] = (
            re.compile(r"^\s*```(\w*)\n([\s\S]+?)\n\s*```$"),
            lambda m, content: self._parse_code_block(m, content),
        )

        # Expand/details block - <details><summary>Title</summary>\nContent\n</details>
        self._block_parsers["expand"] = (
            re.compile(r"^<details>\s*\n<summary>(.*?)</summary>\s*\n([\s\S]*?)\n</details>$", re.IGNORECASE),
            lambda m, content: self._parse_expand(m, content),
        )

        # Blockquote - > Quote
        self._block_parsers["blockquote"] = (
            re.compile(r"^>\s*(.+)(\n>\s*.*)*$"),
            lambda m, content: self._parse_blockquote(m, content),
        )

        # Task list - - [ ] Item or - [x] Item (must be before bullet_list)
        self._block_parsers["task_list"] = (
            re.compile(r"^([*\-+]\s+\[[x ]\]\s+.+)(\n[*\-+]\s+\[[x ]\]\s+.*)*$", re.IGNORECASE),
            lambda m, content: self._parse_task_list(m, content),
        )

        # Bullet list - - Item or * Item (permissive to include indented content)
        self._block_parsers["bullet_list"] = (
            re.compile(r"^([*\-+]\s+.+)(\n.*)*$"),
            lambda m, content: self._parse_bullet_list(m, content),
        )

        # Ordered list - 1. Item (permissive to include indented content)
        self._block_parsers["ordered_list"] = (
            re.compile(r"^(\d+\.\s+.+)(\n.*)*$"),
            lambda m, content: self._parse_ordered_list(m, content),
        )

        # Horizontal rule - ---, ***, or ___
        self._block_parsers["horizontal_rule"] = (
            re.compile(r"^([-*_])\1{2,}\s*$"),
            lambda m, content: self._parse_horizontal_rule(m, content),
        )

        # Table - | Header | Header | with separator line
        self._block_parsers["table"] = (
            re.compile(r"^\|(.+)\|\s*\n\|[-:| ]+\|\s*\n(\|.+\|\s*\n?)+$"),
            lambda m, content: self._parse_table(m, content),
        )

        # Multi-column layout - ::: columns
        self._block_parsers["layout_section"] = (
            re.compile(r"^::: columns\s*\n([\s\S]+?)\n:::$"),
            lambda m, content: self._parse_layout_section(m, content),
        )

        # URL embed - standalone URLs (YouTube, Vimeo, etc.)
        self._block_parsers["url_embed"] = (
            re.compile(r"^(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|vimeo\.com/)\S+)$"),
            lambda m, content: self._parse_url_embed(m, content),
        )

        # Block macros with content - {macro:params}...{macro}
        # Info panel - {info}...{info} or {info:title=...}...{info}
        self._block_parsers["info_macro"] = (
            re.compile(r"^\{info(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{info\}$"),
            lambda m, content: self._parse_info_macro(m, content),
        )

        # Warning panel - {warning}...{warning} or {warning:title=...}...{warning}
        self._block_parsers["warning_macro"] = (
            re.compile(r"^\{warning(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{warning\}$"),
            lambda m, content: self._parse_warning_macro(m, content),
        )

        # Note panel - {note}...{note} or {note:title=...}...{note}
        self._block_parsers["note_macro"] = (
            re.compile(r"^\{note(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{note\}$"),
            lambda m, content: self._parse_note_macro(m, content),
        )

        # Excerpt - {excerpt}...{excerpt} or {excerpt:hidden=...}...{excerpt}
        self._block_parsers["excerpt_macro"] = (
            re.compile(r"^\{excerpt(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{excerpt\}$"),
            lambda m, content: self._parse_excerpt_macro(m, content),
        )

        # Expand macro - {expand:title=...}...{expand}
        self._block_parsers["expand_macro"] = (
            re.compile(r"^\{expand(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{expand\}$"),
            lambda m, content: self._parse_expand_macro(m, content),
        )

        # Code block macro - {code:language=...}...{code}
        self._block_parsers["code_macro"] = (
            re.compile(r"^\{code(?::([^}]+))?\}\s*\n([\s\S]*?)\n\{code\}$"),
            lambda m, content: self._parse_code_macro(m, content),
        )

    def _parse_block(self, block: str) -> Optional[MarkdownNode]:
        """
        Parse a block of Markdown content.

        Args:
            block: Block of Markdown content

        Returns:
            Parsed node or None if not recognized
        """
        # Try all registered block parsers
        for name, (pattern, parser) in self._block_parsers.items():
            match = pattern.match(block)
            if match:
                return parser(match, block)

        # Default to paragraph for unrecognized blocks
        paragraph = ParagraphNode()
        paragraph.children.extend(self._parse_inline(block))
        return paragraph

    def _parse_heading(self, match: Match, content: str) -> HeadingNode:
        """
        Parse a heading.

        Args:
            match: Regex match
            content: Original content

        Returns:
            HeadingNode
        """
        level = len(match.group(1))
        text = match.group(2).strip()

        heading = HeadingNode(level=min(level, 6))
        heading.children.extend(self._parse_inline(text))

        return heading

    def _parse_code_block(self, match: Match, content: str) -> CodeBlockNode:
        """
        Parse a code block.

        Args:
            match: Regex match
            content: Original content

        Returns:
            CodeBlockNode
        """
        language = match.group(1) or None
        code = match.group(2)

        return CodeBlockNode(language=language, content=code)

    def _parse_blockquote(self, match: Match, content: str) -> BlockquoteNode:
        """
        Parse a blockquote.

        Args:
            match: Regex match
            content: Original content

        Returns:
            BlockquoteNode
        """
        # Extract the blockquote content
        lines = content.strip().split("\n")
        quote_content = []

        for line in lines:
            line = re.sub(r"^>\s?", "", line)
            quote_content.append(line)

        combined = "\n".join(quote_content)

        blockquote = BlockquoteNode()
        paragraph = ParagraphNode()
        paragraph.children.extend(self._parse_inline(combined))
        blockquote.children.append(paragraph)

        return blockquote

    def _parse_expand(self, match: Match, content: str) -> ExpandNode:
        """
        Parse an expand/collapsible section.

        Args:
            match: Regex match
            content: Original content

        Returns:
            ExpandNode
        """
        # Extract title from <summary> tag
        title = match.group(1).strip() if match.group(1) else ""

        # Extract content between </summary> and </details>
        expand_content = match.group(2).strip() if match.group(2) else ""

        expand = ExpandNode(title=title)

        if expand_content:
            # Recursively parse the content to get child nodes
            # This allows expand sections to contain paragraphs, lists, headings, etc.
            child_nodes = self.parse(expand_content)
            expand.children.extend(child_nodes)
        else:
            # If no content, add an empty paragraph to satisfy ADF validation
            expand.children.append(ParagraphNode())

        return expand

    def _parse_bullet_list(self, match: Match, content: str) -> BulletListNode:
        """
        Parse a bullet list, supporting multi-line items with nested code blocks.

        Args:
            match: Regex match
            content: Original content

        Returns:
            BulletListNode
        """
        lines = content.strip().split("\n")
        list_node = BulletListNode()

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if this line starts a list item
            list_item_match = re.match(r"^([*\-+])\s+(.*)$", line)
            if list_item_match:
                list_item_match.group(1)
                first_line_content = list_item_match.group(2)

                # Collect all lines that belong to this item (indented continuation)
                item_lines = [first_line_content]
                i += 1

                # Look ahead for indented lines (including code blocks)
                while i < len(lines):
                    next_line = lines[i]
                    # If next line starts a new list item, stop
                    if re.match(r"^[*\-+]\s+", next_line):
                        break
                    # If next line is indented or blank (within item), include it
                    if next_line.startswith((" ", "\t")) or not next_line.strip():
                        item_lines.append(next_line)
                        i += 1
                    else:
                        # Non-indented, non-list-item line - stop
                        break

                # Dedent the item lines before joining to allow nested lists to match
                # List markers like "   - Sub-item" need to become "- Sub-item" to match patterns
                item_lines_dedented = self._dedent_lines(item_lines)

                # Join the dedented content and recursively parse it
                item_content = "\n".join(item_lines_dedented)

                # Parse the item content as blocks (may contain code blocks, paragraphs, etc.)
                item_node = ListItemNode()
                item_blocks = self.parse(item_content)
                item_node.children.extend(item_blocks)

                list_node.children.append(item_node)
            else:
                # Skip non-list-item lines
                i += 1

        return list_node

    def _parse_ordered_list(self, match: Match, content: str) -> OrderedListNode:
        """
        Parse an ordered list, supporting multi-line items with nested code blocks.

        Args:
            match: Regex match
            content: Original content

        Returns:
            OrderedListNode
        """
        lines = content.strip().split("\n")
        list_node = OrderedListNode()

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if this line starts a list item
            list_item_match = re.match(r"^(\d+)\.\s+(.*)$", line)
            if list_item_match:
                list_item_match.group(1)
                first_line_content = list_item_match.group(2)

                # Collect all lines that belong to this item (indented continuation)
                item_lines = [first_line_content]
                i += 1

                # Look ahead for indented lines (including code blocks)
                while i < len(lines):
                    next_line = lines[i]
                    # If next line starts a new list item, stop
                    if re.match(r"^\d+\.\s+", next_line):
                        break
                    # If next line is indented or blank (within item), include it
                    if next_line.startswith((" ", "\t")) or not next_line.strip():
                        item_lines.append(next_line)
                        i += 1
                    else:
                        # Non-indented, non-list-item line - stop
                        break

                # Dedent the item lines before joining to allow nested lists to match
                # List markers like "   1. Sub-item" need to become "1. Sub-item" to match patterns
                item_lines_dedented = self._dedent_lines(item_lines)

                # Join the dedented content and recursively parse it
                item_content = "\n".join(item_lines_dedented)

                # Parse the item content as blocks (may contain code blocks, paragraphs, etc.)
                item_node = ListItemNode()
                item_blocks = self.parse(item_content)
                item_node.children.extend(item_blocks)

                list_node.children.append(item_node)
            else:
                # Skip non-list-item lines
                i += 1

        return list_node

    def _parse_horizontal_rule(self, match: Match, content: str) -> HorizontalRuleNode:
        """
        Parse a horizontal rule.

        Args:
            match: Regex match
            content: Original content

        Returns:
            HorizontalRuleNode
        """
        return HorizontalRuleNode()

    def _parse_table(self, match: Match, content: str) -> TableNode:
        """
        Parse a table.

        Args:
            match: Regex match
            content: Original content

        Returns:
            TableNode
        """
        lines = content.strip().split("\n")
        table = TableNode()

        # Extract headers from first line
        header_line = lines[0]
        headers = [cell.strip() for cell in header_line.split("|")[1:-1]]
        table.headers = headers

        # Skip the header and separator lines
        for i in range(2, len(lines)):
            row_line = lines[i]
            if not row_line.strip():
                continue

            # Extract cells from row
            cells = []
            for cell_content in row_line.split("|")[1:-1]:
                paragraph = ParagraphNode()
                paragraph.children.extend(self._parse_inline(cell_content.strip()))
                cells.append(paragraph)

            table.rows.append(cells)

        return table

    def _parse_mermaid(self, match: Match, content: str) -> MermaidNode:
        """
        Parse a Mermaid diagram.

        Args:
            match: Regex match
            content: Original content

        Returns:
            MermaidNode
        """
        code = match.group(1)
        return MermaidNode(code=code)

    def _parse_task_list(self, match: Match, content: str) -> TaskListNode:
        """
        Parse a task list with checkboxes.

        Args:
            match: Regex match
            content: Original content (e.g., "- [ ] Task 1\n- [x] Task 2")

        Returns:
            TaskListNode
        """
        lines = content.strip().split("\n")
        task_list = TaskListNode()

        for line in lines:
            # Extract checkbox state and content
            # Pattern: - [ ] Item or - [x] Item
            checkbox_match = re.match(r"^[*\-+]\s+\[([x ])\]\s+(.+)$", line, re.IGNORECASE)
            if checkbox_match:
                state = checkbox_match.group(1).lower()
                item_content = checkbox_match.group(2)

                checked = state == 'x'

                # Create task item
                task_item = TaskItemNode(checked=checked)
                paragraph = ParagraphNode()
                paragraph.children.extend(self._parse_inline(item_content))
                task_item.children.append(paragraph)

                task_list.children.append(task_item)

        return task_list

    def _parse_layout_section(self, match: Match, content: str) -> LayoutSectionNode:
        """
        Parse a multi-column layout section.

        Args:
            match: Regex match
            content: Original content

        Returns:
            LayoutSectionNode
        """
        # Extract content between ::: columns and :::
        layout_content = match.group(1).strip()

        # Parse columns - format: ::: column width=50\nContent\n:::
        column_pattern = re.compile(r"::: column(?:\s+width=(\d+))?\s*\n([\s\S]+?)(?=\n:::(?:\s+column|\s*$))", re.MULTILINE)
        columns = column_pattern.findall(layout_content)

        layout_section = LayoutSectionNode()

        for width_str, column_content in columns:
            # Parse width if provided
            width = int(width_str) if width_str else None

            # Create column node
            column = LayoutColumnNode(width=width)

            # Parse column content recursively
            column_nodes = self.parse(column_content.strip())
            column.children.extend(column_nodes)

            layout_section.children.append(column)

        # If no columns were parsed, add empty column
        if not layout_section.children:
            column = LayoutColumnNode()
            column.children.append(ParagraphNode())
            layout_section.children.append(column)

        return layout_section

    def _parse_url_embed(self, match: Match, content: str) -> URLEmbedNode:
        """
        Parse a standalone URL for embedding.

        Args:
            match: Regex match
            content: Original content (just the URL)

        Returns:
            URLEmbedNode
        """
        url = match.group(1)

        # Determine embed type based on URL
        if 'youtube.com' in url or 'youtu.be' in url:
            embed_type = 'video'
        elif 'vimeo.com' in url:
            embed_type = 'video'
        else:
            embed_type = 'card'

        return URLEmbedNode(url=url, embed_type=embed_type, layout='center')

    def _parse_info_macro(self, match: Match, content: str) -> InfoNode:
        """
        Parse an info panel macro.

        Args:
            match: Regex match
            content: Original content

        Returns:
            InfoNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        panel_content = match.group(2).strip()

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        title = params.get("title")
        icon = params.get("icon", "true").lower() == "true"

        # Create info node and parse content
        info_node = InfoNode(title=title, icon=icon)
        # Parse content blocks
        info_node.children.extend(self.parse(panel_content))

        return info_node

    def _parse_warning_macro(self, match: Match, content: str) -> WarningNode:
        """
        Parse a warning panel macro.

        Args:
            match: Regex match
            content: Original content

        Returns:
            WarningNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        panel_content = match.group(2).strip()

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        title = params.get("title")
        icon = params.get("icon", "true").lower() == "true"

        # Create warning node and parse content
        warning_node = WarningNode(title=title, icon=icon)
        warning_node.children.extend(self.parse(panel_content))

        return warning_node

    def _parse_note_macro(self, match: Match, content: str) -> NoteNode:
        """
        Parse a note panel macro.

        Args:
            match: Regex match
            content: Original content

        Returns:
            NoteNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        panel_content = match.group(2).strip()

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        title = params.get("title")
        icon = params.get("icon", "true").lower() == "true"

        # Create note node and parse content
        note_node = NoteNode(title=title, icon=icon)
        note_node.children.extend(self.parse(panel_content))

        return note_node

    def _parse_excerpt_macro(self, match: Match, content: str) -> ExcerptNode:
        """
        Parse an excerpt macro.

        Args:
            match: Regex match
            content: Original content

        Returns:
            ExcerptNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        excerpt_content = match.group(2).strip()

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        hidden = params.get("hidden", "false").lower() == "true"
        output_type = params.get("atlassian-macro-output-type", "BLOCK")

        # Create excerpt node and parse content
        excerpt_node = ExcerptNode(hidden=hidden, atlassian_macro_output_type=output_type)
        excerpt_node.children.extend(self.parse(excerpt_content))

        return excerpt_node

    def _parse_expand_macro(self, match: Match, content: str) -> ExpandMacroNode:
        """
        Parse an expand (collapsible) macro.

        Args:
            match: Regex match
            content: Original content

        Returns:
            ExpandMacroNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        expand_content = match.group(2).strip()

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        title = params.get("title", "")

        # Create expand node and parse content
        expand_node = ExpandMacroNode(title=title)
        expand_node.children.extend(self.parse(expand_content))

        return expand_node

    def _parse_code_macro(self, match: Match, content: str) -> CodeBlockMacroNode:
        """
        Parse a code block macro with enhanced features.

        Args:
            match: Regex match
            content: Original content

        Returns:
            CodeBlockMacroNode
        """
        params_str = match.group(1) if match.lastindex >= 1 and match.group(1) else None
        code_content = match.group(2)

        # Parse parameters
        params = self._parse_macro_parameters(params_str) if params_str else {}
        language = params.get("language")
        title = params.get("title")
        linenumbers = params.get("linenumbers", "false").lower() == "true"
        theme = params.get("theme")
        collapse = params.get("collapse", "false").lower() == "true"

        # Create code block macro node with code content
        code_node = CodeBlockMacroNode(
            language=language,
            title=title,
            linenumbers=linenumbers,
            theme=theme,
            collapse=collapse
        )
        code_node.content = code_content

        return code_node

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

    def _parse_inline(self, text: str) -> List[MarkdownNode]:
        """
        Parse inline elements in text.

        Args:
            text: Text to parse

        Returns:
            List of inline nodes
        """
        # Use the InlineParser to parse inline elements
        return self._inline_parser.parse(text)

    def _find_link_indices(self, text: str) -> List[Tuple[int, int, str, str]]:
        """
        Find Markdown link indices in text.

        Args:
            text: Text to search

        Returns:
            List of (start, end, text, url) tuples
        """
        pattern = r"\[([^\]]+)\]\(([^)]+)\)"
        result = []

        for match in re.finditer(pattern, text):
            start, end = match.span()
            link_text = match.group(1)
            link_url = match.group(2)
            result.append((start, end, link_text, link_url))

        return result
