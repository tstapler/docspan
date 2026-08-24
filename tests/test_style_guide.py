"""Regression test for Epic 5 (gdocs-native-blockquotes): the "don't use `>`
blockquotes" bullet is removed from GOOGLE_DOCS_STYLE_GUIDE now that push
emits native blockquote styling instead of literal `>`-prefixed text.
"""

from __future__ import annotations

from docspan.style_guide import GOOGLE_DOCS_STYLE_GUIDE


def test_google_docs_style_guide_should_not_mention_blockquotes_when_rendered() -> None:
    assert "blockquote" not in GOOGLE_DOCS_STYLE_GUIDE.lower()
    assert "`>`" not in GOOGLE_DOCS_STYLE_GUIDE
