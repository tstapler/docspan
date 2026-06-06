"""Parse Markdown content into DocsParagraphNode list for Google Docs push."""
from __future__ import annotations

from typing import List

from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode, TextSpan


def _extract_text_from_token(token: dict) -> str:
    """Recursively extract plain text from a mistune AST token."""
    if token.get("type") == "raw":
        return token.get("raw", "")
    if token.get("type") == "text":
        return token.get("raw", "")
    if token.get("type") == "codespan":
        return token.get("raw", "")
    # For inline tokens: code_span, emphasis, strong, link, etc.
    children = token.get("children") or token.get("children", [])
    if children:
        return "".join(_extract_text_from_token(c) for c in children)
    return token.get("raw", "")


def _walk_list_items(token: dict, nesting_level: int = 0) -> List[DocsParagraphNode]:
    """Walk a list token and yield DocsParagraphNode for each list item."""
    nodes = []
    for item in token.get("children", []):
        if item.get("type") != "list_item":
            continue
        # Gather text from children of list_item (may be paragraph or inline tokens)
        text_parts = []
        for child in item.get("children", []):
            if child.get("type") == "paragraph":
                for inline in child.get("children", []):
                    text_parts.append(_extract_text_from_token(inline))
            elif child.get("type") == "list":
                # Nested list — recurse
                nodes.append(DocsParagraphNode(
                    style="NORMAL_TEXT",
                    text="".join(text_parts).strip(),
                    is_list_item=True,
                    nesting_level=nesting_level,
                    start_index=0,
                    end_index=0,
                ))
                text_parts = []
                nodes.extend(_walk_list_items(child, nesting_level + 1))
                continue
            else:
                text_parts.append(_extract_text_from_token(child))
        text = "".join(text_parts).strip()
        if text:
            nodes.append(DocsParagraphNode(
                style="NORMAL_TEXT",
                text=text,
                is_list_item=True,
                nesting_level=nesting_level,
                start_index=0,
                end_index=0,
            ))
    return nodes


class MarkdownToParagraphParser:
    """
    Parse Markdown content into a list of DocsParagraphNode.

    Uses mistune>=3.0 (AST renderer) for accurate block-level parsing.
    All target nodes have start_index=0, end_index=0 (not meaningful for push targets).

    Acceptance criteria:
    - A blank line between paragraphs produces two separate nodes (not one)
    - '## Heading' produces style="HEADING_2"
    - '- item' produces is_list_item=True
    - A fenced code block produces a single node with monospace=True span
    """

    def parse(self, content: str) -> List[DocsParagraphNode]:
        """
        Parse markdown content into DocsParagraphNode list.

        Args:
            content: Raw markdown string.

        Returns:
            List of DocsParagraphNode in document order.
        """
        import mistune

        # mistune.create_markdown(renderer=None) returns AST tokens
        md = mistune.create_markdown(renderer=None)
        tokens = md(content) or []

        nodes: List[DocsParagraphNode] = []
        for token in tokens:
            token_type = token.get("type")

            if token_type == "heading":
                level = token.get("attrs", {}).get("level", token.get("level", 1))
                style = f"HEADING_{level}"
                text_parts = []
                for child in token.get("children", []):
                    text_parts.append(_extract_text_from_token(child))
                text = "".join(text_parts).strip()
                nodes.append(DocsParagraphNode(
                    style=style,
                    text=text,
                    start_index=0,
                    end_index=0,
                ))

            elif token_type == "paragraph":
                text_parts = []
                for child in token.get("children", []):
                    text_parts.append(_extract_text_from_token(child))
                text = "".join(text_parts).strip()
                nodes.append(DocsParagraphNode(
                    style="NORMAL_TEXT",
                    text=text,
                    start_index=0,
                    end_index=0,
                ))

            elif token_type == "list":
                nodes.extend(_walk_list_items(token, nesting_level=0))

            elif token_type in ("block_code", "code"):
                # Fenced code block — single node with monospace span
                raw = token.get("raw", "").strip()
                span = TextSpan(text=raw, monospace=True)
                nodes.append(DocsParagraphNode(
                    style="NORMAL_TEXT",
                    text=raw,
                    start_index=0,
                    end_index=0,
                    spans=[span],
                ))

            elif token_type == "blank_line":
                # blank_line between paragraphs — skip (mistune handles separation)
                pass

            # block_quote, thematic_break, html, etc. are silently skipped

        return nodes
