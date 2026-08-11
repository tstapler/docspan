"""Tests for cross-document markdown link resolution (see cross_doc_links.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from docspan.backends.google_docs.cross_doc_links import (
    AmbiguousMappingError,
    CrossDocLinkResolver,
    parse_cross_doc_href,
    resolve_local_mapping,
)


@dataclass
class _Mapping:
    """Stand-in for config.Mapping — only the fields cross_doc_links reads."""

    local: str
    backend: str = "google_docs"
    remote_id: str = "doc-1"
    tab_id: Optional[str] = None


class TestParseCrossDocHref:
    def test_relative_link_with_no_fragment_resolves_against_source_dir(self):
        assert parse_cross_doc_href("../other-doc/README.md", "docs/a/b.md") == (
            "docs/other-doc/README.md",
            None,
        )

    def test_relative_link_with_fragment_splits_it_off(self):
        assert parse_cross_doc_href(
            "../other-doc/README.md#some-heading", "docs/a/b.md"
        ) == ("docs/other-doc/README.md", "some-heading")

    def test_same_document_anchor_is_not_a_cross_doc_link(self):
        assert parse_cross_doc_href("#some-heading", "docs/a/b.md") is None

    def test_bare_hash_is_not_a_cross_doc_link(self):
        assert parse_cross_doc_href("#", "docs/a/b.md") is None

    @pytest.mark.parametrize(
        "href",
        [
            "https://example.com/doc.md",
            "http://example.com/doc.md",
            "mailto:someone@example.com",
            "//example.com/doc.md",
        ],
    )
    def test_absolute_or_scheme_qualified_urls_are_not_cross_doc_links(self, href):
        assert parse_cross_doc_href(href, "docs/a/b.md") is None

    def test_non_markdown_target_is_never_misclassified(self):
        """An image path must never be treated as a cross-doc link, even if it
        happens to collide with a mapping's local path."""
        assert parse_cross_doc_href("../assets/image.png", "docs/a/b.md") is None

    def test_dot_dot_traversal_is_normalized(self):
        assert parse_cross_doc_href("../../x/y.md", "docs/a/b/c.md") == ("docs/x/y.md", None)

    def test_none_href_is_not_a_cross_doc_link(self):
        assert parse_cross_doc_href(None, "docs/a/b.md") is None


class TestResolveLocalMapping:
    def test_no_match_returns_none(self):
        mappings = [_Mapping(local="docs/other.md")]
        assert resolve_local_mapping("docs/target.md", mappings) is None

    def test_exact_match_returns_the_mapping(self):
        target = _Mapping(local="docs/target.md")
        mappings = [_Mapping(local="docs/other.md"), target]
        assert resolve_local_mapping("docs/target.md", mappings) is target

    def test_case_sensitive_independent_of_host_filesystem(self):
        mappings = [_Mapping(local="docs/Target.md")]
        assert resolve_local_mapping("docs/target.md", mappings) is None

    def test_two_mappings_normalizing_to_same_path_raises_loudly(self):
        mappings = [
            _Mapping(local="docs/target.md", remote_id="doc-a"),
            _Mapping(local="docs/./target.md", remote_id="doc-b"),
        ]
        with pytest.raises(AmbiguousMappingError):
            resolve_local_mapping("docs/target.md", mappings)


