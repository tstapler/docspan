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

The default (no-tab_id) pull path through converter.py's
_GoogleDocsMarkdownConverter has the same per-line-inline-code symptom via a
completely separate CSS-font-family-based mechanism and is NOT touched here
— see issue #45's "Scope gap found late": that HTML-export pipeline has no
render_prefix/span-shape equivalent to key fence detection off, would need
its own detection heuristic, and was out of scope for this fix. This was
confirmed rather than silently assumed: a live scope-confirmation request
went unanswered, so the gap was filed as a separate, explicit follow-up
(docspan backlog item a500ef94-2e02-499e-a628-5f843198c49e) instead of being
folded into or dropped from this fix.
"""
from __future__ import annotations

from typing import List, Optional, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
)

Node = Union[DocsParagraphNode, DocsTableNode]

# The literal marker MarkdownToParagraphParser writes ahead of a fenced
# block's lines to carry the language (mistune's token.attrs.info) through
# the node-list representation, which has no field for it. Must stay in
# sync with markdown_to_paragraph_parser.py's FENCE_MARKER.
FENCE_MARKER = "```"

# A rendered code fence can also open with tildes (see _fence_delimiter's
# backtick-in-language fallback) — a bare paragraph starting with either
# fence character must be escaped the same way.
TILDE_FENCE_MARKER = "~~~"


def _run_of_char(text: str, target: str) -> int:
    """The longest run of consecutive occurrences of `target` in text."""
    max_run = run = 0
    for ch in text:
        if ch == target:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run


def _run_of_backticks(text: str) -> int:
    """The longest run of consecutive backticks in text."""
    return _run_of_char(text, "`")


def _wrap_inline_code(text: str) -> str:
    """Wrap text as a CommonMark code span, escaping any backticks inside it.

    CommonMark's rule: the delimiter must be a run of backticks longer than
    the longest run inside the content, and if the content starts or ends
    with a backtick (or starts and ends with a space around non-space
    content), a single space is added inside the delimiters so the content
    doesn't fuse with them.
    """
    delim = "`" * (_run_of_backticks(text) + 1)
    needs_pad = text.startswith("`") or text.endswith("`")
    if not needs_pad and text[:1] == " " and text[-1:] == " " and text.strip():
        needs_pad = True
    if needs_pad:
        return f"{delim} {text} {delim}"
    return f"{delim}{text}{delim}"


def _fence_delimiter(lang: Optional[str], code_lines: List[str]) -> str:
    """The shortest fence (at least 3) longer than any run of its own
    character appearing in the content, so the fence can never be confused
    with a run of that character inside the code itself.

    CommonMark forbids a backtick fence's info string from containing any
    backtick at all — widening the fence doesn't help, since the rule isn't
    about run length there. So a language containing a backtick forces a
    tilde fence instead, which has no such restriction on its info string.
    """
    if lang and "`" in lang:
        max_run = max((_run_of_char(line, "~") for line in code_lines), default=0)
        return "~" * max(3, max_run + 1)
    max_run = max((_run_of_backticks(line) for line in [lang or "", *code_lines]), default=0)
    return "`" * max(3, max_run + 1)


def _render_spans(spans: List[TextSpan]) -> str:
    parts = []
    for span in spans:
        text = span.text
        if span.monospace:
            text = _wrap_inline_code(text)
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


def _is_pure_code_line(node: Node) -> bool:
    """Exactly the shape MarkdownToParagraphParser writes for a code line
    (`:294`): one span, monospace, and no other mark. Deliberately narrower
    than "contains some monospace" — a mixed-mark span (e.g. monospace+bold)
    or an isolated inline-code span inside an otherwise normal paragraph must
    never be swept into a fence.
    """
    if not isinstance(node, DocsParagraphNode):
        return False
    if node.style != "NORMAL_TEXT" or node.is_list_item:
        return False
    spans = node.spans
    return (
        len(spans) == 1
        and spans[0].monospace
        and not spans[0].bold
        and not spans[0].italic
        and not spans[0].link
    )


def _is_blank_code_line(node: Node) -> bool:
    """A blank line inside a fenced block (`:294`'s `spans=[]` branch).

    Only ever absorbed into an already-open code run by `_group_code_runs`
    (it looks ahead for another code line before treating one of these as
    part of the run) — an ordinary blank paragraph between two prose
    paragraphs has this exact shape too and must not, on its own, start or
    extend a fence.
    """
    return (
        isinstance(node, DocsParagraphNode)
        and node.style == "NORMAL_TEXT"
        and not node.is_list_item
        and not node.spans
        and node.text == ""
    )


def _is_language_marker(node: Node) -> bool:
    """The literal, non-monospace marker line MarkdownToParagraphParser
    writes ahead of a fence's lines to carry the language
    (`markdown_to_paragraph_parser.py`'s FENCE_MARKER). Non-monospace is
    what makes it unambiguously decodable: every real code line is
    monospace by construction, so this shape never collides with one.
    """
    if not isinstance(node, DocsParagraphNode):
        return False
    if node.style != "NORMAL_TEXT" or node.is_list_item:
        return False
    if not node.text.startswith(FENCE_MARKER):
        return False
    if node.spans:
        if len(node.spans) != 1:
            return False
        span = node.spans[0]
        if span.monospace or span.bold or span.italic or span.link:
            return False
    return True


def _group_code_runs(nodes: List[Node]) -> List[Tuple]:
    """Partition nodes into ("node", node) passthroughs and
    ("code", lang, code_nodes) runs of consecutive pure code lines
    (optionally preceded by a language marker, and allowing interior blank
    lines that are followed by more code before the run ends)."""
    groups: List[Tuple] = []
    i, n = 0, len(nodes)
    while i < n:
        node = nodes[i]
        lang: Optional[str] = None
        start = i
        if _is_language_marker(node) and i + 1 < n:
            # _is_language_marker already requires DocsParagraphNode; assert
            # narrows the Node union for mypy without re-checking at runtime.
            assert isinstance(node, DocsParagraphNode)
            if _is_pure_code_line(nodes[i + 1]):
                lang = node.text[len(FENCE_MARKER):]
                start = i + 1
            elif _is_blank_code_line(nodes[i + 1]) and (
                i + 2 >= n or not _is_pure_code_line(nodes[i + 2])
            ):
                # A marker immediately followed by exactly one blank-shaped
                # line, with no code line beyond it, is
                # MarkdownToParagraphParser's shape for an explicitly empty
                # fenced block (`:294`) — not an orphaned marker.
                groups.append(("code", node.text[len(FENCE_MARKER):], []))
                i += 2
                continue

        if start < n and _is_pure_code_line(nodes[start]):
            run: List[Node] = []
            j = start
            while j < n:
                if _is_pure_code_line(nodes[j]):
                    run.append(nodes[j])
                    j += 1
                    continue
                if _is_blank_code_line(nodes[j]):
                    k = j
                    while k < n and _is_blank_code_line(nodes[k]):
                        k += 1
                    if k < n and _is_pure_code_line(nodes[k]):
                        run.extend(nodes[j:k])
                        j = k
                        continue
                break
            groups.append(("code", lang, run))
            i = j
        else:
            groups.append(("node", node))
            i += 1
    return groups


def _render_code_group(lang: Optional[str], code_nodes: List[Node]) -> List[str]:
    # code_nodes are only ever populated via _is_pure_code_line/
    # _is_blank_code_line, both of which require DocsParagraphNode; assert
    # narrows the Node union for mypy without re-checking at runtime.
    code_lines = []
    for node in code_nodes:
        assert isinstance(node, DocsParagraphNode)
        code_lines.append(node.text)
    delim = _fence_delimiter(lang, code_lines)
    # CommonMark reads the fence's width off the *leading run* of the fence
    # character on the opening line, not a separately-tokenized delimiter —
    # if the info string starts with that same character (e.g. a tilde
    # fence whose language itself starts with "~"), it silently extends the
    # opening fence past `delim`, so the closing line we write is too short
    # to close the block. A single space breaks the adjacency; CommonMark
    # trims it from the info string on reparse, so it's otherwise a no-op.
    sep = " " if lang and lang[:1] == delim[:1] else ""
    lines = [f"{delim}{sep}{lang or ''}"]
    lines.extend(code_lines)
    lines.append(delim)
    lines.append("")
    return lines


def _escape_leading_fence(text: str) -> str:
    """Escape a plain paragraph line that would otherwise open a live
    CommonMark code fence when reparsed.

    Only the bare-paragraph render path needs this: headings prefix "# ",
    list items prefix "- ", and block quotes prefix "> ", all of which
    already keep a fence-shaped line from starting the line. Without it, a
    marker node left behind by `_group_code_runs` (because it wasn't
    immediately followed by a matching code run) or ordinary user prose that
    happens to start with ``` or ~~~ renders as literal fence-shaped text
    that, on the next parse, opens an unterminated fence and swallows every
    following line as code until EOF instead of staying inert.
    """
    if text.startswith(FENCE_MARKER) or text.startswith(TILDE_FENCE_MARKER):
        return "\\" + text
    return text


def render_nodes_to_markdown(nodes: List[Node]) -> str:
    """Render a parsed node list (document order) back into Markdown text."""
    lines: List[str] = []
    for group in _group_code_runs(nodes):
        if group[0] == "code":
            _, lang, code_nodes = group
            lines.extend(_render_code_group(lang, code_nodes))
            continue

        node = group[1]
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
            lines.append(_escape_leading_fence(text))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
