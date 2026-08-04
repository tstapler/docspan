"""Internal markdown anchors (`[A1](#a1-...)`) as Google Docs heading links.

Covers both directions and what happens when an anchor resolves to nothing:

    markdown `#slug`  --heading_anchors.link_payload-->  {"headingId": ...}
    {"headingId": ...} --DocsStructureParser.parse--->  markdown `#slug`
    an anchor naming no heading  --push()-->  status="warning", text unlinked

Two families carry the weight, and both are about *silent* failure rather than
loud failure:

* the slug tests — an anchor that slugs differently from the heading it names
  resolves to nothing, and before any of this existed was written as a `url`
  link the reader of the Doc could click and land nowhere;
* TestNeverResolvesToTheWrongHeading — an anchor that resolves to the *wrong*
  heading is worse still, because the push reports a green ✓. Every test there
  was written against a mutant that survived this whole suite.
"""
from __future__ import annotations

import json
import pathlib
import unicodedata
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
    available_anchor_slugs,
    heading_id_to_slug,
    heading_slug_to_id,
    link_payload,
    slugify,
    slugify_all,
    unresolved_anchors,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.tabs import heading_ids_by_tab

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

    def test_nfd_combining_marks_survive(self) -> None:
        # "café" decomposed — an `e` followed by U+0301, which is how macOS
        # often hands text over. `str.isalnum()` is False for a combining mark,
        # so a rule built on it silently produced "cafe" and the anchor missed.
        assert slugify("café measurements") == "café-measurements"
        assert slugify("café") != slugify("cafe")


class TestGithubSluggerParity:
    """Check slugify against output from the real github-slugger.

    Every other test in TestSlugify asserts a hand-picked literal, which pins
    the behaviour this implementation *has* rather than parity with the thing it
    claims parity with. These cases came out of running the npm package; the
    two known divergences are enumerated in the fixture rather than omitted, so
    this cannot quietly become a test of the code against itself.
    """

    VECTORS = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "github_slugger_vectors.json")
        .read_text(encoding="utf-8")
    )

    def test_the_fixture_records_its_provenance_and_its_deviations(self) -> None:
        assert "github-slugger" in self.VECTORS["_provenance"]
        assert self.VECTORS["_deviations"]
        assert self.VECTORS["cases"]

    @pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: repr(c["input"])[:48])
    def test_matches_the_real_implementation(self, case: dict) -> None:
        deviations = self.VECTORS["_deviations"]
        if any(heading in deviations for heading in case["input"]):
            pytest.skip(f"documented divergence: {deviations[case['input'][0]][:60]}…")
        assert slugify_all(case["input"]) == case["slugs"]

    @pytest.mark.parametrize("heading", sorted(VECTORS["_deviations"]))
    def test_each_documented_divergence_still_diverges(self, heading: str) -> None:
        # If one of these starts agreeing, the fixture is stale and the skip
        # above is hiding a passing case — which would make the parity claim
        # weaker than it needs to be.
        truth = next(
            case["slugs"] for case in self.VECTORS["cases"] if heading in case["input"]
        )
        assert slugify_all([heading]) != truth


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

    def test_pass_two_writes_no_link_rather_than_one_to_nowhere(self) -> None:
        doc, target = self._dangling_anchor_case()

        requests = builder.build_span_style_requests(doc, target)

        assert not any(
            "link" in request["updateTextStyle"]["textStyle"] for request in requests
        )

    def test_an_unresolvable_anchor_keeps_the_spans_other_marks(self) -> None:
        # Dropping the link must not cost the bold as well — and must not cost
        # any *other* paragraph's styling either, which is why pass 2 reports
        # rather than aborting.
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", "h.intro"),
            _paragraph("dangling", 8, runs=[
                {"textRun": {"content": "dangling\n", "textStyle": {}}}
            ]),
            _paragraph("elsewhere", 17, runs=[
                {"textRun": {"content": "elsewhere\n", "textStyle": {}}}
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="dangling", spans=[
                TextSpan(text="dangling", link="#missing-section", bold=True),
            ]),
            DocsParagraphNode(style="NORMAL_TEXT", text="elsewhere", spans=[
                TextSpan(text="elsewhere", italic=True),
            ]),
        ]

        styles = [
            request["updateTextStyle"]["textStyle"]
            for request in builder.build_span_style_requests(doc, target)
        ]

        assert {"bold": True} in styles       # the dangling span keeps its bold
        assert {"italic": True} in styles     # and the unrelated paragraph is untouched
        assert not any("link" in style for style in styles)

    def test_the_unresolvable_anchor_is_reported(self) -> None:
        doc, target = self._dangling_anchor_case()
        assert builder.unresolved_anchor_links(doc, target) == ["#missing-section"]

    def test_a_resolvable_anchor_is_not_reported(self) -> None:
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", "h.intro"),
            _paragraph("see it", 8, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="#intro"),
            ]),
        ]
        assert builder.unresolved_anchor_links(doc, target) == []

    def test_a_heading_the_document_reports_without_an_id_is_reported(self) -> None:
        # The residue the --dry-run advisory cannot see: the heading exists, in the
        # markdown and in the document, but the document gives it no headingId.
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_1", heading_id=None),
            _paragraph("see it", 8, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
        )
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="#intro"),
            ]),
        ]
        assert builder.unresolved_anchor_links(doc, target) == ["#intro"]

    @staticmethod
    def _dangling_anchor_case() -> tuple[dict, list]:
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
        return doc, target


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
# The dry-run advisory, and what push reports
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


