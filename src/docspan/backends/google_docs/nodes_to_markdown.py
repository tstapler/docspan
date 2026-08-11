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
    """A cell's markdown — with its marks, and with `|` escaped.

    An unescaped `|` ends the cell, so a link whose URL or label contains one would
    split the row into extra columns. Not preserved: a `|` inside a *URL* keeps the
    row intact but comes back percent-encoded (`%7C`) on the next parse, so such a
    link is rewritten once and then stable.

    **A newline is deliberately left alone**, and that is a decision rather than an
    omission. A Docs cell holds a paragraph *list* and markdown's table syntax has no
    cell-internal break, so a two-paragraph cell has no faithful rendering. Every
    encoding tried is worse than the gap:

    * emit the newline — the row ends early and the table reparses as a paragraph.
      Loud: the next diff shows the table gone.
    * emit `<br>` — the table survives, but nothing can decode it back, and the table
      diff key includes cell text, so a pull then an *unmodified* push sees a change
      and answers it by deleting and re-creating the table, taking every comment
      anchored inside it. Silent and permanent, since it converges after one push.
    * emit `<br>` and decode it on parse — closes that, and opens the identical hole
      for a cell whose author *typed* `<br>`: it becomes a newline, the key stops
      matching, and the table is destroyed the same way. A cell holding only `<br>`
      comes back empty. Markdown cannot distinguish the two, so the decode cannot
      either.

    So the loud failure is kept over either quiet one. `_cell_placement` already
    declines a multi-paragraph cell and `unplaced_table_cells` reports it, so the case
    is announced rather than merely broken. See the follow-up issue for a real fix,
    which needs something other than markdown's table syntax to carry the break.
    """
    text = _render_spans(cell.spans) if cell.spans else cell.text
    return text.replace("|", "\\|")


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
                # This structural render (documents.get() -> DocsStructureParser
                # -> here) is used for the tab-scoped pull path, where checked
                # state genuinely can't be recovered: Drive's files.export
                # can't target a single tab, and that's the only read path
                # that exposes a native checkbox's checked bit (see
                # checkbox_state.py, used by the default/non-tab-scoped pull
                # path instead). DocsParagraphNode itself still carries no
                # checked-state field. Rendering unchecked is the only honest
                # option on this path; this is a one-way, lossy render (never
                # fed back through MarkdownToParagraphParser as this exact
                # text), not a claim about the glyph's real state.
                lines.append(f"{indent}- [ ] {text}")
            else:
                lines.append(f"{indent}- {text}")
        else:
            lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
