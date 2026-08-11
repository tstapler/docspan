"""Tests for render_nodes_to_markdown() (docspan.backends.google_docs.nodes_to_markdown).

Covers the tab-scoped pull path's DocsParagraphNode -> Markdown rendering,
including native Google Docs checkboxes (BULLET_CHECKBOX glyph), which the
Docs API never exposes a checked/unchecked bit for (see push_preview.py's
NATIVE CHECKBOX GLYPH warning) — so both checked and unchecked native
checkboxes render as an unchecked `- [ ]` markdown checklist item.
"""
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode, TextSpan
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown


def _node(**kwargs) -> DocsParagraphNode:
    defaults = dict(style="NORMAL_TEXT", text="", is_list_item=False, nesting_level=0)
    defaults.update(kwargs)
    return DocsParagraphNode(**defaults)


def _code_line(text: str) -> DocsParagraphNode:
    """A pure code-line node — the exact shape MarkdownToParagraphParser
    writes for a fenced block's lines: one span, monospace, no other mark."""
    return _node(text=text, spans=[TextSpan(text=text, monospace=True)])


def _blank_code_line() -> DocsParagraphNode:
    return _node(text="", spans=[])


def _lang_marker(lang: str) -> DocsParagraphNode:
    return _node(text=f"```{lang}")


def test_monospace_run_renders_as_fence() -> None:
    # AC0: consecutive full-width-monospace paragraphs become one fenced
    # code block, not per-line inline code.
    nodes = [_code_line("key: value"), _code_line("  indented: yes")]
    md = render_nodes_to_markdown(nodes)
    assert "```\nkey: value\n  indented: yes\n```" in md
    assert "`key: value`" not in md


def test_fence_with_language_round_trip() -> None:
    # AC1: a language marker line ahead of a code run becomes ```lang.
    nodes = [_lang_marker("yaml"), _code_line("key: value")]
    md = render_nodes_to_markdown(nodes)
    assert "```yaml\nkey: value\n```" in md


def test_monospace_content_with_backticks_is_escaped() -> None:
    # AC2: an isolated inline-code span's own backticks are escaped using
    # CommonMark's longer-delimiter rule. Mixed with surrounding plain text
    # so this isn't itself the exact pure-code-line shape (which AC0 says
    # should become a one-line fence instead).
    nodes = [_node(text="see a`b here",
                    spans=[TextSpan(text="see "), TextSpan(text="a`b", monospace=True),
                           TextSpan(text=" here")])]
    md = render_nodes_to_markdown(nodes)
    assert "``a`b``" in md


def test_fence_around_grouped_run_escapes_via_longer_delimiter() -> None:
    # AC2: the outer fence around a grouped run is picked longer than any
    # backtick run inside the code content, rather than corrupting the fence.
    nodes = [_code_line("has ``` inside")]
    md = render_nodes_to_markdown(nodes)
    assert "````\nhas ``` inside\n````" in md


def test_isolated_inline_code_not_grouped() -> None:
    # AC3: a single monospace span inside an otherwise plain paragraph must
    # never be swept into a fence.
    nodes = [_node(text="see `foo` here",
                    spans=[TextSpan(text="see "), TextSpan(text="foo", monospace=True),
                           TextSpan(text=" here")])]
    md = render_nodes_to_markdown(nodes)
    assert "```" not in md
    assert "`foo`" in md


def test_mixed_mark_span_not_grouped_as_fence() -> None:
    # A monospace+bold span is not the exact code-line shape and must render
    # as an inline (bold) code span, not get absorbed into a fence.
    nodes = [_node(text="x", spans=[TextSpan(text="bold code", monospace=True, bold=True)])]
    md = render_nodes_to_markdown(nodes)
    assert "```" not in md
    assert "**`bold code`**" in md


def test_blank_line_inside_fence_preserved() -> None:
    # AC5: a blank line interior to a code run stays inside the fence
    # instead of splitting it into two fences.
    nodes = [_code_line("one"), _blank_code_line(), _code_line("two")]
    md = render_nodes_to_markdown(nodes)
    assert md.count("```") == 2
    assert "one\n\ntwo" in md


def test_two_separate_code_blocks_stay_separate() -> None:
    # A normal paragraph between two code runs must keep them as two fences,
    # not merge them into one.
    nodes = [_code_line("first"), _node(text="prose"), _code_line("second")]
    md = render_nodes_to_markdown(nodes)
    assert md.count("```") == 4


def test_language_marker_without_following_code_is_not_absorbed() -> None:
    # A literal paragraph that happens to start with ``` but isn't followed
    # by a code run renders as plain text rather than crashing.
    nodes = [_lang_marker("yaml"), _node(text="just prose")]
    md = render_nodes_to_markdown(nodes)
    assert "```yaml" in md
    assert "just prose" in md


def test_adjacent_language_marked_blocks_stay_separate() -> None:
    # Two marker+code runs with no intervening node must render as two
    # fences, not fuse into one — the marker is what breaks the run.
    nodes = [
        _lang_marker("yaml"), _code_line("first"),
        _lang_marker("sh"), _code_line("second"),
    ]
    md = render_nodes_to_markdown(nodes)
    assert md.count("```") == 4
    assert "```yaml\nfirst\n```" in md
    assert "```sh\nsecond\n```" in md


def test_empty_fenced_block_with_language_round_trips() -> None:
    # A marker followed by exactly one blank-shaped line and no further code
    # is MarkdownToParagraphParser's shape for an explicitly empty fence
    # (`:294`), not an orphaned marker — it must render as an empty fence,
    # not be lost or misread as plain text.
    nodes = [_lang_marker("yaml"), _blank_code_line()]
    md = render_nodes_to_markdown(nodes)
    assert "```yaml\n```" in md


def test_orphaned_marker_does_not_corrupt_reparse() -> None:
    # A marker left behind with no matching code run renders as an escaped,
    # inert line — reparsing the rendered markdown must not turn the
    # following prose into monospace code content.
    from docspan.backends.google_docs.markdown_to_paragraph_parser import (
        MarkdownToParagraphParser,
    )

    nodes = [_lang_marker("yaml"), _node(text="just prose"), _node(text="more prose")]
    md = render_nodes_to_markdown(nodes)
    reparsed = MarkdownToParagraphParser().parse(md)
    assert not any(span.monospace for node in reparsed for span in node.spans)


def test_fence_delimiter_accounts_for_backticks_in_language() -> None:
    # A run of backticks inside the language string itself must also
    # lengthen the fence delimiter, or it fuses with the fence and corrupts
    # the language. Three backticks in the language forces a 4-backtick
    # fence — the code content alone (no backticks) wouldn't demand it.
    nodes = [_lang_marker("a```b"), _code_line("plain")]
    md = render_nodes_to_markdown(nodes)
    assert "````a```b\nplain\n````" in md


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
