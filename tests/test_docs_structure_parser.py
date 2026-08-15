"""Unit tests for DocsStructureParser — pure dict-to-AST logic, no network."""

from __future__ import annotations

import pytest

from docspan.backends.google_docs.docs_structure_parser import (
    DocsStructureParser,
    DocsTableNode,
)


def _make_para_element(
    text: str,
    style: str = "NORMAL_TEXT",
    start: int = 1,
    end: int = 10,
    bullet: dict | None = None,
    bold: bool = False,
    italic: bool = False,
    link: str | None = None,
    font_family: str = "",
) -> dict:
    text_style: dict = {}
    if bold:
        text_style["bold"] = True
    if italic:
        text_style["italic"] = True
    if link:
        text_style["link"] = {"url": link}
    if font_family:
        text_style["weightedFontFamily"] = {"fontFamily": font_family}
    element: dict = {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [
                {"textRun": {"content": text + "\n", "textStyle": text_style}}
            ],
        },
    }
    if bullet is not None:
        element["paragraph"]["bullet"] = bullet
    return element


def _doc_with_content(content: list) -> dict:
    return {"body": {"content": content}}


parser = DocsStructureParser()


# ─────────────────────────────────────────────────────────────────────────────
# Document structure handling
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_empty_body() -> None:
    nodes = parser.parse({"body": {"content": []}})
    assert nodes == []


def test_parse_raises_on_missing_body_and_tabs() -> None:
    with pytest.raises(KeyError):
        parser.parse({})


def test_parse_tabs_format() -> None:
    doc = {
        "tabs": [
            {
                "documentTab": {
                    "body": {"content": [_make_para_element("hello", start=1, end=7)]}
                }
            }
        ]
    }
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].text == "hello"


def test_parse_legacy_body_format() -> None:
    doc = _doc_with_content([_make_para_element("world", start=1, end=7)])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].text == "world"


# ─────────────────────────────────────────────────────────────────────────────
# Paragraph style extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_heading_style_preserved() -> None:
    doc = _doc_with_content([_make_para_element("Title", style="HEADING_1", start=1, end=7)])
    nodes = parser.parse(doc)
    assert nodes[0].style == "HEADING_1"


def test_normal_text_style() -> None:
    doc = _doc_with_content([_make_para_element("Body", style="NORMAL_TEXT", start=1, end=6)])
    nodes = parser.parse(doc)
    assert nodes[0].style == "NORMAL_TEXT"


def test_trailing_newline_stripped() -> None:
    doc = _doc_with_content([_make_para_element("Line", start=1, end=6)])
    nodes = parser.parse(doc)
    assert not nodes[0].text.endswith("\n")
    assert nodes[0].text == "Line"


# ─────────────────────────────────────────────────────────────────────────────
# Index preservation
# ─────────────────────────────────────────────────────────────────────────────

def test_start_end_index_preserved() -> None:
    doc = _doc_with_content([_make_para_element("X", start=5, end=20)])
    nodes = parser.parse(doc)
    assert nodes[0].start_index == 5
    assert nodes[0].end_index == 20


# ─────────────────────────────────────────────────────────────────────────────
# Text span / formatting
# ─────────────────────────────────────────────────────────────────────────────

def test_bold_span_detected() -> None:
    doc = _doc_with_content([_make_para_element("Bold", bold=True, start=1, end=6)])
    nodes = parser.parse(doc)
    assert nodes[0].spans[0].bold is True


def test_italic_span_detected() -> None:
    doc = _doc_with_content([_make_para_element("Italic", italic=True, start=1, end=7)])
    nodes = parser.parse(doc)
    assert nodes[0].spans[0].italic is True


def test_link_extracted() -> None:
    doc = _doc_with_content([_make_para_element("Click", link="https://example.com", start=1, end=7)])
    nodes = parser.parse(doc)
    assert nodes[0].spans[0].link == "https://example.com"


def test_monospace_detected_by_font() -> None:
    doc = _doc_with_content([_make_para_element("Code", font_family="Courier New", start=1, end=6)])
    nodes = parser.parse(doc)
    assert nodes[0].spans[0].monospace is True


def test_non_monospace_font_not_flagged() -> None:
    doc = _doc_with_content([_make_para_element("Normal", font_family="Arial", start=1, end=8)])
    nodes = parser.parse(doc)
    assert nodes[0].spans[0].monospace is False


