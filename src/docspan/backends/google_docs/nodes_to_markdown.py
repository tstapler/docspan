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
from docspan.backends.google_docs.registry import MarkdownNodeRenderer, MarkdownRenderRegistry

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


def _node_text(node: DocsParagraphNode) -> str:
    return _render_spans(node.spans) if node.spans else node.text


def _dispatch_key(node: Node) -> str:
    """Synthesize a dispatch key — DocsParagraphNode/DocsTableNode carry no `.type` field."""
    if isinstance(node, DocsTableNode):
        return "table"
    if node.style.startswith("HEADING_"):
        return "heading"
    if node.is_list_item:
        return "list_item"
    return "paragraph"


# ─────────────────────────────────────────────────────────────────────────────
# Pull-direction renderers — one class per synthesized dispatch key,
# registered in _build_pull_registry(). To add a new node kind, register a
# renderer there; render_nodes_to_markdown()'s dispatch loop needs no changes.
# ─────────────────────────────────────────────────────────────────────────────

class TableNodeRenderer(MarkdownNodeRenderer):
    node_key = "table"

    def render(self, node: DocsTableNode) -> str:
        return _render_table(node)


class HeadingNodeRenderer(MarkdownNodeRenderer):
    node_key = "heading"

    def render(self, node: DocsParagraphNode) -> str:
        try:
            level = int(node.style.split("_", 1)[1])
        except ValueError:
            level = 1
        level = max(1, min(level, 6))
        return f"{'#' * level} {_node_text(node)}"


class ListItemNodeRenderer(MarkdownNodeRenderer):
    node_key = "list_item"

    def render(self, node: DocsParagraphNode) -> str:
        indent = "  " * node.nesting_level
        text = _node_text(node)
        if node.is_native_checkbox:
            # The Docs API does not expose a native checkbox's
            # checked/unchecked bit anywhere DocsStructureParser can read
            # it (see push_preview.py's NATIVE CHECKBOX GLYPH warning) —
            # DocsParagraphNode carries no checked-state field at all.
            # Rendering unchecked is the only honest option here; this is
            # a one-way, lossy render (never fed back through
            # MarkdownToParagraphParser as this exact text), not a claim
            # about the glyph's real state.
            return f"{indent}- [ ] {text}"
        return f"{indent}- {text}"


class ParagraphNodeRenderer(MarkdownNodeRenderer):
    node_key = "paragraph"

    def render(self, node: DocsParagraphNode) -> str:
        return _node_text(node)


def _build_pull_registry() -> MarkdownRenderRegistry:
    registry = MarkdownRenderRegistry()
    registry.register("table", TableNodeRenderer())
    registry.register("heading", HeadingNodeRenderer())
    registry.register("list_item", ListItemNodeRenderer())
    registry.register("paragraph", ParagraphNodeRenderer())
    return registry


_PULL_REGISTRY = _build_pull_registry()


def render_nodes_to_markdown(nodes: List[Node]) -> str:
    """Render a parsed node list (document order) back into Markdown text."""
    lines: List[str] = []
    for node in nodes:
        renderer = _PULL_REGISTRY.get(_dispatch_key(node))
        lines.append(renderer.render(node))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
