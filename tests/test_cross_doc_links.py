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

    def test_resolves_to_target_url_with_tab_id_and_no_fragment(self):
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID", tab_id="t.abc")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [])
        res = resolver.resolve("docs/source.md", "target.md")
        assert res.kind == "resolved"
        assert res.url == "https://docs.google.com/document/d/TARGETID/edit?tab=t.abc"

    def test_resolves_with_fragment_and_tab_id_includes_both(self):
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        heading_node = DocsParagraphNode(
            text="Some Heading", style="HEADING_2", heading_id="h.abc123"
        )
        mappings = [make_mapping("docs/target.md", remote_id="TARGETID", tab_id="t.abc")]
        resolver = CrossDocLinkResolver(mappings, lambda d, t: [heading_node])
        res = resolver.resolve("docs/source.md", "target.md#some-heading")
        assert res.kind == "resolved"
        assert res.url == (
            "https://docs.google.com/document/d/TARGETID/edit?tab=t.abc#heading=h.abc123"
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


# ─────────────────────────────────────────────────────────────────────────────
# Pull-side reversal: Doc URL -> repo-relative path.
# ─────────────────────────────────────────────────────────────────────────────
import logging  # noqa: E402

from docspan.backends.google_docs.cross_doc_links import (  # noqa: E402
    DocUrl,
    ReverseDocLinkResolver,
    parse_doc_url,
    reverse_cross_doc_links,
)

BASE = "https://docs.google.com/document/d/TARGETID/edit"


def _heading_nodes():
    from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

    return [DocsParagraphNode(text="Some Heading", style="HEADING_2", heading_id="h.abc123")]


class TestParseDocUrl:
    def test_all_forms(self):
        assert parse_doc_url(BASE) == DocUrl("TARGETID", None, None)
        assert parse_doc_url(BASE + "?tab=t.x") == DocUrl("TARGETID", "t.x", None)
        assert parse_doc_url(BASE + "#heading=h.y") == DocUrl("TARGETID", None, "h.y")
        assert parse_doc_url(BASE + "?tab=t.x#heading=h.y") == DocUrl("TARGETID", "t.x", "h.y")

    def test_non_doc_urls_are_none(self):
        assert parse_doc_url("https://example.com/document/d/X/edit") is None
        assert parse_doc_url("https://docs.google.com/spreadsheets/d/X/edit") is None
        assert parse_doc_url("other.md") is None


class TestReverseDocLinkResolver:
    def _resolver(self, mappings, source="docs/source.md", fetch=None):
        return ReverseDocLinkResolver(
            mappings, fetch or (lambda d, t: _heading_nodes()), source
        )

    def test_url_to_path_no_fragment(self):  # AC-0
        r = self._resolver([make_mapping("docs/target.md", remote_id="TARGETID")])
        assert r.reverse(BASE) == ("target.md", None)

    def test_url_with_tab_resolves_correctly(self):  # AC-1
        mappings = [
            make_mapping("docs/a.md", remote_id="TARGETID", tab_id="t.a"),
            make_mapping("docs/b.md", remote_id="TARGETID", tab_id="t.b"),
        ]
        r = self._resolver(mappings)
        assert r.reverse(BASE + "?tab=t.b") == ("b.md", None)
        assert r.reverse(BASE) == (BASE, None)  # no tab-less mapping

    def test_url_with_heading_resolves_to_slug(self):  # AC-2
        r = self._resolver([make_mapping("docs/target.md", remote_id="TARGETID")])
        assert r.reverse(BASE + "#heading=h.abc123") == ("target.md#some-heading", None)

    def test_unmapped_doc_id_left_verbatim(self):  # AC-3
        r = self._resolver([make_mapping("docs/target.md", remote_id="OTHER")])
        assert r.reverse(BASE + "?tab=t.x#heading=h.y") == (BASE + "?tab=t.x#heading=h.y", None)

    def test_non_doc_url_left_verbatim(self):  # AC-4
        r = self._resolver([make_mapping("docs/target.md", remote_id="TARGETID")])
        for url in ("https://example.com/x", "mailto:a@b.c", "other.md#x"):
            assert r.reverse(url) == (url, None)

    def test_relative_path_computed_correctly(self):  # AC-5
        mappings = [make_mapping("docs/api/target.md", remote_id="TARGETID")]
        assert self._resolver(mappings, source="docs/guide/x.md").reverse(BASE)[0] == "../api/target.md"
        abs_mappings = [make_mapping("/proj/docs/api/target.md", remote_id="TARGETID")]
        assert self._resolver(abs_mappings, source="/proj/docs/guide/x.md").reverse(BASE)[0] == "../api/target.md"
        assert self._resolver(mappings, source="x.md").reverse(BASE)[0] == "docs/api/target.md"

    def test_ambiguous_mapping_warns(self):  # AC-6
        mappings = [
            make_mapping("a.md", remote_id="TARGETID"),
            make_mapping("b.md", remote_id="TARGETID"),
        ]
        out, warning = self._resolver(mappings).reverse(BASE)
        assert out == BASE
        assert warning and "a.md" in warning and "b.md" in warning

    def test_heading_fetch_called_once_per_target(self):  # AC-11
        calls = []
        r = self._resolver(
            [make_mapping("docs/target.md", remote_id="TARGETID")],
            fetch=fetch_headings_factory({"TARGETID": _heading_nodes()}, calls),
        )
        for _ in range(3):
            r.reverse(BASE + "#heading=h.abc123")
        assert calls == [("TARGETID", None)]

    def test_shared_cache_across_resolvers(self):
        calls, cache = [], {}
        fetch = fetch_headings_factory({"TARGETID": _heading_nodes()}, calls)
        m = [make_mapping("docs/target.md", remote_id="TARGETID")]
        for src in ("docs/a.md", "docs/b.md"):
            ReverseDocLinkResolver(m, fetch, src, cache=cache).reverse(BASE + "#heading=h.abc123")
        assert len(calls) == 1

    def test_unknown_heading_id_falls_back_to_path(self, caplog):  # AC-12
        r = self._resolver([make_mapping("docs/target.md", remote_id="TARGETID")])
        with caplog.at_level(logging.WARNING):
            assert r.reverse(BASE + "#heading=h.nope") == ("target.md", None)
        assert any("h.nope" in rec.getMessage() for rec in caplog.records)

    def test_heading_fetch_failure_falls_back_to_path(self):
        r = self._resolver(
            [make_mapping("docs/target.md", remote_id="TARGETID")],
            fetch=fetch_headings_factory({}),
        )
        assert r.reverse(BASE + "#heading=h.abc123") == ("target.md", None)


class TestReverseCrossDocLinks:
    def test_only_link_destinations_rewritten(self):
        r = ReverseDocLinkResolver(
            [make_mapping("docs/target.md", remote_id="TARGETID")], lambda d, t: [], "docs/s.md"
        )
        md = f"[a]({BASE}) and bare {BASE} and [b](https://example.com)"
        out, warnings = reverse_cross_doc_links(md, r)
        assert out == f"[a](target.md) and bare {BASE} and [b](https://example.com)"
        assert warnings == []

    def test_collects_warnings(self):
        r = ReverseDocLinkResolver(
            [make_mapping("a.md", remote_id="TARGETID"), make_mapping("b.md", remote_id="TARGETID")],
            lambda d, t: [],
            "s.md",
        )
        out, warnings = reverse_cross_doc_links(f"[a]({BASE})", r)
        assert out == f"[a]({BASE})" and len(warnings) == 1