# ─────────────────────────────────────────────────────────────────────────────
# @-mention "person" smart chips
#
# The Docs API v1 represents an @-mention as a `person` structural element —
# a sibling of `textRun` inside paragraph.elements, never a textRun itself.
# Before this fix, the parser only checked `pe.get("textRun")`; a `person`
# element has no textRun key, so it silently fell through `continue` and its
# name never reached spans/text (see the reported bug: mentions of "Shivam
# Malpani", "Andrew Williams", and "Will Myers" all rendered blank).
# ─────────────────────────────────────────────────────────────────────────────

def _make_person_element(
    name: str | None = None,
    email: str | None = None,
    start: int = 1,
    end: int = 10,
) -> dict:
    person_properties: dict = {}
    if name is not None:
        person_properties["name"] = name
    if email is not None:
        person_properties["email"] = email
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {
                    "startIndex": start,
                    "endIndex": start + 1,
                    "person": {"personProperties": person_properties},
                },
                {
                    "startIndex": start + 1,
                    "endIndex": end,
                    "textRun": {"content": "\n", "textStyle": {}},
                },
            ],
        },
    }


def test_person_mention_with_name_and_email_uses_name() -> None:
    doc = _doc_with_content([
        _make_person_element(name="Will Myers", email="will@example.com", start=1, end=10)
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].text == "Will Myers"
    assert nodes[0].spans[0].text == "Will Myers"


def test_person_mention_with_only_email_falls_back_to_email() -> None:
    doc = _doc_with_content([
        _make_person_element(email="shivam@example.com", start=1, end=10)
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].text == "shivam@example.com"
    assert nodes[0].spans[0].text == "shivam@example.com"


def test_person_mention_inline_with_surrounding_text() -> None:
    """A mention mid-sentence, e.g. "cc @Andrew Williams please", must not
    drop the surrounding textRuns nor the person's name."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 30,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"textRun": {"content": "cc ", "textStyle": {}}},
                {"person": {"personProperties": {"name": "Andrew Williams"}}},
                {"textRun": {"content": " please\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == "cc Andrew Williams please"


def test_person_mention_inside_table_cell_renders_name() -> None:
    """The table-cell text extraction loop (_parse_table) has its own
    textRun-only walk, independent of _parse_paragraph — verify it also
    handles a `person` element instead of silently dropping it."""
    doc = _doc_with_content([{
        "startIndex": 1, "endIndex": 20,
        "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [{
                "content": [{
                    "startIndex": 2, "endIndex": 10,
                    "paragraph": {"elements": [
                        {"person": {"personProperties": {"name": "Shivam Malpani"}}},
                        {"textRun": {"content": "\n"}},
                    ]},
                }],
            }]},
        ]},
    }])
    nodes = parser.parse(doc)
    table = nodes[0]
    assert isinstance(table, DocsTableNode)
    assert [[c.text for c in row] for row in table.rows] == [["Shivam Malpani"]]


def test_person_mention_with_no_name_or_email_is_skipped_not_raised() -> None:
    """Defensive: an empty personProperties dict must not raise, and simply
    contributes no text (there is nothing to render)."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": {"personProperties": {}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == ""


def test_person_mention_with_malformed_person_properties_is_skipped_not_raised() -> None:
    """Defensive: a non-dict personProperties (malformed API payload) must not
    raise — it should be treated the same as "no name or email"."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": {"personProperties": ["not", "a", "dict"]}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == ""


def test_person_mention_with_non_dict_person_is_skipped_not_raised() -> None:
    """Defensive: a non-dict `person` value (malformed API payload) must not
    raise — it should be treated the same as "no name or email"."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": "not-a-dict"},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == ""


def test_person_mention_with_non_string_name_and_no_email_is_skipped_not_raised() -> None:
    """Defensive: a non-string `name` (malformed API payload, e.g. an int)
    must not raise — with no valid email to fall back to, it should be
    treated the same as "no name or email"."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": {"personProperties": {"name": 12345}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == ""


def test_person_mention_with_non_string_name_falls_back_to_valid_email() -> None:
    """Defensive: a non-string `name` alongside a valid string `email` must
    not raise — the non-string name should be treated as absent and the
    email used instead."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": {"personProperties": {"name": 12345, "email": "shivam@example.com"}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == "shivam@example.com"


def test_person_mention_with_non_string_email_and_no_name_is_skipped_not_raised() -> None:
    """Defensive: a non-string `email` (malformed API payload, e.g. an int)
    with no name present must not raise — treated as "no name or email"."""
    doc = _doc_with_content([{
        "startIndex": 1,
        "endIndex": 10,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"person": {"personProperties": {"email": 42}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }])
    nodes = parser.parse(doc)
    assert nodes[0].text == ""


# ─────────────────────────────────────────────────────────────────────────────
# List items
# ─────────────────────────────────────────────────────────────────────────────

def test_bullet_item_flagged() -> None:
    doc = _doc_with_content([
        _make_para_element("Item", bullet={"nestingLevel": 0}, start=1, end=6)
    ])
    nodes = parser.parse(doc)
    assert nodes[0].is_list_item is True
    assert nodes[0].nesting_level == 0


def test_nested_list_item() -> None:
    doc = _doc_with_content([
        _make_para_element("Nested", bullet={"nestingLevel": 2}, start=1, end=8)
    ])
    nodes = parser.parse(doc)
    assert nodes[0].nesting_level == 2


# ─────────────────────────────────────────────────────────────────────────────
# Checklist round-trip (literal-text scheme — see ADR-001)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_paragraph_preserves_literal_checkbox_marker_in_text() -> None:
    """A `[x]`/`[ ]` marker embedded in a bullet paragraph's text must survive
    parsing unmodified — checklist state is opaque literal text (ADR-001),
    never derived from or stripped based on the bullet/glyph itself."""
    doc = _doc_with_content([
        _make_para_element(
            "[x] Whatsapp group",
            bullet={"listId": "kix.abc", "nestingLevel": 0},
            start=10,
            end=30,
        )
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.style == "NORMAL_TEXT"
    assert node.text == "[x] Whatsapp group"
    assert node.is_list_item is True
    assert node.nesting_level == 0
    assert node.start_index == 10
    assert node.end_index == 30


def test_multiple_paragraphs_in_order() -> None:
    doc = _doc_with_content([
        _make_para_element("First", start=1, end=6),
        _make_para_element("Second", start=6, end=13),
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 2
    assert nodes[0].text == "First"
    assert nodes[1].text == "Second"


def test_parse_paragraph_handles_bullet_paragraph_missing_list_id_without_raising() -> None:
    """A bullet-bearing structural element with no listId (malformed/partial
    Docs JSON) must not raise — the parser degrades to is_list_item=True,
    is_native_checkbox=False, rather than crashing the whole parse() pass."""
    doc = _doc_with_content([
        _make_para_element("Item with no listId", bullet={"nestingLevel": 0}, start=1, end=10)
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].is_list_item is True
    assert nodes[0].is_native_checkbox is False


def test_parse_produces_table_node_and_skips_toc_without_raising() -> None:
    """A table structural element now parses into a DocsTableNode (table/
    inline-style support merged from the bidirectional-comments epic — see
    project_plans/gdocs-tables-inline-styles/plan.md), while a
    tableOfContents element parses without error and without corrupting
    adjacent paragraphs — confirmed still a silent skip, not a crash."""
    doc = _doc_with_content([
        _make_para_element("Before", start=1, end=8),
        {"startIndex": 8, "endIndex": 20, "table": {"rows": 1, "columns": 1}},
        {"startIndex": 20, "endIndex": 25, "tableOfContents": {}},
        _make_para_element("After", start=25, end=31),
    ])
    nodes = parser.parse(doc)
    assert [type(n).__name__ for n in nodes] == [
        "DocsParagraphNode", "DocsTableNode", "DocsParagraphNode",
    ]
    assert nodes[0].text == "Before"
    assert isinstance(nodes[1], DocsTableNode)
    assert nodes[1].rows == []  # malformed/minimal table dict has no tableRows
    assert nodes[2].text == "After"


# ─────────────────────────────────────────────────────────────────────────────
# is_native_checkbox resolution (GlyphShapeCheck — plan.md Task 1.2.2d, ADR-001)
# ─────────────────────────────────────────────────────────────────────────────

def _doc_with_lists(content: list, lists: dict) -> dict:
    return {"body": {"content": content}, "lists": lists}


def test_parse_paragraph_sets_is_native_checkbox_true_for_checkbox_glyph_bullet() -> None:
    """A bullet whose resolved glyph is GLYPH_TYPE_UNSPECIFIED is a native
    BULLET_CHECKBOX glyph — confirmed checked/unchecked state is not readable
    via documents.get() (ADR-001)."""
    doc = _doc_with_lists(
        [
            _make_para_element(
                "[ ] Whatsapp group",
                bullet={"listId": "kix.abc", "nestingLevel": 0},
                start=10,
                end=30,
            )
        ],
        {"kix.abc": {"listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}}},
    )
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].is_native_checkbox is True


def test_parse_paragraph_sets_is_native_checkbox_false_for_ordinary_bullet() -> None:
    """An ordinary disc/circle/square bullet (non-checkbox glyphType) must
    resolve to is_native_checkbox=False."""
    doc = _doc_with_lists(
        [
            _make_para_element(
                "Ordinary bullet item",
                bullet={"listId": "kix.def", "nestingLevel": 0},
                start=1,
                end=25,
            )
        ],
        {"kix.def": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}}},
    )
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].is_native_checkbox is False


def test_parse_paragraph_is_native_checkbox_false_when_list_id_missing_from_lists_map() -> None:
    """A bullet referencing a listId absent from the document's `lists` map
    (e.g. incomplete fixture/partial fetch) degrades to False, not a KeyError."""
    doc = _doc_with_lists(
        [
            _make_para_element(
                "Orphaned bullet",
                bullet={"listId": "kix.unknown", "nestingLevel": 0},
                start=1,
                end=17,
            )
        ],
        {},
    )
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].is_native_checkbox is False


def test_parse_paragraph_is_native_checkbox_false_for_non_bullet_paragraph() -> None:
    doc = _doc_with_content([_make_para_element("Plain paragraph", start=1, end=17)])
    nodes = parser.parse(doc)
    assert len(nodes) == 1
    assert nodes[0].is_native_checkbox is False


# ─────────────────────────────────────────────────────────────────────────────
# precedes_structural_element — undeletable-newline detection
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_flags_paragraph_directly_before_a_section_break() -> None:
    """A sectionBreak is never parsed into a node, so the flag on the preceding
    paragraph is the only trace of it — and the only thing stopping
    _make_delete_requests from emitting the newline delete the Docs API
    rejects with "Cannot delete the requested range."."""
    doc = _doc_with_content([
        _make_para_element("Before the break", start=1, end=18),
        {"startIndex": 18, "endIndex": 19, "sectionBreak": {}},
        _make_para_element("After the break", start=19, end=35),
    ])
    nodes = parser.parse(doc)
    assert [n.precedes_structural_element for n in nodes] == [True, False]


def test_parse_flags_paragraph_directly_before_a_table() -> None:
    doc = _doc_with_content([
        _make_para_element("Before the table", start=1, end=18),
        {"startIndex": 18, "endIndex": 30, "table": {"tableRows": []}},
    ])
    nodes = parser.parse(doc)
    assert isinstance(nodes[1], DocsTableNode)
    assert nodes[0].precedes_structural_element is True


def test_parse_flags_paragraph_directly_before_a_table_of_contents() -> None:
    doc = _doc_with_content([
        _make_para_element("Contents", start=1, end=10),
        {"startIndex": 10, "endIndex": 40, "tableOfContents": {"content": []}},
        _make_para_element("Body", start=40, end=45),
    ])
    nodes = parser.parse(doc)
    assert [n.precedes_structural_element for n in nodes] == [True, False]


def test_parse_does_not_flag_ordinary_or_final_paragraphs() -> None:
    doc = _doc_with_content([
        _make_para_element("First", start=1, end=7),
        _make_para_element("Last", start=7, end=12),
    ])
    nodes = parser.parse(doc)
    assert [n.precedes_structural_element for n in nodes] == [False, False]


# ─────────────────────────────────────────────────────────────────────────────
# unreadable_links — bookmark/tabId links _parse_link cannot express, now
# recorded instead of silently dropped (issue #38).
# ─────────────────────────────────────────────────────────────────────────────

def _table_cell_link_doc(link: dict) -> dict:
    """A one-cell table whose only run carries the given `Link` dict."""
    return _doc_with_content([{
        "startIndex": 1, "endIndex": 20,
        "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [{
                "content": [{
                    "startIndex": 2, "endIndex": 10,
                    "paragraph": {"elements": [
                        {"textRun": {
                            "content": "see it",
                            "textStyle": {"link": link},
                        }},
                        {"textRun": {"content": "\n"}},
                    ]},
                }],
            }]},
        ]},
    }])


def test_table_cell_with_bookmark_link_is_reported_unreadable() -> None:
    """Post-#51, cell runs route through the same _parse_link as body text —
    a bookmark link inside a cell must be caught by that same fallthrough,
    not just at the top level."""
    fresh = DocsStructureParser()
    fresh.parse(_table_cell_link_doc({"bookmarkId": "kix.b1"}))
    assert fresh.unreadable_links == ["bookmark link"]


def test_table_cell_with_url_link_still_renders_and_is_not_reported() -> None:
    """Regression guard for #51: a resolvable url link in a cell must not be
    swept up as unreadable just because it shares _parse_link with bookmarks."""
    fresh = DocsStructureParser()
    nodes = fresh.parse(_table_cell_link_doc({"url": "https://example.com"}))
    table = nodes[0]
    assert isinstance(table, DocsTableNode)
    assert table.rows[0][0].spans[0].link == "https://example.com"
    assert fresh.unreadable_links == []
