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
