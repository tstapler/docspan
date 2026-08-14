"""Tests for push/pull orchestration logic using a fake backend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from docspan.backends.base import Backend, CreateResult, PullResult, PushResult
from docspan.config import Mapping
from docspan.core.orchestrator import (
    get_base_content,
    orchestrate_pull,
    orchestrate_push,
    save_base_content,
)
from docspan.core.state import MappingState, SyncState, sha256_of_content

# ─────────────────────────────────────────────────────────────────────────────
# Test double
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FakeBackend(Backend):
    """In-memory backend for testing — no network, no credentials."""

    name: str = "fake"
    remote_version: str = "v1"
    remote_content: str = "remote content\n"
    push_status: str = "ok"
    pull_status: str = "ok"
    pushed: list = field(default_factory=list)
    pushed_sectioned: list = field(default_factory=list)
    pull_calls: list = field(default_factory=list)
    pull_sectioned_calls: list = field(default_factory=list)
    # filename -> content, written into the temp dir by pull_sectioned()
    section_files: dict = field(default_factory=dict)

    def push(self, local_path: str, doc_id: str, **kwargs: object) -> PushResult:
        self.pushed.append((local_path, doc_id))
        return PushResult(status=self.push_status, doc_id=doc_id, url="https://example.com")  # type: ignore[arg-type]

    def push_sectioned(self, directory: str, doc_id: str, **kwargs: object) -> PushResult:
        self.pushed_sectioned.append((directory, doc_id))
        return PushResult(status=self.push_status, doc_id=doc_id, url="https://example.com")  # type: ignore[arg-type]

    def pull(self, doc_id: str, local_path: str, **kwargs: object) -> PullResult:
        self.pull_calls.append((doc_id, local_path))
        if self.pull_status in ("ok", "warning"):
            with open(local_path, "w", encoding="utf-8") as fh:
                fh.write(self.remote_content)
        return PullResult(status=self.pull_status, doc_id=doc_id, local_path=local_path)  # type: ignore[arg-type]

    def pull_sectioned(self, directory: str, doc_id: str, **kwargs: object) -> PullResult:
        self.pull_sectioned_calls.append((directory, doc_id))
        if self.pull_status in ("ok", "warning"):
            for filename, content in self.section_files.items():
                with open(os.path.join(directory, filename), "w", encoding="utf-8") as fh:
                    fh.write(content)
        return PullResult(status=self.pull_status, doc_id=doc_id, local_path=directory)  # type: ignore[arg-type]

    def get_remote_version(self, doc_id: str) -> str:
        return self.remote_version

    def create(self, title: str, **kwargs: object) -> CreateResult:
        return CreateResult(doc_id="new-doc-1", title=title, url="https://example.com/new-doc")

    def auth_setup(self) -> None:
        pass

    def validate_config(self) -> None:
        pass


def _mapping(local: str, remote_id: str = "doc-123") -> Mapping:
    return Mapping(local=local, backend="fake", remote_id=remote_id)


def _synced_state(
    tmp_path,  # type: ignore[no-untyped-def]
    local: str,
    content: str,
    remote_version: str = "v1",
) -> tuple[SyncState, str]:
    state = SyncState()
    state_path = str(tmp_path / ".markgate-state.json")
    base_hash = save_base_content(str(tmp_path), content)
    state.update(
        local,
        MappingState(
            doc_id="doc-123",
            backend="fake",
            last_synced_at="2024-01-01T00:00:00+00:00",
            base_hash=base_hash,
            remote_version=remote_version,
            local_hash=sha256_of_content(content),
        ),
    )
    return state, state_path


# ─────────────────────────────────────────────────────────────────────────────
# orchestrate_push
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratePush:
    def test_push_ok_records_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_push(
            _mapping(str(local)), FakeBackend(), state, str(tmp_path), state_path
        )

        assert outcome.result.status == "ok"
        assert outcome.state_saved
        entry = state.get(str(local))
        assert entry is not None
        assert entry.remote_version == "v1"
        assert entry.backend == "fake"

    def test_push_error_does_not_save_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_push(
            _mapping(str(local)),
            FakeBackend(push_status="error"),
            state,
            str(tmp_path),
            state_path,
        )

        assert outcome.result.status == "error"
        assert not outcome.state_saved
        assert state.get(str(local)) is None

    def test_push_warning_records_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Regression test: a push that succeeds with status='warning' (e.g. multi-tab
        doc without a configured tab_id) must still record sync state, otherwise the
        next pull sees stale state and does a spurious/lossy re-pull."""
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_push(
            _mapping(str(local)),
            FakeBackend(push_status="warning"),
            state,
            str(tmp_path),
            state_path,
        )

        assert outcome.result.status == "warning"
        assert outcome.state_saved
        entry = state.get(str(local))
        assert entry is not None
        assert entry.remote_version == "v1"

    def test_push_invokes_backend_with_correct_args(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("x\n", encoding="utf-8")
        backend = FakeBackend()
        state_path = str(tmp_path / ".markgate-state.json")

        orchestrate_push(_mapping(str(local), "doc-456"), backend, SyncState(), str(tmp_path), state_path)

        assert backend.pushed == [(str(local), "doc-456")]


# ─────────────────────────────────────────────────────────────────────────────
# orchestrate_pull
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratePull:
    def test_first_sync_writes_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_pull(
            _mapping(str(local)), FakeBackend(remote_content="hello\n"), state, str(tmp_path), state_path
        )

        assert outcome.action == "first-sync"
        assert local.read_text(encoding="utf-8") == "hello\n"
        assert state.get(str(local)) is not None

    def test_up_to_date_skips_pull(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        content = "unchanged\n"
        local = tmp_path / "doc.md"
        local.write_text(content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), content)
        backend = FakeBackend(remote_version="v1", remote_content="different\n")

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "up-to-date"
        assert local.read_text(encoding="utf-8") == content  # unchanged

    def test_fast_forward_updates_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        content = "old\n"
        local = tmp_path / "doc.md"
        local.write_text(content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), content, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content="new remote\n")

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "fast-forward"
        assert local.read_text(encoding="utf-8") == "new remote\n"
        assert state.get(str(local)).remote_version == "v2"  # type: ignore[union-attr]

    def test_local_only_skips_pull(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        content = "synced\n"
        local = tmp_path / "doc.md"
        local.write_text("locally modified\n", encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), content, remote_version="v1")
        backend = FakeBackend(remote_version="v1")

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "local-only"
        assert local.read_text(encoding="utf-8") == "locally modified\n"

    def test_three_way_merge_clean(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        base = "line1\nline2\nline3\n"
        local_content = "line1\nlocal edit\nline3\n"
        remote_content = "line1\nline2\nline3\nremote addition\n"

        local = tmp_path / "doc.md"
        local.write_text(local_content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), base, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content=remote_content)

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "merged"
        assert not outcome.has_conflicts
        merged = local.read_text(encoding="utf-8")
        assert "local edit" in merged
        assert "remote addition" in merged

    def test_three_way_merge_with_conflicts(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        base = "same line\n"
        local_content = "ours version\n"
        remote_content = "theirs version\n"

        local = tmp_path / "doc.md"
        local.write_text(local_content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), base, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content=remote_content)

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "merged"
        assert outcome.has_conflicts
        assert outcome.conflict_count == 1
        assert "<<<<<<< ours" in local.read_text(encoding="utf-8")

    def test_first_sync_warning_records_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Regression test: pull_status='warning' (ambiguous multi-tab doc) must still
        record sync state on first sync, not just 'ok'."""
        local = tmp_path / "doc.md"
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_pull(
            _mapping(str(local)),
            FakeBackend(remote_content="hello\n", pull_status="warning"),
            state,
            str(tmp_path),
            state_path,
        )

        assert outcome.action == "first-sync"
        assert outcome.result.status == "warning"
        assert local.read_text(encoding="utf-8") == "hello\n"
        assert state.get(str(local)) is not None

    def test_fast_forward_warning_records_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Regression test: pull_status='warning' during a fast-forward pull must still
        record sync state, otherwise the next pull re-does the same fast-forward."""
        content = "old\n"
        local = tmp_path / "doc.md"
        local.write_text(content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), content, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content="new remote\n", pull_status="warning")

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "fast-forward"
        assert outcome.result.status == "warning"
        assert local.read_text(encoding="utf-8") == "new remote\n"
        assert state.get(str(local)).remote_version == "v2"  # type: ignore[union-attr]

    def test_merge_pull_warning_completes_merge_not_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Regression test: a warning status on the 'theirs' pull inside a three-way
        merge must still complete the merge (treated like 'ok'), not fall through to
        the error branch."""
        base = "line1\nline2\nline3\n"
        local_content = "line1\nlocal edit\nline3\n"
        remote_content = "line1\nline2\nline3\nremote addition\n"

        local = tmp_path / "doc.md"
        local.write_text(local_content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), base, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content=remote_content, pull_status="warning")

        outcome = orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        assert outcome.action == "merged"
        assert not outcome.has_conflicts
        merged = local.read_text(encoding="utf-8")
        assert "local edit" in merged
        assert "remote addition" in merged

    def test_orig_file_created_before_merge(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        base = "base\n"
        local_content = "local\n"

        local = tmp_path / "doc.md"
        local.write_text(local_content, encoding="utf-8")
        state, state_path = _synced_state(tmp_path, str(local), base, remote_version="v1")
        backend = FakeBackend(remote_version="v2", remote_content="remote\n")

        orchestrate_pull(_mapping(str(local)), backend, state, str(tmp_path), state_path)

        orig = tmp_path / "doc.md.orig"
        assert orig.exists()
        assert orig.read_text(encoding="utf-8") == local_content


# ─────────────────────────────────────────────────────────────────────────────
# Base content store
# ─────────────────────────────────────────────────────────────────────────────

class TestBaseStore:
    def test_save_and_get_roundtrip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        content = "some content\n"
        sha = save_base_content(str(tmp_path), content)
        assert get_base_content(str(tmp_path), sha) == content

    def test_save_is_idempotent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        content = "idempotent\n"
        sha1 = save_base_content(str(tmp_path), content)
        sha2 = save_base_content(str(tmp_path), content)
        assert sha1 == sha2

    def test_get_missing_returns_empty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert get_base_content(str(tmp_path), "deadbeef" * 8) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Sectioned dispatch (gdocs-sectioned-sync Epic 5)
# ─────────────────────────────────────────────────────────────────────────────

def _sectioned_mapping(local: str, remote_id: str = "doc-123") -> Mapping:
    return Mapping(
        local=local, backend="fake", remote_id=remote_id,
        sectioned=True, split_level="HEADING_1",
    )


class TestOrchestrateSectioned:
    def test_orchestrate_pull_should_call_pull_sectioned_when_mapping_sectioned_is_true(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        directory = tmp_path / "big-doc"
        mapping = _sectioned_mapping(str(directory))
        backend = FakeBackend(section_files={"01-intro.md": "intro\n"})
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_pull(mapping, backend, state, str(tmp_path), state_path)

        assert len(backend.pull_sectioned_calls) == 1
        assert backend.pull_sectioned_calls[0][1] == "doc-123"
        assert not backend.pull_calls
        assert outcome.action == "first-sync"
        assert (directory / "01-intro.md").read_text(encoding="utf-8") == "intro\n"
        assert state.get(str(directory / "01-intro.md")) is not None

    def test_orchestrate_pull_should_call_legacy_pull_when_mapping_sectioned_is_false(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        mapping = _mapping(str(local))
        backend = FakeBackend(remote_content="hello\n")
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_pull(mapping, backend, state, str(tmp_path), state_path)

        assert backend.pull_calls == [("doc-123", str(local))]
        assert not backend.pull_sectioned_calls
        assert outcome.action == "first-sync"
        assert local.read_text(encoding="utf-8") == "hello\n"

    def test_orchestrate_pull_should_merge_each_section_file_independently(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        directory = tmp_path / "big-doc"
        directory.mkdir()
        mapping = _sectioned_mapping(str(directory))
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")
        state_dir = str(tmp_path)

        # Section A: only the remote side changed -> fast-forward (silent overwrite).
        a_local_path = str(directory / "01-intro.md")
        (directory / "01-intro.md").write_text("unchanged\n", encoding="utf-8")
        a_base_hash = save_base_content(state_dir, "unchanged\n")
        state.update(a_local_path, MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2024-01-01T00:00:00+00:00",
            base_hash=a_base_hash, remote_version="v1",
            local_hash=sha256_of_content("unchanged\n"),
        ))

        # Section B: both sides changed -> clean three-way merge.
        base_b = "line1\nline2\nline3\n"
        local_b = "line1\nlocal edit\nline3\n"
        remote_b = "line1\nline2\nline3\nremote addition\n"
        b_local_path = str(directory / "02-body.md")
        (directory / "02-body.md").write_text(local_b, encoding="utf-8")
        b_base_hash = save_base_content(state_dir, base_b)
        state.update(b_local_path, MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2024-01-01T00:00:00+00:00",
            base_hash=b_base_hash, remote_version="v1",
            # local_hash records what was synced *last time* (the base), not the
            # current on-disk content — that mismatch is what marks this section
            # "locally changed" and triggers the three-way merge below.
            local_hash=sha256_of_content(base_b),
        ))

        backend = FakeBackend(
            section_files={"01-intro.md": "new remote A\n", "02-body.md": remote_b},
        )

        outcome = orchestrate_pull(mapping, backend, state, state_dir, state_path)

        assert outcome.action == "merged"
        assert not outcome.has_conflicts

        # Section A was fast-forwarded: a plain overwrite, no merge markers.
        content_a = (directory / "01-intro.md").read_text(encoding="utf-8")
        assert content_a == "new remote A\n"
        assert "<<<<<<<" not in content_a

        # Section B was actually merged: both edits present.
        content_b = (directory / "02-body.md").read_text(encoding="utf-8")
        assert "local edit" in content_b
        assert "remote addition" in content_b

    def test_orchestrate_push_should_call_push_sectioned_when_mapping_sectioned_is_true(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        directory = tmp_path / "big-doc"
        directory.mkdir()
        (directory / "01-intro.md").write_text("intro\n", encoding="utf-8")
        (directory / "02-body.md").write_text("body\n", encoding="utf-8")
        mapping = _sectioned_mapping(str(directory))
        backend = FakeBackend()
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        outcome = orchestrate_push(mapping, backend, state, str(tmp_path), state_path)

        assert backend.pushed_sectioned == [(str(directory), "doc-123")]
        assert not backend.pushed
        assert outcome.state_saved
        assert state.get(str(directory / "01-intro.md")) is not None
        assert state.get(str(directory / "02-body.md")) is not None

    def test_orchestrate_push_should_call_legacy_push_when_mapping_sectioned_is_false(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("hello\n", encoding="utf-8")
        mapping = _mapping(str(local))
        backend = FakeBackend()
        state = SyncState()
        state_path = str(tmp_path / ".markgate-state.json")

        orchestrate_push(mapping, backend, state, str(tmp_path), state_path)

        assert backend.pushed == [(str(local), "doc-123")]
        assert not backend.pushed_sectioned
