"""Tests for docspan.backends.google_docs.checkbox_state."""
from docspan.backends.google_docs.checkbox_state import (
    extract_checkbox_states,
    patch_checkbox_lines,
)


def test_extract_checkbox_states_reads_checked_and_unchecked() -> None:
    md = "- [ ] Native cb one\n- [x] Native cb two\n"
    assert extract_checkbox_states(md) == [False, True]


def test_extract_checkbox_states_ignores_plain_bullets() -> None:
    md = "* Disc bullet plain\n- [x] real checkbox\n"
    assert extract_checkbox_states(md) == [True]


def test_extract_checkbox_states_ignores_escaped_literal_brackets() -> None:
    md = r"* \[ \] Disc bullet literal" + "\n- [x] real checkbox\n"
    assert extract_checkbox_states(md) == [True]


def test_extract_checkbox_states_ignores_mid_line_brackets() -> None:
    md = "- some text [x] not a checkbox\n- [ ] actual checkbox\n"
    assert extract_checkbox_states(md) == [False]


def test_extract_checkbox_states_preserves_document_order_across_nesting() -> None:
    md = "- [x] top\n  - [ ] nested\n- [x] top two\n"
    assert extract_checkbox_states(md) == [True, False, True]


def test_patch_checkbox_lines_rewrites_matching_lines() -> None:
    markdown = "- ●  buy milk\n- ●  buy eggs\n"
    patched, all_found = patch_checkbox_lines(
        markdown, [("buy milk", False), ("buy eggs", True)]
    )
    assert all_found is True
    assert "- [ ] buy milk" in patched
    assert "- [x] buy eggs" in patched


def test_patch_checkbox_lines_preserves_indentation() -> None:
    markdown = "- top\n  - ●  nested item\n"
    patched, all_found = patch_checkbox_lines(markdown, [("nested item", True)])
    assert all_found is True
    assert "  - [x] nested item" in patched


def test_patch_checkbox_lines_matches_by_document_order_not_just_content() -> None:
    markdown = "- ●  same text\n- ●  same text\n"
    patched, all_found = patch_checkbox_lines(
        markdown, [("same text", False), ("same text", True)]
    )
    assert all_found is True
    lines = patched.splitlines()
    assert lines[0] == "- [ ] same text"
    assert lines[1] == "- [x] same text"


def test_patch_checkbox_lines_reports_not_all_found_when_line_missing() -> None:
    markdown = "- ●  buy milk\n"
    patched, all_found = patch_checkbox_lines(
        markdown, [("buy milk", False), ("buy eggs", True)]
    )
    assert all_found is False


def test_patch_checkbox_lines_skips_non_list_lines() -> None:
    markdown = "# Heading\nSome prose about buy milk.\n- ●  buy milk\n"
    patched, all_found = patch_checkbox_lines(markdown, [("buy milk", False)])
    assert all_found is True
    assert "- [ ] buy milk" in patched
    assert "Some prose about buy milk." in patched
