"""Tests for conflict resolution helpers in cli/main.py."""

from __future__ import annotations

import pytest
import typer

from docspan.backends.base import PullResult
from docspan.cli.main import _resolve_local, _resolve_merged, _resolve_remote
from docspan.core.orchestrator import save_base_content
from docspan.core.state import MappingState, SyncState, sha256_of_content

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_entry(
    file: str,
    state_dir: str,
    base_content: str = "base content\n",
    remote_version: str = "v1",
) -> MappingState:
    base_hash = save_base_content(state_dir, base_content)
    return MappingState(
        doc_id="doc-123",
        backend="fake",
        last_synced_at="2024-01-01T00:00:00+00:00",
        base_hash=base_hash,
        remote_version=remote_version,
        local_hash=sha256_of_content(base_content),
    )


class FakeBackend:
    name = "fake"

    def pull(self, doc_id: str, local_path: str, **kwargs: object) -> PullResult:
        with open(local_path, "w", encoding="utf-8") as fh:
            fh.write("remote resolved\n")
        return PullResult(status="ok", doc_id=doc_id, local_path=local_path)

    def get_remote_version(self, doc_id: str) -> str:
        return "v2"


class FakeFailingBackend:
    name = "fake_failing"

    def pull(self, doc_id: str, local_path: str, **kwargs: object) -> PullResult:
        return PullResult(status="error", doc_id=doc_id, local_path=local_path, message="network error")

    def get_remote_version(self, doc_id: str) -> str:
        raise RuntimeError("unreachable")


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_remote
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRemote:
    def test_success_writes_remote_content(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("conflicted\n", encoding="utf-8")
        orig = tmp_path / "doc.md.orig"
        orig.write_text("orig\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        _resolve_remote(str(local), entry, FakeBackend(), state, state_path, str(tmp_path))

        assert local.read_text(encoding="utf-8") == "remote resolved\n"
        assert not orig.exists()
        assert state.get(str(local)) is not None

    def test_removes_orig_on_success(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("conflicted\n", encoding="utf-8")
        orig = tmp_path / "doc.md.orig"
        orig.write_text("backup\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        _resolve_remote(str(local), entry, FakeBackend(), state, state_path, str(tmp_path))

        assert not orig.exists()

    def test_raises_on_backend_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("conflicted\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        with pytest.raises(typer.Exit):
            _resolve_remote(str(local), entry, FakeFailingBackend(), state, state_path, str(tmp_path))


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_local
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveLocal:
    def test_restores_from_orig(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("conflicted content\n", encoding="utf-8")
        orig = tmp_path / "doc.md.orig"
        orig.write_text("pre-conflict local\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        _resolve_local(str(local), entry, state, state_path, str(tmp_path))

        assert local.read_text(encoding="utf-8") == "pre-conflict local\n"
        assert not orig.exists()
        assert state.get(str(local)) is not None

    def test_restores_from_base_when_no_orig(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        base_content = "base content\n"
        local = tmp_path / "doc.md"
        local.write_text("conflicted\n", encoding="utf-8")
        # no .orig file

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path), base_content=base_content)

        _resolve_local(str(local), entry, state, state_path, str(tmp_path))

        assert local.read_text(encoding="utf-8") == base_content

    def test_raises_when_no_orig_and_no_base(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("conflicted\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        # entry with a base_hash that doesn't exist on disk
        entry = MappingState(
            doc_id="doc-123",
            backend="fake",
            last_synced_at="2024-01-01T00:00:00+00:00",
            base_hash="deadbeef" * 8,
            remote_version="v1",
            local_hash="abc",
        )

        with pytest.raises(typer.Exit):
            _resolve_local(str(local), entry, state, state_path, str(tmp_path))


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_merged
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveMerged:
    def test_success_on_clean_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("clean resolved content\n", encoding="utf-8")

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        _resolve_merged(str(local), entry, state, state_path, str(tmp_path))

        assert state.get(str(local)) is not None

    def test_raises_when_conflict_markers_remain(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text(
            "<<<<<<< ours\nours\n=======\ntheirs\n>>>>>>> theirs\n",
            encoding="utf-8",
        )

        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(local), str(tmp_path))

        with pytest.raises(typer.Exit):
            _resolve_merged(str(local), entry, state, state_path, str(tmp_path))

    def test_raises_when_file_missing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        entry = _make_entry(str(tmp_path / "ghost.md"), str(tmp_path))

        with pytest.raises(typer.Exit):
            _resolve_merged(
                str(tmp_path / "ghost.md"), entry, state, state_path, str(tmp_path)
            )
