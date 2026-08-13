"""Unit tests for tabs.py — resolve_document_tab()/list_tabs() (no network)."""
from __future__ import annotations

import json

import pytest

from docspan.backends.google_docs.tabs import (
    TabNotFoundError,
    heading_ids_by_tab,
    list_tabs,
    resolve_document_tab,
)


def _tab(tab_id: str, title: str, body_text: str, child_tabs: list | None = None) -> dict:
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": body_text}}]}}]},
            "lists": {},
        },
        "childTabs": child_tabs or [],
    }


LEGACY_DOC = {"revisionId": "r1", "body": {"content": []}, "lists": {}}

MULTI_TAB_DOC = {
    "revisionId": "r1",
    "body": {"content": []},  # legacy field, mirrors tab[0] per the real API
    "tabs": [
        _tab("t.first", "Overview", "first tab content"),
        _tab("t.second", "Details", "second tab content"),
    ],
}

SINGLE_TAB_DOC = {
    "revisionId": "r1",
    "body": {"content": []},
    "tabs": [_tab("t.only", "Only", "only tab content")],
}


# ─────────────────────────────────────────────────────────────────────────────
# resolve_document_tab
# ─────────────────────────────────────────────────────────────────────────────

def test_legacy_doc_with_no_tabs_is_returned_unchanged() -> None:
    resolved, resolved_tab_id, warning = resolve_document_tab(LEGACY_DOC, None)
    assert resolved is LEGACY_DOC
    assert resolved_tab_id is None
    assert warning is None


