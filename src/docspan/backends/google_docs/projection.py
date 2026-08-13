"""What markdown can say about a Google Doc — the one place that decides.

`DocsRequestBuilder._opcodes` diffs two lists of the same Python type produced
by two *unrelated* codecs: `DocsStructureParser.parse(live_doc)` and
`MarkdownToParagraphParser.parse(local_file)`. Nothing in the type system
requires them to draw from the same alphabet, and they do not — so a document
can hold state that no markdown file can round-trip back, and the diff reads
that state as a difference the user asked for.

`project()` is the retraction that was missing. Both sides of the diff pass
through it, so the diff only ever sees the part of a document that markdown can
faithfully represent. State it removes is recorded as `Residue` rather than
dropped silently, because a no-op nobody is told about is worse than a visible
failure.

Scope today is one rule — the empty paragraph. It is deliberately the whole
module rather than a helper inside the builder, so the next rule (checkbox
markers, `TITLE`, fenced code) lands in one obvious place instead of as another
special case at another call site.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Literal, Sequence, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsImageNode,
    DocsParagraphNode,
    DocsTableNode,
    _is_all_private_use,
    _utf16_len,
)

# The same alias DocsRequestBuilder uses. Declared here rather than imported
# from there so this module stays a leaf — the builder will eventually depend on
# it, not the other way round.
Node = Union[DocsParagraphNode, DocsTableNode, DocsImageNode]

ResidueKind = Literal[
    "empty_paragraph", "paragraph_style", "private_use_glyph", "ambiguous_code_prefix"
]

# Named styles markdown has no syntax for, mapped to the nearest style it does.
# Google Docs' own outline treats TITLE/SUBTITLE as document-level headings, and
# `#`/`##` is what a markdown reader will do with them, so the mapping loses the
# distinction rather than the structure.
_UNWRITABLE_STYLES: Dict[str, str] = {
    "TITLE": "HEADING_1",
    "SUBTITLE": "HEADING_2",
}


@dataclass(frozen=True)
class Residue:
    """State `project()` removed because markdown cannot express it.

    Never enters the diff, never gets rendered as text, and is always available
    to the caller so a push can report what it could not act on. `index` is the
    position in the *unprojected* sequence, which is what a human-facing message
    needs in order to say where.
    """

    kind: ResidueKind
    index: int
    detail: str = ""


def project(nodes: Sequence[Node]) -> Tuple[List[Node], List[Residue]]:
    """Reduce ``nodes`` to what markdown can represent, plus what was removed.

    Rule 1 — an empty paragraph is dropped.

    A blank line in markdown is a *separator*, not content, so an empty
    paragraph has no representation at all: ``MarkdownToParagraphParser`` never
    emits an empty-text node, while ``DocsStructureParser`` does whenever the
    document contains one. That asymmetry is the whole of the bug. A document of
    ``[Alpha, (empty), Omega]`` renders to ``'Alpha\\n\\n\\n\\nOmega\\n'``,
    which re-parses to two nodes rather than three — so a zero-edit round trip
    produced a spurious ``remove`` and an unflagged ``deleteContentRange``
    against a live document (issue #17).

    Dropping it from *both* sides makes it invisible to the diff, and therefore
    preserved. The cost, which is deliberate: docspan can no longer create or
    destroy an empty paragraph. That is the correct trade — it is a *reported*
    no-op instead of a silent deletion of someone's blank line, and blank
    paragraphs are load-bearing layout in real documents.

    It also retires residue docspan manufactures itself: a delete trimmed to
    protect the newline anchoring a Table/ToC/SectionBreak leaves an empty
    paragraph behind, so the tool was creating the very state it could not
    represent, then trying to delete it on the next push and failing.

    Filtering the current side is index-safe: every surviving node keeps its own
    ``start_index``/``end_index``, and an insert positioned at a preceding
    node's ``end_index`` lands in front of the dropped paragraph rather than
    inside it.

    Rule 1b — a paragraph holding only Private-Use-Area glyphs is dropped.

    Exactly rule 1's situation with a different unrepresentable character. Google
    Docs writes `U+E907` into a paragraph for constructs it renders itself, and
    markdown has no syntax for one — so `MarkdownToParagraphParser` can never
    produce a matching node, the diff reads the paragraph as "the user deleted
    this", and push emits a delete for it.

    Docs then **refuses that delete**, and because `batchUpdate` is atomic the
    *entire push fails*:

        Invalid requests[4].deleteContentRange: Invalid deletion range.
        Cannot delete the requested range.

    Measured on a real design doc — three such paragraphs, 56 requests, HTTP 400,
    nothing written. Any document containing one could never be pushed at all,
    which makes this the same self-inflicted trap rule 1 already describes: the
    tool cannot represent the state, so it tries to delete it, and fails forever.

    Only a paragraph whose text is *entirely* PUA (ignoring surrounding
    whitespace) is dropped. One that also carries real text is kept, because
    dropping it would lose the author's content — the glyph is then a cosmetic
    wart on a paragraph that still round-trips.

    Rule 2 — ``TITLE`` and ``SUBTITLE`` become ``HEADING_1``/``HEADING_2``.

    Markdown has no syntax for either, so ``nodes_to_markdown`` rendered them as
    *bare text* — and re-parsing bare text gives ``NORMAL_TEXT``. A zero-edit
    round trip over a document with a title therefore produced
    ``change 'My Doc' -> 'My Doc'`` (identical text, which is also a useless
    thing to show a user in a preview) and five requests that deleted the
    paragraph and reinserted it as body text, silently demoting the title.

    Mapping to the nearest style markdown *can* say makes the round trip a no-op:
    ``HEADING_1`` renders as ``# My Doc``, re-parses as ``HEADING_1``, and
    matches. An intentional demotion still works — markdown saying plain
    ``My Doc`` differs from ``HEADING_1`` and is applied as the user asked.

    The distinction between a title and a heading is lost, which is why it is
    recorded as residue. Unlike rule 1 this is a *substitution*, not a removal,
    so ``kept`` keeps the same length.

    Idempotent by construction — ``project(project(x)[0])[0] == project(x)[0]``
    — because both rules are properties of a single node, not of the sequence,
    and ``HEADING_1``/``HEADING_2`` are not themselves keys of the map. A test
    pins it anyway, since later rules will not have that for free.
    """
    kept: List[Node] = []
    residue: List[Residue] = []
    for index, node in enumerate(nodes):
        if isinstance(node, DocsParagraphNode) and node.text == "":
            residue.append(
                Residue(
                    kind="empty_paragraph",
                    index=index,
                    detail=_describe_empty(node),
                )
            )
            continue
        if isinstance(node, DocsParagraphNode) and _is_all_private_use(node.text):
            residue.append(
                Residue(kind="private_use_glyph", index=index, detail=node.text.strip())
            )
            continue
        if isinstance(node, DocsParagraphNode) and node.render_prefix:
            # A paragraph *inside* a Docs-rendered block: the glyph goes, the
            # author's content stays. The paragraph still participates in the
            # diff and can be restyled or have its text changed. Only deleting
            # it is off limits, which DocsRequestBuilder enforces from
            # `render_prefix`.
            stripped = _without_render_prefix(node)
            # There is no signal in the parsed API data that reliably tells
            # Docs' own render chrome apart from an author's own PUA character
            # sitting alone in its leading run (e.g. a bold/italic boundary
            # puts it in its own run). A real code block's first line is
            # monospace; if what remains after the prefix is not, this may be
            # an author's character silently dropped rather than chrome, so
            # it is reported instead of assumed safe.
            if not (stripped.spans and stripped.spans[0].monospace):
                residue.append(
                    Residue(
                        kind="ambiguous_code_prefix",
                        index=index,
                        detail=node.render_prefix,
                    )
                )
            kept.append(stripped)
            continue
        if isinstance(node, DocsParagraphNode) and node.style in _UNWRITABLE_STYLES:
            residue.append(
                Residue(kind="paragraph_style", index=index, detail=node.style)
            )
            # `replace` rather than mutation: these nodes come from the caller's
            # parse and are also used for index arithmetic and preview text.
            node = replace(node, style=_UNWRITABLE_STYLES[node.style])
        kept.append(node)
    return kept, residue


def _without_render_prefix(node: DocsParagraphNode) -> DocsParagraphNode:
    """The same paragraph as the markdown would describe it — prefix removed.

    `start_index` advances by the prefix's width and the spans lose it, so pass 2
    still places styling correctly: the API counted those units, and removing them
    from the text without moving the index puts every span in the paragraph one
    unit early.

    `render_prefix` is *kept* on the result. It is what tells
    `DocsRequestBuilder` the paragraph belongs to a block Docs renders and must
    not be taken apart — the whole reason the prefix is recorded rather than
    discarded at parse time.
    """
    width = _utf16_len(node.render_prefix)
    remaining, spans = width, list(node.spans)
    while remaining > 0 and spans:
        head = _utf16_len(spans[0].text)
        if head <= remaining:
            remaining -= head
            spans.pop(0)
        else:
            spans[0] = replace(spans[0], text=spans[0].text[remaining:])
            remaining = 0
    return replace(
        node,
        text=node.text[len(node.render_prefix):],
        start_index=node.start_index + width,
        spans=spans,
    )


def _describe_empty(node: DocsParagraphNode) -> str:
    """A short human-facing description of a dropped empty paragraph."""
    if node.is_list_item:
        return "empty list item"
    if node.style != "NORMAL_TEXT":
        return f"empty {node.style}"
    return "blank paragraph"


def describe_target_residue(residue: List[Residue]) -> str:
    """One line summarising residue removed from the *markdown* side.

    Separate from describe_residue because the two are opposite situations and the
    wording cannot be shared. Doc-side residue is state markdown cannot express, so
    it is *left alone* — nothing is lost. Markdown-side residue is content the
    author wrote that push will **not write**, which is a loss and has to say so.

    Reachable since fenced code blocks became one node per line: a blank line
    inside a block, an empty fence and a blank-only fence all produce `text=""`.
    """
    parts: List[str] = []
    blanks = sum(1 for r in residue if r.kind == "empty_paragraph")
    if blanks:
        parts.append(
            f"⚠ {blanks} blank line(s) inside a code block were not written to the doc — "
            "markdown can express them but the diff cannot carry them. Add them in Google "
            "Docs directly if they matter."
        )
    glyphs = sum(1 for r in residue if r.kind == "private_use_glyph")
    if glyphs:
        parts.append(
            f"⚠ {glyphs} paragraph(s) in the markdown hold only a Google Docs "
            "private-use character and were not written to the doc — that character has "
            "no meaning outside a Doc Google renders itself. Remove it from the markdown "
            "if it was not intentional."
        )
    return " ".join(parts)


def describe_residue(residue: List[Residue]) -> str:
    """One line summarising residue, for a push message. Empty string if none."""
    if not residue:
        return ""
    parts: List[str] = []
    blanks = sum(1 for r in residue if r.kind == "empty_paragraph")
    if blanks:
        parts.append(
            f"{blanks} blank paragraph(s) in the doc are not represented in markdown, "
            "so they were left alone. Add or remove them in Google Docs directly."
        )
    glyphs = sum(1 for r in residue if r.kind == "private_use_glyph")
    if glyphs:
        parts.append(
            f"{glyphs} paragraph(s) hold only a Google Docs private-use glyph, which "
            "markdown cannot express, so they were left alone. Docs refuses to delete "
            "them, and batchUpdate is atomic, so trying would fail the whole push."
        )
    ambiguous = sum(1 for r in residue if r.kind == "ambiguous_code_prefix")
    if ambiguous:
        parts.append(
            f"{ambiguous} paragraph(s) start with a Google Docs private-use glyph that "
            "was treated as code-block chrome and dropped, but the rest of the paragraph "
            "is not monospace, so it may instead be a character the author wrote. Check "
            "these in Google Docs if that glyph was intentional."
        )
    styles = sorted({r.detail for r in residue if r.kind == "paragraph_style"})
    if styles:
        parts.append(
            f"{'/'.join(styles)} has no markdown equivalent and is treated as a "
            "heading, so docspan will not change it. Edit it in Google Docs directly."
        )
    return " ".join(parts)


__all__ = ["DocsTableNode", "Residue", "ResidueKind", "describe_residue", "project"]
