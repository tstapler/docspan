"""Tests for the shared `core.atomic_dir.atomic_replace_dir` helper.

Extracted out of `core/orchestrator.py` and
`backends/google_docs/backend.py`, which previously each carried a verbatim
copy (one string-path, one pathlib) of this same directory-swap logic.
"""

from __future__ import annotations

import pathlib

import pytest

from docspan.core import atomic_dir as atomic_dir_module
from docspan.core.atomic_dir import atomic_replace_dir


def test_atomic_replace_dir_swaps_fresh_dir_into_empty_target(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target_dir = tmp_path / "target"
    tmp_dir = tmp_path / "staging"
    tmp_dir.mkdir()
    (tmp_dir / "fresh.md").write_text("fresh\n", encoding="utf-8")

    atomic_replace_dir(str(tmp_dir), str(target_dir))

    assert (target_dir / "fresh.md").read_text(encoding="utf-8") == "fresh\n"
    assert not tmp_dir.exists()


def test_atomic_replace_dir_swaps_over_existing_target_and_cleans_up_old(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "existing.md").write_text("old\n", encoding="utf-8")
    tmp_dir = tmp_path / "staging"
    tmp_dir.mkdir()
    (tmp_dir / "fresh.md").write_text("fresh\n", encoding="utf-8")

    atomic_replace_dir(str(tmp_dir), str(target_dir))

    assert (target_dir / "fresh.md").read_text(encoding="utf-8") == "fresh\n"
    assert not (target_dir / "existing.md").exists()
    # No stray `.old.` sibling directory left behind.
    leftovers = [p for p in tmp_path.iterdir() if p.name not in {"target"}]
    assert leftovers == []


def test_atomic_replace_dir_accepts_pathlib_paths(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target_dir = tmp_path / "target"
    tmp_dir = tmp_path / "staging"
    tmp_dir.mkdir()
    (tmp_dir / "fresh.md").write_text("fresh\n", encoding="utf-8")

    atomic_replace_dir(tmp_dir, target_dir)

    assert (target_dir / "fresh.md").read_text(encoding="utf-8") == "fresh\n"


def test_atomic_replace_dir_restores_original_on_swap_failure(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "existing.md").write_text("old\n", encoding="utf-8")
    tmp_dir = tmp_path / "staging"
    tmp_dir.mkdir()
    (tmp_dir / "fresh.md").write_text("fresh\n", encoding="utf-8")

    real_replace = atomic_dir_module.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated swap-in failure")
        return real_replace(src, dst)  # move-aside (1st) and restore (3rd) succeed

    monkeypatch.setattr(atomic_dir_module.os, "replace", _flaky_replace)

    with pytest.raises(OSError):
        atomic_replace_dir(str(tmp_dir), str(target_dir))

    # Restore succeeded (the third os.replace call, moving old_dir back),
    # so the original content is back in place.
    assert (target_dir / "existing.md").read_text(encoding="utf-8") == "old\n"


def test_atomic_replace_dir_logs_both_failures_on_double_failure(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "existing.md").write_text("old\n", encoding="utf-8")
    tmp_dir = tmp_path / "staging"
    tmp_dir.mkdir()
    (tmp_dir / "fresh.md").write_text("fresh\n", encoding="utf-8")

    real_replace = atomic_dir_module.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_replace(src, dst)  # move target aside -- let it succeed
        raise OSError("simulated failure")  # swap-in AND restore both fail

    monkeypatch.setattr(atomic_dir_module.os, "replace", _flaky_replace)

    with caplog.at_level("ERROR", logger=atomic_dir_module.logger.name):
        with pytest.raises(OSError):
            atomic_replace_dir(str(tmp_dir), str(target_dir))

    messages = [record.getMessage() for record in caplog.records]
    assert any("Failed to swap" in m for m in messages)
    assert any("also failed" in m for m in messages)
