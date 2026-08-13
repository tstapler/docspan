"""Parse Markdown content into DocsParagraphNode/DocsTableNode list for Google Docs push."""
from __future__ import annotations

import html
import re
from typing import List, Optional, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsImageNode,
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
    _trim_spans_to_cell_text,
)

Node = Union[DocsParagraphNode, DocsTableNode, DocsImageNode]

_HTML_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_HTML_CELL_RE = re.compile(r"<(th|td)>(.*?)</\1>", re.S)

_inline_md = None

_BLANK_PARAGRAPH_MARKER = "​"


def _spans_from_markdown_text(text: str) -> List[TextSpan]:
    """Inline-parse one paragraph fragment of a multi-paragraph HTML table cell.

    A pipe-table cell already goes through mistune's inline tokenizer as part of
    parsing the surrounding GFM table. A `<table>` block is opaque raw HTML to
    mistune (see `_table_from_html_block`), so each paragraph's text is re-parsed
    here the same way, independently, to recover bold/link/monospace spans.
    """
    if not text:
        return []
    global _inline_md
    if _inline_md is None:
        import mistune

        _inline_md = mistune.create_markdown(renderer=None, plugins=["table"])
    for token in _inline_md(text) or []:
        # token is `str | dict[str, Any]` per mistune's stubs; the isinstance
        # guard narrows it for mypy (matches the pattern used elsewhere in
        # this file for the same union).
        if isinstance(token, dict) and token.get("type") == "paragraph":
            return _spans_from_inline(token.get("children", []))
    # Not a paragraph (e.g. text that is itself a bare "<table>" or "<br>") —
    # the whole fragment is opaque raw HTML to mistune's block parser, so keep it
    # as literal text rather than dropping it or guessing at its structure.
    return [TextSpan(text=text)]


def _cell_from_html_text(raw_cell_html: str) -> TableCell:
    """A `<td>`/`<th>` inner HTML string, decoded back into a `TableCell`.

    The `\\n` between paragraphs is kept as a literal, unstyled `TextSpan` — it was
    never HTML-encoded on render (see `_render_table_html`), so there is nothing to
    decode there; only each paragraph fragment's own text needs unescaping. An
    interior empty paragraph was rendered with `_BLANK_PARAGRAPH_MARKER` in place of
    the blank line CommonMark would otherwise treat as ending the HTML block (see
    `_guard_blank_paragraph_lines`); strip it back to empty here.

    The marker check happens *before* `html.unescape` on each fragment, not on the
    whole string upfront: a real cell containing a literal U+200B was entity-escaped
    to `&#8203;` on render (`_escape_html`), so it never collides with the raw
    marker byte here — only the guard's own insertion does.
    """
    if not raw_cell_html:
        return TableCell(text="", spans=[])
    spans: List[TextSpan] = []
    for i, fragment in enumerate(raw_cell_html.split("\n")):
        if i > 0:
            spans.append(TextSpan(text="\n"))
        paragraph = "" if fragment == _BLANK_PARAGRAPH_MARKER else html.unescape(fragment)
        spans.extend(_spans_from_markdown_text(paragraph))
    full_text = "".join(s.text for s in spans)
    return TableCell(text=full_text, spans=spans if _has_styling(spans) else [])


def _table_from_html_block(raw: str) -> DocsTableNode:
    """Convert a raw HTML `<table>` block (see `_render_table_html`) into a DocsTableNode."""
    rows: List[List[TableCell]] = []
    for row_match in _HTML_ROW_RE.finditer(raw):
        rows.append([
            _cell_from_html_text(cell_match.group(2))
            for cell_match in _HTML_CELL_RE.finditer(row_match.group(1))
        ])
    width = max((len(r) for r in rows), default=0)
    rows = [r + [TableCell() for _ in range(width - len(r))] for r in rows]
    return DocsTableNode(rows=rows, start_index=0, end_index=0)


