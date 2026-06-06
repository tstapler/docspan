"""Unit tests for three_way_merge — pure function, no I/O."""

import pytest

from docspan.core.merge import MergeResult, three_way_merge


def test_no_changes_returns_original() -> None:
    content = "line1\nline2\nline3\n"
    result = three_way_merge(content, content, content)
    assert not result.has_conflicts
    assert result.conflict_count == 0
    assert result.merged == content


def test_only_ours_changed() -> None:
    base = "line1\nline2\nline3\n"
    ours = "line1\nours edit\nline3\n"
    result = three_way_merge(base, base, ours)
    assert not result.has_conflicts
    assert "ours edit" in result.merged


def test_only_theirs_changed() -> None:
    base = "line1\nline2\nline3\n"
    theirs = "line1\ntheirs edit\nline3\n"
    result = three_way_merge(base, theirs, base)
    assert not result.has_conflicts
    assert "theirs edit" in result.merged


def test_clean_merge_both_sides() -> None:
    base = "line1\nline2\nline3\n"
    ours = "line1\nours edit\nline3\n"
    theirs = "line1\nline2\nline3\nappended\n"
    result = three_way_merge(base, theirs, ours)
    assert not result.has_conflicts
    assert "ours edit" in result.merged
    assert "appended" in result.merged


def test_conflict_on_same_line() -> None:
    base = "shared line\n"
    ours = "ours changed\n"
    theirs = "theirs changed\n"
    result = three_way_merge(base, theirs, ours)
    assert result.has_conflicts
    assert result.conflict_count == 1
    assert "<<<<<<< ours" in result.merged
    assert "=======" in result.merged
    assert ">>>>>>> theirs" in result.merged


def test_multiple_conflicts() -> None:
    base = "a\nb\nc\n"
    ours = "A\nb\nC\n"
    theirs = "a_\nb\nc_\n"
    result = three_way_merge(base, theirs, ours)
    assert result.has_conflicts
    assert result.conflict_count == 2


def test_empty_base_same_content_no_conflict() -> None:
    content = "some content\n"
    result = three_way_merge("", content, content)
    assert not result.has_conflicts


def test_empty_base_diverged_conflict() -> None:
    result = three_way_merge("", "theirs\n", "ours\n")
    assert result.has_conflicts


def test_merge_result_is_dataclass() -> None:
    result = three_way_merge("x\n", "x\n", "x\n")
    assert isinstance(result, MergeResult)
    assert isinstance(result.merged, str)
    assert isinstance(result.has_conflicts, bool)
    assert isinstance(result.conflict_count, int)
