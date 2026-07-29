"""Internal markdown anchors (`[A1](#a1-...)`) as Google Docs heading links.

Covers both directions and the gate between them:

    markdown `#slug`  --heading_anchors.link_payload-->  {"headingId": ...}
    {"headingId": ...} --DocsStructureParser.parse--->  markdown `#slug`
    an anchor naming no heading  --push()-->  status="error", nothing written

The slug tests are the load-bearing ones: an anchor that slugs differently from
the heading it names does not fail, it *silently* fails — the link resolves to
nothing and, before the gate existed, was written as a `url` link the reader of
the Doc could click and land nowhere.
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    TextSpan,
)
from docspan.backends.google_docs.heading_anchors import (
    UnresolvedAnchorError,
    heading_id_to_slug,
    heading_slug_to_id,
    link_payload,
    slugify,
    slugify_all,
    unresolved_anchors,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

structure = DocsStructureParser()
builder = DocsRequestBuilder()
markdown = MarkdownToParagraphParser()


# ─────────────────────────────────────────────────────────────────────────────
# Doc JSON helpers
# ─────────────────────────────────────────────────────────────────────────────

def _paragraph(
    text: str,
    start: int,
    style: str = "NORMAL_TEXT",
    heading_id: str | None = None,
    runs: list[dict] | None = None,
) -> dict:
    """One paragraph structural element, with `runs` overriding the plain text."""
    paragraph_style: dict = {"namedStyleType": style}
    if heading_id:
        paragraph_style["headingId"] = heading_id
    elements = runs if runs is not None else [
        {"textRun": {"content": text + "\n", "textStyle": {}}}
    ]
    return {
        "startIndex": start,
        "endIndex": start + len(text) + 1,
        "paragraph": {"paragraphStyle": paragraph_style, "elements": elements},
    }


def _doc(*paragraphs: dict, revision_id: str = "rev-1") -> dict:
    return {"revisionId": revision_id, "body": {"content": list(paragraphs)}}


# ─────────────────────────────────────────────────────────────────────────────
# slugify — parity with github-slugger
# ─────────────────────────────────────────────────────────────────────────────

class TestSlugify:
    @pytest.mark.parametrize(
        "heading, expected",
        [
            ("Current state", "current-state"),
            ("Decision requested", "decision-requested"),
            # The case from the report: the em dash is dropped and the two
            # spaces that surrounded it each become a hyphen. A naive
            # re.sub(r"\s+", "-") collapses them to one and the anchor misses.
            ("A1 — Current-state measurements", "a1--current-state-measurements"),
            # Every ASCII punctuation mark except - and _ is dropped.
            ("What's next? (probably)", "whats-next-probably"),
            ("0. Decision requested", "0-decision-requested"),
            ("snake_case and kebab-case", "snake_case-and-kebab-case"),
            # Accented letters survive; the rule is alphanumeric, not ASCII.
            ("Café measurements", "café-measurements"),
            # Non-breaking space is punctuation to the slugger, not a space, so
            # it is dropped rather than becoming a hyphen.
            ("A B", "ab"),
            ("", ""),
        ],
    )
    def test_matches_github(self, heading: str, expected: str) -> None:
        assert slugify(heading) == expected

    def test_leading_and_trailing_space_do_not_leak_into_the_slug(self) -> None:
        # ATX heading text is trimmed by the markdown parser before GitHub's
        # slugger ever sees it, so parity end-to-end means trimming here.
        assert slugify("  Intro  ") == "intro"

    def test_duplicate_headings_get_github_s_numeric_suffixes(self) -> None:
        assert slugify_all(["Intro", "Intro", "Intro"]) == ["intro", "intro-1", "intro-2"]

    def test_suffix_skips_a_slug_a_literal_heading_already_owns(self) -> None:
        # "Intro 1" slugs to intro-1 on its own, so the second "Intro" cannot
        # take that suffix — github-slugger keeps searching and lands on
        # intro-2. A bare counter hands two headings the same anchor.
        assert slugify_all(["Intro", "Intro 1", "Intro"]) == ["intro", "intro-1", "intro-2"]

    def test_slug_order_depends_on_position_not_text(self) -> None:
        assert slugify_all(["A", "B", "A"]) == ["a", "b", "a-1"]


# ─────────────────────────────────────────────────────────────────────────────
# Heading maps
# ─────────────────────────────────────────────────────────────────────────────

class TestHeadingMaps:
    def test_slug_maps_to_the_headings_own_id(self) -> None:
        nodes = structure.parse(_doc(
            _paragraph("A1 — Current-state measurements", 1, "HEADING_2", "h.abc123"),
            _paragraph("body", 33),
        ))
        assert heading_slug_to_id(nodes) == {"a1--current-state-measurements": "h.abc123"}
        assert heading_id_to_slug(nodes) == {"h.abc123": "a1--current-state-measurements"}

    def test_a_heading_without_an_id_still_consumes_its_duplicate_suffix(self) -> None:
        # Otherwise the third heading's slug would be intro-1 here and intro-2
        # on GitHub, and the anchor the author wrote would miss.
        nodes = structure.parse(_doc(
            _paragraph("Intro", 1, "HEADING_1", "h.first"),
            _paragraph("Intro", 8, "HEADING_1", None),
            _paragraph("Intro", 15, "HEADING_1", "h.third"),
        ))
        assert heading_slug_to_id(nodes) == {"intro": "h.first", "intro-2": "h.third"}

    def test_non_headings_are_not_anchor_targets(self) -> None:
        nodes = structure.parse(_doc(_paragraph("Just a paragraph", 1)))
        assert heading_slug_to_id(nodes) == {}


# ─────────────────────────────────────────────────────────────────────────────
# Write direction
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteDirection:
    def test_url_link_is_unchanged(self) -> None:
        assert link_payload("https://example.com") == {"url": "https://example.com"}

    def test_anchor_becomes_a_heading_id_link(self) -> None:
        assert link_payload("#intro", {"intro": "h.abc"}) == {"headingId": "h.abc"}

    def test_an_anchor_naming_a_heading_id_verbatim_resolves(self) -> None:
        # Closes the round trip: a pull that could not name a slug emits the
        # bare id, and this takes it straight back without either side
        # inventing an escape syntax.
        assert link_payload("#h.abc", {}, {"h.abc"}) == {"headingId": "h.abc"}

    def test_unresolvable_anchor_is_never_downgraded_to_a_url(self) -> None:
        assert link_payload("#nope", {"intro": "h.abc"}) is None

    def test_pass_two_writes_a_heading_id_link_for_an_anchor(self) -> None:
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", "h.intro"),
            _paragraph("see Intro", 8, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {"content": "Intro\n", "textStyle": {}}},
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="see Intro", spans=[
                TextSpan(text="see "),
                TextSpan(text="Intro", link="#intro"),
            ]),
        ]

        requests = builder.build_span_style_requests(doc, target)

        links = [
            r["updateTextStyle"]["textStyle"]["link"]
            for r in requests
            if "link" in r["updateTextStyle"]["textStyle"]
        ]
        assert links == [{"headingId": "h.intro"}]

    def test_a_url_and_an_anchor_in_one_paragraph_each_get_their_own_union_member(self) -> None:
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", "h.intro"),
            _paragraph("web and Intro", 8, runs=[
                {"textRun": {"content": "web", "textStyle": {}}},
                {"textRun": {"content": " and ", "textStyle": {}}},
                {"textRun": {"content": "Intro\n", "textStyle": {}}},
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="web and Intro", spans=[
                TextSpan(text="web", link="https://example.com"),
                TextSpan(text=" and "),
                TextSpan(text="Intro", link="#intro"),
            ]),
        ]

        requests = builder.build_span_style_requests(doc, target)

        links = [
            r["updateTextStyle"]["textStyle"]["link"]
            for r in requests
            if "link" in r["updateTextStyle"]["textStyle"]
        ]
        assert links == [{"url": "https://example.com"}, {"headingId": "h.intro"}]

    def test_pass_two_raises_rather_than_writing_a_link_to_nowhere(self) -> None:
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", "h.intro"),
            _paragraph("dangling", 8, runs=[
                {"textRun": {"content": "dangling\n", "textStyle": {}}}
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="dangling", spans=[
                TextSpan(text="dangling", link="#missing-section"),
            ]),
        ]

        with pytest.raises(UnresolvedAnchorError) as caught:
            builder.build_span_style_requests(doc, target)

        assert "#missing-section" in str(caught.value)
        assert "#intro" in str(caught.value)  # names what was available


# ─────────────────────────────────────────────────────────────────────────────
# Read direction — the half that makes the anchor survive a pull
# ─────────────────────────────────────────────────────────────────────────────

class TestReadDirection:
    def test_a_heading_id_link_reads_back_as_an_anchor(self) -> None:
        # Before this, `link` was read as textStyle.link.url — which a headingId
        # link does not have — so the span came back with link=None and `pull`
        # deleted the cross-reference from the markdown without saying so.
        nodes = structure.parse(_doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n",
                             "textStyle": {"link": {"headingId": "h.cur"}}}},
            ]),
        ))
        assert nodes[1].spans[0].link == "#current-state"

    def test_a_url_link_still_reads_back_as_the_url(self) -> None:
        nodes = structure.parse(_doc(
            _paragraph("x", 1, runs=[
                {"textRun": {"content": "x\n",
                             "textStyle": {"link": {"url": "https://example.com"}}}},
            ]),
        ))
        assert nodes[0].spans[0].link == "https://example.com"

    def test_a_heading_id_with_no_heading_keeps_the_bare_id(self) -> None:
        # A link into a deleted heading, or into another tab. Emitting the id
        # is lossless and the write direction resolves it by exact match;
        # dropping it would be the silent data loss this fixes.
        nodes = structure.parse(_doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n",
                             "textStyle": {"link": {"headingId": "h.gone"}}}},
            ]),
        ))
        assert nodes[0].spans[0].link == "#h.gone"

    def test_a_bookmark_link_stays_unread(self) -> None:
        # bookmarkId is explicitly out of scope; None is its pre-existing
        # behaviour and this pins it so "anchors work now" is not overread.
        nodes = structure.parse(_doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n",
                             "textStyle": {"link": {"bookmarkId": "kix.b1"}}}},
            ]),
        ))
        assert nodes[0].spans[0].link is None

    def test_pull_renders_the_anchor_back_into_markdown(self) -> None:
        nodes = structure.parse(_doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n",
                             "textStyle": {"link": {"headingId": "h.cur"}}}},
            ]),
        ))
        assert "[see it](#current-state)" in render_nodes_to_markdown(nodes)


# ─────────────────────────────────────────────────────────────────────────────
# Round trip
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_markdown_survives_push_then_pull_unchanged(self) -> None:
        source = "## Current state\n\nsee [it](#current-state)\n"

        # What a pull of the doc that source produces gives back.
        doc = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {"content": "it\n",
                             "textStyle": {"link": {"headingId": "h.cur"}}}},
            ]),
        )
        pulled = render_nodes_to_markdown(structure.parse(doc))

        assert markdown.parse(pulled)[1].spans[-1].link == "#current-state"
        assert markdown.parse(pulled)[1].spans == markdown.parse(source)[1].spans

    def test_re_pushing_an_unchanged_anchor_re_emits_the_same_heading_id_link(self) -> None:
        """The anchor's *content* is stable across pushes, which is what matters.

        Pass 2 re-emits every styled span on every push — it uses the live
        paragraph only for its bounds and never compares against the styling
        already there — so a doc holding any inline styling at all is not
        zero-request, anchors or not. That is pre-existing and outside this
        change; what this pins is that the repeated request is the *same*
        `headingId` link rather than drifting to a `url` or to a different
        heading, so re-pushing cannot degrade a link that already worked.
        """
        doc = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {"content": "it\n",
                             "textStyle": {"link": {"headingId": "h.cur"}}}},
            ]),
        )
        target = structure.parse(doc)

        first = builder.build_span_style_requests(doc, target)
        second = builder.build_span_style_requests(doc, target)

        assert first == second
        assert [
            r["updateTextStyle"]["textStyle"]["link"]
            for r in first
            if "link" in r["updateTextStyle"]["textStyle"]
        ] == [{"headingId": "h.cur"}]


# ─────────────────────────────────────────────────────────────────────────────
# The pre-write gate
# ─────────────────────────────────────────────────────────────────────────────

class TestUnresolvedAnchorsCheck:
    def test_an_anchor_matching_a_heading_in_the_same_markdown_is_resolvable(self) -> None:
        target = markdown.parse("## Current state\n\nsee [it](#current-state)\n")
        assert unresolved_anchors(target, []) == []

    def test_a_typo_is_reported(self) -> None:
        target = markdown.parse("## Current state\n\nsee [it](#current-stat)\n")
        assert unresolved_anchors(target, []) == ["#current-stat"]

    def test_an_anchor_onto_a_heading_only_the_document_has_is_resolvable(self) -> None:
        # A partial push: the heading is not in this markdown, but it is in the
        # doc, so the link has a real target.
        target = markdown.parse("see [it](#current-state)\n")
        document = structure.parse(_doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
        ))
        assert unresolved_anchors(target, document) == []

    def test_a_bare_heading_id_from_an_earlier_pull_is_resolvable(self) -> None:
        target = markdown.parse("see [it](#h.cur)\n")
        document = structure.parse(_doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
        ))
        assert unresolved_anchors(target, document) == []

    def test_each_bad_anchor_is_reported_once_in_use_order(self) -> None:
        target = markdown.parse("[a](#zz) and [b](#yy) and [c](#zz)\n")
        assert unresolved_anchors(target, []) == ["#zz", "#yy"]


class TestPushGate:
    def _local(self, tmp_path, text: str) -> str:
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_push_refuses_and_writes_nothing_when_an_anchor_names_no_heading(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(revision_id="rev-1")
        local = self._local(tmp_path, "## Current state\n\nsee [it](#typo)\n")

        result = backend.push(local, "doc-1")

        assert result.status == "error"
        assert "#typo" in result.message
        assert local in result.message  # names the file, per the report
        fake_client.batch_update.assert_not_called()

    def test_force_does_not_buy_a_broken_link(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # force exists to overwrite a human's work, not to write something
        # already known to be broken.
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(revision_id="rev-1")
        local = self._local(tmp_path, "## Current state\n\nsee [it](#typo)\n")

        result = backend.push(local, "doc-1", force=True)

        assert result.status == "error"
        fake_client.batch_update.assert_not_called()

    def test_a_good_anchor_is_not_blocked(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(revision_id="rev-1")
        local = self._local(tmp_path, "## Current state\n\nsee [it](#current-state)\n")

        result = backend.push(local, "doc-1")

        assert result.status != "error", result.message
        assert fake_client.batch_update.call_count >= 1
