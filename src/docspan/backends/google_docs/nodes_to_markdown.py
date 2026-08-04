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


def _render_cell(cell: TableCell) -> str:
    """A cell's markdown — with its marks, and with `|` and newlines neutralised.

    A cell holds a *paragraph list*, not a string, and markdown's table syntax has
    no cell-internal line break. Both characters that would end something early are
    escaped, for the same reason and in ascending order of damage:

    * an unescaped `|` ends the **cell**, so a link whose URL or label contains one
      splits the row into extra columns;
    * an unescaped newline ends the **row**, so a two-paragraph cell reparses as a
      paragraph and the table is destroyed outright. `<br>` is the conventional
      stand-in and is what GitHub-flavoured markdown renders.

    Escaping the pipe alone was half of its own argument, and once spans are
    rendered it was actively worse: `**line one\nline two**` emits a dangling `**`
    across the break.

    Not preserved: a `|` inside a link *URL* survives as a row but comes back
    percent-encoded (`%7C`) on the next parse, so that link is rewritten once and
    then stable. Spans are not in the table diff key, so it surfaces as a
    non-idempotent `updateTextStyle` rather than a visible diff.
    """
    text = _render_spans(cell.spans) if cell.spans else cell.text
    return text.replace("|", "\\|").replace("\n", "<br>")


def _render_table(node: DocsTableNode) -> str:
    if not node.rows:
        return ""
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
