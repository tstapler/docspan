"""Tests for Epic 5: the `migrate_sectioned()` orchestrator and its two CLI
surfaces (`migrate-sectioned` and `pull --to-sectioned`).

Split from `test_migration.py` (Epics 1-4's stage-by-stage unit tests) because
this file exercises the *pipeline as a whole* -- real git repos, a stubbed
Google Docs client, and (for the two CLI-surface tests) Typer's `CliRunner` --
rather than any single helper in isolation.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

from typer.testing import CliRunner

from docspan.backends.google_docs import migration as migration_module
from docspan.backends.google_docs.migration import (
    MigrationOutcome,
    _acquire_migration_lock,
    migrate_sectioned,
)
from docspan.cli.main import app
from docspan.config import Mapping, MarkgateConfig, load_config, save_config
from docspan.core.orchestrator import get_state_dir, get_state_path
from docspan.core.state import SyncState

runner = CliRunner()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures shared by every test below
# ─────────────────────────────────────────────────────────────────────────────


def _git(*args: str, cwd: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(repo_dir) -> None:  # type: ignore[no-untyped-def]
    repo_dir.mkdir(exist_ok=True)
    _git("init", cwd=str(repo_dir))
    _git("config", "user.email", "test@example.com", cwd=str(repo_dir))
    _git("config", "user.name", "Test", cwd=str(repo_dir))


def _head(repo_dir: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()


# Content shared by every "real pipeline" test: two headings, both bodies long
# enough for git's -C20% rename-detection heuristic to actually match
# (mirrors test_migration.py's test_commit_swap_preserves_history_via_git_log_follow).
_CONTENT = (
    "Intro body.\n\n# First\n\nBody one paragraph with enough text to survive "
    "similarity detection across the split.\n\n# Second\n\nBody two paragraph, "
    "likewise long enough for -C20% rename detection to find a match.\n"
)


def _make_para_element(text: str, style: str = "NORMAL_TEXT", heading_id: str | None = None) -> dict:
    paragraph_style: dict = {"namedStyleType": style}
    if heading_id is not None:
        paragraph_style["headingId"] = heading_id
    return {
        "startIndex": 1,
        "endIndex": 1 + len(text) + 1,
        "paragraph": {
            "paragraphStyle": paragraph_style,
            "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
        },
    }


def _doc_for_content() -> dict:
    """A Docs-API-shaped doc whose headings/text line up with `_CONTENT`."""
    return {
        "body": {
            "content": [
                _make_para_element("Intro body.", style="NORMAL_TEXT"),
                _make_para_element("First", style="HEADING_1", heading_id="h.abc123"),
                _make_para_element(
                    "Body one paragraph with enough text to survive similarity "
                    "detection across the split.",
                    style="NORMAL_TEXT",
                ),
                _make_para_element("Second", style="HEADING_1", heading_id="h.def456"),
                _make_para_element(
                    "Body two paragraph, likewise long enough for -C20% rename "
                    "detection to find a match.",
                    style="NORMAL_TEXT",
                ),
            ]
        }
    }


class _StubDocsClient:
    def __init__(self, doc: dict) -> None:
        self._doc = doc

    def get_document(self, doc_id: str, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
        return self._doc


@dataclass
class _StubMigrationBackend:
    """Minimal stand-in for `GoogleDocsBackend` -- `migrate_sectioned()` only
    ever touches `.client` and `.get_remote_version()`."""

    doc: dict
    remote_version: str = "v1"
    _client: Optional[_StubDocsClient] = None

    def _ensure_client(self) -> None:
        if self._client is None:
            self._client = _StubDocsClient(self.doc)

    @property
    def client(self) -> _StubDocsClient:
        self._ensure_client()
        assert self._client is not None
        return self._client

    def get_remote_version(self, doc_id: str) -> str:
        return self.remote_version


def _many_sections_content(n: int) -> str:
    """Local markdown with `n` `HEADING_1` sections, mirroring `_CONTENT`'s shape."""
    parts = ["Intro body.\n"]
    for i in range(n):
        parts.append(
            f"\n# Section {i}\n\nBody paragraph for section {i}, long enough to be "
            "a realistic chunk of migrated content rather than a one-word stub.\n"
        )
    return "".join(parts)


