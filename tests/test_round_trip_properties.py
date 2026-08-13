"""Property-based round-trip laws for the Google Docs pipeline.

Every bug fixed in this backend so far was found by someone picking a document
shape by hand — a blank paragraph, a title, a link, text starting with `#`. That
is a slow way to enumerate an alphabet. These tests state the *law* instead and
let Hypothesis look for the counterexamples.

The law, and it is the one the whole design rests on:

    push(pull(d)) emits no requests, for every document d

A document is pulled to markdown and pushed straight back with no edits. If that
produces even one `batchUpdate` request, docspan is about to change a document
nobody asked it to change. Anything Hypothesis finds here is, by construction, a
silent-corruption bug.

Two deliberate choices about scope:

* The generators build `DocsParagraphNode` lists directly rather than raw
  `documents.get()` JSON. The parser has its own tests; the asymmetry this hunts
  for lives between the *renderer* and the *markdown parser*, and going through
  JSON would only add a layer that can mask a shrink.
* `project()` is applied to both sides, exactly as `_build_push_plan` does, so a
  failure here is a real failure of the shipped pipeline and not of a
  hypothetical one that skips the projection.
"""
from __future__ import annotations

from typing import List

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.projection import project

builder = DocsRequestBuilder()
parser = MarkdownToParagraphParser()

# Styles the pipeline is expected to round-trip. TITLE/SUBTITLE are included
# because projection maps them onto headings — that mapping is part of the law.
STYLES = ["NORMAL_TEXT", "TITLE", "SUBTITLE"] + [f"HEADING_{n}" for n in range(1, 7)]

# Text likely to collide with markdown's own syntax. Hypothesis will also try
# arbitrary text; these are seeded because they are where the bugs cluster and a
# purely random generator finds them rarely.
HOSTILE_TEXT = [
    "plain",
    "# h",
    "## h",
    "###### h",
    "#nospace",
    "- b",
    "* b",
    "+ b",
    "1. n",
    "2) n",
    "---",
    "***",
    "___",
    "```code",
    "~~~x",
    "[ref]: /x",
    "> q",
    "| a | b |",
    "    indented",
    "\ttabbed",
    "text with  two spaces",
    "trailing ",
    " leading",
    "*emph*",
    "__bold__",
    "`code`",
    "[link](/x)",
    "!image",
    "a\\b",
    "100% \\* literal",
    "<!-- comment -->",
    "<div>",
    "&amp;",
]


def _text() -> st.SearchStrategy[str]:
    """Non-empty single-line text — empty is projected out, newlines aren't a paragraph."""
    printable = st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\n\r"),
        min_size=1,
        max_size=24,
    )
    return st.one_of(st.sampled_from(HOSTILE_TEXT), printable).filter(
        lambda s: s.strip() != ""
    )


@st.composite
def _paragraphs(draw: st.DrawFn) -> List[DocsParagraphNode]:
    """A document as a list of paragraph nodes with consistent UTF-16 indices."""
    specs = draw(
        st.lists(
            st.tuples(_text(), st.sampled_from(STYLES), st.booleans()),
            min_size=1,
            max_size=6,
        )
    )
    nodes: List[DocsParagraphNode] = []
    index = 1
    for text, style, is_list_item in specs:
        # A heading that is also a list item is not a shape the renderer claims
        # to support; keep the generator inside the contract.
        if style != "NORMAL_TEXT":
            is_list_item = False
        end = index + len(text.encode("utf-16-le")) // 2 + 1
        nodes.append(
            DocsParagraphNode(
                style=style,
                text=text,
                is_list_item=is_list_item,
                start_index=index,
                end_index=end,
            )
        )
        index = end
    return nodes


def _round_trip(nodes: List[DocsParagraphNode]):
    """pull → push with no edits. Returns (requests, rendered markdown, reparsed)."""
    doc_end = nodes[-1].end_index
    pulled, _residue = project(nodes)
    markdown = render_nodes_to_markdown(pulled)
    current, _ = project(nodes)
    target, _ = project(parser.parse(markdown))
    return builder.build(current, target, doc_end), markdown, target


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_paragraphs())
def test_pull_then_push_emits_no_requests(nodes: List[DocsParagraphNode]) -> None:
    """The fixpoint law. A counterexample here is a silent-corruption bug."""
    requests, markdown, target = _round_trip(nodes)

    assert requests == [], (
        "a zero-edit round trip wants to write\n"
        f"  document  : {[(n.style, n.text, n.is_list_item) for n in nodes]}\n"
        f"  rendered  : {markdown!r}\n"
        f"  reparsed  : {[(n.style, n.text, n.is_list_item) for n in target]}\n"
        f"  requests  : {requests}"
    )


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_paragraphs())
def test_pull_then_push_preserves_the_paragraph_count(
    nodes: List[DocsParagraphNode]
) -> None:
    """A sharper, separately-useful law: no paragraph may vanish or appear.

    Worth asserting on its own because losing a paragraph is worse than
    restyling one, and it shrinks to a much smaller counterexample — a single
    `---` renders as a thematic break and re-parses to *nothing*, so push would
    delete that paragraph outright.
    """
    _requests, markdown, target = _round_trip(nodes)
    projected, _ = project(nodes)

    assert len(target) == len(projected), (
        f"paragraph count changed {len(projected)} -> {len(target)}\n"
        f"  rendered : {markdown!r}\n"
        f"  reparsed : {[n.text for n in target]}"
    )


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_paragraphs())
def test_rendering_is_idempotent(nodes: List[DocsParagraphNode]) -> None:
    """render(parse(render(x))) == render(x).

    Weaker than the fixpoint law and it holds independently: even where the
    pipeline cannot preserve a *style*, the text it produces should settle after
    one pass rather than drifting on every sync.
    """
    projected, _ = project(nodes)
    once = render_nodes_to_markdown(projected)
    reparsed, _ = project(parser.parse(once))
    twice = render_nodes_to_markdown(reparsed)

    assert twice == once, f"rendering drifts:\n  once: {once!r}\n  twice: {twice!r}"
