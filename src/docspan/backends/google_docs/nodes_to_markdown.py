"""Render DocsParagraphNode/DocsTableNode lists back into Markdown.

Used by GoogleDocsBackend.pull() only for the tab-scoped path (an explicit
Mapping.tab_id): Drive's HTML export (files.export) has no supported way to
target a single tab, so a tab-specific pull instead re-uses the structural
API path push() already relies on — DocsStructureParser.parse() — and needs
a nodes-to-markdown direction to go with MarkdownToParagraphParser's
markdown-to-nodes direction. Default (no tab_id) pulls keep using the
existing, more mature Drive HTML export -> DocumentConverter path unchanged.

This is a best-effort renderer, not a byte-for-byte inverse of
MarkdownToParagraphParser — round-tripping through Markdown is inherently
lossy (e.g. Docs' native nested-list structure vs. flat nesting_level here).
"""
from __future__ import annotations

from typing import List, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
)

Node = Union[DocsParagraphNode, DocsTableNode]


def _render_spans(spans: List[TextSpan]) -> str:
    parts = []
    for span in spans:
        text = span.text
        if span.monospace:
            text = f"`{text}`"
        if span.bold:
            text = f"**{text}**"
        if span.italic:
            text = f"*{text}*"
        if span.link:
            text = f"[{text}]({span.link})"
        parts.append(text)
    return "".join(parts)


def _cell_markdown_text(cell: TableCell) -> str:
    """The cell's content rendered through the same markdown-span rules as a paragraph."""
    return _render_spans(cell.spans) if cell.spans else cell.text


def _render_cell(cell: TableCell) -> str:
    """A cell's markdown — with its marks, and with `|` escaped.

    An unescaped `|` ends the cell, so a link whose URL or label contains one would
    split the row into extra columns. Not preserved: a `|` inside a *URL* keeps the
    row intact but comes back percent-encoded (`%7C`) on the next parse, so such a
    link is rewritten once and then stable.

    Used only for single-paragraph cells: `_render_table` routes any table holding a
    multi-paragraph cell (`\\n` in `cell.text`) to `_render_table_html` instead, since
    pipe-table syntax has no cell-internal line break. See that function's docstring
    for why raw HTML, not `<br>` or a diff-key change, is the fix — and issue #61.
    """
    return _cell_markdown_text(cell).replace("|", "\\|")


_BLANK_PARAGRAPH_MARKER = "​"


