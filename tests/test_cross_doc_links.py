"""Tests for cross-document markdown link resolution (cross_doc_links.py).

Covers the backlog item's acceptance criteria that live in this module:
0 (resolve to target URL), 1 (resolve with fragment against target's live
headings), 3 (unmapped link left untouched), 5 (target fetch failure
degrades to reported, not raised), 6/10 (per-target fetch caching), 7
(ambiguous mapping loudly fails), 8 (path traversal / case-sensitivity /
non-cross-doc hrefs).
"""
from __future__ import annotations

import pytest

from docspan.backends.google_docs.cross_doc_links import (
    AmbiguousMappingError,
    CrossDocHref,
    CrossDocLinkResolver,
    link_payload,
    normalize_local_path,
    parse_cross_doc_href,
    resolve_local_mapping,
)
from docspan.config import Mapping


def make_mapping(local, remote_id="doc123", backend="google_docs", tab_id=None):
    return Mapping(local=local, backend=backend, remote_id=remote_id, tab_id=tab_id)


class TestParseCrossDocHref:
    def test_relative_path_no_fragment(self):
        assert parse_cross_doc_href("../other/README.md") == CrossDocHref(
            path="../other/README.md", fragment=None
        )

    def test_relative_path_with_fragment(self):
        assert parse_cross_doc_href("../other/README.md#some-heading") == CrossDocHref(
            path="../other/README.md", fragment="some-heading"
        )

    def test_same_document_anchor_is_not_a_candidate(self):
        assert parse_cross_doc_href("#some-heading") is None

    def test_absolute_http_url_is_not_a_candidate(self):
        assert parse_cross_doc_href("https://example.com/other.md") is None

    def test_mailto_is_not_a_candidate(self):
        assert parse_cross_doc_href("mailto:someone@example.com") is None

    def test_empty_href_is_not_a_candidate(self):
        assert parse_cross_doc_href("") is None
        assert parse_cross_doc_href(None) is None

    def test_image_link_is_still_a_path_candidate(self):
        # Criterion 8: non-.md targets are still parsed as paths — it's
        # resolve_local_mapping (no matching mapping) that rules them out,
        # not the href parser itself.
        assert parse_cross_doc_href("../assets/diagram.png") == CrossDocHref(
            path="../assets/diagram.png", fragment=None
        )

    def test_absolute_path_is_not_a_candidate(self):
        # An href starting with "/" isn't "relative to the pushing file" —
        # resolve_local_mapping's posixpath.join would otherwise silently
        # discard source_dir for such a path.
        assert parse_cross_doc_href("/other/README.md") is None
        assert parse_cross_doc_href("/other/README.md#some-heading") is None


class TestNormalizeLocalPath:
    def test_dot_dot_traversal(self):
        assert normalize_local_path("a/../b.md") == "b.md"

    def test_backslash_normalized_to_forward_slash(self):
        assert normalize_local_path("a\\b.md") == "a/b.md"

    def test_case_is_preserved(self):
        assert normalize_local_path("Docs/README.md") == "Docs/README.md"
        assert normalize_local_path("Docs/README.md") != normalize_local_path(
            "docs/readme.md"
        )


class TestResolveLocalMapping:
    def test_resolves_relative_to_source_dir(self):
        mappings = [make_mapping("docs/other-doc/README.md")]
        m = resolve_local_mapping(
            "docs/source-doc/README.md", "../other-doc/README.md", mappings
        )
        assert m is mappings[0]

    def test_unmapped_path_returns_none(self):
        mappings = [make_mapping("docs/other-doc/README.md")]
        assert (
            resolve_local_mapping("docs/source-doc/README.md", "../nope.md", mappings)
            is None
        )

    def test_case_sensitive_mismatch_is_unmapped(self):
        mappings = [make_mapping("docs/Other-Doc/README.md")]
        assert (
            resolve_local_mapping(
                "docs/source-doc/README.md", "../other-doc/README.md", mappings
            )
            is None
        )

    def test_ambiguous_mapping_raises(self):
        mappings = [
            make_mapping("docs/other-doc/README.md", remote_id="a"),
            make_mapping("docs/other-doc/../other-doc/README.md", remote_id="b"),
        ]
        with pytest.raises(AmbiguousMappingError):
            resolve_local_mapping(
                "docs/source-doc/README.md", "../other-doc/README.md", mappings
            )

    def test_image_link_never_matches_a_local_md_mapping(self):
        mappings = [make_mapping("docs/other-doc/README.md")]
        assert (
            resolve_local_mapping(
                "docs/source-doc/README.md", "../assets/diagram.png", mappings
            )
            is None
        )


def fetch_headings_factory(by_doc_id, calls=None):
    def fetch(doc_id, tab_id):
        calls is not None and calls.append((doc_id, tab_id))
        if doc_id not in by_doc_id:
            raise RuntimeError("fetch failed")
        return by_doc_id[doc_id]

    return fetch


