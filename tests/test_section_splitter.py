"""Tests for `section_splitter.split_nodes` (gdocs-sectioned-sync Epic 2)."""
from __future__ import annotations

import pytest

from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
from docspan.backends.google_docs.manifest import PREAMBLE_HEADING_ID, SectionManifestEntry
from docspan.backends.google_docs.section_splitter import (
    PREAMBLE_SLUG,
    SectionSplitError,
    split_nodes,
)


def _p(style: str, text: str, heading_id=None) -> DocsParagraphNode:
    return DocsParagraphNode(style=style, text=text, heading_id=heading_id)


def _doc_with_five_headings_and_preamble() -> list:
    nodes = [
        _p("NORMAL_TEXT", "Preamble body text."),
    ]
    for i in range(1, 6):
        nodes.append(_p("HEADING_1", f"Section {i}", heading_id=f"h.section{i}"))
        nodes.append(_p("NORMAL_TEXT", f"Body of section {i}."))
    return nodes


class TestSplitNodesHappyPath:
    def test_split_nodes_should_produce_one_section_per_split_level_heading(self) -> None:
        nodes = _doc_with_five_headings_and_preamble()

        sections = split_nodes(nodes, "HEADING_1")

        assert len(sections) == 6
        preamble, *rest = sections
        assert preamble.heading_id == PREAMBLE_HEADING_ID
        assert preamble.slug == PREAMBLE_SLUG
        assert preamble.nodes == [nodes[0]]
        for i, section in enumerate(rest, start=1):
            assert section.heading_id == f"h.section{i}"
            assert section.title == f"Section {i}"
            assert section.slug == f"section-{i}"
            # Heading node itself plus its body paragraph.
            assert section.nodes[0].style == "HEADING_1"
            assert section.nodes[0].text == f"Section {i}"

    def test_split_nodes_should_keep_nested_deeper_headings_inside_enclosing_section(self) -> None:
        nodes = [
            _p("HEADING_1", "Top", heading_id="h.top"),
            _p("HEADING_2", "Sub", heading_id="h.sub"),
            _p("NORMAL_TEXT", "body"),
        ]

        sections = split_nodes(nodes, "HEADING_1")

        assert len(sections) == 2  # preamble (empty) + one HEADING_1 section
        assert [n.style for n in sections[1].nodes] == ["HEADING_1", "HEADING_2", "NORMAL_TEXT"]


class TestSplitNodesPreamble:
    def test_split_nodes_should_produce_preamble_only_section_when_no_headings_present(self) -> None:
        nodes = [_p("NORMAL_TEXT", "Just body text, no headings at all.")]

        sections = split_nodes(nodes, "HEADING_1")

        assert len(sections) == 1
        assert sections[0].heading_id == PREAMBLE_HEADING_ID
        assert sections[0].slug == PREAMBLE_SLUG
        assert sections[0].nodes == nodes

    def test_split_nodes_should_produce_empty_preamble_when_doc_starts_with_a_heading(self) -> None:
        nodes = [_p("HEADING_1", "First", heading_id="h.first")]

        sections = split_nodes(nodes, "HEADING_1")

        assert len(sections) == 2
        assert sections[0].heading_id == PREAMBLE_HEADING_ID
        assert sections[0].nodes == []


class TestSplitNodesEmptyDocument:
    def test_split_nodes_should_handle_empty_node_list(self) -> None:
        sections = split_nodes([], "HEADING_1")

        assert len(sections) == 1
        assert sections[0].heading_id == PREAMBLE_HEADING_ID
        assert sections[0].slug == PREAMBLE_SLUG
        assert sections[0].nodes == []


class TestSplitNodesErrorPath:
    def test_split_nodes_should_raise_when_split_level_not_present_in_doc(self) -> None:
        nodes = [
            _p("HEADING_1", "Only top level", heading_id="h.1"),
            _p("NORMAL_TEXT", "body"),
        ]

        with pytest.raises(SectionSplitError, match="HEADING_1"):
            split_nodes(nodes, "HEADING_2")

    def test_split_nodes_should_not_raise_for_an_empty_document(self) -> None:
        # No headings at all is not an error — nothing to name as "the
        # deepest heading found" — it is just a doc with only a preamble.
        sections = split_nodes([], "HEADING_1")
        assert len(sections) == 1


class TestSplitNodesDuplicateTitles:
    def test_split_nodes_should_disambiguate_duplicate_heading_titles(self) -> None:
        nodes = [
            _p("HEADING_1", "Intro", heading_id="h.a"),
            _p("NORMAL_TEXT", "first"),
            _p("HEADING_1", "Intro", heading_id="h.b"),
            _p("NORMAL_TEXT", "second"),
        ]

        sections = split_nodes(nodes, "HEADING_1")

        slugs = [s.slug for s in sections[1:]]
        assert slugs == ["intro", "intro-1"]
        assert len(set(slugs)) == len(slugs)


class TestSplitNodesRenameMatching:
    def test_split_nodes_should_reuse_existing_slug_on_heading_id_match_with_unchanged_text(
        self,
    ) -> None:
        # Old slugs deliberately differ from what fresh slugify_all(title)
        # would produce, so a passing assertion actually proves reuse
        # happened rather than coincidentally matching a freshly derived
        # slug (the bug this test used to hide: existing_entries was a
        # no-op, so this only ever exercised fresh derivation).
        existing = [
            SectionManifestEntry(
                heading_id="h.1", slug="one-legacy", filename="01-one-legacy.md", title="One"
            ),
            SectionManifestEntry(
                heading_id="h.2", slug="two-legacy", filename="02-two-legacy.md", title="Two"
            ),
            SectionManifestEntry(
                heading_id="h.3", slug="three", filename="03-three.md", title="Three"
            ),
        ]
        nodes = [
            _p("HEADING_1", "One", heading_id="h.1"),
            _p("HEADING_1", "Two", heading_id="h.2"),
            # Section 3's heading text changed; heading_id is unchanged.
            _p("HEADING_1", "Three Renamed", heading_id="h.3"),
        ]

        sections = split_nodes(nodes, "HEADING_1", existing_entries=existing)

        by_id = {s.heading_id: s for s in sections if s.heading_id != PREAMBLE_HEADING_ID}
        # Unchanged heading text -> reuse the prior slug verbatim, not a
        # freshly derived "one"/"two".
        assert by_id["h.1"].slug == "one-legacy"
        assert by_id["h.2"].slug == "two-legacy"
        # heading_id still matches the existing manifest entry, but the
        # heading text changed, so the caller can detect this as a
        # content-driven rename (old filename 03-three.md -> new slug
        # three-renamed) rather than a delete+insert.
        assert by_id["h.3"].title == "Three Renamed"
        assert by_id["h.3"].slug == "three-renamed"
        existing_ids = {e.heading_id for e in existing}
        assert by_id["h.3"].heading_id in existing_ids

    def test_split_nodes_should_treat_missing_heading_id_as_new_section(self) -> None:
        existing = [
            SectionManifestEntry(heading_id="h.1", slug="one", filename="01-one.md"),
        ]
        nodes = [
            _p("HEADING_1", "One", heading_id="h.1"),
            # A heading with no Docs-assigned id at all — never round-tripped
            # through the API yet, so it cannot be genuinely matched to any
            # existing entry by position.
            _p("HEADING_1", "Brand New", heading_id=None),
        ]

        sections = split_nodes(nodes, "HEADING_1", existing_entries=existing)

        new_section = sections[-1]
        assert new_section.title == "Brand New"
        existing_ids = {e.heading_id for e in existing}
        assert new_section.heading_id not in existing_ids
