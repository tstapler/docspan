"""Parse Markdown content into DocsParagraphNode/DocsTableNode list for Google Docs push."""
from __future__ import annotations

from typing import List, Optional, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
    _trim_spans_to_cell_text,
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


def _prefix_node_text(node: DocsParagraphNode, prefix: str) -> DocsParagraphNode:
    """Return a copy of node with prefix prepended to its text and first span.

    Used to render block-quote lines as literal "> "-prefixed text (same
    approach as ADR-001's literal checklist markers) so the quote survives
    push→pull round-trips even though Google Docs has no native blockquote
    paragraph style to map onto.
    """
    spans = list(node.spans)
    if spans:
        spans = [TextSpan(text=prefix)] + spans
    return DocsParagraphNode(
        style=node.style, text=prefix + node.text, is_list_item=node.is_list_item,
        nesting_level=node.nesting_level, start_index=node.start_index,
        end_index=node.end_index, spans=spans,
    )


def _walk_block_quote(token: dict, quote_depth: int = 1) -> List[DocsParagraphNode]:
    """Walk a block_quote token, prefixing each contained line with '> ' markers.

    Nested block_quote tokens increase quote_depth (rendered as repeated
    '> > ' markers), matching standard Markdown nesting syntax.
    """
    prefix = "> " * quote_depth
    nodes: List[DocsParagraphNode] = []
    for child in token.get("children", []):
        ctype = child.get("type")
        if ctype == "paragraph":
            spans = _spans_from_inline(child.get("children", []))
            nodes.append(_prefix_node_text(
                DocsParagraphNode(style="NORMAL_TEXT", text=_text_of(spans).strip(),
                                  start_index=0, end_index=0,
                                  spans=spans if _has_styling(spans) else []),
                prefix,
            ))
        elif ctype == "list":
            nodes.extend(
                _prefix_node_text(n, prefix) for n in _walk_list_items(child, nesting_level=0)
            )
        elif ctype == "block_quote":
            nodes.extend(_walk_block_quote(child, quote_depth + 1))
        elif ctype == "blank_line":
            continue
        # nested tables/code inside a block quote are rare; fall back to
        # skipping rather than mis-rendering them.
    return nodes


def _cell_from_token(token: dict) -> TableCell:
    """A table cell, with its inline styling kept.

    Cells used to be flattened with `_extract_text_from_token`, which walks to the
    leaf text and discards every mark on the way — so a link written inside a cell
    was silently reduced to its label. `_spans_from_inline` is the same walk the
    paragraph path already uses, and it keeps them.
    """
    spans = _spans_from_inline(token.get("children", []))
    text = _text_of(spans).strip()
    if not text:
        return TableCell(text="", spans=[])
    # Re-derive the spans against the stripped text so they still concatenate to
    # it; pass 2 walks span widths to place ranges inside the cell.
    spans = _trim_spans_to_cell_text(spans, _text_of(spans), text)
    return TableCell(text=text, spans=spans if _has_styling(spans) else [])


def _table_from_token(token: dict) -> DocsTableNode:
    """Convert a mistune table token into a DocsTableNode."""
    rows: List[List[TableCell]] = []

    def cells_of(row_token: dict) -> List[TableCell]:
        return [_cell_from_token(cell)
                for cell in row_token.get("children", [])
                if cell.get("type") in ("table_cell", "block_text") or "children" in cell]

    for child in token.get("children", []):
        ctype = child.get("type")
        if ctype == "table_head":
            rows.append([_cell_from_token(c) for c in child.get("children", [])])
        elif ctype == "table_body":
            for row in child.get("children", []):
                rows.append([_cell_from_token(c) for c in row.get("children", [])])
        elif ctype == "table_row":
            rows.append(cells_of(child))
    # Normalize ragged rows to a uniform column count.
    width = max((len(r) for r in rows), default=0)
    # A comprehension, not `[TableCell()] * n`. The repeated form puts *one* object at
    # every padded position — nothing to do with a mutable default, which
    # `field(default_factory=list)` already avoids; `[x] * n` aliases whatever `x` is.
    #
    # Unreachable through mistune today: it rejects a ragged GFM table outright rather
    # than padding it, so `width` equals every row's length and the slice is empty.
    # Kept because `cells_of` can still shorten a bare `table_row` token, and
    # correct-but-unreached beats a latent alias.
    rows = [r + [TableCell() for _ in range(width - len(r))] for r in rows]
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
                # One node per line, because a Google Doc has no multi-line
                # paragraph. Emitting the whole block as a single node with
                # embedded newlines meant `insertText` wrote "\nline one\nline
                # two", which Docs splits into N paragraphs — so every later diff
                # saw N document paragraphs against 1 markdown node and
                # delete-and-reinserted the whole block, on every push, forever.
                # The text survived that; what did not was idempotence, any comment
                # anchored to a line of code, and the monospace styling (pass 2
                # reported the block unaligned and emitted no span requests at
                # all). See issue #40.
                #
                # `strip("\n")` rather than `strip()`: the fence's own blank edges
                # go, indentation does not. Leading whitespace is meaning in code.
                raw = token.get("raw", "").strip("\n")
                for line in raw.split("\n"):
                    nodes.append(DocsParagraphNode(
                        style="NORMAL_TEXT", text=line, start_index=0, end_index=0,
                        # A blank line inside a block carries no span to style.
                        # projection.project() drops it from *both* sides, so the
                        # diff never sees it and never tries to delete it.
                        spans=[TextSpan(text=line, monospace=True)] if line else [],
                    ))

            elif token_type == "table":
                nodes.append(_table_from_token(token))

            elif token_type == "block_quote":
                nodes.extend(_walk_block_quote(token))

            elif token_type == "blank_line":
                pass

            # thematic_break, html, etc. are silently skipped

        return nodes