class TestCrossDocLinkResolverBasics:
    def test_link_to_unmapped_file_is_untouched(self):
        resolver = CrossDocLinkResolver(mappings=[_Mapping(local="docs/other.md")])
        result = resolver.resolve("../missing.md", "docs/a.md", current_doc_id="doc-1")
        assert result.untouched is True
        assert result.payload is None
        assert result.unresolved is False

    def test_mapped_file_no_fragment_resolves_to_edit_url(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        resolver = CrossDocLinkResolver(mappings=[target])
        result = resolver.resolve(
            "target.md", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.untouched is False
        assert result.unresolved is False
        assert result.payload == {
            "url": "https://docs.google.com/document/d/target-doc-id/edit"
        }

    def test_mapped_file_with_fragment_fetches_and_resolves_heading(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        calls = []

        def fetch_headings(mapping):
            calls.append(mapping)
            return {"some-heading": "h.abc123"}, {"h.abc123"}

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        result = resolver.resolve(
            "target.md#some-heading", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.payload == {
            "url": "https://docs.google.com/document/d/target-doc-id/edit#heading=h.abc123"
        }
        assert len(calls) == 1

    def test_mapped_file_with_tab_id_no_fragment_includes_tab_in_url(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id", tab_id="t.abc")
        resolver = CrossDocLinkResolver(mappings=[target])
        result = resolver.resolve(
            "target.md", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.payload == {
            "url": "https://docs.google.com/document/d/target-doc-id/edit?tab=t.abc"
        }

    def test_mapped_file_with_tab_id_and_fragment_includes_tab_and_heading(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id", tab_id="t.abc")

        def fetch_headings(mapping):
            return {"some-heading": "h.abc123"}, {"h.abc123"}

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        result = resolver.resolve(
            "target.md#some-heading", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.payload == {
            "url": (
                "https://docs.google.com/document/d/target-doc-id/edit"
                "?tab=t.abc#heading=h.abc123"
            )
        }

    def test_fragment_not_found_in_target_is_unresolved_not_dead_link(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")

        def fetch_headings(mapping):
            return {}, set()

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        result = resolver.resolve(
            "target.md#no-such-heading", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.unresolved is True
        assert result.payload is None
        assert "no-such-heading" in result.reason

    def test_target_fetch_failure_is_unresolved_and_does_not_raise(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")

        def fetch_headings(mapping):
            raise RuntimeError("boom: 403")

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        result = resolver.resolve(
            "target.md#heading", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.unresolved is True
        assert result.payload is None
        assert "boom" in result.reason

    def test_multiple_links_to_same_target_fetch_only_once(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        calls = []

        def fetch_headings(mapping):
            calls.append(mapping)
            return {"a": "h.a", "b": "h.b"}, {"h.a", "h.b"}

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        resolver.resolve("target.md#a", "docs/x.md", current_doc_id="current-doc-id")
        resolver.resolve("target.md#b", "docs/y.md", current_doc_id="current-doc-id")
        resolver.resolve("target.md#a", "docs/z.md", current_doc_id="current-doc-id")
        assert len(calls) == 1

    def test_failed_fetch_is_also_cached_not_retried(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        calls = []

        def fetch_headings(mapping):
            calls.append(mapping)
            raise RuntimeError("403")

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        resolver.resolve("target.md#a", "docs/x.md", current_doc_id="current-doc-id")
        resolver.resolve("target.md#b", "docs/y.md", current_doc_id="current-doc-id")
        assert len(calls) == 1

    def test_different_backend_target_is_unresolved_not_attempted(self):
        target = _Mapping(local="docs/target.md", backend="confluence", remote_id="page-1")
        resolver = CrossDocLinkResolver(mappings=[target])
        result = resolver.resolve(
            "target.md#a", "docs/a.md", current_doc_id="current-doc-id"
        )
        assert result.unresolved is True
        assert result.payload is None
        assert "confluence" in result.reason

    def test_ambiguous_mapping_propagates(self):
        mappings = [
            _Mapping(local="docs/target.md", remote_id="doc-a"),
            _Mapping(local="docs/./target.md", remote_id="doc-b"),
        ]
        resolver = CrossDocLinkResolver(mappings=mappings)
        with pytest.raises(AmbiguousMappingError):
            resolver.resolve("target.md", "docs/a.md", current_doc_id="current-doc-id")


class TestSelfReference:
    def test_self_reference_with_fragment_routes_to_same_doc_not_a_fetch(self):
        target = _Mapping(local="docs/self.md", remote_id="doc-1", tab_id=None)
        calls = []

        def fetch_headings(mapping):
            calls.append(mapping)
            return {}, set()

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=fetch_headings)
        result = resolver.resolve(
            "self.md#a-heading", "docs/self.md", current_doc_id="doc-1", current_tab_id=None
        )
        assert result.same_doc_fragment == "a-heading"
        assert result.untouched is False
        assert result.unresolved is False
        assert calls == []

    def test_self_reference_with_no_fragment_resolves_to_own_edit_url(self):
        target = _Mapping(local="docs/self.md", remote_id="doc-1", tab_id=None)
        resolver = CrossDocLinkResolver(mappings=[target])
        result = resolver.resolve(
            "self.md", "docs/self.md", current_doc_id="doc-1", current_tab_id=None
        )
        assert result.same_doc_fragment is None
        assert result.payload == {"url": "https://docs.google.com/document/d/doc-1/edit"}

    def test_same_remote_id_but_different_tab_is_not_a_self_reference(self):
        target = _Mapping(local="docs/other-tab.md", remote_id="doc-1", tab_id="t.other")
        resolver = CrossDocLinkResolver(mappings=[target])
        result = resolver.resolve(
            "other-tab.md", "docs/self.md", current_doc_id="doc-1", current_tab_id="t.mine"
        )
        assert result.same_doc_fragment is None
        assert result.payload == {
            "url": "https://docs.google.com/document/d/doc-1/edit?tab=t.other"
        }


class TestBindFetchHeadings:
    def test_binding_supplies_the_callback_when_none_given_at_construction(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        resolver = CrossDocLinkResolver(mappings=[target])

        def fetch_headings(mapping):
            return {"a": "h.a"}, {"h.a"}

        resolver.bind_fetch_headings(fetch_headings)
        result = resolver.resolve(
            "target.md#a", "docs/x.md", current_doc_id="current-doc-id"
        )
        assert result.payload == {
            "url": "https://docs.google.com/document/d/target-doc-id/edit#heading=h.a"
        }

    def test_binding_a_second_time_is_a_no_op(self):
        target = _Mapping(local="docs/target.md", remote_id="target-doc-id")
        first_calls = []
        second_calls = []

        def first(mapping):
            first_calls.append(mapping)
            return {"a": "h.a"}, {"h.a"}

        def second(mapping):
            second_calls.append(mapping)
            return {"a": "h.other"}, {"h.other"}

        resolver = CrossDocLinkResolver(mappings=[target], fetch_headings=first)
        resolver.bind_fetch_headings(second)
        result = resolver.resolve(
            "target.md#a", "docs/x.md", current_doc_id="current-doc-id"
        )
        assert first_calls == [target]
        assert second_calls == []
        assert result.payload["url"].endswith("#heading=h.a")