class TestPushReporting:
    def _local(self, tmp_path, text: str) -> str:
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_a_typo_does_not_block_the_content_but_is_never_reported_as_ok(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # The content changes are correct and wanted; only the link has no
        # target. So the push proceeds and the anchor is named — what must never
        # happen is a green "ok" over a cross-reference that did not land.
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        local = self._local(tmp_path, "## Current state\n\n[see it](#typo)\n")

        result = backend.push(local, "doc-1")

        assert result.status == "warning"
        assert "#typo" in result.message

    def test_push_warns_when_the_written_heading_has_no_id_to_link_to(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # The document already holds the content, so pass 1 writes nothing and
        # pass 2 runs against this same doc — whose heading has no headingId.
        # push must not report a green "ok" over a cross-reference that did not
        # land, and must not report it as "styling was not applied" either: the
        # paragraph is styled, only the link is missing.
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(
            _paragraph("Current state", 1, "HEADING_2", heading_id=None),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        local = self._local(tmp_path, "## Current state\n\n[see it](#current-state)\n")

        result = backend.push(local, "doc-1")

        assert result.status == "warning"
        assert "#current-state" in result.message
        # Cause-neutral wording: three different causes reach this report, and
        # an earlier version asserted the rarest of them.
        assert "nothing in the document matches what they name" in result.message
        # The "did you mean" tail must not offer the anchor it just called dead.
        # It did: the markdown heading is present, so its slug was listed as
        # available, while resolution had discarded the mapping because the
        # document reports that heading with no `headingId`. Here nothing
        # resolves, and saying so is the honest answer.
        assert "the document has no headings to anchor to" in result.message
        assert "available heading anchors" not in result.message

    def test_a_good_anchor_writes_the_heading_id_link(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """A resolvable anchor reaches the document as a `headingId` link.

        The pre/post `side_effect` is load-bearing. With a single
        `return_value`, pass 2 refetches the *pre-write* document — a state that
        cannot occur in production — and this test passed while the push
        returned `warning` and wrote **zero** links, which is the opposite of
        what its name claims. Asserting on the request that actually reaches
        batch_update, not on the status, is the other half.
        """
        backend, fake_client = make_backend()
        # Pass 1 sees an empty document and writes the content; pass 2 refetches
        # and finds the heading it just created, carrying the id Docs assigns.
        before = _doc(revision_id="rev-1")
        after = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.current"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-2",
        )
        fake_client.get_document.side_effect = [before, after]
        local = self._local(tmp_path, "## Current state\n\nsee [it](#current-state)\n")

        result = backend.push(local, "doc-1")

        assert result.status in ("ok", "warning"), result.message
        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for call in fake_client.batch_update.call_args_list
            for request in call.args[1]
            if "updateTextStyle" in request
            and "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert {"headingId": "h.current"} in links, links


# ─────────────────────────────────────────────────────────────────────────────
# Never a link to the WRONG heading
#
# The failure these pin is worse than a missing link and invisible without
# them: the anchor resolves, push reports a green "ok", and the reader lands on
# a different section. Each test was written against a mutant that survived the
# whole suite.
# ─────────────────────────────────────────────────────────────────────────────

class TestNeverResolvesToTheWrongHeading:
    def _links(self, doc: dict, md: str) -> list[dict]:
        target = markdown.parse(md)
        return [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"].get("textStyle", {})
        ]

    def test_a_duplicate_suffix_never_lands_on_an_unaligned_heading(self) -> None:
        """`#overview-1` means "my second `## Overview`" — never the first.

        Kills the mutant that seeds slug->id from the document alone. difflib
        pairs the markdown's *second* Overview with the document's *only* one
        (`[Overview, Details]` is the longest matching block), so the markdown's
        first Overview goes unpaired. Reading the document's numbering then maps
        `overview-1` onto the first Overview's id and both anchors point at the
        same heading.
        """
        doc = _doc(
            _paragraph("Overview", 1, "HEADING_2", "h.first"),
            _paragraph("Details", 10, "HEADING_2", "h.details"),
            _paragraph("see one and two", 18, runs=[
                {"textRun": {"content": "see one and two\n", "textStyle": {}}}
            ]),
        )
        md = (
            "## Overview\n\n## Overview\n\n## Details\n\n"
            "see [one](#overview) and [two](#overview-1)\n"
        )
        links = self._links(doc, md)
        heading_ids = [link.get("headingId") for link in links]

        # The two anchors name different headings, so they must never share an
        # id — that is the whole defect, stated without depending on which of
        # the two difflib happens to pair.
        assert len(set(heading_ids)) == len(heading_ids), heading_ids
        assert builder.unresolved_anchor_links(doc, markdown.parse(md)), (
            "an anchor with no heading of its own must be reported, not silently "
            "redirected to another heading"
        )

    def test_a_heading_the_document_reports_without_an_id_does_not_capture_an_anchor(
        self,
    ) -> None:
        """`#intro-1` must not land on a literal `## Intro 1` heading.

        The markdown's second `## Intro` owns `intro-1`. The document also holds
        a heading whose own text slugs to `intro-1`, and the paragraph the
        markdown's second Intro aligns with reports no `headingId` — so seeding
        from the document hands the anchor to the wrong heading entirely.
        """
        doc = _doc(
            _paragraph("Intro", 1, "HEADING_2", "h.a"),
            _paragraph("Intro 1", 7, "HEADING_2", "h.x"),
            _paragraph("Intro", 15, "HEADING_2", heading_id=None),
            _paragraph("see it", 21, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
        )
        md = "## Intro\n\n## Intro\n\nsee [it](#intro-1)\n"

        assert "h.x" not in [link.get("headingId") for link in self._links(doc, md)]
        assert builder.unresolved_anchor_links(doc, markdown.parse(md)) == ["#intro-1"]

    def test_a_document_title_is_a_valid_anchor_target(self) -> None:
        """TITLE and SUBTITLE are anchor targets, not just `HEADING_*`.

        This is a **read**-side property. Markdown `#` parses to `HEADING_1`, not
        to `TITLE` — measured — so the push path never consults a TITLE style and
        a test there cannot see this branch (one did not, and the mutant
        survived). What does consult it is a document Docs itself styled as
        TITLE, which is every doc whose title was typed into the title bar.

        Verified against a live Google Doc: a TITLE paragraph does carry a
        `headingId` (`h.cw5ps6hndpkc`) and a SUBTITLE one does too
        (`h.xkpxvmbu5mys`), so this fixture is not an invented API shape.
        """
        doc = _doc(
            _paragraph("My doc", 1, "TITLE", "h.title"),
            _paragraph("Sub", 8, "SUBTITLE", "h.sub"),
        )
        nodes = structure.parse(doc)

        # Without TITLE/SUBTITLE these maps are empty and a link into the
        # document's own title reads back as no link at all.
        assert heading_slug_to_id(nodes) == {"my-doc": "h.title", "sub": "h.sub"}
        assert heading_id_to_slug(nodes) == {"h.title": "my-doc", "h.sub": "sub"}

    def test_a_title_link_reads_back_as_its_slug(self) -> None:
        """The same property end to end: pull renders `#my-doc`, not a bare id."""
        doc = _doc(
            _paragraph("My doc", 1, "TITLE", "h.title"),
            _paragraph("back to top", 8, runs=[
                {"textRun": {"content": "back to ", "textStyle": {}}},
                {"textRun": {
                    "content": "top",
                    "textStyle": {"link": {"headingId": "h.title"}},
                }},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ]),
        )
        assert "[top](#my-doc)" in render_nodes_to_markdown(structure.parse(doc))


# ─────────────────────────────────────────────────────────────────────────────
# Non-ASCII anchors survive the markdown parser
# ─────────────────────────────────────────────────────────────────────────────

class TestPercentEncodedAnchors:
    def test_the_parser_percent_encodes_a_non_ascii_anchor(self) -> None:
        """Pins the upstream behaviour the rest of this class compensates for.

        If mistune ever stops encoding, this fails and the decode below can go.
        """
        spans = [
            span
            for node in markdown.parse("see [Café](#café-notes)\n")
            for span in (node.spans or [])
            if span.link
        ]
        assert [span.link for span in spans] == ["#caf%C3%A9-notes"]

    def test_an_accented_anchor_still_resolves(self) -> None:
        doc = _doc(
            _paragraph("Café notes", 1, "HEADING_2", "h.cafe"),
            _paragraph("see Café", 12, runs=[
                {"textRun": {"content": "see Café\n", "textStyle": {}}}
            ]),
        )
        md = "## Café notes\n\nsee [Café](#café-notes)\n"
        target = markdown.parse(md)

        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert {"headingId": "h.cafe"} in links, links
        assert builder.unresolved_anchor_links(doc, target) == []


# ─────────────────────────────────────────────────────────────────────────────
# The tabs-aware Link union
# ─────────────────────────────────────────────────────────────────────────────

class TestTabsAwareLinkUnion:
    @pytest.mark.parametrize(
        "link, expected",
        [
            # Returned when includeTabsContent is false or unset.
            ({"headingId": "h.abc"}, "#h.abc"),
            # Returned when it is true — which client.get_document defaults to,
            # so this is the shape the parser actually sees. Verified against a
            # live Doc: the same document parsed to 5 links without the flag and
            # 0 with it, before this member was handled.
            ({"heading": {"id": "h.abc", "tabId": "t.0"}}, "#h.abc"),
            ({"url": "https://example.com"}, "https://example.com"),
            # Still out of scope, and named so the union is visibly closed.
            ({"bookmarkId": "kix.b1"}, None),
            ({"bookmark": {"id": "kix.b1", "tabId": "t.0"}}, None),
            ({"tabId": "t.1"}, None),
            ({}, None),
        ],
    )
    def test_every_union_member_is_accounted_for(self, link: dict, expected) -> None:
        assert structure._parse_link(link) == expected

    def test_a_tab_scoped_document_round_trips_its_anchors(self) -> None:
        """The read half, on the shape a real tab-scoped fetch returns."""
        doc = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {
                    "content": "it",
                    "textStyle": {"link": {"heading": {"id": "h.cur", "tabId": "t.0"}}},
                }},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ]),
        )
        assert "[it](#current-state)" in render_nodes_to_markdown(structure.parse(doc))


