"""Parse Markdown content into DocsParagraphNode/DocsTableNode list for Google Docs push."""
from __future__ import annotations

from typing import List, Optional, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TextSpan,
)

Node = Union[DocsParagraphNode, DocsTableNode]


def _extract_text_from_token(token: dict) -> str:
    """Recursively extract plain text from a mistune AST token."""
    if token.get("type") in ("raw", "text", "codespan"):
        return token.get("raw", "")
    children = token.get("children")
    if children:
        return "".join(_extract_text_from_token(c) for c in children)
    return token.get("raw", "")


def _link_url(token: dict) -> str:
    """Return the URL of a mistune link token across attr shapes."""
    attrs = token.get("attrs") or {}
    return attrs.get("url") or token.get("link") or ""


def _spans_from_inline(
    children: List[dict],
    bold: bool = False,
    italic: bool = False,
    link: Optional[str] = None,
    monospace: bool = False,
) -> List[TextSpan]:
    """Walk mistune inline tokens into ordered TextSpans, propagating styling through nesting."""
    spans: List[TextSpan] = []
    for tok in children or []:
        ttype = tok.get("type")
        if ttype in ("text", "raw"):
            spans.append(TextSpan(text=tok.get("raw", ""), bold=bold, italic=italic,
                                  link=link, monospace=monospace))
        elif ttype == "codespan":
            spans.append(TextSpan(text=tok.get("raw", ""), bold=bold, italic=italic,
                                  link=link, monospace=True))
        elif ttype == "strong":
            spans.extend(_spans_from_inline(tok.get("children", []), True, italic, link, monospace))
        elif ttype == "emphasis":
            spans.extend(_spans_from_inline(tok.get("children", []), bold, True, link, monospace))
        elif ttype == "link":
            url = _link_url(tok) or link
            spans.extend(_spans_from_inline(tok.get("children", []), bold, italic, url, monospace))
        elif ttype in ("linebreak", "softbreak"):
            spans.append(TextSpan(text=" ", bold=bold, italic=italic, link=link, monospace=monospace))
        else:
            kids = tok.get("children")
            if kids:
                spans.extend(_spans_from_inline(kids, bold, italic, link, monospace))
            else:
                raw = tok.get("raw", "")
                if raw:
                    spans.append(TextSpan(text=raw, bold=bold, italic=italic,
                                          link=link, monospace=monospace))
    return _merge_spans(spans)


def _merge_spans(spans: List[TextSpan]) -> List[TextSpan]:
    """Coalesce consecutive spans that share identical styling."""
    merged: List[TextSpan] = []
    for span in spans:
        if merged:
            prev = merged[-1]
            if (prev.bold == span.bold and prev.italic == span.italic
                    and prev.link == span.link and prev.monospace == span.monospace):
                merged[-1] = TextSpan(
                    text=prev.text + span.text, bold=prev.bold, italic=prev.italic,
                    link=prev.link, monospace=prev.monospace,
                )
                continue
        merged.append(span)
    return merged


def _text_of(spans: List[TextSpan]) -> str:
    return "".join(s.text for s in spans)


def _has_styling(spans: List[TextSpan]) -> bool:
    return any(s.bold or s.italic or s.link or s.monospace for s in spans)


def _walk_list_items(token: dict, nesting_level: int = 0) -> List[DocsParagraphNode]:
    """Walk a list token and yield DocsParagraphNode for each list item."""
    nodes: List[DocsParagraphNode] = []
    for item in token.get("children", []):
        if item.get("type") != "list_item":
            continue
        spans: List[TextSpan] = []
        for child in item.get("children", []):
            if child.get("type") == "paragraph":
                spans.extend(_spans_from_inline(child.get("children", [])))
            elif child.get("type") == "block_text":
                spans.extend(_spans_from_inline(child.get("children", [])))
            elif child.get("type") == "list":
                text = _text_of(spans).strip()
                if text:
                    nodes.append(DocsParagraphNode(
                        style="NORMAL_TEXT", text=text, is_list_item=True,
                        nesting_level=nesting_level, start_index=0, end_index=0,
                        spans=spans if _has_styling(spans) else [],
                    ))
                spans = []
                nodes.extend(_walk_list_items(child, nesting_level + 1))
                continue
            else:
                spans.extend(_spans_from_inline([child]))
        text = _text_of(spans).strip()
        if text:
            nodes.append(DocsParagraphNode(
                style="NORMAL_TEXT", text=text, is_list_item=True,
                nesting_level=nesting_level, start_index=0, end_index=0,
                spans=spans if _has_styling(spans) else [],
            ))
    return nodes


def _table_from_token(token: dict) -> DocsTableNode:
    """Convert a mistune table token into a DocsTableNode (plain-text cells)."""
    rows: List[List[str]] = []

    def cells_of(row_token: dict) -> List[str]:
        return [_extract_text_from_token(cell).strip()
                for cell in row_token.get("children", [])
                if cell.get("type") in ("table_cell", "block_text") or "children" in cell]

    for child in token.get("children", []):
        ctype = child.get("type")
        if ctype == "table_head":
            rows.append([_extract_text_from_token(c).strip() for c in child.get("children", [])])
        elif ctype == "table_body":
            for row in child.get("children", []):
                rows.append([_extract_text_from_token(c).strip() for c in row.get("children", [])])
        elif ctype == "table_row":
            rows.append(cells_of(child))
    # Normalize ragged rows to a uniform column count.
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return DocsTableNode(rows=rows, start_index=0, end_index=0)


class MarkdownToParagraphParser:
    """
    Parse Markdown content into a list of DocsParagraphNode / DocsTableNode.

    Uses mistune>=3.0 (AST renderer) for accurate block-level parsing.
    All target nodes have start_index=0, end_index=0 (not meaningful for push targets).
    """

    def parse(self, content: str) -> List[Node]:
        """Parse markdown content into a node list in document order."""
        import mistune

        # mistune.create_markdown(renderer=None) returns AST tokens. "table" is
        # enabled for table support; "task_lists" is deliberately NOT enabled —
        # checklist state is kept as literal text (ADR-001); that plugin would
        # strip the [ ]/[x] marker into attrs.checked and lose it from .text.
        md = mistune.create_markdown(renderer=None, plugins=["table"])
        tokens = md(content) or []

        nodes: List[Node] = []
        for token in tokens:
            token_type = token.get("type")

            if token_type == "heading":
                level = token.get("attrs", {}).get("level", token.get("level", 1))
                spans = _spans_from_inline(token.get("children", []))
                nodes.append(DocsParagraphNode(
                    style=f"HEADING_{level}", text=_text_of(spans).strip(),
                    start_index=0, end_index=0,
                    spans=spans if _has_styling(spans) else [],
                ))

            elif token_type == "paragraph":
                spans = _spans_from_inline(token.get("children", []))
                nodes.append(DocsParagraphNode(
                    style="NORMAL_TEXT", text=_text_of(spans).strip(),
                    start_index=0, end_index=0,
                    spans=spans if _has_styling(spans) else [],
                ))

            elif token_type == "list":
                nodes.extend(_walk_list_items(token, nesting_level=0))

            elif token_type in ("block_code", "code"):
                raw = token.get("raw", "").strip()
                nodes.append(DocsParagraphNode(
                    style="NORMAL_TEXT", text=raw, start_index=0, end_index=0,
                    spans=[TextSpan(text=raw, monospace=True)],
                ))

            elif token_type == "table":
                nodes.append(_table_from_token(token))

            elif token_type == "blank_line":
                pass

            # block_quote, thematic_break, html, etc. are silently skipped

        return nodes
