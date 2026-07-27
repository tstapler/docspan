"""Tests for render_nodes_to_markdown() (docspan.backends.google_docs.nodes_to_markdown).

Covers the tab-scoped pull path's DocsParagraphNode -> Markdown rendering,
including native Google Docs checkboxes (BULLET_CHECKBOX glyph), which the
Docs API never exposes a checked/unchecked bit for (see push_preview.py's
NATIVE CHECKBOX GLYPH warning) — so both checked and unchecked native
checkboxes render as an unchecked `- [ ]` markdown checklist item.
"""
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown


def _node(**kwargs) -> DocsParagraphNode:
    defaults = dict(style="NORMAL_TEXT", text="", is_list_item=False, nesting_level=0)
    defaults.update(kwargs)
    return DocsParagraphNode(**defaults)


def test_native_checkbox_unchecked_renders_as_markdown_checklist_item() -> None:
    nodes = [_node(text="buy milk", is_list_item=True, is_native_checkbox=True)]
    md = render_nodes_to_markdown(nodes)
    assert "- [ ] buy milk" in md


def test_native_checkbox_checked_still_renders_unchecked_since_api_cannot_expose_state() -> None:
    # There is no "checked" field on DocsParagraphNode — the Docs API doesn't
    # surface a native checkbox's checked/unchecked bit anywhere
    # DocsStructureParser can read it. Rendering unchecked is the only
    # honest option, regardless of the glyph's real (unknowable) state.
    nodes = [_node(text="buy eggs", is_list_item=True, is_native_checkbox=True)]
    md = render_nodes_to_markdown(nodes)
    assert "- [ ] buy eggs" in md
    assert "[x]" not in md


def test_plain_bullet_still_renders_without_checklist_marker() -> None:
    nodes = [_node(text="plain item", is_list_item=True, is_native_checkbox=False)]
    md = render_nodes_to_markdown(nodes)
    assert "- plain item" in md
    assert "[ ]" not in md
    assert "[x]" not in md


def test_ordered_list_item_still_renders_as_bullet() -> None:
    # DocsParagraphNode/DocsStructureParser doesn't currently distinguish
    # ordered from unordered lists (only nestingLevel) — both render with a
    # "-" marker. This test guards that the checkbox fix above doesn't
    # regress that existing (non-checkbox) list-item path.
    nodes = [
        _node(text="first", is_list_item=True, nesting_level=0),
        _node(text="second", is_list_item=True, nesting_level=0),
    ]
    md = render_nodes_to_markdown(nodes)
    assert "- first" in md
    assert "- second" in md


def test_nested_checkbox_preserves_indent() -> None:
    nodes = [_node(text="nested", is_list_item=True, nesting_level=1, is_native_checkbox=True)]
    md = render_nodes_to_markdown(nodes)
    assert "  - [ ] nested" in md
