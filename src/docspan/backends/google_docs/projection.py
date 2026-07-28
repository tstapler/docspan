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

from dataclasses import dataclass
from typing import List, Literal, Sequence, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
)

# The same alias DocsRequestBuilder uses. Declared here rather than imported
# from there so this module stays a leaf — the builder will eventually depend on
# it, not the other way round.
Node = Union[DocsParagraphNode, DocsTableNode]

ResidueKind = Literal["empty_paragraph"]


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

    Idempotent by construction — ``project(project(x)[0])[0] == project(x)[0]``
    — because the predicate is a property of a single node, not of the sequence.
    A test pins it anyway, since later rules will not have that for free.
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
    blanks = sum(1 for r in residue if r.kind == "empty_paragraph")
    return (
        f"{blanks} blank paragraph(s) in the doc are not represented in markdown, "
        "so they were left alone. Add or remove them in Google Docs directly."
    )


__all__ = ["DocsTableNode", "Residue", "ResidueKind", "describe_residue", "project"]
