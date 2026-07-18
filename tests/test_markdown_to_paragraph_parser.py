"""Unit tests for MarkdownToParagraphParser — mistune AST traversal, no network."""

from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser

parser = MarkdownToParagraphParser()


# ─────────────────────────────────────────────────────────────────────────────
# Basic block types
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_string_returns_empty_list() -> None:
    assert parser.parse("") == []


def test_single_paragraph() -> None:
    nodes = parser.parse("Hello world")
    assert len(nodes) == 1
    assert nodes[0].style == "NORMAL_TEXT"
    assert nodes[0].text == "Hello world"


def test_two_paragraphs_separated_by_blank_line() -> None:
    nodes = parser.parse("First\n\nSecond")
    texts = [n.text for n in nodes]
    assert "First" in texts
    assert "Second" in texts
    assert len(nodes) == 2


def test_heading_1() -> None:
    nodes = parser.parse("# Title")
    assert any(n.style == "HEADING_1" and "Title" in n.text for n in nodes)


def test_heading_2() -> None:
    nodes = parser.parse("## Subtitle")
    assert any(n.style == "HEADING_2" and "Subtitle" in n.text for n in nodes)


def test_heading_3() -> None:
    nodes = parser.parse("### Section")
    assert any(n.style == "HEADING_3" and "Section" in n.text for n in nodes)


def test_heading_levels_1_through_6() -> None:
    for level in range(1, 7):
        nodes = parser.parse(f"{'#' * level} H{level}")
        assert any(n.style == f"HEADING_{level}" for n in nodes), f"Missing HEADING_{level}"


# ─────────────────────────────────────────────────────────────────────────────
# List items
# ─────────────────────────────────────────────────────────────────────────────

def test_unordered_list_item_flagged() -> None:
    nodes = parser.parse("- Item one")
    list_nodes = [n for n in nodes if n.is_list_item]
    assert len(list_nodes) >= 1
    assert any("Item one" in n.text for n in list_nodes)


def test_ordered_list_item_flagged() -> None:
    nodes = parser.parse("1. First item")
    list_nodes = [n for n in nodes if n.is_list_item]
    assert len(list_nodes) >= 1


def test_multiple_list_items() -> None:
    nodes = parser.parse("- Alpha\n- Beta\n- Gamma")
    list_nodes = [n for n in nodes if n.is_list_item]
    assert len(list_nodes) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Checklist round-trip (literal-text scheme — see ADR-001)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_preserves_literal_checkbox_markers_in_list_item_text() -> None:
    """`- [x] Foo` / `- [ ] Bar` must parse with the literal bracket marker
    intact inside `.text` — confirms the `task_lists` mistune plugin is NOT
    enabled (it would strip the marker into a separate `attrs.checked` field
    and lose it from `.text`), per ADR-001's LiteralTextScheme decision."""
    nodes = parser.parse("- [x] Whatsapp group\n- [ ] Splitwise\n")
    list_nodes = [n for n in nodes if n.is_list_item]
    assert len(list_nodes) == 2
    assert list_nodes[0].text == "[x] Whatsapp group"
    assert list_nodes[0].nesting_level == 0
    assert list_nodes[1].text == "[ ] Splitwise"
    assert list_nodes[1].nesting_level == 0


# ─────────────────────────────────────────────────────────────────────────────
# Code blocks
# ─────────────────────────────────────────────────────────────────────────────

def test_fenced_code_block_is_monospace() -> None:
    nodes = parser.parse("```python\nprint('hi')\n```")
    code_nodes = [n for n in nodes if n.spans and n.spans[0].monospace]
    assert len(code_nodes) == 1
    assert "print" in code_nodes[0].text


def test_indented_code_block_produces_node() -> None:
    # 4-space indented code block
    nodes = parser.parse("    x = 1")
    assert len(nodes) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Index values for push targets
# ─────────────────────────────────────────────────────────────────────────────

def test_target_nodes_have_zero_indices() -> None:
    nodes = parser.parse("Any paragraph")
    for n in nodes:
        assert n.start_index == 0
        assert n.end_index == 0


# ─────────────────────────────────────────────────────────────────────────────
# Mixed document
# ─────────────────────────────────────────────────────────────────────────────

def test_mixed_document_order_preserved() -> None:
    md = "# Title\n\nIntro paragraph.\n\n- List item\n\nConclusion."
    nodes = parser.parse(md)
    styles = [n.style for n in nodes]
    assert styles[0] == "HEADING_1"
    # Remaining nodes should contain NORMAL_TEXT entries
    assert "NORMAL_TEXT" in styles