def test_single_tab_doc_resolves_without_warning_even_with_no_tab_id() -> None:
    resolved, resolved_tab_id, warning = resolve_document_tab(SINGLE_TAB_DOC, None)
    assert resolved_tab_id == "t.only"
    assert warning is None
    assert resolved["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] == "only tab content"
    assert resolved["tabs"] == []


def test_multi_tab_doc_with_no_tab_id_defaults_to_first_tab_and_warns() -> None:
    resolved, resolved_tab_id, warning = resolve_document_tab(MULTI_TAB_DOC, None)
    assert resolved_tab_id == "t.first"
    assert warning is not None
    assert "Overview" in warning and "Details" in warning
    assert resolved["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] == "first tab content"


def test_multi_tab_doc_with_explicit_tab_id_selects_that_tab_and_has_no_warning() -> None:
    resolved, resolved_tab_id, warning = resolve_document_tab(MULTI_TAB_DOC, "t.second")
    assert resolved_tab_id == "t.second"
    assert warning is None
    assert resolved["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] == "second tab content"


def test_legacy_doc_with_explicit_tab_id_raises_tab_not_found_error() -> None:
    with pytest.raises(TabNotFoundError) as exc_info:
        resolve_document_tab(LEGACY_DOC, "some-tab-id")
    message = str(exc_info.value)
    assert "some-tab-id" in message


def test_unknown_tab_id_raises_tab_not_found_error_listing_available_tabs() -> None:
    with pytest.raises(TabNotFoundError) as exc_info:
        resolve_document_tab(MULTI_TAB_DOC, "t.nonexistent")
    message = str(exc_info.value)
    assert "t.nonexistent" in message
    assert "t.first" in message and "t.second" in message


def test_nested_child_tabs_are_flattened_and_selectable() -> None:
    nested_doc = {
        "revisionId": "r1",
        "body": {"content": []},
        "tabs": [
            _tab(
                "t.parent",
                "Parent",
                "parent content",
                child_tabs=[_tab("t.child", "Child", "child content")],
            )
        ],
    }
    resolved, resolved_tab_id, warning = resolve_document_tab(nested_doc, "t.child")
    assert resolved_tab_id == "t.child"
    assert resolved["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] == "child content"


# ─────────────────────────────────────────────────────────────────────────────
# list_tabs
# ─────────────────────────────────────────────────────────────────────────────

def test_list_tabs_empty_for_legacy_doc() -> None:
    assert list_tabs(LEGACY_DOC) == []


def test_list_tabs_returns_all_tabs_in_document_order() -> None:
    infos = list_tabs(MULTI_TAB_DOC)
    assert [i.tab_id for i in infos] == ["t.first", "t.second"]
    assert [i.title for i in infos] == ["Overview", "Details"]


# ─────────────────────────────────────────────────────────────────────────────
# heading_ids_by_tab
# ─────────────────────────────────────────────────────────────────────────────

def _heading_paragraph(heading_id: str) -> dict:
    return {"paragraph": {"paragraphStyle": {"headingId": heading_id}, "elements": []}}


def _tab_with_content(tab_id: str, title: str, content: list, child_tabs: list | None = None) -> dict:
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {"body": {"content": content}, "lists": {}},
        "childTabs": child_tabs or [],
    }


def test_no_tabs_key_returns_empty_map() -> None:
    assert heading_ids_by_tab({"body": {"content": []}}) == {}


def test_heading_in_a_sibling_tab_is_discoverable() -> None:
    doc = {
        "tabs": [
            _tab_with_content("t.first", "Overview", [_heading_paragraph("h.a")]),
            _tab_with_content("t.second", "Details", [_heading_paragraph("h.b")]),
        ]
    }
    assert heading_ids_by_tab(doc) == {"h.a": "t.first", "h.b": "t.second"}


def test_heading_id_present_in_more_than_one_tab_is_dropped_not_won() -> None:
    doc = {
        "tabs": [
            _tab_with_content("t.first", "Overview", [_heading_paragraph("h.dup")]),
            _tab_with_content("t.second", "Details", [_heading_paragraph("h.dup")]),
        ]
    }
    assert heading_ids_by_tab(doc) == {}


def test_duplicate_heading_id_is_dropped_regardless_of_tab_order() -> None:
    forward = {
        "tabs": [
            _tab_with_content("t.a", "A", [_heading_paragraph("h.dup")]),
            _tab_with_content("t.b", "B", [_heading_paragraph("h.dup")]),
        ]
    }
    backward = {
        "tabs": [
            _tab_with_content("t.b", "B", [_heading_paragraph("h.dup")]),
            _tab_with_content("t.a", "A", [_heading_paragraph("h.dup")]),
        ]
    }
    assert heading_ids_by_tab(forward) == heading_ids_by_tab(backward) == {}


def test_heading_in_a_nested_child_tab_is_discoverable() -> None:
    doc = {
        "tabs": [
            _tab_with_content(
                "t.parent",
                "Parent",
                [_heading_paragraph("h.parent")],
                child_tabs=[_tab_with_content("t.child", "Child", [_heading_paragraph("h.child")])],
            )
        ]
    }
    assert heading_ids_by_tab(doc) == {"h.parent": "t.parent", "h.child": "t.child"}


def test_heading_inside_a_table_cell_is_excluded() -> None:
    table_content = [
        {
            "table": {
                "tableRows": [
                    {
                        "tableCells": [
                            {"content": [_heading_paragraph("h.in-cell")]},
                        ]
                    }
                ]
            }
        }
    ]
    doc = {"tabs": [_tab_with_content("t.only", "Only", table_content)]}
    assert heading_ids_by_tab(doc) == {}


def test_a_table_cell_heading_does_not_make_a_real_heading_ambiguous() -> None:
    # h.shared is a real heading in t.first and *also* the id of a heading-shaped
    # paragraph buried in a table cell in t.second. The table occurrence is
    # already excluded on its own (test_heading_inside_a_table_cell_is_excluded)
    # — this proves exclusion happens *before* ambiguity detection, not after,
    # so the real heading in t.first still resolves instead of being dropped.
    table_content = [
        {
            "table": {
                "tableRows": [
                    {
                        "tableCells": [
                            {"content": [_heading_paragraph("h.shared")]},
                        ]
                    }
                ]
            }
        }
    ]
    doc = {
        "tabs": [
            _tab_with_content("t.first", "Overview", [_heading_paragraph("h.shared")]),
            _tab_with_content("t.second", "Details", table_content),
        ]
    }
    assert heading_ids_by_tab(doc) == {"h.shared": "t.first"}


def test_is_pure_and_does_not_mutate_the_input_document() -> None:
    doc = {
        "tabs": [
            _tab_with_content("t.first", "Overview", [_heading_paragraph("h.a")]),
        ]
    }
    before = json.loads(json.dumps(doc))
    heading_ids_by_tab(doc)
    assert doc == before
