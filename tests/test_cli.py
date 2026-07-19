"""CLI smoke tests — drives the Typer app through CliRunner.

Mocks at load_config / _get_backend / orchestrate_* boundaries.
Tests verify exit codes and output text; no real backends or network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from docspan.backends.base import Backend, PullResult, PushResult
from docspan.cli.main import LIVE_WEDDING_DOC_ID, SCRATCH_VERIFIED_MARKER, app
from docspan.config import Mapping, MarkgateConfig
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
    auth_setup_called: bool = False
    push_calls: list = field(default_factory=list)

    def push(self, local_path: str, doc_id: str, force: bool = False, **kwargs) -> PushResult:
        self.push_calls.append({"local_path": local_path, "doc_id": doc_id, "force": force})
        return PushResult(status=self.push_status, doc_id=doc_id, url="https://example.com/doc")  # type: ignore[arg-type]

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        return PullResult(status=self.pull_status, doc_id=doc_id, local_path=local_path)  # type: ignore[arg-type]

    def get_remote_version(self, doc_id: str) -> str:
        return self.remote_version

    def auth_setup(self, config_path=None) -> None:
        self.auth_setup_called = True

    def validate_config(self) -> None:
        pass


@dataclass
class FakePushPreview:
    """Minimal stand-in for push_preview.PushPreview — just needs .render()
    and (optionally) .error, mirroring the real dataclass's error field used
    by the CLI's dry-run error handling (getattr(preview, "error", None))."""
    text: str = "Preview: 1 change(s), 0 addition(s), 0 removal(s), 0 unchanged\n  ~ [ ] Splitwise → [x] Splitwise"
    error: Optional[str] = None

    def render(self) -> str:
        return self.text


@dataclass
class FakeBackendWithPreview(FakeBackend):
    """A FakeBackend that also supports preview_push(), for --dry-run tests."""
    name: str = "fake"
    preview_text: Optional[str] = None
    preview_error: Optional[str] = None

    def preview_push(self, local_path: str, doc_id: str) -> FakePushPreview:
        if self.preview_error is not None:
            return FakePushPreview(text=f"✗ dry-run failed: {self.preview_error}", error=self.preview_error)
        if self.preview_text is not None:
            return FakePushPreview(text=self.preview_text)
        return FakePushPreview()


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
# pull
# ─────────────────────────────────────────────────────────────────────────────

class TestPull:
    def test_dry_run_prints_preview_and_exits_zero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        cfg = _cfg_file(tmp_path)
        with patch("docspan.cli.main.load_config", return_value=_config(_mapping(local=str(local)))):
            result = runner.invoke(app, ["pull", "--dry-run", "--config", cfg])
        assert result.exit_code == 0
        assert "dry-run" in result.output

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
        assert "conflict markers" in result.output

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