# Literal, non-monospace marker line written ahead of a fenced block's lines to
# carry mistune's token.attrs.info (the fence language) through the node-list
# representation, which otherwise has nowhere to put it. Must stay in sync
# with nodes_to_markdown.py's FENCE_MARKER, which decodes it back into a real
# ```lang fence on render.
FENCE_MARKER = "```"


def _extract_text_from_token(token: dict) -> str:
    """Recursively extract plain text from a mistune AST token."""
    if token.get("type") in ("raw", "text", "codespan"):
        return token.get("raw", "")
    children = token.get("children")
    if children:
        return "".join(_extract_text_from_token(c) for c in children)
    return token.get("raw", "")


def _link_url(token: dict) -> str:
    """Return the URL of a mistune link/image token across attr shapes."""
    attrs = token.get("attrs") or {}
    return attrs.get("url") or token.get("link") or ""


def _image_only_token(children: List[dict]) -> Optional[dict]:
    """Return the sole `image` token if `children` has no other meaningful content.

    v1 scope: an image mixed into a paragraph alongside real running text
    falls through to the plain-paragraph path (recursing into the image's
    alt-text children as plain spans, losing `src`) rather than becoming a
    `DocsImageNode` — see `DocsImageNode`'s docstring for why interleaved
    image+text is out of scope.
    """
    image_tok: Optional[dict] = None
    for tok in children or []:
        ttype = tok.get("type")
        if ttype == "image":
            if image_tok is not None:
                return None
            image_tok = tok
        elif ttype in ("text", "raw") and not tok.get("raw", "").strip():
            continue
        elif ttype in ("linebreak", "softbreak"):
            continue
        else:
            return None
    return image_tok


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
            elif child.get("type") == "block_code":
                text = _text_of(spans).strip()
                if text:
                    nodes.append(DocsParagraphNode(
                        style="NORMAL_TEXT", text=text, is_list_item=True,
                        nesting_level=nesting_level, start_index=0, end_index=0,
                        spans=spans if _has_styling(spans) else [],
                    ))
                spans = []
                nodes.extend(_nodes_from_code_block(
                    child, is_list_item=True, nesting_level=nesting_level,
                ))
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


