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


# ─────────────────────────────────────────────────────────────────────────────
# REQ-13 tab_id x sectioned matrix (validation.md rows 43-45)
#
# sectioned=true/tab_id=set (a), sectioned=true/tab_id=unset (b), and
# sectioned=false/tab_id=set (c, regression) must all behave independently:
# the sectioned split logic (section_splitter.split_nodes) never reads
# start_index/end_index (confirmed by inspection — those fields don't appear
# in section_splitter.py at all), so unlike the push-side diffing fixtures
# elsewhere in this feature, these pull-only fixtures don't need realistic
# contiguous indices.
# ─────────────────────────────────────────────────────────────────────────────

def _full_heading_paragraph(text: str, heading_id: str, style: str = "HEADING_1") -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style, "headingId": heading_id},
            "elements": [{"textRun": {"content": text + "\n"}}],
        }
    }


def _full_body_paragraph(text: str) -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [{"textRun": {"content": text + "\n"}}],
        }
    }


def test_pull_sectioned_should_target_specified_tab_and_use_structural_path(tmp_path, make_backend) -> None:
    # Matrix cell (a): sectioned=true, tab_id=set.
    doc = {
        "revisionId": "r1",
        "body": {"content": []},
        "tabs": [
            _tab_with_content(
                "t.first",
                "Overview",
                [
                    _full_heading_paragraph("Wrong Tab Heading", "h.wrong"),
                    _full_body_paragraph("This lives in the first tab and must not be pulled."),
                ],
            ),
            _tab_with_content(
                "t.second",
                "Details",
                [
                    _full_heading_paragraph("Alpha", "h.alpha"),
                    _full_body_paragraph("Alpha body content."),
                    _full_heading_paragraph("Beta", "h.beta"),
                    _full_body_paragraph("Beta body content."),
                ],
            ),
        ],
    }
    backend, fake_client = make_backend()
    fake_client.get_document.return_value = doc

    local_dir = tmp_path / "doc"
    result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1", tab_id="t.second")

    assert result.status == "ok", result.message
    written = sorted(p.name for p in local_dir.iterdir())
    assert written == ["00-preamble.md", "01-alpha.md", "02-beta.md", "_manifest.yaml"]

    alpha_text = (local_dir / "01-alpha.md").read_text()
    assert "Alpha body content." in alpha_text
    assert "Wrong Tab Heading" not in alpha_text
    all_text = "\n".join((local_dir / f).read_text() for f in written if f.endswith(".md"))
    assert "This lives in the first tab" not in all_text

    # Sectioned pull always uses the structural path, never Drive's HTML export.
    fake_client.get_doc_content.assert_not_called()


def test_pull_sectioned_should_be_unaffected_by_absent_tab_id(tmp_path, make_backend) -> None:
    # Matrix cell (b): sectioned=true, tab_id unset — legacy single-doc shape
    # with no `tabs` key at all, matching Story 7.1's original fixture shape.
    doc = {
        "revisionId": "r1",
        "body": {
            "content": [
                _full_heading_paragraph("Alpha", "h.alpha"),
                _full_body_paragraph("Alpha body content."),
                _full_heading_paragraph("Beta", "h.beta"),
                _full_body_paragraph("Beta body content."),
            ]
        },
    }
    backend, fake_client = make_backend()
    fake_client.get_document.return_value = doc

    local_dir = tmp_path / "doc"
    result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

    assert result.status == "ok", result.message
    written = sorted(p.name for p in local_dir.iterdir())
    assert written == ["00-preamble.md", "01-alpha.md", "02-beta.md", "_manifest.yaml"]
    assert "Alpha body content." in (local_dir / "01-alpha.md").read_text()
    assert "Beta body content." in (local_dir / "02-beta.md").read_text()
    fake_client.get_doc_content.assert_not_called()


def test_pull_tab_scoped_should_be_unaffected_by_sectioned_mode_code_when_sectioned_false(
    tmp_path, make_backend
) -> None:
    # Matrix cell (c) / regression: tab_id=set, sectioned=false must be
    # byte-for-byte the pre-existing tab-scoped pull() path, untouched by any
    # of the sectioned-mode code added for this feature.
    backend, fake_client = make_backend()
    fake_client.get_document.return_value = MULTI_TAB_DOC

    local_path = tmp_path / "doc.md"
    result = backend.pull("doc-1", str(local_path), tab_id="t.second")

    assert result.status == "ok", result.message
    content = local_path.read_text()
    assert "second tab content" in content
    assert "first tab content" not in content
    fake_client.get_doc_content.assert_not_called()
