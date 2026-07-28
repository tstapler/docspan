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
    DocsParagraphNode,
    DocsTableNode,
)

# The same alias DocsRequestBuilder uses. Declared here rather than imported
# from there so this module stays a leaf — the builder will eventually depend on
# it, not the other way round.
Node = Union[DocsParagraphNode, DocsTableNode]

ResidueKind = Literal["empty_paragraph", "paragraph_style"]

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
        if isinstance(node, DocsParagraphNode) and node.style in _UNWRITABLE_STYLES:
            residue.append(
                Residue(kind="paragraph_style", index=index, detail=node.style)
            )
            # `replace` rather than mutation: these nodes come from the caller's
            # parse and are also used for index arithmetic and preview text.
            node = replace(node, style=_UNWRITABLE_STYLES[node.style])
        kept.append(node)
    return kept, residue


def _describe_empty(node: DocsParagraphNode) -> str:
    """A short human-facing description of a dropped empty paragraph."""
    if node.is_list_item:
        return "empty list item"
    if node.style != "NORMAL_TEXT":
        return f"empty {node.style}"
    return "blank paragraph"


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
    styles = sorted({r.detail for r in residue if r.kind == "paragraph_style"})
    if styles:
        parts.append(
            f"{'/'.join(styles)} has no markdown equivalent and is treated as a "
            "heading, so docspan will not change it. Edit it in Google Docs directly."
        )
    return " ".join(parts)


__all__ = ["DocsTableNode", "Residue", "ResidueKind", "describe_residue", "project"]
