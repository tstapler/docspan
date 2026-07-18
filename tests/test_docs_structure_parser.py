"""Unit tests for DocsStructureParser — pure dict-to-AST logic, no network."""

from __future__ import annotations

import pytest

from docspan.backends.google_docs.docs_structure_parser import (
    DocsStructureParser,
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


def test_parse_skips_table_and_toc_elements_without_raising() -> None:
    """A table/tableOfContents structural element parses without error and
    without corrupting adjacent paragraphs — confirms the documented feature
    gap is a silent skip, not a crash."""
    doc = _doc_with_content([
        _make_para_element("Before", start=1, end=8),
        {"startIndex": 8, "endIndex": 20, "table": {"rows": 1, "columns": 1}},
        {"startIndex": 20, "endIndex": 25, "tableOfContents": {}},
        _make_para_element("After", start=25, end=31),
    ])
    nodes = parser.parse(doc)
    assert [n.text for n in nodes] == ["Before", "After"]


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