def _escape_html(text: str) -> str:
    """Entity-escape `&`, `<`, `>` — plus any *real* occurrence of the guard marker.

    Escaping the marker here means the raw U+200B byte only ever appears in the
    rendered HTML where `_guard_blank_paragraph_lines` deliberately put it — a real
    cell whose text happens to contain a stray U+200B (copy-pasted from another
    editor is a real source of these) round-trips as itself instead of being
    mistaken for the guard and silently dropped to "" on decode.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace(_BLANK_PARAGRAPH_MARKER, "&#8203;")
    )


def _guard_blank_paragraph_lines(paragraphs: List[str]) -> str:
    """Join already-escaped paragraph fragments, guarding interior blank ones.

    CommonMark ends an HTML block (type 6, e.g. `<table>`) at the first blank line —
    even one produced by an empty paragraph *inside* a cell's own text (an author
    leaving a blank line between two paragraphs). Left alone, that blank line would
    fracture mistune's single opaque `block_html` token into two, losing the rest of
    the table (see #61). A leading/trailing empty paragraph is safe as-is: it shares
    its physical line with the `<th>`/`</th>` tag text, so it's never actually blank.
    """
    guarded = list(paragraphs)
    for i in range(1, len(guarded) - 1):
        if guarded[i] == "":
            guarded[i] = _BLANK_PARAGRAPH_MARKER
    return "\n".join(guarded)


def _split_paragraph_spans(spans: List[TextSpan]) -> List[List[TextSpan]]:
    """Split a cell's spans at each embedded "\\n" into one span-list per paragraph.

    A cell's paragraph break lives inside whichever `TextSpan` happens to contain
    it (e.g. a bold run spanning "line one\\nline two") — span boundaries are
    styling boundaries, not paragraph boundaries. Rendering markdown syntax (e.g.
    `**`) across a paragraph break corrupts it: each side gets an unmatched marker
    once the fragment is later parsed on its own. Each paragraph must be rendered
    independently instead, exactly as `_spans_from_markdown_text` on the decode
    side expects.
    """
    paragraphs: List[List[TextSpan]] = [[]]
    for span in spans:
        parts = span.text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                paragraphs.append([])
            if part:
                paragraphs[-1].append(TextSpan(
                    text=part, bold=span.bold, italic=span.italic,
                    link=span.link, monospace=span.monospace,
                ))
    return paragraphs


def _cell_html_paragraphs(cell: TableCell) -> List[str]:
    """Each of a cell's paragraphs, independently markdown-rendered and escaped."""
    if cell.spans:
        return [_escape_html(_render_spans(p)) for p in _split_paragraph_spans(cell.spans)]
    return [_escape_html(p) for p in cell.text.split("\n")]


def _render_table_html(node: DocsTableNode) -> str:
    """Render a table holding a multi-paragraph cell as a raw HTML `<table>` block.

    Markdown's pipe-table syntax has no cell-internal paragraph break — every
    encoding into that syntax (a bare newline, `<br>`, `<br>` decoded back on parse)
    either breaks the row or silently destroys the table on the next unmodified push
    (see #51, #61). Raw HTML sidesteps the problem instead of encoding around it:
    mistune tokenizes the whole `<table>...</table>` as one opaque `block_html` raw
    string, so a literal `\\n` inside a `<td>` is just a character — nothing here
    reparses it as a row terminator. No `\\n`-encoding is needed at all, except for
    the blank-paragraph edge case `_guard_blank_paragraph_lines` handles.

    `_table_from_html_block` decodes the entity-escaping back and re-parses each
    paragraph's markdown independently (bold/links survive, joined across
    paragraphs by a literal `\\n` `TextSpan`).
    """
    header, *body = node.rows

    def render_row(row: List[TableCell], tag: str) -> str:
        cells = "".join(
            f"<{tag}>{_guard_blank_paragraph_lines(_cell_html_paragraphs(c))}</{tag}>"
            for c in row
        )
        return f"<tr>{cells}</tr>"

    rows_html = [render_row(header, "th")] + [render_row(r, "td") for r in body]
    return "<table>\n" + "\n".join(rows_html) + "\n</table>"


def _render_table(node: DocsTableNode) -> str:
    if not node.rows:
        return ""
    if any("\n" in c.text for row in node.rows for c in row):
        return _render_table_html(node)
    header, *body = node.rows
    lines = [
        "| " + " | ".join(_render_cell(c) for c in header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    lines.extend("| " + " | ".join(_render_cell(c) for c in row) + " |" for row in body)
    return "\n".join(lines)


def render_nodes_to_markdown(nodes: List[Node]) -> str:
    """Render a parsed node list (document order) back into Markdown text."""
    lines: List[str] = []
    for node in nodes:
        if isinstance(node, DocsTableNode):
            lines.append(_render_table(node))
            lines.append("")
            continue

        text = _render_spans(node.spans) if node.spans else node.text

        if node.style.startswith("HEADING_"):
            try:
                level = int(node.style.split("_", 1)[1])
            except ValueError:
                level = 1
            level = max(1, min(level, 6))
            lines.append(f"{'#' * level} {text}")
        elif node.is_list_item:
            indent = "  " * node.nesting_level
            if node.is_native_checkbox:
                # The Docs API does not expose a native checkbox's
                # checked/unchecked bit anywhere DocsStructureParser can read
                # it (see push_preview.py's NATIVE CHECKBOX GLYPH warning) —
                # DocsParagraphNode carries no checked-state field at all.
                # Rendering unchecked is the only honest option here; this is
                # a one-way, lossy render (never fed back through
                # MarkdownToParagraphParser as this exact text), not a claim
                # about the glyph's real state.
                lines.append(f"{indent}- [ ] {text}")
            else:
                lines.append(f"{indent}- {text}")
        else:
            lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
