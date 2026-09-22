"""CLI smoke tests — drives the Typer app through CliRunner.

Mocks at load_config / _get_backend / orchestrate_* boundaries.
Tests verify exit codes and output text; no real backends or network calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from docspan.backends.base import Backend, CreateResult, PullResult, PushResult
from docspan.backends.google_docs.push_preview import HighRiskParagraph
from docspan.cli.main import (
    LIVE_WEDDING_DOC_ID,
    SCRATCH_VERIFIED_MARKER,
    _default_title,
    app,
    resolve_mapping_for_path,
)
from docspan.config import ConfigConflictError, Mapping, MarkgateConfig
from docspan.core.orchestrator import PullOutcome, PushOutcome
from docspan.core.state import MappingState, SyncState, sha256_of_content

runner = CliRunner()  # Typer's CliRunner mixes stderr into result.output via StreamMixer by default


# ─────────────────────────────────────────────────────────────────────────────
# Stubs and helpers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FakeBackend(Backend):
    name: str = "fake"
    remote_version: str = "v1"
    push_status: str = "ok"
    pull_status: str = "ok"
    # None (the default) preserves every existing test's behavior — pull()
    # leaves local_path untouched, exactly as before this field existed.
    # Set explicitly by tests that need pull() to behave like a real
    # backend and actually write the remote's content (e.g. a preview_pull
    # merge test, where reading back an untouched empty temp file would
    # make the three-way merge it exercises trivially pass regardless of
    # what "the remote" is supposed to contain).
    remote_content: Optional[str] = None
    auth_setup_called: bool = False
    push_calls: list = field(default_factory=list)

    def push(self, local_path: str, doc_id: str, force: bool = False, **kwargs) -> PushResult:
        self.push_calls.append({"local_path": local_path, "doc_id": doc_id, "force": force})
        return PushResult(status=self.push_status, doc_id=doc_id, url="https://example.com/doc")  # type: ignore[arg-type]

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        if self.remote_content is not None:
            with open(local_path, "w", encoding="utf-8") as fh:
                fh.write(self.remote_content)
        return PullResult(status=self.pull_status, doc_id=doc_id, local_path=local_path)  # type: ignore[arg-type]

    def get_remote_version(self, doc_id: str) -> str:
        return self.remote_version

    def create(self, title: str, **kwargs) -> CreateResult:
        return CreateResult(doc_id="new-doc-1", title=title, url="https://example.com/new-doc")

    def create_tab(self, doc_id: str, title: str, **kwargs) -> CreateResult:
        return CreateResult(
            doc_id=doc_id, title=title, tab_id="t.newtab",
            url="https://example.com/new-doc?tab=t.newtab",
        )

    def auth_setup(self, config_path=None) -> None:
        self.auth_setup_called = True

    def validate_config(self) -> None:
        pass


@dataclass
class FakePushPreview:
    """Minimal stand-in for push_preview.PushPreview — just needs .render()
    and (optionally) .error, mirroring the real dataclass's error field used
    by the CLI's dry-run error handling (getattr(preview, "error", None)).
    `high_risk` defaults to [] (deliberately absent from most existing
    fixtures' construction) — the CLI reads it via getattr(...,
    "high_risk", None) precisely so a stand-in/backend without this field
    doesn't crash STYLE_UPGRADE_COUNT counting (Story 4.3)."""
    text: str = "Preview: 1 change(s), 0 addition(s), 0 removal(s), 0 unchanged\n  ~ [ ] Splitwise → [x] Splitwise"
    error: Optional[str] = None
    high_risk: list = field(default_factory=list)

    def render(self) -> str:
        return self.text


@dataclass
class FakeBackendWithPreview(FakeBackend):
    """A FakeBackend that also supports preview_push(), for --dry-run tests."""
    name: str = "fake"
    preview_text: Optional[str] = None
    preview_error: Optional[str] = None
    preview_high_risk: list = field(default_factory=list)

    def preview_push(self, local_path: str, doc_id: str, tab_id: Optional[str] = None) -> FakePushPreview:
        if self.preview_error is not None:
            return FakePushPreview(text=f"✗ dry-run failed: {self.preview_error}", error=self.preview_error)
        if self.preview_text is not None:
            return FakePushPreview(text=self.preview_text, high_risk=self.preview_high_risk)
        return FakePushPreview(high_risk=self.preview_high_risk)


def _config(*mappings: Mapping) -> MarkgateConfig:
    return MarkgateConfig(mappings=list(mappings))


def _mapping(
    local: str = "doc.md",
    backend: str = "fake",
    remote_id: str = "doc-123",
    direction: str = "both",
) -> Mapping:
    return Mapping(local=local, backend=backend, remote_id=remote_id, direction=direction)


def _cfg_file(tmp_path) -> str:  # type: ignore[no-untyped-def]
    """Write a stub markgate.yaml; routes state path to tmp_path."""
    p = tmp_path / "markgate.yaml"
    p.write_text("mappings: []\n", encoding="utf-8")
    return str(p)


def _state_with(local_path: str, entry: MappingState) -> SyncState:
    state = SyncState()
    state.update(local_path, entry)
    return state


def _write_state(tmp_path, local_path: str, entry: MappingState) -> None:
    state = SyncState()
    state.update(local_path, entry)
    state.save(str(tmp_path / ".markgate-state.json"))


