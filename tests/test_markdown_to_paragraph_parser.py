"""Unit tests for MarkdownToParagraphParser — mistune AST traversal, no network."""

from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser

parser = MarkdownToParagraphParser()


# ─────────────────────────────────────────────────────────────────────────────
# Basic block types
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_string_returns_empty_list() -> None:
    assert parser.parse("") == []


def test_unregistered_token_types_are_silently_skipped() -> None:
    """thematic_break (---) and block_html (<div>) have no registered converter;
    parse() must skip them rather than raise, keeping the surrounding paragraphs."""
    nodes = parser.parse("Before\n\n---\n\n<div>raw html</div>\n\nAfter")
    texts = [n.text for n in nodes]
    assert texts == ["Before", "After"]


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


def test_fenced_code_block_language_emits_literal_marker_line() -> None:
    # AC1: mistune's token.attrs.info (fence language) has no field on
    # DocsParagraphNode, so it's carried as a literal, non-monospace marker
    # paragraph ahead of the code lines.
    nodes = parser.parse("```python\nprint('hi')\n```")
    assert nodes[0].text == "```python"
    assert nodes[0].style == "NORMAL_TEXT"
    assert not nodes[0].spans
    assert nodes[1].spans and nodes[1].spans[0].monospace
    assert "print" in nodes[1].text


def test_fenced_code_block_without_language_has_no_marker_line() -> None:
    nodes = parser.parse("```\nplain\n```")
    assert len(nodes) == 1
    assert nodes[0].spans and nodes[0].spans[0].monospace
    assert nodes[0].text == "plain"


def test_fenced_code_block_reparses_via_block_code_not_paragraph_strip() -> None:
    # AC4: rendering the parsed fence back out and re-parsing it must route
    # through the block_code branch (preserving indentation), never the
    # paragraph branch's .strip(), which would lose it.
    from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

    nodes = parser.parse("```yaml\nkey: value\n  indented: yes\n```")
    md = render_nodes_to_markdown(nodes)
    reparsed = parser.parse(md)
    code_lines = [n for n in reparsed if n.spans and n.spans[0].monospace]
    assert [n.text for n in code_lines] == ["key: value", "  indented: yes"]
    assert reparsed[0].text == "```yaml"


# ─────────────────────────────────────────────────────────────────────────────
# Block quotes (regression: block_quote tokens used to be silently dropped,
# losing the entire paragraph on push — see issue "push paragraph-loss bug")
# ─────────────────────────────────────────────────────────────────────────────

def test_block_quote_is_not_dropped() -> None:
    nodes = parser.parse("> TL;DR: this is important")
    assert len(nodes) == 1
    assert nodes[0].text == "TL;DR: this is important"
    assert nodes[0].is_blockquote is True
    assert nodes[0].quote_depth == 1


def test_block_quote_survives_between_paragraphs() -> None:
    nodes = parser.parse("Before.\n\n> Quoted line.\n\nAfter.")
    texts = [n.text for n in nodes]
    assert texts == ["Before.", "Quoted line.", "After."]
    quoted = nodes[1]
    assert quoted.is_blockquote is True
    assert nodes[0].is_blockquote is False
    assert nodes[2].is_blockquote is False


def test_nested_block_quote_uses_repeated_markers() -> None:
    # No literal "> > " marker is written anymore (Story 2.1): nesting is
    # carried purely by quote_depth. The blank "> " line between the outer
    # and nested quote surfaces as its own empty, depth-1 blockquote node
    # (Story 2.5) rather than being skipped.
    nodes = parser.parse("> outer\n>\n> > inner")
    assert [(n.text, n.quote_depth) for n in nodes] == [
        ("outer", 1),
        ("", 1),
        ("inner", 2),
    ]
    assert all(n.is_blockquote for n in nodes)


def test_block_quote_preserves_inline_styling() -> None:
    nodes = parser.parse("> **bold** and normal")
    assert len(nodes) == 1
    assert nodes[0].text == "bold and normal"
    assert nodes[0].is_blockquote is True
    bold_spans = [s for s in nodes[0].spans if s.bold]
    assert bold_spans and bold_spans[0].text == "bold"


def test_block_quote_containing_list() -> None:
    # Story 2.6 (quote-containing-list, in scope): no literal "> " prefix is
    # written into list item text; the quote is instead carried entirely by
    # is_blockquote/quote_depth, composing with the list's own nesting_level.
    nodes = parser.parse("> - item one\n> - item two")
    assert [n.text for n in nodes] == ["item one", "item two"]
    assert all(n.is_list_item for n in nodes)
    assert all(n.is_blockquote for n in nodes)
    assert all(n.quote_depth == 1 for n in nodes)


def test_walk_block_quote_should_set_blockquote_fields_without_prefix_when_parsing_plain_quote() -> None:
    nodes = parser.parse("> a plain quote")
    assert len(nodes) == 1
    node = nodes[0]
    assert node.text == "a plain quote"
    assert node.is_blockquote is True
    assert node.quote_depth == 1


def test_walk_block_quote_should_set_depth_two_when_parsing_nested_quote() -> None:
    nodes = parser.parse("> > deeply quoted")
    assert len(nodes) == 1
    assert nodes[0].text == "deeply quoted"
    assert nodes[0].is_blockquote is True
    assert nodes[0].quote_depth == 2


def test_walk_block_quote_should_produce_empty_text_blockquote_node_when_parsing_blank_quote_line() -> None:
    nodes = parser.parse("> \n")
    assert len(nodes) == 1
    assert nodes[0].text == ""
    assert nodes[0].is_blockquote is True
    assert nodes[0].quote_depth == 1


def test_walk_block_quote_should_emit_language_marker_when_quote_contains_fenced_code_block() -> None:
    # Every continuation line needs its own "> " prefix for mistune to keep
    # the fence inside the block_quote rather than splitting it into
    # malformed top-level tokens.
    md = "> ```python\n> print('hi')\n> ```\n"
    nodes = parser.parse(md)
    assert nodes[0].text == "```python"
    assert nodes[0].is_blockquote is True
    assert nodes[0].quote_depth == 1
    assert not nodes[0].spans
    assert nodes[1].spans and nodes[1].spans[0].monospace
    assert "print" in nodes[1].text
    assert nodes[1].is_blockquote is True

    from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

    rendered = render_nodes_to_markdown(nodes)
    reparsed = parser.parse(rendered)
    code_lines = [n for n in reparsed if n.spans and n.spans[0].monospace]
    assert len(code_lines) == 1
    assert "print" in code_lines[0].text


def test_walk_list_items_should_still_misrender_blockquote_child_when_list_contains_quote() -> None:
    # Out of scope per plan.md's Scope Decision: a list item containing a
    # nested quote (as opposed to a quote containing a list, Story 2.6) still
    # falls through `_walk_list_items`'s generic inline-child branch and
    # loses the blockquote's structure/identity entirely. This regression
    # test pins the current (broken) shape so Story 2.1's rewrite of
    # `_walk_block_quote` doesn't silently change it as a side effect: the
    # nested quote's text is absorbed into the list item's own text and it
    # is never tagged is_blockquote.
    nodes = parser.parse("- > note")
    assert len(nodes) == 1
    assert nodes[0].is_list_item is True
    assert nodes[0].is_blockquote is False
    assert "note" in nodes[0].text


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