def _nodes_from_code_block(
    token: dict, *, is_list_item: bool = False, nesting_level: int = 0,
    emit_language_marker: bool = False,
) -> List[DocsParagraphNode]:
    """One DocsParagraphNode per line of a fenced code block.

    A Google Doc has no multi-line paragraph. Emitting the whole block as a
    single node with embedded newlines meant `insertText` wrote "\\nline
    one\\nline two", which Docs splits into N paragraphs — so every later diff
    saw N document paragraphs against 1 markdown node and delete-and-reinserted
    the whole block, on every push, forever. The text survived that; what did
    not was idempotence, any comment anchored to a line of code, and the
    monospace styling (pass 2 reported the block unaligned and emitted no span
    requests at all). See issue #40 (top level) and #43 (list items and
    blockquotes — the same token falls through to raw multi-line text at
    those other two parse sites unless routed through this helper).

    `strip("\n")` rather than `strip()`: the fence's own blank edges go,
    indentation does not. Leading whitespace is meaning in code.

    `emit_language_marker` carries mistune's `token.attrs.info` (the fence
    language) through the node-list representation via a literal,
    non-monospace `FENCE_MARKER + lang` line written ahead of the code lines
    (see `FENCE_MARKER`'s docstring). Only the top-level call site
    (`parse()`) passes True. `nodes_to_markdown.py`'s `_is_language_marker`
    (like `_is_pure_code_line`) requires `not node.is_list_item`, so a marker
    emitted for a list item would never decode back. A blockquote's
    `_prefix_node_text` rewrites the marker's leading "```" into "> ```"
    anyway, breaking the match on `FENCE_MARKER` the same way. Either way the
    marker would just be a stray literal line, so list items and blockquotes
    stay marker-less on purpose.
    """
    nodes: List[DocsParagraphNode] = []
    if emit_language_marker:
        # token is `str | dict[str, Any]` per mistune's stubs; the isinstance
        # guard narrows it for mypy (this loop's other branches already
        # tolerate the same union untyped).
        attrs = token.get("attrs") if isinstance(token, dict) else None
        info = (attrs or {}).get("info") or ""
        lang = info.strip()
        if lang:
            nodes.append(DocsParagraphNode(
                style="NORMAL_TEXT", text=f"{FENCE_MARKER}{lang}",
                start_index=0, end_index=0, spans=[],
            ))
    raw = token.get("raw", "").strip("\n")
    # An empty fenced block (` ```\n```\n `) has `raw == ""`, so
    # `"".split("\n")` yields `[""]` — one blank-shaped node right after the
    # marker (when present). That's deliberate: it's the signal
    # `_group_code_runs` uses to render an explicit empty fence rather than
    # losing the block or leaving an unterminated marker behind.
    for line in raw.split("\n"):
        nodes.append(DocsParagraphNode(
            style="NORMAL_TEXT", text=line, is_list_item=is_list_item,
            nesting_level=nesting_level, start_index=0, end_index=0,
            # A blank line inside a block carries no span to style.
            # projection.project() drops it from *both* sides, so the
            # diff never sees it and never tries to delete it.
            spans=[TextSpan(text=line, monospace=True)] if line else [],
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
        elif ctype == "block_code":
            # A blank line inside the fence becomes a bare "> " line rather than
            # being dropped: `_prefix_node_text` turns the empty text into a
            # non-empty one, so it survives projection's blank-drop rule (which
            # is keyed on text == "") — intentionally, so the quote's visual
            # continuity isn't broken by a vanishing line mid-block. It carries
            # no monospace span, matching every other blank code line.
            nodes.extend(
                _prefix_node_text(n, prefix) for n in _nodes_from_code_block(child)
            )
        elif ctype == "block_quote":
            nodes.extend(_walk_block_quote(child, quote_depth + 1))
        elif ctype == "blank_line":
            continue
        # nested tables inside a block quote are rare; fall back to skipping
        # rather than mis-rendering them.
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
                image_tok = _image_only_token(token.get("children", []))
                if image_tok is not None:
                    nodes.append(DocsImageNode(
                        src=_link_url(image_tok),
                        alt=_extract_text_from_token(image_tok),
                        start_index=0, end_index=0,
                    ))
                else:
                    spans = _spans_from_inline(token.get("children", []))
                    nodes.append(DocsParagraphNode(
                        style="NORMAL_TEXT", text=_text_of(spans).strip(),
                        start_index=0, end_index=0,
                        spans=spans if _has_styling(spans) else [],
                    ))

            elif token_type == "list":
                nodes.extend(_walk_list_items(token, nesting_level=0))

            elif token_type in ("block_code", "code"):
                # Top level is the only one of the three `_nodes_from_code_block`
                # call sites (here, `_walk_list_items`, `_walk_block_quote`)
                # that carries the fence's language through a marker line —
                # see the helper's docstring for why list items and
                # blockquotes stay marker-less. A *native* Google Docs code
                # block (typed in the Docs UI, not pushed by this tool) never
                # has a marker either, and matching push's target against
                # that live structure depends on the two shapes being
                # identical (`:test_an_unchanged_code_block_emits_nothing`),
                # which is also why a lang-less fence here stays marker-less.
                nodes.extend(_nodes_from_code_block(token, emit_language_marker=True))

            elif token_type == "table":
                nodes.append(_table_from_token(token))

            elif token_type == "block_quote":
                nodes.extend(_walk_block_quote(token))

            elif token_type == "blank_line":
                pass

            elif token_type == "block_html":
                # A multi-paragraph table cell renders as a raw <table> block
                # (_render_table_html) since pipe syntax has no cell-internal
                # break. Any other raw HTML is unsupported and silently
                # skipped, as before.
                raw = token.get("raw", "").strip() if isinstance(token, dict) else ""
                if raw.lower().startswith("<table"):
                    nodes.append(_table_from_html_block(raw))

            # thematic_break, etc. are silently skipped

        return nodes