def _fake_entry(local_path: str = "doc.md", remote_version: str = "v1") -> MappingState:
    return MappingState(
        doc_id="doc-123",
        backend="fake",
        last_synced_at="2024-01-01T00:00:00+00:00",
        base_hash="abc",
        remote_version=remote_version,
        local_hash=sha256_of_content("content\n"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# push
# ─────────────────────────────────────────────────────────────────────────────

class TestPush:
    def test_dry_run_prints_preview_and_exits_zero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Plain FakeBackend has no preview_push — this exercises the
        # fallback stub message (see test_dry_run_falls_back_to_stub_when_
        # backend_has_no_preview below for the same scenario, named per
        # Task 1.2.4c).
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "dry-run" in result.output

    def test_dry_run_renders_preview_when_backend_supports_it(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("- [x] Splitwise\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(
            preview_text="Preview: 1 change(s), 0 addition(s), 0 removal(s), 12 unchanged\n"
            "  ~ [ ] Splitwise → [x] Splitwise"
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "~ [ ] Splitwise → [x] Splitwise" in result.output
        # This call is purely informational — no real write occurs.

    def test_dry_run_falls_back_to_stub_when_backend_has_no_preview(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "dry-run" in result.output

    def test_dry_run_prints_clean_message_and_exits_nonzero_on_preview_failure(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Bug fix: preview_push() failures (expired auth, network error,
        malformed doc) must render as one clean line — never a raw
        traceback — and the CLI must still exit nonzero so a failed
        --dry-run doesn't look like a clean success."""
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(preview_error="<HttpError 401 Unauthorized>")
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 1
        assert "dry-run failed" in result.output
        assert "Traceback" not in result.output

    def test_pull_only_mapping_is_skipped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local), direction="pull"))):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "pull-only" in result.output

    def test_no_mappings_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 1

    def test_ok_result_prints_checkmark(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "✓" in result.output

    def test_error_result_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="error", doc_id="doc-123", message="network failure"),
            state_saved=False,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 1
        assert "✗" in result.output

    def test_state_not_saved_prints_warning(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com"),
            state_saved=False,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "Warning" in result.output

    def test_file_filter_no_match_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local="other.md"))):
            result = runner.invoke(app, ["push", "nonexistent.md", "--config", cfg])
        assert result.exit_code == 1

    def test_push_should_resolve_sectioned_mapping_when_given_a_section_file_path(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Gap 5 fix: `push <section-file>` must resolve via resolve_mapping_for_path
        # (Specification pattern), not the old exact `m.local in files` check, since
        # a sectioned mapping's `local` is a directory, never equal to any section path.
        cfg = _cfg_file(tmp_path)
        sectioned = Mapping(
            local="docs/big-doc", backend="fake", remote_id="doc-1",
            sectioned=True, split_level="HEADING_1",
        )
        outcome = PushOutcome(
            local_path=sectioned.local,
            result=PushResult(status="ok", doc_id="doc-1", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(sectioned)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "docs/big-doc/02-intro.md", "--config", cfg])
        assert result.exit_code == 0
        assert "docs/big-doc" in result.output

    def test_unknown_backend_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local), backend="no_such_backend"))):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output

    def test_push_reports_blocked_status_as_error_without_force(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="blocked", doc_id="doc-123", message="⚠ COMMENT AT RISK: ..."),
            state_saved=False,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 1
        assert "✗" in result.output

    def test_push_force_flag_reaches_backend_push_call(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--force", "--config", cfg])
        assert result.exit_code == 0
        assert len(backend.push_calls) == 1
        assert backend.push_calls[0]["force"] is True

    def test_push_without_force_flag_reaches_backend_push_call_as_false(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert len(backend.push_calls) == 1
        assert backend.push_calls[0]["force"] is False

    def test_push_reports_warning_status_with_yellow_icon_and_nonzero_exit(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(
                status="warning",
                doc_id="doc-123",
                message="⚠ open comment count dropped (2→1)",
            ),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        # CommentCountBackstop's finding must never render/exit like a clean
        # "ok" (green ✓, exit 0) — it gets its own yellow ⚠ and nonzero exit.
        assert result.exit_code == 1
        assert "⚠" in result.output
        assert "✓" not in result.output

    # ─────────────────────────────────────────────────────────────────
    # STYLE_UPGRADE_COUNT / --fail-on-comment-loss (Epic 4, Story 4.3)
    # ─────────────────────────────────────────────────────────────────

    def test_dry_run_prints_style_upgrade_count_zero_when_no_style_upgrades(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview()
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=0" in result.output

    def test_dry_run_prints_style_upgrade_count_matching_high_risk_entries(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(
            preview_high_risk=[
                HighRiskParagraph(paragraph_text="> A note", reasons=["style_upgrade"]),
                HighRiskParagraph(paragraph_text="> Another note", reasons=["style_upgrade"]),
            ]
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["push", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=2" in result.output

    def test_real_push_prints_style_upgrade_count_from_pre_push_preview(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(
            preview_high_risk=[
                HighRiskParagraph(paragraph_text="> A note", reasons=["style_upgrade"]),
            ]
        )
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=1" in result.output

    def test_real_push_prints_style_upgrade_count_zero_when_backend_has_no_preview(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=0" in result.output

    def test_fail_on_comment_loss_not_passed_leaves_exit_code_zero_despite_style_upgrades(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(
            preview_high_risk=[
                HighRiskParagraph(paragraph_text="> A note", reasons=["style_upgrade"]),
            ]
        )
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=1" in result.output

    def test_fail_on_comment_loss_passed_with_style_upgrades_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackendWithPreview(
            preview_high_risk=[
                HighRiskParagraph(paragraph_text="> A note", reasons=["style_upgrade"]),
            ]
        )
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--fail-on-comment-loss", "--config", cfg])
        assert result.exit_code == 1
        assert "STYLE_UPGRADE_COUNT=1" in result.output

    def test_fail_on_comment_loss_passed_with_zero_style_upgrades_stays_exit_zero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="doc-123", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome):
            result = runner.invoke(app, ["push", "--fail-on-comment-loss", "--config", cfg])
        assert result.exit_code == 0
        assert "STYLE_UPGRADE_COUNT=0" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# push — ScratchVerificationMarker (Story 1.2.5)
# ─────────────────────────────────────────────────────────────────────────────

class TestPushScratchVerificationMarker:
    def test_live_doc_push_prompts_when_marker_missing_and_aborts_on_no(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "wedding.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=LIVE_WEDDING_DOC_ID)
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push") as mock_orchestrate:
            result = runner.invoke(app, ["push", "--config", cfg], input="n\n")
        assert result.exit_code == 1
        assert "Push cancelled." in result.output
        mock_orchestrate.assert_not_called()
        assert not (tmp_path / SCRATCH_VERIFIED_MARKER).exists()

    def test_live_doc_push_prompts_and_proceeds_and_writes_marker_on_yes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "wedding.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=LIVE_WEDDING_DOC_ID)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id=LIVE_WEDDING_DOC_ID, url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome) as mock_orchestrate:
            result = runner.invoke(app, ["push", "--config", cfg], input="y\n")
        assert result.exit_code == 0
        mock_orchestrate.assert_called_once()
        assert (tmp_path / SCRATCH_VERIFIED_MARKER).exists()

    def test_live_doc_push_skips_prompt_when_marker_present(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "wedding.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        (tmp_path / SCRATCH_VERIFIED_MARKER).write_text("verified\n", encoding="utf-8")
        mapping = _mapping(local=str(local), remote_id=LIVE_WEDDING_DOC_ID)
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id=LIVE_WEDDING_DOC_ID, url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome) as mock_orchestrate:
            # No input provided — if the prompt fired, this would hang/fail.
            result = runner.invoke(app, ["push", "--config", cfg], input="")
        assert result.exit_code == 0
        mock_orchestrate.assert_called_once()

    def test_scratch_doc_push_never_prompts(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "wedding-scratch.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id="scratch-doc-id")
        outcome = PushOutcome(
            local_path=str(local),
            result=PushResult(status="ok", doc_id="scratch-doc-id", url="https://example.com/doc"),
            state_saved=True,
        )
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_push", return_value=outcome) as mock_orchestrate:
            # No input provided — if the prompt fired, this would hang/fail.
            result = runner.invoke(app, ["push", "--config", cfg], input="")
        assert result.exit_code == 0
        mock_orchestrate.assert_called_once()
        assert not (tmp_path / SCRATCH_VERIFIED_MARKER).exists()


# ─────────────────────────────────────────────────────────────────────────────
# push — blank remote_id auto-create flow
# ─────────────────────────────────────────────────────────────────────────────

class TestPushAutoCreate:
    def test_non_interactive_errors_without_prompting(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=None)
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main._can_prompt", return_value=False):
            result = runner.invoke(app, ["push", "--config", cfg], input="")
        assert result.exit_code == 1
        output = " ".join(result.output.split())
        assert "no remote_id" in output
        assert "docspan map" in output

    def test_interactive_confirm_yes_creates_persists_and_pushes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=None)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._can_prompt", return_value=True):
            result = runner.invoke(app, ["push", "--config", cfg], input="y\n")
        assert result.exit_code == 0
        assert "Created fake doc" in result.output
        assert backend.push_calls and backend.push_calls[0]["doc_id"] == "new-doc-1"
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        assert saved["mappings"][0]["remote_id"] == "new-doc-1"

    def test_interactive_confirm_no_cancels(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=None)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._can_prompt", return_value=True):
            result = runner.invoke(app, ["push", "--config", cfg], input="n\n")
        assert result.exit_code == 1
        assert "Push cancelled." in result.output
        assert not backend.push_calls

    def test_orphaned_doc_surfaced_on_conflict_during_auto_create(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=None)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._can_prompt", return_value=True), \
             patch("docspan.cli.main.save_config", side_effect=ConfigConflictError("markgate.yaml changed on disk")):
            result = runner.invoke(app, ["push", "--config", cfg], input="y\n")
        assert result.exit_code == 1
        assert "NOT recorded in markgate.yaml" in result.output
        assert "new-doc-1" in result.output
        assert not backend.push_calls

    def test_backend_value_error_surfaces_and_does_not_write_mapping(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), remote_id=None)
        backend = MagicMock()
        backend.create.side_effect = ValueError("Confluence page creation requires a space key.")
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._can_prompt", return_value=True):
            result = runner.invoke(app, ["push", "--config", cfg], input="y\n")
        assert result.exit_code == 1
        assert "space key" in result.output
        assert "docspan map" in result.output
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        assert saved["mappings"] == []


# ─────────────────────────────────────────────────────────────────────────────
# map
# ─────────────────────────────────────────────────────────────────────────────

class TestMap:
    def test_creates_google_docs_mapping_and_pushes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "Created google_docs doc" in result.output
        assert backend.push_calls and backend.push_calls[0]["doc_id"] == "new-doc-1"
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        assert saved["mappings"][0]["local"] == str(local)
        assert saved["mappings"][0]["backend"] == "google_docs"
        assert saved["mappings"][0]["remote_id"] == "new-doc-1"

    def test_creates_confluence_mapping_with_space(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend) as get_backend:
            result = runner.invoke(
                app, ["map", str(local), "--backend", "confluence", "--space", "ENG", "--config", cfg]
            )
        assert result.exit_code == 0
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        assert saved["mappings"][0]["backend"] == "confluence"
        get_backend.assert_called_once()

    def test_refuses_when_already_mapped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 1
        assert "already mapped" in result.output

    def test_unknown_backend_errors(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = runner.invoke(app, ["map", str(local), "--backend", "nope", "--config", cfg])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output

    def test_backend_value_error_surfaces_and_does_not_write_mapping(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = MagicMock()
        backend.create.side_effect = ValueError("Confluence page creation requires a space key.")
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "confluence", "--config", cfg])
        assert result.exit_code == 1
        assert "space key" in result.output
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        assert saved["mappings"] == []

    def test_conflict_on_save_surfaces_orphaned_doc(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.save_config", side_effect=ConfigConflictError("markgate.yaml changed on disk")):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 1
        assert "NOT recorded in markgate.yaml" in result.output
        assert "new-doc-1" in result.output
        assert "https://example.com/new-doc" in result.output

    def test_pushes_immediately_even_for_pull_direction(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # A freshly created remote doc/page is empty. A pull-direction mapping
        # must still get its initial push, otherwise the next `docspan pull`
        # has no prior sync state and overwrites the local file with that
        # empty remote content — see main.py's map_() comment for detail.
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(
                app, ["map", str(local), "--backend", "google_docs", "--direction", "pull", "--config", cfg]
            )
        assert result.exit_code == 0
        assert backend.push_calls

    def test_skips_immediate_push_when_local_file_missing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        missing = tmp_path / "missing.md"
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(missing), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert not backend.push_calls

    def test_defaults_title_to_first_h1_not_basename(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("# My Great Document\n\nBody text.\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'My Great Document'" in result.output

    def test_explicit_title_overrides_h1(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("# My Great Document\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(
                app, ["map", str(local), "--backend", "google_docs", "--title", "Custom Title", "--config", cfg]
            )
        assert result.exit_code == 0
        assert "'Custom Title'" in result.output

    def test_falls_back_to_basename_when_no_h1(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("Just a paragraph, no heading.\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'README'" in result.output

    def _new_tab_fixtures(self, tmp_path, parent_backend: str = "google_docs"):  # type: ignore[no-untyped-def]
        """(parent_local, new_local, cfg, parent_mapping) for --new-tab-in tests."""
        parent_local = tmp_path / "parent.md"
        parent_local.write_text("# Parent\n", encoding="utf-8")
        new_local = tmp_path / "discussion.md"
        new_local.write_text("# Discussion\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        parent_mapping = _mapping(
            local=str(parent_local), backend=parent_backend, remote_id="parent-doc-1"
        )
        return parent_local, new_local, cfg, parent_mapping

    def _invoke_new_tab(self, new_local, new_tab_in, cfg, backend: str = "google_docs", extra_args=None):  # type: ignore[no-untyped-def]
        args = [
            "map", str(new_local), "--backend", backend,
            "--new-tab-in", str(new_tab_in), "--config", cfg,
        ]
        return runner.invoke(app, args + list(extra_args or []))

    def test_new_tab_in_creates_tab_in_existing_doc_and_maps_it(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = self._invoke_new_tab(new_local, parent_local, cfg)
        assert result.exit_code == 0, result.output
        assert "Created google_docs tab" in result.output
        saved = yaml.safe_load(open(cfg, encoding="utf-8"))
        new_mapping = saved["mappings"][1]
        assert new_mapping["local"] == str(new_local)
        assert new_mapping["remote_id"] == "parent-doc-1"
        assert new_mapping["tab_id"] == "t.newtab"

    def test_new_tab_in_requires_google_docs_backend(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)):
            result = self._invoke_new_tab(new_local, parent_local, cfg, backend="confluence")
        assert result.exit_code == 1
        assert "requires --backend google_docs" in result.output

    def test_new_tab_in_rejects_unmapped_parent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        _parent_local, new_local, cfg, _parent_mapping = self._new_tab_fixtures(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = self._invoke_new_tab(new_local, "not-mapped.md", cfg)
        assert result.exit_code == 1
        assert "is not mapped" in _unwrapped(result.output)

    def test_new_tab_in_rejects_non_google_docs_parent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(
            tmp_path, parent_backend="confluence"
        )
        with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)):
            result = self._invoke_new_tab(new_local, parent_local, cfg)
        assert result.exit_code == 1
        assert "not google_docs" in _unwrapped(result.output)

    def test_new_tab_in_and_tab_id_are_mutually_exclusive(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)):
            result = self._invoke_new_tab(
                new_local, parent_local, cfg, extra_args=["--tab-id", "t.other"]
            )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_new_tab_in_rejects_space(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)):
            result = self._invoke_new_tab(
                new_local, parent_local, cfg, extra_args=["--space", "ENG"]
            )
        assert result.exit_code == 1
        assert "--space is ignored by --new-tab-in" in result.output

    def test_new_tab_in_rejects_parent_with_no_remote_id_yet(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        parent_local, new_local, cfg, _parent_mapping = self._new_tab_fixtures(tmp_path)
        unpushed_parent = _mapping(local=str(parent_local), backend="google_docs", remote_id=None)
        with patch("docspan.cli.main.load_config", return_value=_config(unpushed_parent)):
            result = self._invoke_new_tab(new_local, parent_local, cfg)
        assert result.exit_code == 1
        assert "has no remote_id yet" in _unwrapped(result.output)

    def test_default_title_falls_back_to_basename_when_file_is_not_utf8(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Exercised as a unit test on _default_title directly: routing this through
        # the full `map` CLI invocation also trips the unrelated downstream push's
        # own (pre-existing) content read, which isn't what this case is testing.
        local = tmp_path / "README.md"
        local.write_bytes(b"\xff\xfe# not valid utf-8\x00")
        assert _default_title(str(local)) == "README"

    def test_falls_back_to_basename_when_h1_line_is_empty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("# \n\nSome body text.\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'README'" in result.output

    def test_falls_back_to_basename_when_only_h2(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("## Subheading only\n\nBody.\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'README'" in result.output

    def test_strips_whitespace_around_h1_text(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text("#   Title with spaces   \n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'Title with spaces'" in result.output

    def test_skips_hash_comment_inside_frontmatter(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text(
            "---\nconnie-title: x\n# a note, not a heading\n---\n# Real Heading\n",
            encoding="utf-8",
        )
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'Real Heading'" in result.output

    def test_skips_hash_comment_inside_fenced_code_block(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "README.md"
        local.write_text(
            "```bash\n# this is a shell comment\n```\n# Real Title\n",
            encoding="utf-8",
        )
        cfg = _cfg_file(tmp_path)
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--config", cfg])
        assert result.exit_code == 0
        assert "'Real Title'" in result.output

    def test_respects_prefix_resolution(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Criterion 11: map resolves the config via the same central-config
        machinery push/pull use, rather than a hardcoded/local-only path."""
        local = tmp_path / "new.md"
        local.write_text("# Hello\n", encoding="utf-8")
        prefixed_cfg = tmp_path / "prefixed-markgate.yaml"
        prefixed_cfg.write_text("mappings: []\n", encoding="utf-8")
        backend = FakeBackend()
        with patch("docspan.cli.main.resolve_active_project", return_value=(str(prefixed_cfg), "myproj")) as mock_resolve, \
             patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main._get_backend", return_value=backend):
            result = runner.invoke(app, ["map", str(local), "--backend", "google_docs", "--prefix", "myproj"])
        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(prefix="myproj", config_path=None)
        saved = yaml.safe_load(open(prefixed_cfg, encoding="utf-8"))
        assert saved["mappings"][0]["local"] == str(local)


# ─────────────────────────────────────────────────────────────────────────────
# pull
# ─────────────────────────────────────────────────────────────────────────────

class TestPull:
    def test_dry_run_prints_preview_and_exits_zero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
            result = runner.invoke(app, ["pull", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "dry-run" in result.output

    def test_dry_run_reports_up_to_date_without_writing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """--dry-run must actually check the remote (issue #131) — a mapping
        with no local/remote drift is reported up to date, and the local
        file is never touched."""
        local = tmp_path / "doc.md"
        local.write_text("hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local))
        state_entry = MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2026-01-01T00:00:00Z",
            local_hash=sha256_of_content("hello\n"), remote_version="v1", base_hash="base",
        )
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend(remote_version="v1")), \
             patch("docspan.cli.main._load_state", return_value=_state_with(str(local), state_entry)):
            result = runner.invoke(app, ["pull", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "up to date" in result.output
        assert local.read_text(encoding="utf-8") == "hello\n"

    def test_dry_run_reports_would_merge_without_writing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Both sides changed: --dry-run reports a real conflict count from
        an actual three-way merge, but never writes it to the local file.

        `remote_content` is set (and differs from both the empty merge base
        and the local edit) so the merge FakeBackend.pull feeds into is a
        genuine three-way merge against real "theirs" content, not a merge
        against an untouched empty temp file — which would pass regardless
        of what the classification logic actually computed.
        """
        local = tmp_path / "doc.md"
        local.write_text("local change\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local))
        state_entry = MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2026-01-01T00:00:00Z",
            local_hash=sha256_of_content("base\n"), remote_version="v1", base_hash="base",
        )
        backend = FakeBackend(remote_version="v2", remote_content="remote change\n")
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._load_state", return_value=_state_with(str(local), state_entry)):
            result = runner.invoke(app, ["pull", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "would merge" in result.output
        assert "1 conflicts" in result.output
        assert local.read_text(encoding="utf-8") == "local change\n"

    def test_push_only_mapping_is_skipped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local), direction="push"))):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "push-only" in result.output

    def test_no_mappings_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 1

    def test_pull_should_resolve_sectioned_mapping_when_given_a_section_file_path(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Gap 5 fix: `pull <section-file>` must resolve via resolve_mapping_for_path
        # (Specification pattern), not the old exact `m.local in files` check, since
        # a sectioned mapping's `local` is a directory, never equal to any section path.
        cfg = _cfg_file(tmp_path)
        sectioned = Mapping(
            local="docs/big-doc", backend="fake", remote_id="doc-1",
            sectioned=True, split_level="HEADING_1",
        )
        outcome = PullOutcome(
            local_path=sectioned.local,
            action="fast-forward",
            result=PullResult(status="ok", doc_id="doc-1", local_path=sectioned.local),
        )
        with patch("docspan.cli.main.load_config", return_value=_config(sectioned)), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "docs/big-doc/02-intro.md", "--config", cfg])
        assert result.exit_code == 0
        assert "docs/big-doc" in result.output

    def test_up_to_date_prints_message(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(local_path=str(local), action="up-to-date")
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "up to date" in result.output

    def test_local_only_prints_warning(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(local_path=str(local), action="local-only")
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "local changes" in result.output

    def test_merged_clean_prints_success(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(local_path=str(local), action="merged", has_conflicts=False)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "Merged cleanly" in result.output

    def test_merged_with_conflicts_prints_count_and_hint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(local_path=str(local), action="merged", has_conflicts=True, conflict_count=3)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "3" in result.output
        assert "conflict" in result.output.lower()

    def test_error_action_prints_error_message(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(
            local_path=str(local),
            action="error",
            result=PullResult(status="error", doc_id="doc-123", local_path=str(local), message="API unavailable"),
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 1
        assert "unavailable" in result.output

    def test_fast_forward_ok_prints_checkmark(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(
            local_path=str(local),
            action="fast-forward",
            result=PullResult(status="ok", doc_id="doc-123", local_path=str(local)),
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        assert result.exit_code == 0
        assert "✓" in result.output

    def test_pull_reports_warning_status_with_yellow_icon_and_nonzero_exit(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        outcome = PullOutcome(
            local_path=str(local),
            action="fast-forward",
            result=PullResult(
                status="warning",
                doc_id="doc-123",
                local_path=str(local),
                message="Document has 2 tabs but no tab_id is configured",
            ),
        )
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.orchestrate_pull", return_value=outcome):
            result = runner.invoke(app, ["pull", "--config", cfg])
        # A multi-tab ambiguity warning must never render/exit like a clean
        # "ok" (green ✓, exit 0) — it gets its own yellow ⚠ and nonzero exit,
        # mirroring push's CommentCountBackstop warning handling.
        assert result.exit_code == 1
        assert "⚠" in result.output
        assert "✓" not in result.output


# ─────────────────────────────────────────────────────────────────────────────
# sync
# ─────────────────────────────────────────────────────────────────────────────

class TestSync:
    def test_up_to_date_mapping_gets_pushed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """No remote drift to pull -> sync still pushes (there may be local
        edits push hasn't seen yet)."""
        local = tmp_path / "doc.md"
        local.write_text("hello\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local))
        state_entry = MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2026-01-01T00:00:00Z",
            local_hash=sha256_of_content("hello\n"), remote_version="v1", base_hash="base",
        )
        backend = FakeBackend(remote_version="v1")
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._load_state", return_value=_state_with(str(local), state_entry)), \
             patch("docspan.cli.main.save_config"):
            result = runner.invoke(app, ["sync", "--config", cfg])
        assert result.exit_code == 0
        assert "up to date" in result.output
        assert len(backend.push_calls) == 1

    def test_merge_conflicts_block_the_push(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A pull that leaves conflicts must not be followed by a push over
        them — that's the whole point of `sync` over a bare `pull && push`."""
        local = tmp_path / "doc.md"
        local.write_text("local change\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local))
        state_entry = MappingState(
            doc_id="doc-123", backend="fake", last_synced_at="2026-01-01T00:00:00Z",
            local_hash=sha256_of_content("base\n"), remote_version="v1", base_hash="base",
        )
        backend = FakeBackend(remote_version="v2")
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main._load_state", return_value=_state_with(str(local), state_entry)), \
             patch(
                 "docspan.cli.main.orchestrate_pull",
                 return_value=PullOutcome(local_path=str(local), action="merged", has_conflicts=True, conflict_count=1),
             ), \
             patch("docspan.cli.main.save_config"):
            result = runner.invoke(app, ["sync", "--config", cfg])
        assert result.exit_code == 1
        assert "Merge conflicts" in result.output
        assert backend.push_calls == []

    def test_pull_only_mapping_never_pushed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        mapping = _mapping(local=str(local), direction="pull")
        backend = FakeBackend()
        with patch("docspan.cli.main.load_config", return_value=_config(mapping)), \
             patch("docspan.cli.main._get_backend", return_value=backend), \
             patch("docspan.cli.main.save_config"):
            result = runner.invoke(app, ["sync", "--config", cfg])
        assert result.exit_code == 0
        assert backend.push_calls == []


# ─────────────────────────────────────────────────────────────────────────────
# status
# ─────────────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_no_mappings_prints_message(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = runner.invoke(app, ["status", "--config", cfg])
        assert result.exit_code == 0
        assert "No mappings" in result.output

    def test_with_mappings_prints_table(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local="doc.md", remote_id="doc-456"))):
            result = runner.invoke(app, ["status", "--config", cfg])
        assert result.exit_code == 0
        assert "doc.md" in result.output
        assert "doc-456" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# auth setup
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthSetup:
    def test_unknown_backend_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            result = runner.invoke(app, ["auth", "setup", "no_such_backend", "--config", cfg])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output

    def test_known_backend_calls_auth_setup(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        fake = FakeBackend()
        fake_cls = MagicMock()
        fake_cls.from_config.return_value = fake
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main.BACKENDS", {"mybackend": fake_cls}):
            result = runner.invoke(app, ["auth", "setup", "mybackend", "--config", cfg])
        assert result.exit_code == 0
        assert fake.auth_setup_called


# ─────────────────────────────────────────────────────────────────────────────
# conflicts list
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictsList:
    def test_no_tracked_files_prints_no_conflicts(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        result = runner.invoke(app, ["conflicts", "list", "--config", cfg])
        assert result.exit_code == 0
        assert "No unresolved conflicts" in result.output

    def test_file_with_conflict_markers_appears_in_table(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("<<<<<<< ours\nlocal\n=======\nremote\n>>>>>>> theirs\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        _write_state(tmp_path, str(local), _fake_entry(str(local)))
        result = runner.invoke(app, ["conflicts", "list", "--config", cfg])
        assert result.exit_code == 0
        assert "Files with merge conflicts" in result.output
        assert "1" in result.output  # conflict block count

    def test_file_without_markers_not_listed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Clean content\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        _write_state(tmp_path, str(local), _fake_entry(str(local)))
        result = runner.invoke(app, ["conflicts", "list", "--config", cfg])
        assert result.exit_code == 0
        assert "No unresolved conflicts" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# conflicts resolve
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictsResolve:
    def test_invalid_accept_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        result = runner.invoke(app, ["conflicts", "resolve", "doc.md", "--accept", "invalid", "--config", cfg])
        assert result.exit_code == 1
        assert "remote" in result.output

    def test_untracked_file_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        result = runner.invoke(app, ["conflicts", "resolve", "not_tracked.md", "--accept", "local", "--config", cfg])
        assert result.exit_code == 1
        assert "not tracked" in result.output

    def test_resolve_merged_with_conflict_markers_exits_nonzero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        conflicted = "<<<<<<< ours\nlocal\n=======\nremote\n>>>>>>> theirs\n"
        local.write_text(conflicted, encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        _write_state(tmp_path, str(local), _fake_entry(str(local)))
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
            result = runner.invoke(app, ["conflicts", "resolve", str(local), "--accept", "merged", "--config", cfg])
        assert result.exit_code == 1
        assert "conflict markers" in _unwrapped(result.output)

    def test_resolve_merged_clean_succeeds(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        local.write_text("# Clean file\nNo conflicts here.\n", encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        _write_state(tmp_path, str(local), _fake_entry(str(local)))
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.record_state", return_value=True):
            result = runner.invoke(app, ["conflicts", "resolve", str(local), "--accept", "merged", "--config", cfg])
        assert result.exit_code == 0
        assert "Resolved" in result.output

    def test_resolve_local_restores_orig_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        orig_content = "# Original local content\n"
        local.write_text("<<<<<<< ours\nlocal\n=======\nremote\n>>>>>>> theirs\n", encoding="utf-8")
        orig = tmp_path / "doc.md.orig"
        orig.write_text(orig_content, encoding="utf-8")
        cfg = _cfg_file(tmp_path)
        _write_state(tmp_path, str(local), _fake_entry(str(local)))
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))), \
             patch("docspan.cli.main._get_backend", return_value=FakeBackend()), \
             patch("docspan.cli.main.record_state", return_value=True):
            result = runner.invoke(app, ["conflicts", "resolve", str(local), "--accept", "local", "--config", cfg])
        assert result.exit_code == 0
        assert "Resolved" in result.output
        assert local.read_text(encoding="utf-8") == orig_content
        assert not orig.exists()


# ─────────────────────────────────────────────────────────────────────────────
# --config / --prefix accepted before the subcommand (#20)
# ─────────────────────────────────────────────────────────────────────────────

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _unwrapped(output: str) -> str:
    """`output` with escapes stripped and whitespace collapsed to single spaces.

    **Rich** wraps this CLI's output — not Click. `main.py` holds module-level Rich
    `Console`s, and `Console.size` reads `COLUMNS` from the live environment at print
    time. Click's own formatter is not in play: Typer's `CliRunner` hardcodes
    `FORCED_WIDTH = 80` inside `isolation()`, so Click's `terminal_width` knob has no
    effect at all here — measured, max line length stays 80 whatever it is set to.

    Which means a phrase an assertion looks for can be split by a line break, and
    whether it is depends on how much interpolated content precedes it.
    `test_resolve_merged_with_conflict_markers_exits_nonzero` asserted
    `"conflict markers" in result.output` and broke between the two words once the
    pytest `tmp_path` reached 126 characters — i.e. **it was green for this
    machine's first 999 pytest runs and went red when the run counter grew a fourth
    digit.** CI cannot catch it either: the Linux tmp_path is 69 characters and never
    breaks at that phrase.

    Collapsing whitespace makes the assertion about the text rather than about the
    terminal, and unlike pinning a wide `COLUMNS` it has no headroom limit — a pin of
    1000 merely moves the first breaking content length out to ~958. Verified holding
    at every width from 8 upward.

    Generalised from `_plain_help`, which already did exactly this for `--help` and
    documents the same cause. The other 42 substring assertions in this file are
    latently exposed the same way and can adopt it as they bite.
    """
    return " ".join(_ANSI_ESCAPE.sub("", output).split())


def _plain_help(*argv: str) -> str:
    """`--help` output as plain text, with the renderer's environment pinned.

    Rich decides independently of the test whether to emit colour and how wide to
    wrap, and it interleaves escape sequences *inside* option names when colour is
    on — so `"--config" in result.output` is false in an environment that forces
    colour even though the help reads correctly on screen.

    That is not hypothetical: this assertion passed locally and failed on all four
    Python versions in CI, because GitHub Actions turns colour on. Pinning
    NO_COLOR and a wide COLUMNS makes the rendering deterministic, and stripping
    any residual escapes makes the assertion about the text rather than about the
    terminal.
    """
    result = runner.invoke(
        app,
        [*argv, "--help"],
        env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200", "FORCE_COLOR": ""},
    )
    assert result.exit_code == 0, result.output
    # Collapse whitespace too: Rich wraps, so a phrase can span a line break.
    return _unwrapped(result.output)


class TestGroupLevelOptions:
    """`docspan --config X status` used to fail with "No such option: --config".

    Both options are now accepted on either side of the subcommand name. The
    group-level values live in a module-level `_GROUP`, so the interesting cases
    are precedence and that a value cannot leak from one invocation into the
    next (these tests share one process via CliRunner).
    """

    def test_config_before_the_subcommand_is_accepted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()) as load:
            result = runner.invoke(app, ["--config", cfg, "status"])
        assert result.exit_code == 0, result.output
        assert "No such option" not in result.output
        # The path really reached config loading, rather than being ignored.
        assert load.call_args[0][0] == cfg

    def test_config_after_the_subcommand_still_works(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()) as load:
            result = runner.invoke(app, ["status", "--config", cfg])
        assert result.exit_code == 0, result.output
        assert load.call_args[0][0] == cfg

    def test_the_subcommand_position_wins_when_both_are_given(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The more specific position takes effect."""
        group_cfg = _cfg_file(tmp_path)
        specific = tmp_path / "specific.yaml"
        specific.write_text("mappings: []\n", encoding="utf-8")
        with patch("docspan.cli.main.load_config", return_value=_config()) as load:
            result = runner.invoke(
                app, ["--config", group_cfg, "status", "--config", str(specific)]
            )
        assert result.exit_code == 0, result.output
        assert load.call_args[0][0] == str(specific)

    def test_a_group_level_value_does_not_leak_into_the_next_invocation(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        """Module-level state is only safe because the callback always resets it.

        Without the reset, the second invocation below would silently reuse the
        first one's config path — and every test in this file that relies on the
        default resolution would depend on invocation order.
        """
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()):
            runner.invoke(app, ["--config", cfg, "status"])
        with patch("docspan.cli.main.load_config", return_value=_config()) as load:
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert load.call_args[0][0] != cfg, "the previous invocation's --config leaked"

    def test_prefix_before_the_subcommand_is_accepted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config()), \
             patch("docspan.cli.main.resolve_active_project", return_value=(cfg, "myprefix")) as resolve:
            result = runner.invoke(app, ["--prefix", "myprefix", "status"])
        assert result.exit_code == 0, result.output
        assert resolve.call_args.kwargs["prefix"] == "myprefix"

    def test_both_options_are_listed_in_the_group_help(self) -> None:
        """#20's actual complaint: `docspan --help` listed only `--help`.

        The help is rendered by Rich (`rich_markup_mode="rich"`), so the
        assertion has to be made against text, not against whatever Rich decided
        to emit for this environment. See _plain_help.
        """
        output = _plain_help()
        assert "--config" in output
        assert "--prefix" in output
        # The group help text must survive adding a callback.
        assert "Push and pull markdown" in output


class TestResolveMappingForPath:
    """resolve_mapping_for_path (Specification pattern) replaces the old
    exact `m.local == file` checks at the map/comments-respond/conflicts-resolve
    call sites so a path inside a sectioned mapping's directory resolves to
    that mapping too, not just an exact single-file match."""

    def test_resolve_mapping_for_path_should_match_sectioned_mapping_when_path_is_a_file_inside_its_directory(self) -> None:  # type: ignore[no-untyped-def]
        sectioned = Mapping(
            local="docs/big-doc", backend="google_docs", remote_id="doc-1",
            sectioned=True, split_level="HEADING_1",
        )
        other = Mapping(local="docs/other.md", backend="google_docs", remote_id="doc-2")

        result = resolve_mapping_for_path([other, sectioned], "docs/big-doc/02-intro.md")

        assert result is sectioned

    def test_resolve_mapping_for_path_should_return_none_when_path_is_outside_any_mapping_directory(self) -> None:  # type: ignore[no-untyped-def]
        sectioned = Mapping(
            local="docs/big-doc", backend="google_docs", remote_id="doc-1",
            sectioned=True, split_level="HEADING_1",
        )
        single_file = Mapping(local="docs/other.md", backend="google_docs", remote_id="doc-2")

        # "docs/big-doc-appendix.md" shares a string prefix with "docs/big-doc"
        # but is not a path *inside* that directory — a naive prefix check
        # (without the os.sep boundary) would wrongly match it.
        result = resolve_mapping_for_path([sectioned, single_file], "docs/big-doc-appendix.md")

        assert result is None