def _doc_for_many_sections(n: int) -> dict:
    """A Docs-API-shaped doc whose headings/text line up with `_many_sections_content(n)`."""
    elements = [_make_para_element("Intro body.", style="NORMAL_TEXT")]
    for i in range(n):
        elements.append(_make_para_element(f"Section {i}", style="HEADING_1", heading_id=f"h.{i}"))
        elements.append(
            _make_para_element(
                f"Body paragraph for section {i}, long enough to be a realistic "
                "chunk of migrated content rather than a one-word stub.",
                style="NORMAL_TEXT",
            )
        )
    return {"body": {"content": elements}}


def _repo_with_handbook(tmp_path):  # type: ignore[no-untyped-def]
    """Build a real git repo with a committed `handbook.md`, and a markgate.yaml
    mapping it to a fake google_docs remote. Returns (repo_root, local_file, config_path).
    """
    _init_repo(tmp_path)
    local_file = tmp_path / "handbook.md"
    local_file.write_text(_CONTENT, encoding="utf-8")
    _git("add", "handbook.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))

    config_path = tmp_path / "markgate.yaml"
    mapping = Mapping(local=str(local_file), backend="google_docs", remote_id="doc123")
    save_config(MarkgateConfig(mappings=[mapping]), str(config_path))
    return tmp_path, local_file, config_path, mapping


# ─────────────────────────────────────────────────────────────────────────────
# `migrate_sectioned()` direct pipeline tests -- rollback + lock behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_should_GitResetHardToPreMigrationHead_when_FailureIsPostCommit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
    pre_migration_head = _head(str(repo_root))

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("state disk full")

    monkeypatch.setattr(migration_module, "_record_migrated_state", _boom)

    config = load_config(str(config_path))
    state = SyncState()
    state_path = get_state_path(str(config_path), None)
    state_dir = get_state_dir(str(config_path), None)
    backend = _StubMigrationBackend(doc=_doc_for_content())

    result = migrate_sectioned(
        mapping, backend, config, str(config_path), state, state_dir, state_path,
        "HEADING_1", dry_run=False,
    )

    assert result.outcome == MigrationOutcome.FAILED
    assert result.rolled_back is True
    assert _head(str(repo_root)) == pre_migration_head
    assert local_file.exists()
    assert not (repo_root / "handbook").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "handbook.md", "handbook"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    )
    assert status.stdout == ""