# ─────────────────────────────────────────────────────────────────────────────
# Normalization must not silently pick a winner
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizationAmbiguity:
    def test_two_headings_differing_only_by_normal_form_report_rather_than_collide(
        self,
    ) -> None:
        """The regression the NFC comparison key introduced, now pinned.

        `slugify` keeps combining marks, so NFD and NFC forms of one heading
        produce two *distinct* slugs — `slugify_all` adds no `-1` — and a
        last-writer-wins NFC map pointed both anchors at whichever came second,
        overwriting a correct link and reporting `ok`. Before any normalization
        both were dead and both were reported, so that trade was strictly worse.
        Reachable through docspan's own round trip, and not exotic: Korean typed
        through a jamo IME, Vietnamese, anything macOS handed over as NFD.
        """
        nfd = unicodedata.normalize("NFD", "Café notes")
        nfc = unicodedata.normalize("NFC", "Café notes")
        assert nfd != nfc
        doc = _doc(
            _paragraph(nfd, 1, "HEADING_2", "h.first"),
            _paragraph(nfc, 2 + len(nfd), "HEADING_2", "h.second"),
            _paragraph("see one and two", 3 + len(nfd) + len(nfc), runs=[
                {"textRun": {"content": "see one and two\n", "textStyle": {}}}
            ]),
        )
        # Each anchor spelled in the form of the heading it means. This is the
        # case that must resolve, and the one normalizing-first destroyed: it
        # folded both hrefs together, made them indistinguishable, and reported
        # *both* dead — giving up two links that were unambiguous as written.
        nfd_slug = nfd.lower().replace(" ", "-")
        nfc_slug = nfc.lower().replace(" ", "-")
        target = markdown.parse(
            f"## {nfd}\n\n## {nfc}\n\nsee [one](#{nfd_slug}) and [two](#{nfc_slug})\n"
        )

        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert links == [{"headingId": "h.first"}, {"headingId": "h.second"}], links
        assert builder.unresolved_anchor_links(doc, target) == []

    def test_a_cross_form_anchor_is_reported_not_guessed(self) -> None:
        """An NFD heading and an NFC href no longer match — deliberately.

        Two earlier versions folded to NFC so this would resolve, and each one
        introduced a silent wrong link when two headings differed only by normal
        form. Folding could only ever help a *hand-written* cross-form anchor: one
        a pull wrote comes from the same source as the slug and matches byte for
        byte. Paying for that with silent arbitration between headings that look
        identical on screen is the wrong trade, so this is reported instead — and
        the available-anchors list shows the spelling that works.
        """
        nfd = unicodedata.normalize("NFD", "Café notes")
        doc = _doc(
            _paragraph(nfd, 1, "HEADING_2", "h.cafe"),
            _paragraph("see x", 2 + len(nfd), runs=[
                {"textRun": {"content": "see x\n", "textStyle": {}}}
            ]),
        )
        target = markdown.parse(f"## {nfd}\n\nsee [x](#café-notes)\n")

        assert builder.unresolved_anchor_links(doc, target) == ["#caf%C3%A9-notes"]
        # And the remedy is offered in the form that actually resolves.
        assert slugify(nfd) in available_anchor_slugs(target, structure.parse(doc))

    def test_an_anchor_spelled_exactly_resolves(self) -> None:
        """The case that must keep working: the slug as the heading produces it."""
        nfd = unicodedata.normalize("NFD", "Café notes")
        doc = _doc(
            _paragraph(nfd, 1, "HEADING_2", "h.cafe"),
            _paragraph("see x", 2 + len(nfd), runs=[
                {"textRun": {"content": "see x\n", "textStyle": {}}}
            ]),
        )
        target = markdown.parse(f"## {nfd}\n\nsee [x](#{slugify(nfd)})\n")

        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert links == [{"headingId": "h.cafe"}], links

    def test_an_unpaired_heading_with_a_unique_slug_keeps_its_link(self) -> None:
        """The narrowing on the pop, pinned.

        A document with one `Intro` and markdown with one `## Intro`: `#intro`
        can only mean `h.intro`, even when difflib leaves the heading out of
        every `equal` run because it moved past a body paragraph. Deleting the
        mapping there replaced a correct link with a dead-anchor warning for a
        heading plainly present — and `unaligned_span_targets` cannot even
        explain it, since it filters on `node.spans` and a heading has none.
        """
        doc = _doc(
            _paragraph("body first", 1),
            _paragraph("Intro", 12, "HEADING_2", "h.intro"),
            _paragraph("see it", 18, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
        )
        target = markdown.parse("## Intro\n\nbody first\n\nsee [it](#intro)\n")

        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert links == [{"headingId": "h.intro"}], links
        assert builder.unresolved_anchor_links(doc, target) == []


# ─────────────────────────────────────────────────────────────────────────────
# Every warning reaches the user, and the report agrees with resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestWarningsAreCollectedNotRaced:
    def _local(self, tmp_path, text: str) -> str:
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    @staticmethod
    def _two_tab_doc() -> dict:
        one = {
            "tabProperties": {"tabId": "t.0", "title": "Tab 1"},
            "documentTab": _doc(
                _paragraph("Current state", 1, "HEADING_2", "h.cur"),
                _paragraph("see it", 16, runs=[
                    {"textRun": {"content": "see it\n", "textStyle": {}}}
                ]),
            ),
        }
        two = {"tabProperties": {"tabId": "t.1", "title": "Tab 2"}, "documentTab": _doc()}
        return {"revisionId": "rev-1", "tabs": [one, two]}

    def test_a_dead_anchor_does_not_suppress_the_multi_tab_warning(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """One typo'd anchor used to hide the multi-tab warning completely.

        Both are facts about the same push and they are independent. Returning on
        the first meant docspan kept writing to a possibly-wrong tab — the thing
        the tab warning exists to prevent — while the user read a message about a
        link.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = self._two_tab_doc()
        local = self._local(tmp_path, "## Current state\n\n[see it](#typo)\n")

        result = backend.push(local, "doc-1")

        assert result.status == "warning"
        assert "#typo" in result.message
        assert "tab_id" in result.message, result.message

    def test_the_comment_backstop_is_not_consulted_when_nothing_was_written(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """Its contract is "re-check the count after a successful batch_update".

        A push that only reports a dead anchor writes nothing, and no longer
        short-circuits to "skipped" — so without the gate it would run the
        backstop, spend an extra list_comments call, and be able to blame docspan
        for a comment a human resolved mid-run.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
        )
        local = self._local(tmp_path, "## Current state\n\nsee [it](#typo)\n")

        result = backend.push(local, "doc-1")

        assert result.status == "warning"
        assert "#typo" in result.message
        assert fake_client.batch_update.call_count == 0
        assert fake_client.list_comments.call_count == 1  # the plan's own fetch only

    def test_the_available_list_never_offers_the_anchor_it_called_dead(self) -> None:
        """The tail must come from what resolution consults, not the markdown.

        Fed `heading_slugs(target_nodes)` it listed a slug whose mapping
        resolution had discarded, telling the author their spelling was both
        wrong and right. It also denied any anchors existed for a document-only
        heading that resolves perfectly well.
        """
        # Document-only heading: resolvable, and must be offered.
        document = structure.parse(_doc(_paragraph("Doc only", 1, "HEADING_2", "h.a")))
        target = markdown.parse("see [x](#typoo)\n")
        assert available_anchor_slugs(target, document) == ["doc-only"]

        # A markdown heading the document reports without an id cannot resolve,
        # so it must NOT be offered. Offering it is the self-contradiction this
        # function exists to remove: the same slug named as dead and as the fix.
        document2 = structure.parse(
            _doc(_paragraph("Current state", 1, "HEADING_2", heading_id=None))
        )
        target2 = markdown.parse("## Current state\n\nsee [x](#current-state)\n")
        assert available_anchor_slugs(target2, document2) == []

    def test_a_bare_hash_is_never_offered(self) -> None:
        """An empty heading slugs to "", which would render as a bare `#`.

        `is_anchor` rejects it, so offering it would be advice that cannot work.
        """
        document = structure.parse(_doc(_paragraph("", 1, "HEADING_2", "h.empty")))
        target = markdown.parse("see [x](#typo)\n")
        assert "" not in available_anchor_slugs(target, document)


# ─────────────────────────────────────────────────────────────────────────────
# A link markdown cannot express is reported, not dropped in silence
# ─────────────────────────────────────────────────────────────────────────────

class TestUnreadableLinksAreReported:
    """The same defect anchors were fixed for, on the union's other members.

    A bookmark link, a link to a tab, and any link inside a table cell all come
    back as no link at all, so `pull` writes the text without them and the
    author's file loses the reference. The Doc keeps it, so nothing is lost
    *yet* — the point of the warning is that they find out now rather than after
    a later push rewrites that paragraph and takes the link with it.
    """

    @pytest.mark.parametrize(
        "link, described",
        [
            ({"bookmarkId": "kix.b1"}, "bookmark link"),
            ({"bookmark": {"id": "kix.b1", "tabId": "t.0"}}, "bookmark link"),
            ({"tabId": "t.1"}, "link to a tab"),
        ],
    )
    def test_each_unreadable_member_is_named(self, link: dict, described: str) -> None:
        parser = DocsStructureParser()
        doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {"content": "it", "textStyle": {"link": link}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ])
        )
        nodes = parser.parse(doc)

        # Still unread — this reports the loss, it does not fix it.
        assert [s.link for n in nodes for s in (n.spans or []) if s.link] == []
        assert parser.unreadable_links == [described]

    def test_a_resolvable_heading_link_is_not_reported(self) -> None:
        parser = DocsStructureParser()
        doc = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see ", "textStyle": {}}},
                {"textRun": {
                    "content": "it",
                    "textStyle": {"link": {"heading": {"id": "h.cur", "tabId": "t.0"}}},
                }},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ]),
        )
        parser.parse(doc)
        assert parser.unreadable_links == []

    def test_a_link_inside_a_table_cell_is_reported(self) -> None:
        """Cells flatten to plain strings, so every cell link is dropped — `url`
        ones too. Wider than anchors and out of scope to fix; not to report."""
        parser = DocsStructureParser()
        doc = {
            "revisionId": "rev-1",
            "body": {"content": [{
                "startIndex": 1,
                "endIndex": 40,
                "table": {"tableRows": [{"tableCells": [{"content": [{"paragraph": {
                    "elements": [{"textRun": {
                        "content": "jump",
                        "textStyle": {"link": {"url": "https://example.com"}},
                    }}]
                }}]}]}]},
            }]},
        }
        parser.parse(doc)
        assert parser.unreadable_links == ["link inside a table cell"]

    def test_each_kind_is_reported_once(self) -> None:
        parser = DocsStructureParser()
        doc = _doc(
            _paragraph("a", 1, runs=[
                {"textRun": {"content": "a", "textStyle": {"link": {"bookmarkId": "kix.1"}}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ]),
            _paragraph("b", 3, runs=[
                {"textRun": {"content": "b", "textStyle": {"link": {"bookmarkId": "kix.2"}}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ]),
        )
        parser.parse(doc)
        assert parser.unreadable_links == ["bookmark link"]

    def test_the_list_does_not_leak_between_instances(self) -> None:
        """A shared mutable class attribute would put one doc's warning on another."""
        first = DocsStructureParser()
        first.parse(_doc(
            _paragraph("a", 1, runs=[
                {"textRun": {"content": "a", "textStyle": {"link": {"bookmarkId": "k"}}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ])
        ))
        assert first.unreadable_links == ["bookmark link"]

        second = DocsStructureParser()
        second.parse(_doc(_paragraph("clean", 1)))
        assert second.unreadable_links == []


# ─────────────────────────────────────────────────────────────────────────────
# Cross-tab anchors
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossTabAnchors:
    def _local(self, tmp_path, text: str) -> str:
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    @staticmethod
    def _doc_with_heading_in_another_tab() -> dict:
        tab0 = {
            "tabProperties": {"tabId": "t.0", "title": "One"},
            "documentTab": {"body": {"content": [
                _paragraph("Here", 1, "HEADING_2", "h.here"),
                _paragraph("see other", 7, runs=[
                    {"textRun": {"content": "see other\n", "textStyle": {}}}
                ]),
            ]}},
        }
        tab1 = {
            "tabProperties": {"tabId": "t.1", "title": "Two"},
            "documentTab": {"body": {"content": [
                _paragraph("Elsewhere", 1, "HEADING_2", "h.elsewhere"),
            ]}},
        }
        return {"revisionId": "rev-1", "tabs": [tab0, tab1]}

    def test_heading_ids_by_tab_spans_every_tab(self) -> None:
        assert heading_ids_by_tab(self._doc_with_heading_in_another_tab()) == {
            "h.here": "t.0",
            "h.elsewhere": "t.1",
        }
        # A single-tab document cannot pose the question.
        assert heading_ids_by_tab(_doc(_paragraph("X", 1, "HEADING_2", "h.x"))) == {}

    def test_an_anchor_into_another_tab_resolves_instead_of_warning_forever(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """`known_ids` used to be tab-scoped, so this warned on every push.

        The file is exactly what `pull` wrote (a cross-tab heading has no slug on
        this side, so the bare id is all there is) and the Doc's link is fine —
        yet every push reported a dead anchor, forever, and the link was lost the
        moment a text edit made pass 1 rewrite the paragraph.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = self._doc_with_heading_in_another_tab()
        local = self._local(tmp_path, "## Here\n\nsee [other](#h.elsewhere)\n")

        result = backend.push(local, "doc-1", tab_id="t.0")

        assert result.status == "ok", result.message
        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for call in fake_client.batch_update.call_args_list
            for request in call.args[1]
            if "updateTextStyle" in request
            and "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        # The tabs-aware member, not the flat one: Google resolves a bare
        # `headingId` against "the tab specified in the request, defaulting to
        # the first tab", so the flat form would point at the wrong tab.
        assert links == [{"heading": {"id": "h.elsewhere", "tabId": "t.1"}}], links

    def test_a_same_tab_heading_is_not_treated_as_foreign(self) -> None:
        """`align()` filters the map, so a same-tab id keeps the flat form.

        Asserting the filtering rather than calling `link_payload` with a
        same-tab id in `foreign_ids` — that input cannot occur, so a test built
        on it would pin a contract nothing upholds. The flat form is what the
        document already carries; rewriting it as the object form would be a
        diff for its own sake.
        """
        doc = self._doc_with_heading_in_another_tab()["tabs"][0]["documentTab"]
        doc["revisionId"] = "rev-1"
        target = markdown.parse("## Here\n\nsee [here](#here)\n")

        alignment = builder.align(doc, target, {"h.here": "t.0", "h.elsewhere": "t.1"})

        assert alignment.foreign_ids == {"h.elsewhere": "t.1"}
        assert link_payload(
            "#here", alignment.slug_to_id, alignment.known_ids, alignment.foreign_ids
        ) == {"headingId": "h.here"}


class TestCrossTabAmbiguityAndTheDryRun:
    @staticmethod
    def _tab(tab_id: str, *paragraphs: dict) -> dict:
        return {
            "tabProperties": {"tabId": tab_id, "title": tab_id},
            "documentTab": {"body": {"content": list(paragraphs)}},
        }

    def test_a_heading_id_in_two_tabs_is_refused_not_won(self) -> None:
        """Last-write-wins made the tab a reader lands in depend on tab order.

        Silent, `ok`, and reversible by reordering the tabs — the same trade
        `_match_keyed` refuses for slugs.
        """
        doc = {
            "revisionId": "r",
            "tabs": [
                self._tab("t.0", _paragraph("A", 1, "HEADING_2", "h.a")),
                self._tab("t.1", _paragraph("D", 1, "HEADING_2", "h.dupe")),
                self._tab("t.2", _paragraph("D", 1, "HEADING_2", "h.dupe")),
            ],
        }
        assert heading_ids_by_tab(doc) == {"h.a": "t.0"}

    def test_nested_child_tabs_are_included(self) -> None:
        parent = self._tab("t.0", _paragraph("Top", 1, "HEADING_2", "h.top"))
        parent["childTabs"] = [self._tab("t.0.0", _paragraph("N", 1, "HEADING_2", "h.n"))]
        assert heading_ids_by_tab({"revisionId": "r", "tabs": [parent]}) == {
            "h.top": "t.0",
            "h.n": "t.0.0",
        }

    def test_the_dry_run_does_not_report_a_cross_tab_anchor_push_resolves(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """`unresolved_anchors` promises it never over-reports.

        It was tab-scoped while push() had learned about sibling tabs, so the
        dry-run told the author a link was dead that the push wrote correctly.
        """
        doc = {
            "revisionId": "rev-1",
            "tabs": [
                self._tab(
                    "t.0",
                    _paragraph("Here", 1, "HEADING_2", "h.here"),
                    _paragraph("see other", 7, runs=[
                        {"textRun": {"content": "see other\n", "textStyle": {}}}
                    ]),
                ),
                self._tab("t.1", _paragraph("Elsewhere", 1, "HEADING_2", "h.elsewhere")),
            ],
        }
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = doc
        local = tmp_path / "doc.md"
        local.write_text("## Here\n\nsee [other](#h.elsewhere)\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1", tab_id="t.0")
        result = backend.push(str(local), "doc-1", tab_id="t.0")

        assert preview.unresolved_anchors == []
        assert result.status == "ok", result.message
