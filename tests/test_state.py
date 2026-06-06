"""Unit tests for SyncState, MappingState, and sha256 helpers."""

import json
import os

import pytest

from docspan.core.state import MappingState, SyncState, sha256_of_content, sha256_of_file

# ─────────────────────────────────────────────────────────────────────────────
# sha256 helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_sha256_of_content_deterministic() -> None:
    assert sha256_of_content("hello") == sha256_of_content("hello")


def test_sha256_of_content_different_inputs() -> None:
    assert sha256_of_content("hello") != sha256_of_content("world")


def test_sha256_of_content_is_hex_64_chars() -> None:
    digest = sha256_of_content("test content")
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


def test_sha256_of_content_empty_string() -> None:
    digest = sha256_of_content("")
    assert len(digest) == 64


def test_sha256_of_file_matches_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "file.txt"
    f.write_text("file content", encoding="utf-8")
    assert sha256_of_file(str(f)) == sha256_of_content("file content")


# ─────────────────────────────────────────────────────────────────────────────
# MappingState
# ─────────────────────────────────────────────────────────────────────────────

def _make_entry(**overrides: object) -> MappingState:
    defaults = dict(
        doc_id="doc-123",
        backend="confluence",
        last_synced_at="2024-01-01T00:00:00+00:00",
        base_hash="abc123",
        remote_version="5",
        local_hash="def456",
    )
    defaults.update(overrides)  # type: ignore[arg-type]
    return MappingState(**defaults)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# SyncState
# ─────────────────────────────────────────────────────────────────────────────

def test_state_load_missing_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    state = SyncState.load(str(tmp_path / "nonexistent.json"))
    assert state.mappings == {}


def test_state_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "state.json")
    state = SyncState()
    entry = _make_entry()
    state.update("foo.md", entry)
    state.save(path)

    loaded = SyncState.load(path)
    assert loaded.get("foo.md") == entry


def test_state_save_multiple_entries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "state.json")
    state = SyncState()
    state.update("a.md", _make_entry(doc_id="a"))
    state.update("b.md", _make_entry(doc_id="b"))
    state.save(path)

    loaded = SyncState.load(path)
    assert loaded.get("a.md").doc_id == "a"  # type: ignore[union-attr]
    assert loaded.get("b.md").doc_id == "b"  # type: ignore[union-attr]


def test_state_get_missing_key() -> None:
    state = SyncState()
    assert state.get("nope.md") is None


def test_state_update_overwrites() -> None:
    state = SyncState()
    state.update("x.md", _make_entry(remote_version="1"))
    state.update("x.md", _make_entry(remote_version="2"))
    assert state.get("x.md").remote_version == "2"  # type: ignore[union-attr]


def test_state_save_is_atomic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The .tmp file must not persist after a successful save."""
    path = str(tmp_path / "state.json")
    SyncState().save(path)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_state_file_is_valid_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "state.json")
    state = SyncState()
    state.update("doc.md", _make_entry())
    state.save(path)

    with open(path) as fh:
        data = json.load(fh)
    assert "mappings" in data
    assert "doc.md" in data["mappings"]