def test_should_RollBack_when_KeyboardInterruptRaisedMidCommitSwap(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A non-`Exception` `BaseException` (KeyboardInterrupt/SystemExit) raised
    *inside* `_commit_swap` itself -- after `atomic_replace_dir`/`git add`/
    `git rm --cached` have already mutated the working tree, but before `git
    commit` returns -- must still trigger a full `_rollback` back to the
    pre-migration working tree. This is the scenario the BLOCKER bug covered:
    `_commit_swap` only returned `pre_migration_head` on success, so a
    failure at exactly this point left `migrate_sectioned()`'s
    `pre_migration_head` variable `None` and `_rollback`'s `git reset --hard`
    guard skipped entirely, stranding the repo with the original file
    deleted and the section files staged-but-uncommitted while falsely
    reporting `rolled_back=True`.

    Simulated by patching `_run_git` to raise `KeyboardInterrupt` only when
    invoked with `"commit"` -- letting the real `add`/`rm --cached` calls
    inside `_commit_swap` run for real first, so the working tree is
    genuinely half-mutated when the interrupt hits.
    """
    repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
    pre_migration_head = _head(str(repo_root))

    real_run_git = migration_module._run_git

    def _interrupt_on_commit(*args, **kwargs):  # type: ignore[no-untyped-def]
        if args and args[0] == "commit":
            raise KeyboardInterrupt()
        return real_run_git(*args, **kwargs)

    monkeypatch.setattr(migration_module, "_run_git", _interrupt_on_commit)

    config = load_config(str(config_path))
    state = SyncState()
    state_path = get_state_path(str(config_path), None)
    state_dir = get_state_dir(str(config_path), None)
    backend = _StubMigrationBackend(doc=_doc_for_content())

    result = migrate_sectioned(
        mapping, backend, config, str(config_path), state, state_dir, state_path,
        "HEADING_1", dry_run=False,
    )

    assert result.outcome == MigrationOutcome.FAILED
    assert result.rolled_back is True
    assert _head(str(repo_root)) == pre_migration_head
    assert local_file.exists()
    assert not (repo_root / "handbook").exists()

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "handbook.md", "handbook"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    )
    assert status.stdout == ""


def test_should_ReleaseLock_when_MigrationSucceedsOrRollsBack(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Success path.
    repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
    config = load_config(str(config_path))
    state = SyncState()
    state_path = get_state_path(str(config_path), None)
    state_dir = get_state_dir(str(config_path), None)
    backend = _StubMigrationBackend(doc=_doc_for_content())

    result = migrate_sectioned(
        mapping, backend, config, str(config_path), state, state_dir, state_path,
        "HEADING_1", dry_run=False,
    )
    assert result.outcome == MigrationOutcome.SUCCESS
    assert not list(repo_root.glob(".docspan-migration-*.lock"))

    # Rollback path, in a second fresh repo (the mapping migrated above is no
    # longer a valid "unmigrated" mapping to reuse).
    tmp_path2 = tmp_path / "second"
    tmp_path2.mkdir()
    repo_root2, local_file2, config_path2, mapping2 = _repo_with_handbook(tmp_path2)

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(migration_module, "_record_migrated_state", _boom)
    config2 = load_config(str(config_path2))
    state2 = SyncState()
    state_path2 = get_state_path(str(config_path2), None)
    state_dir2 = get_state_dir(str(config_path2), None)
    backend2 = _StubMigrationBackend(doc=_doc_for_content())

    result2 = migrate_sectioned(
        mapping2, backend2, config2, str(config_path2), state2, state_dir2, state_path2,
        "HEADING_1", dry_run=False,
    )
    assert result2.outcome == MigrationOutcome.FAILED
    assert not list(repo_root2.glob(".docspan-migration-*.lock"))


def test_should_RefuseWithoutTouchingLockFile_when_LockAlreadyHeld(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
    mapping_key = "handbook"
    lock_path = _acquire_migration_lock(str(repo_root), mapping_key)
    try:
        config = load_config(str(config_path))
        state = SyncState()
        state_path = get_state_path(str(config_path), None)
        state_dir = get_state_dir(str(config_path), None)
        backend = _StubMigrationBackend(doc=_doc_for_content())

        result = migrate_sectioned(
            mapping, backend, config, str(config_path), state, state_dir, state_path,
            "HEADING_1", dry_run=False,
        )
        assert result.outcome == MigrationOutcome.REFUSED
        assert lock_path.exists()  # this call never touched the pre-existing lock
    finally:
        from docspan.backends.google_docs.migration import _release_migration_lock

        _release_migration_lock(lock_path)


def test_dry_run_returns_preview_and_writes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
    pre_migration_head = _head(str(repo_root))

    config = load_config(str(config_path))
    state = SyncState()
    state_path = get_state_path(str(config_path), None)
    state_dir = get_state_dir(str(config_path), None)
    backend = _StubMigrationBackend(doc=_doc_for_content())

    result = migrate_sectioned(
        mapping, backend, config, str(config_path), state, state_dir, state_path,
        "HEADING_1", dry_run=True,
    )

    assert result.outcome == MigrationOutcome.DRY_RUN
    assert len(result.sections) == 1
    assert [row.filename for row in result.sections[0].rows] == [
        "00-preamble.md",
        "01-first.md",
        "02-second.md",
    ]
    assert result.new_mapping is None
    assert _head(str(repo_root)) == pre_migration_head
    assert local_file.exists()
    assert not (repo_root / "handbook").exists()
    assert not list(repo_root.glob(".docspan-migration-*.lock"))


def test_should_CommitSuccessfully_when_MigratingFiftyPlusSections(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """pre-mortem P1 #1: `_run_git`'s per-call-site timeout (Task 1.1) was
    calibrated for tiny plumbing calls, but `_commit_swap` (Task 3.2) runs
    `git add`/`git commit` over N newly-created section files from exactly
    the large documents this feature exists to serve -- a too-tight timeout
    would abort mid-write on the largest, most section-heavy documents.
    `_GIT_WRITE_TIMEOUT_S` (60s) is the resolved mitigation; this stages and
    commits 60 real section files through the full `migrate_sectioned`
    pipeline against a real git repo to prove that budget holds and the
    commit lands with every file present.
    """
    n_sections = 60
    content = _many_sections_content(n_sections)

    _init_repo(tmp_path)
    local_file = tmp_path / "handbook.md"
    local_file.write_text(content, encoding="utf-8")
    _git("add", "handbook.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))
    pre_migration_head = _head(str(tmp_path))

    config_path = tmp_path / "markgate.yaml"
    mapping = Mapping(local=str(local_file), backend="google_docs", remote_id="doc123")
    save_config(MarkgateConfig(mappings=[mapping]), str(config_path))

    config = load_config(str(config_path))
    state = SyncState()
    state_path = get_state_path(str(config_path), None)
    state_dir = get_state_dir(str(config_path), None)
    backend = _StubMigrationBackend(doc=_doc_for_many_sections(n_sections))

    result = migrate_sectioned(
        mapping, backend, config, str(config_path), state, state_dir, state_path,
        "HEADING_1", dry_run=False,
    )

    assert result.outcome == MigrationOutcome.SUCCESS
    assert not local_file.exists()
    section_dir = tmp_path / "handbook"
    section_files = sorted(p.name for p in section_dir.glob("*.md"))
    assert len(section_files) == n_sections + 1  # +1 for the preamble section

    # Exactly one new commit landed on top of the pre-migration head.
    assert _head(str(tmp_path)) != pre_migration_head
    rev_count = subprocess.run(
        ["git", "rev-list", "--count", f"{pre_migration_head}..HEAD"],
        cwd=str(tmp_path), capture_output=True, text=True, check=True,
    )
    assert rev_count.stdout.strip() == "1"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "handbook.md", "handbook"],
        cwd=str(tmp_path), capture_output=True, text=True, check=True,
    )
    assert status.stdout == ""


# ─────────────────────────────────────────────────────────────────────────────
# CLI surface: `migrate-sectioned`
# ─────────────────────────────────────────────────────────────────────────────


class TestMigrateSectionedCliDryRun:
    def test_renders_preview_table_and_exits_zero_without_writing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        pre_migration_head = _head(str(repo_root))
        backend = _StubMigrationBackend(doc=_doc_for_content())

        with patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(
                app,
                [
                    "migrate-sectioned",
                    str(local_file),
                    "--dry-run",
                    "--config",
                    str(config_path),
                    "--split-level",
                    "HEADING_1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Target file" in result.output
        assert "Heading" in result.output
        assert "Nothing written" in result.output
        assert _head(str(repo_root)) == pre_migration_head
        assert local_file.exists()
        assert not (repo_root / "handbook").exists()
        reloaded = load_config(str(config_path))
        assert reloaded.mappings[0].sectioned is False


class TestMigrateSectionedCliSuccess:
    def test_full_success_path_creates_commit_and_updates_config(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        pre_migration_head = _head(str(repo_root))
        backend = _StubMigrationBackend(doc=_doc_for_content())

        with patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(
                app,
                [
                    "migrate-sectioned",
                    str(local_file),
                    "--config",
                    str(config_path),
                    "--split-level",
                    "HEADING_1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "MIGRATED_SECTIONS=3" in result.output
        assert _head(str(repo_root)) != pre_migration_head
        assert not local_file.exists()
        assert (repo_root / "handbook").is_dir()

        reloaded = load_config(str(config_path))
        assert reloaded.mappings[0].sectioned is True
        assert reloaded.mappings[0].local == str(repo_root / "handbook")

    def test_exits_nonzero_when_mapping_already_sectioned(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        sectioned_mapping = Mapping(
            local=str(local_file), backend="google_docs", remote_id="doc123",
            sectioned=True, split_level="HEADING_1",
        )
        save_config(MarkgateConfig(mappings=[sectioned_mapping]), str(config_path))

        result = runner.invoke(
            app, ["migrate-sectioned", str(local_file), "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "already sectioned" in result.output

    def test_exits_nonzero_when_backend_unsupported(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        confluence_mapping = Mapping(local=str(local_file), backend="confluence", remote_id="PAGE-1")
        save_config(MarkgateConfig(mappings=[confluence_mapping]), str(config_path))

        result = runner.invoke(
            app, ["migrate-sectioned", str(local_file), "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "does not support" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# CLI surface: `pull --to-sectioned`
# ─────────────────────────────────────────────────────────────────────────────


class TestPullToSectioned:
    def test_dry_run_renders_byte_identical_table_to_standalone_command(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Task 5.4: `pull --dry-run --to-sectioned` and `migrate-sectioned
        --dry-run` must render the identical preview -- both funnel through
        the same `_render_migration_result()` helper, so this pins that
        cross-surface guarantee rather than merely re-testing the table."""
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        backend = _StubMigrationBackend(doc=_doc_for_content())

        with patch("docspan.cli.main._get_backend", return_value=backend):
            standalone = runner.invoke(
                app,
                [
                    "migrate-sectioned", str(local_file), "--dry-run",
                    "--config", str(config_path), "--split-level", "HEADING_1",
                ],
            )
            via_pull = runner.invoke(
                app,
                [
                    "pull", str(local_file), "--dry-run", "--to-sectioned",
                    "--config", str(config_path), "--split-level", "HEADING_1",
                ],
            )

        assert standalone.exit_code == 0, standalone.output
        assert via_pull.exit_code == 0, via_pull.output
        assert standalone.output == via_pull.output

    def test_migrates_then_falls_through_to_normal_pull(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        backend = _StubMigrationBackend(doc=_doc_for_content())

        with patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.orchestrate_pull") as mock_orchestrate_pull:
            from docspan.core.orchestrator import PullOutcome

            mock_orchestrate_pull.return_value = PullOutcome(local_path=str(local_file), action="up-to-date")
            result = runner.invoke(
                app,
                [
                    "pull", str(local_file), "--to-sectioned",
                    "--config", str(config_path), "--split-level", "HEADING_1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "MIGRATED_SECTIONS=3" in result.output
        reloaded = load_config(str(config_path))
        assert reloaded.mappings[0].sectioned is True
        # The pull that ran afterward was for one of the newly-migrated
        # section mappings, not the pre-migration single-file mapping.
        assert mock_orchestrate_pull.called
        called_mapping = mock_orchestrate_pull.call_args.args[0] if mock_orchestrate_pull.call_args.args else None
        if called_mapping is not None:
            assert called_mapping.local != str(local_file)

    def test_migration_failure_reports_error_and_continues(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        repo_root, local_file, config_path, mapping = _repo_with_handbook(tmp_path)
        backend = _StubMigrationBackend(doc=_doc_for_content())

        def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        monkeypatch.setattr(migration_module, "_record_migrated_state", _boom)

        with patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(
                app,
                [
                    "pull", str(local_file), "--to-sectioned",
                    "--config", str(config_path), "--split-level", "HEADING_1",
                ],
            )

        assert result.exit_code == 1
        assert "Migration failed" in result.output
        reloaded = load_config(str(config_path))
        assert reloaded.mappings[0].sectioned is False