class TestCrossDocLinkResolverResolve:
    def test_not_cross_doc_falls_through(self):
        resolver = CrossDocLinkResolver([], lambda d, t: [])
        res = resolver.resolve("docs/source.md", "#local-anchor")
        assert res.kind == "not_cross_doc"

    def test_absolute_path_href_falls_through_not_cross_doc(self):
        # Guards against posixpath.join("docs", "/other/target.md") silently
        # discarding "docs" and matching a mapping the href never actually
        # named relative to the pushing file.
        mappings = [make_mapping("other/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "/other/target.md")
        assert res.kind == "not_cross_doc"

    def test_unmapped_link_is_left_untouched(self):
        resolver = CrossDocLinkResolver([], lambda d, t: [])
        res = resolver.resolve("docs/source.md", "../nope.md")
        assert res.kind == "unmapped"

    def test_resolves_to_target_url_with_no_fragment(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md")
        assert res.kind == "resolved"
        assert res.url == "https://docs.google.com/document/d/TARGETID/edit"

    def test_resolves_with_fragment_against_target_live_headings(self):
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        heading_node = DocsParagraphNode(
            text="Some Heading", style="HEADING_2", heading_id="h.abc123"
        )
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(
            mappings, lambda d, t: [heading_node]
        )
        res = resolver.resolve("docs/source.md", "target.md#some-heading")
        assert res.kind == "resolved"
        assert res.url == (
            "https://docs.google.com/document/d/TARGETID/edit#heading=h.abc123"
        )

    def test_fragment_matching_no_heading_is_unresolved_anchor(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md#missing")
        assert res.kind == "unresolved_anchor"
        assert res.detail is not None

    def test_target_fetch_failure_is_reported_not_raised(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]

        def fetch(doc_id, tab_id):
            raise RuntimeError("403 forbidden")

        resolver = CrossDocLinkResolver(mappings, fetch)
        res = resolver.resolve("docs/source.md", "target.md#some-heading")
        assert res.kind == "fetch_failed"
        assert res.detail is not None

    def test_target_fetch_failure_detail_includes_original_error(self):
        # A real bug in the fetch/parse path should be distinguishable from a
        # transient network failure, not swallowed into a generic message.
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]

        def fetch(doc_id, tab_id):
            raise RuntimeError("403 forbidden")

        resolver = CrossDocLinkResolver(mappings, fetch)
        res = resolver.resolve("docs/source.md", "target.md#some-heading")
        assert "403 forbidden" in res.detail

    def test_ambiguous_mapping_is_reported(self):
        mappings = [
            make_mapping("docs/target.md", remote_id="a"),
            make_mapping("docs/target.md", remote_id="b"),
        ]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md")
        assert res.kind == "ambiguous"

    def test_unsupported_backend_is_reported(self):
        mappings = [make_mapping("docs/target.md", backend="confluence", remote_id="X")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md")
        assert res.kind == "unsupported"

    def test_unsupported_when_target_has_no_remote_id_yet(self):
        mappings = [make_mapping("docs/target.md", remote_id=None)]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md")
        assert res.kind == "unsupported"


class TestCrossDocLinkResolverCaching:
    def test_multiple_links_to_same_target_fetch_once(self):
        calls = []
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(
            mappings, fetch_headings_factory({"TARGETID": []}, calls)
        )
        resolver.resolve("docs/source.md", "target.md#one")
        resolver.resolve("docs/source.md", "target.md#two")
        resolver.resolve("docs/source.md", "target.md#three")
        assert calls == [("TARGETID", None)]

    def test_distinct_targets_each_fetched_once(self):
        calls = []
        mappings = [
            make_mapping("docs/a.md", remote_id="A"),
            make_mapping("docs/b.md", remote_id="B"),
        ]
        resolver = CrossDocLinkResolver(
            mappings, fetch_headings_factory({"A": [], "B": []}, calls)
        )
        resolver.resolve("docs/source.md", "a.md#x")
        resolver.resolve("docs/source.md", "b.md#y")
        resolver.resolve("docs/source.md", "a.md#z")
        assert sorted(calls) == [("A", None), ("B", None)]

    def test_failed_fetch_result_is_also_cached(self):
        calls = []

        def fetch(doc_id, tab_id):
            calls.append((doc_id, tab_id))
            raise RuntimeError("boom")

        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, fetch)
        resolver.resolve("docs/source.md", "target.md#one")
        resolver.resolve("docs/source.md", "target.md#two")
        assert calls == [("TARGETID", None)]


class TestLinkPayload:
    def test_no_resolver_falls_through_to_same_doc_behavior(self):
        payload, detail = link_payload("#some-heading", "docs/source.md", None)
        assert detail is None
        # Same-document anchor with no slug map resolves to nothing (heading
        # unknown) — this asserts the fallthrough happened, not a specific url.
        assert payload is None or "url" in payload

    def test_cross_doc_href_without_source_path_falls_through(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        # source_local_path is None -> resolver is bypassed entirely, so this
        # relative path is treated as an ordinary (opaque) URL by the
        # same-document fallback rather than resolved.
        payload, detail = link_payload("target.md", None, resolver)
        assert detail is None

    def test_unmapped_cross_doc_link_is_written_untouched(self):
        resolver = CrossDocLinkResolver([], lambda d, t: [])
        payload, detail = link_payload("../nope.md", "docs/source.md", resolver)
        assert detail is None
        assert payload == {"url": "../nope.md"}

    def test_resolved_cross_doc_link_writes_target_url(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        payload, detail = link_payload("target.md", "docs/source.md", resolver)
        assert detail is None
        assert payload == {"url": "https://docs.google.com/document/d/TARGETID/edit"}

    def test_unresolvable_cross_doc_link_writes_nothing_and_reports(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        payload, detail = link_payload(
            "target.md#missing-heading", "docs/source.md", resolver
        )
        assert payload is None
        assert detail is not None
