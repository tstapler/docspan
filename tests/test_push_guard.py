"""Push guard: refuse to overwrite remote edits made since the last sync."""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from docspan.cli.main import app
from docspan.config import Mapping, MarkgateConfig
from docspan.core.orchestrator import orchestrate_push, record_state
from docspan.core.state import SyncState
from tests.test_orchestrator import FakeBackend

runner = CliRunner()


def _synced(tmp_path, base="base\n", version="rev-A"):
    local = tmp_path / "doc.md"
    local.write_text(base, encoding="utf-8")
    state_dir, state_path = str(tmp_path), str(tmp_path / ".markgate-state.json")
    state = SyncState()
    record_state(state, state_path, state_dir, str(local), "doc-123", "fake", base, version)
    mapping = Mapping(local=str(local), backend="fake", remote_id="doc-123")
    return mapping, state, state_dir, state_path


def test_push_guard_blocks_on_remote_change(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-B", remote_content="base\nmanual edit\n")
    outcome = orchestrate_push(mapping, backend, state, state_dir, state_path)
    assert outcome.result.status == "blocked"
    assert "+manual edit" in outcome.result.message
    assert backend.pushed == []
    assert SyncState.load(state_path).get(mapping.local).remote_version == "rev-A"


def test_push_guard_force_proceeds(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-B")
    outcome = orchestrate_push(mapping, backend, state, state_dir, state_path, force=True)
    assert outcome.result.status == "ok"
    assert backend.pushed


def test_push_guard_skipped_without_recorded_version(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path, version="")
    backend = FakeBackend(remote_version="rev-B")
    assert orchestrate_push(mapping, backend, state, state_dir, state_path).result.status == "ok"


def test_push_records_remote_version(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-A")

    def push(local_path, doc_id, **kw):
        backend.remote_version = "rev-C"  # the write advances the revision
        return FakeBackend.push(backend, local_path, doc_id, **kw)

    backend.push = push  # type: ignore[method-assign]
    outcome = orchestrate_push(mapping, backend, state, state_dir, state_path)
    assert outcome.result.status == "ok" and outcome.state_saved
    assert SyncState.load(state_path).get(mapping.local).remote_version == "rev-C"


def test_failed_push_does_not_update_remote_version(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-A", push_status="error")
    orchestrate_push(mapping, backend, state, state_dir, state_path)
    assert SyncState.load(state_path).get(mapping.local).remote_version == "rev-A"


def test_second_tab_of_same_doc_not_blocked_by_first_tab_push(tmp_path):
    a, state, state_dir, state_path = _synced(tmp_path)
    b_local = tmp_path / "b.md"
    b_local.write_text("b\n", encoding="utf-8")
    record_state(state, state_path, state_dir, str(b_local), "doc-123", "fake", "b\n", "rev-A")
    b = Mapping(local=str(b_local), backend="fake", remote_id="doc-123")
    backend = FakeBackend(remote_version="rev-A")

    def push(local_path, doc_id, **kw):
        backend.remote_version = "rev-C"
        return FakeBackend.push(backend, local_path, doc_id, **kw)

    backend.push = push  # type: ignore[method-assign]
    assert orchestrate_push(a, backend, state, state_dir, state_path).result.status == "ok"
    assert orchestrate_push(b, backend, state, state_dir, state_path).result.status == "ok"


def test_cli_push_exits_nonzero_and_prints_diff(tmp_path):
    mapping, _state, _d, _p = _synced(tmp_path)
    cfg = tmp_path / "docspan.yaml"
    cfg.write_text("mappings: []\n", encoding="utf-8")
    backend = FakeBackend(remote_version="rev-B", remote_content="base\nmanual edit\n")
    with patch("docspan.cli.main.load_config", return_value=MarkgateConfig(mappings=[mapping])), \
         patch("docspan.cli.main._get_backend", return_value=backend):
        result = runner.invoke(app, ["push", "--config", str(cfg)])
        forced = runner.invoke(app, ["push", "--force", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "Remote-only changes" in result.output and "+manual edit" in result.output
    assert forced.exit_code == 0


def test_force_push_advances_sibling_tab_versions(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    sibling = tmp_path / "other.md"
    sibling.write_text("s\n", encoding="utf-8")
    record_state(state, state_path, state_dir, str(sibling), "doc-123", "fake", "s\n", "rev-A")
    backend = FakeBackend(remote_version="rev-A")

    def push(local_path, doc_id, **kw):
        backend.remote_version = "rev-C"
        return FakeBackend.push(backend, local_path, doc_id, **kw)

    backend.push = push  # type: ignore[method-assign]
    orchestrate_push(mapping, backend, state, state_dir, state_path, force=True)
    assert state.get(str(sibling)).remote_version == "rev-C"


def test_post_push_version_failure_keeps_prior_version_so_guard_stays_armed(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-A")
    calls = {"n": 0}

    def get_version(doc_id):
        calls["n"] += 1
        if calls["n"] > 1:  # guard read succeeds, post-push read fails
            raise RuntimeError("boom")
        return "rev-A"

    backend.get_remote_version = get_version  # type: ignore[method-assign]
    assert orchestrate_push(mapping, backend, state, state_dir, state_path).result.status == "ok"
    assert state.get(mapping.local).remote_version == "rev-A"


def test_blocked_message_explains_failed_diff(tmp_path):
    mapping, state, state_dir, state_path = _synced(tmp_path)
    backend = FakeBackend(remote_version="rev-B")

    def bad_pull(*a, **kw):
        raise RuntimeError("network down")

    backend.pull = bad_pull  # type: ignore[method-assign]
    outcome = orchestrate_push(mapping, backend, state, state_dir, state_path)
    assert outcome.result.status == "blocked"
    assert "could not fetch remote content for diff: network down" in outcome.result.message
