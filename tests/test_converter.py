"""Unit tests for DocumentConverter.html_to_markdown (no network).

Covers the pull-side formatting-fidelity bug: Google Docs' HTML export
represents bold/italic/monospace as inline <span style="..."> attributes
(not semantic tags) and wraps hyperlinks through a google.com/url redirector.
"""

from docspan.backends.google_docs.converter import DocumentConverter


def test_bold_span_becomes_markdown_bold() -> None:
    html = '<p><span style="font-weight:700">bold text</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "**bold text**"


def test_normal_weight_span_is_not_bolded() -> None:
    html = '<p><span style="font-weight:400">normal text</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "normal text"


def test_italic_span_becomes_markdown_italic() -> None:
    html = '<p><span style="font-style:italic">italic text</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "*italic text*"


def test_monospace_font_family_span_becomes_code_span() -> None:
    html = '<p><span style="font-family:\'Courier New\'">code text</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "`code text`"


def test_monospace_span_with_backtick_escapes_via_a_longer_fence() -> None:
    # issue #44: the naive `f'`{text}`'` wrap on this (default, non-tab-scoped)
    # pull path corrupted a monospace span whose own text contains a backtick.
    # It must use the same CommonMark longer-fence/padding rule as the
    # tab-scoped path's `_render_spans`.
    html = '<p><span style="font-family:\'Courier New\'">A=`date`</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "`` A=`date` ``"


def test_bold_and_italic_span_combine() -> None:
    html = '<p><span style="font-weight:700;font-style:italic">both</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "***both***"


def test_plain_span_without_style_is_unstyled() -> None:
    html = "<p><span>plain text</span></p>"
    assert DocumentConverter.html_to_markdown(html) == "plain text"


def test_google_redirect_link_is_unwrapped() -> None:
    html = (
        '<p><a href="https://www.google.com/url?q=https://example.com/target'
        '&amp;sa=D&amp;source=editors&amp;ust=123&amp;usg=abc">link text</a></p>'
    )
    assert DocumentConverter.html_to_markdown(html) == "[link text](https://example.com/target)"


def test_plain_link_is_left_unchanged() -> None:
    html = '<p><a href="https://example.com/target">link text</a></p>'
    assert DocumentConverter.html_to_markdown(html) == "[link text](https://example.com/target)"


def test_empty_link_is_removed_but_image_with_empty_alt_is_kept() -> None:
    """Regression: Drive's HTML export gives inline images an empty alt
    attribute, which markdownify renders as `![](src)`. The "remove empty
    Google Docs links" cleanup used to match that `[](src)` substring too,
    stripping everything but a bare `!` and losing the image entirely."""
    html = '<p><a href=""></a><img alt="" src="https://example.com/x.png"></p>'
    assert DocumentConverter.html_to_markdown(html) == "![](https://example.com/x.png)"


# --- default-path code fences (follow-up to #45) ---

import pytest

from docspan.backends.google_docs.docs_structure_parser import _MONOSPACE_FONT_MARKERS

_MONO = "font-family:'Courier New'"


def _mono_p(text: str, extra: str = "") -> str:
    return f'<p><span style="{_MONO};{extra}">{text}</span></p>'


def test_multiline_monospace_paragraphs_become_fenced_block() -> None:
    html = _mono_p("def f():") + _mono_p("    return 1") + "<p>after</p>"
    assert DocumentConverter.html_to_markdown(html) == "```\ndef f():\n    return 1\n```\n\nafter"


def test_inline_monospace_in_prose_is_not_fenced() -> None:
    html = f'<p>Run <span style="{_MONO}">foo</span> now</p><p>and <span style="{_MONO}">bar</span> too</p>'
    assert DocumentConverter.html_to_markdown(html) == "Run `foo` now\n\nand `bar` too"


def test_bold_monospace_span_is_excluded_from_fence() -> None:
    html = _mono_p("one") + _mono_p("two", "font-weight:700")
    assert DocumentConverter.html_to_markdown(html) == "`one`\n\n**`two`**"


@pytest.mark.parametrize("font", _MONOSPACE_FONT_MARKERS)
def test_every_tab_id_monospace_font_is_detected(font: str) -> None:
    html = f'<p><span style="font-family:\'{font}\'">x</span></p>'
    assert DocumentConverter.html_to_markdown(html) == "`x`"


def test_language_marker_paragraph_sets_fence_language() -> None:
    html = "<p>```python</p>" + _mono_p("x = 1")
    assert DocumentConverter.html_to_markdown(html) == "```python\nx = 1\n```"


def test_run_without_marker_gets_bare_fence() -> None:
    html = _mono_p("a") + _mono_p("b")
    assert DocumentConverter.html_to_markdown(html).startswith("```\n")


def test_indentation_and_underscores_survive_fencing() -> None:
    html = _mono_p("if a_b:") + _mono_p("&nbsp;&nbsp;    x = 1")
    assert DocumentConverter.html_to_markdown(html) == "```\nif a_b:\n      x = 1\n```"


def test_backticks_in_code_widen_the_fence() -> None:
    html = _mono_p("```") + _mono_p("x")
    assert DocumentConverter.html_to_markdown(html) == "````\n```\nx\n````"


def test_code_blocks_separated_by_prose_stay_separate() -> None:
    html = _mono_p("a") + _mono_p("b") + "<p>between</p>" + _mono_p("c") + _mono_p("d")
    assert DocumentConverter.html_to_markdown(html) == "```\na\nb\n```\n\nbetween\n\n```\nc\nd\n```"


def test_blank_code_line_is_empty_not_a_space() -> None:
    html = _mono_p("a") + _mono_p("&nbsp;") + _mono_p("b")
    assert DocumentConverter.html_to_markdown(html) == "```\na\n\nb\n```"


def test_orphan_language_marker_does_not_open_a_live_fence() -> None:
    out = DocumentConverter.html_to_markdown("<p>```py</p><p>hello</p>")
    assert not any(line.startswith("```") for line in out.split("\n"))
    assert "hello" in out
