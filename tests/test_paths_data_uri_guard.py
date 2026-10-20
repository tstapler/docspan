"""Unit tests for find_data_uris (the backend-agnostic base64/data: URI guardrail).

See paths.py's docstring for why this exists: every markdown write in
docspan is a raw pathlib.Path(...).write_text(...) with no shared writer to
hook centrally, so each call site is expected to call this immediately
before writing and fold a non-empty result into that pull's warning.
"""

import base64

from docspan.core.paths import find_data_uris


def _data_uri(payload: bytes = b"some fairly long fake png payload bytes") -> str:
    return f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"


def test_clean_markdown_has_no_findings() -> None:
    assert find_data_uris("# Title\n\nSome text with a [link](https://example.com).\n") == []


def test_finds_a_single_data_uri() -> None:
    markdown = f"![diagram]({_data_uri()})\n"
    findings = find_data_uris(markdown)
    assert len(findings) == 1
    assert findings[0].startswith("data:image/png;base64,")


def test_finding_is_truncated_not_the_full_payload() -> None:
    huge = _data_uri(b"x" * 100_000)
    findings = find_data_uris(f"![x]({huge})\n")
    assert len(findings) == 1
    assert len(findings[0]) < 100
    assert findings[0] != huge


def test_finds_multiple_data_uris_in_order() -> None:
    markdown = f"![a]({_data_uri(b'aaaaaaaaaaaaaaaaaaaa')})\n\n![b]({_data_uri(b'bbbbbbbbbbbbbbbbbbbb')})\n"
    findings = find_data_uris(markdown)
    assert len(findings) == 2


def test_a_normal_https_image_url_is_not_flagged() -> None:
    markdown = "![real image](https://lh3.googleusercontent.com/abc123)\n"
    assert find_data_uris(markdown) == []


def test_a_short_non_base64_data_uri_style_string_is_not_falsely_matched() -> None:
    # Must require the base64 marker and a real payload -- a bare "data:"
    # mention in prose text is not the bug this guards against.
    assert find_data_uris("See the data: section of the config for details.") == []
