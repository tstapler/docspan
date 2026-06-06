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


def test_multiple_paragraphs_in_order() -> None:
    doc = _doc_with_content([
        _make_para_element("First", start=1, end=6),
        _make_para_element("Second", start=6, end=13),
    ])
    nodes = parser.parse(doc)
    assert len(nodes) == 2
    assert nodes[0].text == "First"
    assert nodes[1].text == "Second"
