"""Tests for GoogleDocsClient.batch_update revision guard and GoogleDocsBackend.push()
conflict handling (RevisionGuard, Epic 1.1 of wedding-planning-workflow).

Mocks at the docs_service / drive_service boundary — no real network calls.

Shared `make_client`/`make_http_error`/`make_backend` factory fixtures live in
tests/conftest.py (also used by tests/test_push_preview.py).
"""
from __future__ import annotations

import json
import pathlib
from typing import Callable, List
from unittest import mock
from unittest.mock import MagicMock

import pytest

from docspan.backends.base import PushResult
from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.backends.google_docs.push_preview import HighRiskParagraph, PushPlan

# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsClient.batch_update — writeControl.requiredRevisionId
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchUpdateRevisionGuard:
    def test_batch_update_includes_write_control_when_required_revision_id_given(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        execute_mock = client.docs_service.documents.return_value.batchUpdate.return_value.execute
        execute_mock.return_value = {"documentId": "doc-1"}

        requests = [{"insertText": {"location": {"index": 1}, "text": "hi"}}]
        client.batch_update("doc-1", requests, required_revision_id="ALm37abc")

        _, kwargs = client.docs_service.documents.return_value.batchUpdate.call_args
        assert kwargs["documentId"] == "doc-1"
        assert kwargs["body"]["requests"] == requests
        assert kwargs["body"]["writeControl"] == {"requiredRevisionId": "ALm37abc"}

    def test_batch_update_omits_write_control_when_required_revision_id_is_none(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        execute_mock = client.docs_service.documents.return_value.batchUpdate.return_value.execute
        execute_mock.return_value = {"documentId": "doc-1"}

        requests = [{"insertText": {"location": {"index": 1}, "text": "hi"}}]
        client.batch_update("doc-1", requests)

        _, kwargs = client.docs_service.documents.return_value.batchUpdate.call_args
        assert kwargs["body"]["requests"] == requests
        assert "writeControl" not in kwargs["body"]


# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsBackend.push() — threads revisionId, handles stale-revision conflict
# ─────────────────────────────────────────────────────────────────────────────

def _empty_doc(revision_id: str = "ALm37abc") -> dict:
    """A minimal Google Doc resource with an empty body and a given revisionId."""
    return {
        "revisionId": revision_id,
        "body": {"content": []},
    }


class TestPushRevisionGuard:
    def test_push_passes_fetched_revision_id_into_batch_update(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok"
        assert fake_client.batch_update.call_count == 1
        args, kwargs = fake_client.batch_update.call_args
        assert args[0] == "doc-1"
        assert kwargs["required_revision_id"] == "ALm37abc"

    def test_push_returns_conflict_status_with_friendly_message_on_stale_revision(
        self,
        tmp_path,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
        make_http_error: Callable[[int, str], object],
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = make_http_error(
            400, "Invalid requests[0]: requiredRevisionId does not match current revision"
        )

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result == PushResult(
            status="conflict",
            doc_id="doc-1",
            message="The doc changed since your last pull — run `docspan pull` again",
        )

    def test_push_returns_error_status_for_non_revision_http_error(
        self,
        tmp_path,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
        make_http_error: Callable[[int, str], object],
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = make_http_error(500, "Internal server error")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "error"
        assert result.message != "The doc changed since your last pull — run `docspan pull` again"

    def test_push_returns_error_status_for_generic_exception(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = RuntimeError("network exploded")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "error"
        assert "network exploded" in (result.message or "")

    def test_push_does_not_call_batch_update_when_no_changes(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")

        local = tmp_path / "doc.md"
        local.write_text("", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        fake_client.batch_update.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# High-risk gate — PushPlan single-fetch invariant, blocked/force paths
# (Epic 1.2, Story 1.2.3, plan.md Task 1.2.3d)
# ─────────────────────────────────────────────────────────────────────────────

def _checkbox_glyph_doc(revision_id: str = "rev-checkbox") -> dict:
    """A doc with one paragraph that resolves as a native BULLET_CHECKBOX
    glyph — GlyphShapeCheck must flag any change to it as high_risk, even
    with zero open comments."""
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 21,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "elements": [{"textRun": {"content": "[ ] Whatsapp group\n"}}],
                        "bullet": {"listId": "kix.abc", "nestingLevel": 0},
                    },
                }
            ]
        },
        "lists": {
            "kix.abc": {
                "listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}
            }
        },
    }


class TestPushHighRiskGate:
    def test_preview_push_never_calls_batch_update_even_when_high_risk(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _checkbox_glyph_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("- [x] Whatsapp group\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert len(preview.high_risk) == 1
        assert preview.high_risk[0].reasons == ["native_glyph"]
        fake_client.batch_update.assert_not_called()

    def test_push_blocks_on_high_risk_using_exactly_one_fetch_it_performed_itself(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _checkbox_glyph_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("- [x] Whatsapp group\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "blocked"
        assert "NATIVE CHECKBOX GLYPH" in (result.message or "")
        fake_client.batch_update.assert_not_called()
        # Proves the block decision came from push()'s own single fetch —
        # not a stale externally-supplied preview, and not a duplicate-fetch
        # design (the backstop's second list_comments call never fires
        # because batch_update was never reached).
        assert fake_client.get_document.call_count == 1
        assert fake_client.list_comments.call_count == 1

    def test_push_force_true_proceeds_using_revision_id_from_its_own_fetch(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _checkbox_glyph_doc(revision_id="rev-force")
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("- [x] Whatsapp group\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=True)

        assert result.status == "ok"
        fake_client.batch_update.assert_called_once()
        args, kwargs = fake_client.batch_update.call_args
        assert kwargs["required_revision_id"] == "rev-force"

        # Assert on the actual request payload, not just that *a* call
        # happened — a corrupting diff (e.g. a spurious delete of a
        # neighboring paragraph, or the old "[ ] " marker surviving
        # unflipped) would still satisfy assert_called_once() but must fail
        # here.
        doc_id, requests = args
        assert doc_id == "doc-1"
        insert_texts = [
            r["insertText"]["text"] for r in requests if "insertText" in r
        ]
        # No trailing "\n": this paragraph is the last (only) one in the doc,
        # so _make_insert_requests's bare_last mode reuses the deleted
        # range's own clamp-spared terminal newline instead of writing a
        # second one (see its docstring).
        assert insert_texts == ["[x] Whatsapp group"]
        delete_ranges = [
            r["deleteContentRange"]["range"] for r in requests if "deleteContentRange" in r
        ]
        assert delete_ranges == [{"startIndex": 1, "endIndex": 20}]
        # The original unchecked marker must not appear anywhere in the
        # requests sent to Docs — proves the escape hatch actually replaced
        # the literal text rather than layering on top of it.
        assert not any("[ ] Whatsapp group" in json.dumps(r) for r in requests)


# ─────────────────────────────────────────────────────────────────────────────
# preview_push() cross-tab dry-run parity with push()
#
# unresolved_anchors' own contract is "never over-report" — a dry run must not
# name an anchor as dead when push() would actually resolve it into a sibling
# tab's heading via Link.heading={id,tabId}.
# ─────────────────────────────────────────────────────────────────────────────

def _multi_tab_doc_for_dry_run() -> dict:
    return {
        "revisionId": "rev-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.cur", "title": "Current"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 15,
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "HEADING_2",
                                        "headingId": "h.cur",
                                    },
                                    "elements": [{"textRun": {"content": "Current state\n"}}],
                                },
                            },
                            {
                                "startIndex": 15,
                                "endIndex": 22,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                    "elements": [{"textRun": {"content": "see it\n"}}],
                                },
                            },
                        ]
                    },
                    "lists": {},
                },
                "childTabs": [],
            },
            {
                "tabProperties": {"tabId": "t.other", "title": "Other"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 14,
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "HEADING_2",
                                        "headingId": "h.other",
                                    },
                                    "elements": [{"textRun": {"content": "Other heading\n"}}],
                                },
                            }
                        ]
                    },
                    "lists": {},
                },
                "childTabs": [],
            },
        ],
    }


class TestDryRunCrossTabParity:
    def test_preview_push_does_not_over_report_an_anchor_push_would_resolve(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        doc = _multi_tab_doc_for_dry_run()
        fake_client.get_document.return_value = doc
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("## Current state\n\nsee [it](#h.other)\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert preview.unresolved_anchors == [], preview.unresolved_anchors

        backend.push(str(local), "doc-1")
        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for call in fake_client.batch_update.call_args_list
            for request in call.args[1]
            if "updateTextStyle" in request
            and "link" in request["updateTextStyle"].get("textStyle", {})
        ]
        assert {"heading": {"id": "h.other", "tabId": "t.other"}} in links, links

    def test_preview_push_still_reports_a_genuinely_unresolvable_anchor(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc_for_dry_run()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("## Current state\n\nsee [it](#h.nonexistent)\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert preview.unresolved_anchors == ["#h.nonexistent"]


# ─────────────────────────────────────────────────────────────────────────────
# push()'s temp-Drive-upload cleanup — blocked/skipped/success paths all
# route through the same best-effort _cleanup_temp_uploads() helper (round-2
# review finding: two of these previously called client.delete_temp_upload()
# in a bare loop with no exception handling, so a single transient Drive
# delete failure turned an otherwise-successful/skipped push into a reported
# "error", and the blocked path reported the full upload list as retryable
# even when cleanup succeeded).
#
# _build_push_plan is monkeypatched to return a hand-built PushPlan so each
# test isolates push()'s cleanup handling from the diff/build logic that
# produces temp_drive_file_ids in the first place (covered elsewhere, e.g.
# tests/test_gdocs_images.py).
# ─────────────────────────────────────────────────────────────────────────────

def _canned_plan(
    *, requests, high_risk=None, target_nodes=None, temp_drive_file_ids=None
) -> "PushPlan":
    return PushPlan(
        current_nodes=[],
        target_nodes=target_nodes or [],
        requests=requests,
        doc={"revisionId": "rev-1"},
        entries=[],
        unchanged_count=0,
        comments=[],
        high_risk=high_risk or [],
        temp_drive_file_ids=temp_drive_file_ids or [],
    )


class TestPushTempDriveCleanup:
    def test_blocked_push_cleans_up_and_reports_none_retryable_on_success(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[{"insertText": {"location": {"index": 1}, "text": "x"}}],
            high_risk=[HighRiskParagraph(paragraph_text="p", reasons=["native_glyph"])],
            temp_drive_file_ids=["file-1", "file-2"],
        )
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "blocked"
        assert fake_client.delete_temp_upload.call_count == 2
        assert result.retryable_temp_drive_file_ids == []

    def test_blocked_push_still_reports_files_delete_temp_upload_failed_on(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[{"insertText": {"location": {"index": 1}, "text": "x"}}],
            high_risk=[HighRiskParagraph(paragraph_text="p", reasons=["native_glyph"])],
            temp_drive_file_ids=["file-1"],
        )
        fake_client.delete_temp_upload.side_effect = Exception("transient Drive error")
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "blocked"
        assert result.retryable_temp_drive_file_ids == ["file-1"]

    def test_skipped_push_cleans_up_temp_uploads(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[], temp_drive_file_ids=["file-1"]
        )
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "skipped"
        fake_client.delete_temp_upload.assert_called_once_with("file-1")

    def test_skipped_push_is_not_downgraded_to_error_when_cleanup_fails(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[], temp_drive_file_ids=["file-1"]
        )
        fake_client.delete_temp_upload.side_effect = Exception("transient Drive error")
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        # This is the actual regression: a bare delete_temp_upload loop with
        # no exception handling used to let this propagate out of push() as
        # an uncaught exception (or get misreported as status="error"),
        # turning a true no-op into a false failure.
        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "skipped"

    def test_successful_push_cleans_up_temp_uploads(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.list_comments.return_value = []
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[{"insertText": {"location": {"index": 1}, "text": "x"}}],
            temp_drive_file_ids=["file-1"],
        )
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "ok"
        fake_client.delete_temp_upload.assert_called_once_with("file-1")

    def test_successful_push_is_not_downgraded_to_error_when_cleanup_fails(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.list_comments.return_value = []
        backend._build_push_plan = lambda *a, **kw: _canned_plan(  # type: ignore[method-assign]
            requests=[{"insertText": {"location": {"index": 1}, "text": "x"}}],
            temp_drive_file_ids=["file-1"],
        )
        fake_client.delete_temp_upload.side_effect = Exception("transient Drive error")
        local = tmp_path / "doc.md"
        local.write_text("hi\n", encoding="utf-8")

        # The other actual regression: a batch_update that genuinely
        # succeeded must not be reported as "error" just because the
        # best-effort Drive cleanup that follows it hit a transient failure.
        result = backend.push(str(local), "doc-1", force=False)

        assert result.status == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# preview_push() exception handling — mirrors push()'s try/except pattern
# around _build_push_plan() so a --dry-run failure (expired auth, network
# error, malformed doc) never propagates a raw exception (Phase 6 verify
# finding: preview_push() previously had no exception handling at all).
# ─────────────────────────────────────────────────────────────────────────────

class TestPreviewPushExceptionHandling:
    def test_preview_push_returns_error_preview_instead_of_raising_on_http_error(
        self,
        tmp_path,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
        make_http_error: Callable[[int, str], object],
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.side_effect = make_http_error(401, "Invalid credentials")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert preview.error is not None
        assert preview.entries == []
        assert preview.high_risk == []
        assert preview.request_count == 0
        fake_client.batch_update.assert_not_called()

    def test_preview_push_returns_error_preview_instead_of_raising_on_generic_exception(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.side_effect = RuntimeError("network exploded")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert preview.error is not None
        assert "network exploded" in preview.error

    def test_preview_push_error_renders_as_one_clean_line_not_a_traceback(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.side_effect = RuntimeError("boom")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")
        rendered = preview.render()

        assert rendered == "✗ dry-run failed: boom"
        assert "Traceback" not in rendered


class TestDiffTooExpensiveSurfacesAsUserFacingError:
    """AC6: DiffTooExpensive raised while building a push plan must be caught
    by push() and reported through PushResult, never an uncaught traceback —
    same shape as the HttpError/generic-exception handling above."""

    def test_push_returns_error_status_instead_of_raising(
        self, tmp_path, monkeypatch, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs.docs_request_builder import (
            DiffTooExpensive,
            DocsRequestBuilder,
        )

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")

        def _raise_too_expensive(*args: object, **kwargs: object) -> None:
            raise DiffTooExpensive("document", 6000, 3000)

        monkeypatch.setattr(DocsRequestBuilder, "build", _raise_too_expensive)

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "error"
        assert result.message is not None
        fake_client.batch_update.assert_not_called()

    def test_push_error_message_is_the_diff_too_expensive_message_not_a_traceback(
        self, tmp_path, monkeypatch, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs.docs_request_builder import (
            DiffTooExpensive,
            DocsRequestBuilder,
        )

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")

        def _raise_too_expensive(*args: object, **kwargs: object) -> None:
            raise DiffTooExpensive("document", 6000, 3000)

        monkeypatch.setattr(DocsRequestBuilder, "build", _raise_too_expensive)

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.message == str(DiffTooExpensive("document", 6000, 3000))
        assert "Traceback" not in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKER regression: a failed local-image resolution must not read as
# "image deleted" to the diff. `_build_push_plan` substitutes the original
# (pre-resolution) DocsImageNode for a slot that failed to resolve instead
# of dropping it, so `_node_key`/`_content_key` (which key images on
# alt/width_pt/height_pt, never src) still see "unchanged" against the
# image already live in the doc.
# ─────────────────────────────────────────────────────────────────────────────


def _doc_with_existing_image() -> dict:
    """A live doc that already has one inline image, matching how a
    previous, successful push would have left it. No `size` -- the
    markdown parser never produces width_pt/height_pt (see
    MarkdownToParagraphParser), so the current doc's image must also have
    none of its own for the substituted node's identity to match."""
    return {
        "revisionId": "ALm37abc",
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 2,
                    "paragraph": {
                        "elements": [
                            {"inlineObjectElement": {"inlineObjectId": "kix.obj1"}},
                        ]
                    },
                },
                {
                    "startIndex": 2,
                    "endIndex": 3,
                    "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
                },
            ]
        },
        "inlineObjects": {
            "kix.obj1": {
                "inlineObjectProperties": {
                    "embeddedObject": {
                        "contentUri": "https://docs.google.com/existing-content-uri",
                        "description": "a diagram",
                    }
                }
            }
        },
    }


class TestFailedImageResolutionDoesNotDeleteExistingImage:
    def test_build_push_plan_emits_no_delete_when_local_image_is_missing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_existing_image()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        # References a local file that does not exist -- simulates the
        # local image having been renamed/deleted (or a transient upload
        # error) on a push that happens *after* the image was already
        # synced to the live doc once.
        local.write_text("![a diagram](./missing.png)\n", encoding="utf-8")

        plan = backend._build_push_plan(str(local), "doc-1")

        assert plan.image_warnings and "missing.png" in plan.image_warnings[0]
        # The real bug: dropping the unresolved node from target_nodes made
        # the diff see "present in current, absent in target" and emit a
        # delete for the image already in the doc.
        assert not any("deleteContentRange" in r for r in plan.requests)
        assert not any("insertInlineImage" in r for r in plan.requests)
        fake_client.upload_temp_image.assert_not_called()

    def test_push_reports_skipped_not_a_delete_when_local_image_is_missing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_existing_image()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./missing.png)\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        fake_client.batch_update.assert_not_called()


class TestFailedResolutionOfNewImageDropsNodeInsteadOfEmptyUri:
    """The actual reported bug: a brand-new (never-before-synced) mermaid
    diagram whose render fails has no existing-doc node to fall back to, and
    the pre-resolution node's `src` is always "" for a ```mermaid fence
    (_mermaid_image_node never sets it). Falling back to it produced an
    insertInlineImage request with an empty uri, rejected by the Docs API
    with "The URL should not be empty." The fix drops the node instead.
    """

    def test_build_push_plan_emits_no_empty_uri_insert_when_new_mermaid_render_fails(
        self,
        tmp_path,
        monkeypatch,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs import image_source
        from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []

        def _raise(*args: object, **kwargs: object) -> None:
            raise MermaidRenderError("mmdc not found")

        monkeypatch.setattr(image_source, "render_mermaid_png", _raise)

        local = tmp_path / "doc.md"
        local.write_text("```mermaid\ngraph TD; A-->B;\n```\n", encoding="utf-8")

        plan = backend._build_push_plan(str(local), "doc-1")

        assert plan.image_warnings and "mermaid render failed" in plan.image_warnings[0]
        assert not any(
            "insertInlineImage" in r and not r["insertInlineImage"].get("uri")
            for r in plan.requests
        )
        fake_client.upload_temp_image.assert_not_called()

    def test_push_reports_skipped_not_a_failed_batch_update(
        self,
        tmp_path,
        monkeypatch,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs import image_source
        from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []

        def _raise(*args: object, **kwargs: object) -> None:
            raise MermaidRenderError("mmdc not found")

        monkeypatch.setattr(image_source, "render_mermaid_png", _raise)

        local = tmp_path / "doc.md"
        local.write_text("```mermaid\ngraph TD; A-->B;\n```\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        fake_client.batch_update.assert_not_called()


class TestFailedResolutionOfNewPlainImageDropsNodeInsteadOfEmptyUri:
    """Same drop-vs-substitute decision as the mermaid case above, but for a
    plain `![alt](path)` reference to a missing local file -- the generic
    (non-mermaid) new-image path through the same substituted_images/
    existing_image_alts logic in backend.py, previously only exercised via
    ```mermaid fences."""

    def test_build_push_plan_emits_no_empty_uri_insert_for_new_plain_image(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./missing.png)\n", encoding="utf-8")

        plan = backend._build_push_plan(str(local), "doc-1")

        assert plan.image_warnings and "missing.png" in plan.image_warnings[0]
        assert not any("insertInlineImage" in r for r in plan.requests)
        fake_client.upload_temp_image.assert_not_called()

    def test_push_reports_skipped_not_a_failed_batch_update_for_new_plain_image(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./missing.png)\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        fake_client.batch_update.assert_not_called()


def _doc_with_existing_mermaid_image(alt: str) -> dict:
    """Same shape as `_doc_with_existing_image`, but keyed by a caller-supplied
    alt so a test can match it against `_mermaid_image_node`'s content-hash alt
    (`f"mermaid diagram {sha256(diagram)[:12]}"`)."""
    return {
        "revisionId": "ALm37abc",
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 2,
                    "paragraph": {
                        "elements": [
                            {"inlineObjectElement": {"inlineObjectId": "kix.obj1"}},
                        ]
                    },
                },
                {
                    "startIndex": 2,
                    "endIndex": 3,
                    "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
                },
            ]
        },
        "inlineObjects": {
            "kix.obj1": {
                "inlineObjectProperties": {
                    "embeddedObject": {
                        "contentUri": "https://docs.google.com/existing-content-uri",
                        "description": alt,
                    }
                }
            }
        },
    }


class TestFailedReRenderOfExistingMermaidImageDoesNotDeleteIt:
    """The already-synced counterpart to
    TestFailedResolutionOfNewImageDropsNodeInsteadOfEmptyUri: a mermaid
    diagram that was successfully pushed before (so the live doc has an
    image whose alt matches this fence's content-hash alt) must survive a
    later re-render failure via the existing_image_alts fallback, not be
    dropped/deleted."""

    _DIAGRAM = "graph TD; A-->B;"

    @staticmethod
    def _alt_for(diagram: str) -> str:
        import hashlib

        digest = hashlib.sha256(diagram.encode("utf-8")).hexdigest()[:12]
        return f"mermaid diagram {digest}"

    def test_build_push_plan_falls_back_to_original_node_not_a_delete(
        self,
        tmp_path,
        monkeypatch,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs import image_source
        from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError

        backend, fake_client = make_backend()
        alt = self._alt_for(self._DIAGRAM)
        fake_client.get_document.return_value = _doc_with_existing_mermaid_image(alt)
        fake_client.list_comments.return_value = []

        def _raise(*args: object, **kwargs: object) -> None:
            raise MermaidRenderError("mmdc not found")

        monkeypatch.setattr(image_source, "render_mermaid_png", _raise)

        local = tmp_path / "doc.md"
        local.write_text(f"```mermaid\n{self._DIAGRAM}\n```\n", encoding="utf-8")

        plan = backend._build_push_plan(str(local), "doc-1")

        assert plan.image_warnings and "mermaid render failed" in plan.image_warnings[0]
        assert not any("deleteContentRange" in r for r in plan.requests)
        assert not any("insertInlineImage" in r for r in plan.requests)
        fake_client.upload_temp_image.assert_not_called()

    def test_push_reports_skipped_not_a_delete(
        self,
        tmp_path,
        monkeypatch,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs import image_source
        from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError

        backend, fake_client = make_backend()
        alt = self._alt_for(self._DIAGRAM)
        fake_client.get_document.return_value = _doc_with_existing_mermaid_image(alt)
        fake_client.list_comments.return_value = []

        def _raise(*args: object, **kwargs: object) -> None:
            raise MermaidRenderError("mmdc not found")

        monkeypatch.setattr(image_source, "render_mermaid_png", _raise)

        local = tmp_path / "doc.md"
        local.write_text(f"```mermaid\n{self._DIAGRAM}\n```\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        fake_client.batch_update.assert_not_called()


class TestMixedImageResolutionOutcomes:
    """Two images in the same push, one resolving and one (brand-new) failing
    -- exercises the shared `subst_iter`/zip(resolved_images, image_nodes)
    splice in backend.py across more than one image, where an off-by-one
    would misattribute a success/failure outcome to the wrong node."""

    def test_failed_new_image_is_dropped_while_the_other_still_gets_inserted(
        self,
        tmp_path,
        monkeypatch,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs import image_source
        from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []
        fake_client.upload_temp_image.return_value = {
            "file_id": "file-1",
            "uri": "https://drive.example.com/file-1",
        }

        def _fail_second_diagram_only(diagram: str, *args: object, **kwargs: object) -> bytes:
            if "B-->C" in diagram:
                raise MermaidRenderError("mmdc not found")
            return b"\x89PNG\r\n\x1a\n" + b"pngbytes"

        monkeypatch.setattr(image_source, "render_mermaid_png", _fail_second_diagram_only)

        local = tmp_path / "doc.md"
        local.write_text(
            "```mermaid\ngraph TD; A-->B;\n```\n\n"
            "```mermaid\ngraph TD; B-->C;\n```\n",
            encoding="utf-8",
        )

        plan = backend._build_push_plan(str(local), "doc-1")

        assert len(plan.image_warnings) == 1
        assert "mermaid render failed" in plan.image_warnings[0]
        insert_uris = [
            r["insertInlineImage"]["uri"]
            for r in plan.requests
            if "insertInlineImage" in r
        ]
        assert insert_uris == ["https://drive.example.com/file-1"]
        fake_client.upload_temp_image.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsClient.upload_temp_image / delete_temp_upload -- direct coverage
# against a mocked docs_service/drive_service (previously untested at this
# boundary; see TestBatchUpdateRevisionGuard above for the same pattern).
# ─────────────────────────────────────────────────────────────────────────────


class TestUploadTempImage:
    def test_returns_file_id_and_uri(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.create.return_value.execute.return_value = {
            "id": "file-1"
        }
        client.drive_service.permissions.return_value.create.return_value.execute.return_value = {}

        result = client.upload_temp_image(b"pngbytes", "diagram.png", "image/png")

        assert result == {
            "file_id": "file-1",
            "uri": "https://drive.google.com/uc?export=view&id=file-1",
        }

    def test_uploads_with_given_filename(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.create.return_value.execute.return_value = {
            "id": "file-1"
        }
        client.drive_service.permissions.return_value.create.return_value.execute.return_value = {}

        client.upload_temp_image(b"pngbytes", "diagram.png", "image/png")

        _, kwargs = client.drive_service.files.return_value.create.call_args
        assert kwargs["body"] == {"name": "diagram.png"}

    def test_shares_publicly_with_allow_file_discovery_false(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        """MAJOR security fix: a public-with-discovery share on a Drive file
        makes it turn up in search for anyone, not just holders of the link
        insertInlineImage needs. `allowFileDiscovery: False` keeps it
        link-only for the temp file's brief public window."""
        client = make_client()
        client.drive_service.files.return_value.create.return_value.execute.return_value = {
            "id": "file-1"
        }
        client.drive_service.permissions.return_value.create.return_value.execute.return_value = {}

        client.upload_temp_image(b"pngbytes", "diagram.png", "image/png")

        _, kwargs = client.drive_service.permissions.return_value.create.call_args
        assert kwargs["fileId"] == "file-1"
        assert kwargs["body"] == {
            "role": "reader",
            "type": "anyone",
            "allowFileDiscovery": False,
        }

    def test_deletes_orphan_and_reraises_when_share_fails(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.create.return_value.execute.return_value = {
            "id": "file-1"
        }
        client.drive_service.permissions.return_value.create.return_value.execute.side_effect = (
            RuntimeError("share failed")
        )
        client.drive_service.files.return_value.delete.return_value.execute.return_value = {}

        with pytest.raises(RuntimeError, match="share failed"):
            client.upload_temp_image(b"pngbytes", "diagram.png", "image/png")

        _, kwargs = client.drive_service.files.return_value.delete.call_args
        assert kwargs["fileId"] == "file-1"


class TestDeleteTempUpload:
    def test_deletes_by_file_id(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.delete.return_value.execute.return_value = {}

        client.delete_temp_upload("file-1")

        _, kwargs = client.drive_service.files.return_value.delete.call_args
        assert kwargs["fileId"] == "file-1"

    def test_tolerates_404(
        self,
        make_client: Callable[[], GoogleDocsClient],
        make_http_error: Callable[[int, str], object],
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.delete.return_value.execute.side_effect = (
            make_http_error(404, "File not found")
        )

        client.delete_temp_upload("file-1")  # must not raise

    def test_reraises_non_404_errors(
        self,
        make_client: Callable[[], GoogleDocsClient],
        make_http_error: Callable[[int, str], object],
    ) -> None:
        client = make_client()
        client.drive_service.files.return_value.delete.return_value.execute.side_effect = (
            make_http_error(500, "Internal error")
        )

        with pytest.raises(Exception):
            client.delete_temp_upload("file-1")


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL: _build_push_plan's own exception-cleanup path and preview_push's
# dry-run cleanup path, exercised through a real local image reference
# rather than a monkeypatched _build_push_plan (TestPushTempDriveCleanup
# above never runs the code inside _build_push_plan/preview_push at all).
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildPushPlanCleansUpOnException:
    def test_exception_after_upload_deletes_the_temp_upload(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.upload_temp_image.return_value = {
            "file_id": "file-1",
            "uri": "https://drive.example.com/file-1",
        }
        # get_document is the first thing _build_push_plan does after
        # resolving images -- raising here lands squarely inside the
        # try/except that's supposed to clean up temp_drive_file_ids.
        fake_client.get_document.side_effect = RuntimeError("network exploded")

        png = tmp_path / "diagram.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./diagram.png)\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="network exploded"):
            backend._build_push_plan(str(local), "doc-1")

        fake_client.delete_temp_upload.assert_called_once_with("file-1")

    def test_cleanup_failure_does_not_mask_the_original_exception(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.upload_temp_image.return_value = {
            "file_id": "file-1",
            "uri": "https://drive.example.com/file-1",
        }
        fake_client.get_document.side_effect = RuntimeError("network exploded")
        fake_client.delete_temp_upload.side_effect = Exception("drive delete also failed")

        png = tmp_path / "diagram.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./diagram.png)\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="network exploded"):
            backend._build_push_plan(str(local), "doc-1")


class TestPreviewPushDeletesTempUploadsAfterDryRun:
    def test_preview_push_deletes_temp_upload_it_made(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.upload_temp_image.return_value = {
            "file_id": "file-1",
            "uri": "https://drive.example.com/file-1",
        }
        fake_client.get_document.return_value = _empty_doc()
        fake_client.list_comments.return_value = []

        png = tmp_path / "diagram.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        local = tmp_path / "doc.md"
        local.write_text("![a diagram](./diagram.png)\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert preview.error is None
        fake_client.delete_temp_upload.assert_called_once_with("file-1")
        fake_client.batch_update.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# CommentCountBackstop (plan.md Task 1.2.3c/1.2.3d)
# ─────────────────────────────────────────────────────────────────────────────

class TestCommentCountBackstop:
    def test_push_appends_comment_count_dropped_warning_when_post_push_count_is_lower(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.side_effect = [
            [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],  # before batch_update (in PushPlan)
            [{"id": "c1"}, {"id": "c2"}],  # after batch_update (backstop re-check)
        ]

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "warning"
        assert "⚠ open comment count dropped (3→2)" in (result.message or "")

    def test_push_message_has_no_drop_warning_when_comment_count_unchanged(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.side_effect = [
            [{"id": "c1"}],
            [{"id": "c1"}],
        ]

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok"
        assert "dropped" not in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# tab_id (Mapping.tab_id) — pull()/push() targeting a specific tab of a
# multi-tab doc, and the backward-compatible no-tab_id case.
# ─────────────────────────────────────────────────────────────────────────────

def _tab(tab_id: str, title: str, text: str) -> dict:
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + len(text) + 1,
                        "paragraph": {
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                            "elements": [{"textRun": {"content": text + "\n"}}],
                        },
                    }
                ]
            },
            "lists": {},
        },
        "childTabs": [],
    }


def _multi_tab_doc(revision_id: str = "rev-tabs") -> dict:
    return {
        "revisionId": revision_id,
        "body": {"content": []},
        "tabs": [
            _tab("t.first", "Overview", "First tab content"),
            _tab("t.second", "Details", "Second tab content"),
        ],
    }


class TestPullTabId:
    def test_pull_with_tab_id_uses_structural_path_and_writes_that_tabs_content(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local), tab_id="t.second")

        assert result.status == "ok"
        content = local.read_text(encoding="utf-8")
        assert "Second tab content" in content
        assert "First tab content" not in content
        # Structural path (get_document), never Drive's HTML export, when
        # tab_id is given — Drive export can't target a specific tab.
        fake_client.get_doc_content.assert_not_called()

    def test_pull_with_unknown_tab_id_returns_error_status(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local), tab_id="t.nonexistent")

        assert result.status == "error"
        assert "t.nonexistent" in (result.message or "")

    def test_pull_without_tab_id_on_multi_tab_doc_uses_html_export_and_warns(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Backward-compatible no-tab_id case: default pull path (Drive HTML
        export) is unchanged, but a multi-tab doc now escalates to
        'warning' instead of silently claiming 'ok' on a doc where the
        export always returns just the first tab."""
        backend, fake_client = make_backend()
        fake_client.get_doc_content.return_value = "<p>First tab content</p>"
        fake_client.get_document.return_value = _multi_tab_doc()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "warning"
        assert "Overview" in (result.message or "") and "Details" in (result.message or "")
        fake_client.get_doc_content.assert_called_once()

    def test_pull_without_tab_id_ambiguity_check_runs_before_destructive_html_export(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Regression test: the multi-tab ambiguity check (get_document +
        resolve_document_tab) must run BEFORE the destructive Drive HTML
        export/write (get_doc_content), not after — otherwise the file gets
        overwritten via a lossy markdown round trip before the ambiguity is
        even detected. Asserting outcomes alone (status='warning') doesn't
        prove ordering, since those pass identically either way — this
        checks call order directly via mock_calls on the shared client mock.
        """
        backend, fake_client = make_backend()
        fake_client.get_doc_content.return_value = "<p>First tab content</p>"
        fake_client.get_document.return_value = _multi_tab_doc()

        local = tmp_path / "doc.md"
        backend.pull("doc-1", str(local))

        call_names = [c[0] for c in fake_client.mock_calls if c[0] in ("get_document", "get_doc_content")]
        assert call_names == ["get_document", "get_doc_content"], (
            f"expected get_document before get_doc_content, got {call_names}"
        )

    def test_pull_aborts_before_destructive_write_when_get_document_fails(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Regression test: if the ambiguity check's get_document() call raises,
        pull() must return status='error' and must NOT have performed the
        destructive Drive export/write — proving the safety-check-first
        ordering actually protects the local file, not just that the
        happy-path status looks right."""
        backend, fake_client = make_backend()
        fake_client.get_document.side_effect = RuntimeError("boom")
        fake_client.get_doc_content.return_value = "<p>Should never be written</p>"

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "error"
        assert not local.exists()
        fake_client.get_doc_content.assert_not_called()

    def test_pull_without_tab_id_on_single_tab_legacy_doc_stays_ok(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Backward-compatible no-tab_id case: a doc with no `tabs` field at
        all (pre-tabs-support / single-tab doc) pulls exactly as before."""
        backend, fake_client = make_backend()
        fake_client.get_doc_content.return_value = "<p>Hello</p>"
        fake_client.get_document.return_value = _empty_doc()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "ok"
        assert result.message is None


# ─────────────────────────────────────────────────────────────────────────────
# unreadable_links — bookmark/tabId links reported as a pull warning, but only
# on the tab-scoped structural path (issue #38). The default path's markdown
# comes from Drive's HTML export, which renders these correctly — reporting
# there was the exact regression shipped on feat/internal-anchors-full
# (f0294a8) and corrected by 62aa8d1.
# ─────────────────────────────────────────────────────────────────────────────

def _bookmark_link_tab(tab_id: str, title: str) -> dict:
    text = "see it"
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + len(text) + 1,
                        "paragraph": {
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                            "elements": [
                                {"textRun": {
                                    "content": text,
                                    "textStyle": {"link": {"bookmarkId": "kix.b1"}},
                                }},
                                {"textRun": {"content": "\n", "textStyle": {}}},
                            ],
                        },
                    }
                ]
            },
            "lists": {},
        },
        "childTabs": [],
    }


def _single_tab_doc_with_bookmark_link(revision_id: str = "rev-bookmark") -> dict:
    """A doc with exactly one tab (no multi-tab ambiguity) whose only link is
    a bookmark link — unreadable on the structural path, fine on Drive's
    HTML export."""
    return {
        "revisionId": revision_id,
        "body": {"content": []},
        "tabs": [_bookmark_link_tab("t.only", "Only")],
    }


class TestPullReportsUnreadableLinks:
    def test_tab_scoped_pull_warns_and_names_the_bookmark_link(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _single_tab_doc_with_bookmark_link()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local), tab_id="t.only")

        assert result.status == "warning"
        assert "bookmark link" in (result.message or "")

    def test_default_pull_never_reports_unreadable_links_for_the_same_doc(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """The exact regression guard: the same doc that warns under
        --tab-id must not warn (or must not warn about links) when pulled
        the default way, since Drive's HTML export renders the bookmark link
        correctly and the throwaway DocsStructureParser built only for the
        heading-slug map must never be consulted for unreadable_links here."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _single_tab_doc_with_bookmark_link()
        fake_client.get_doc_content.return_value = '<p><a href="#bookmark=kix.b1">see it</a></p>'

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "ok"
        assert result.message is None


# ─────────────────────────────────────────────────────────────────────────────
# Default (non-tab) path — checkbox round trip regression (AC4, issue #17).
#
# The tab-scoped path's zero-edit corruption came from DocsRequestBuilder's
# diff key not accounting for the synthetic "[ ] " prefix that the
# *structural* renderer (nodes_to_markdown.py) puts on a native checkbox
# paragraph. The default path never goes through that renderer at all: it
# exports via Drive's HTML API and DocumentConverter.html_to_markdown(),
# which has no glyph/checkbox awareness whatsoever (verified: no "checkbox"
# or bracket handling anywhere in converter.py) and renders any <li> — a
# native checkbox item included — as a plain "- text" bullet with no
# bracket marker. So the bug this ticket fixes cannot occur on this path;
# this test locks that in as a regression guard.
# ─────────────────────────────────────────────────────────────────────────────

def _native_checkbox_doc(revision_id: str = "rev-default-checkbox") -> dict:
    """A doc with one clean (uncorrupted) native BULLET_CHECKBOX paragraph —
    text is just "Whatsapp group", with checkbox state carried only by the
    bullet's glyph type, never as literal bracket text."""
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 16,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "elements": [{"textRun": {"content": "Whatsapp group\n"}}],
                        "bullet": {"listId": "kix.abc", "nestingLevel": 0},
                    },
                }
            ]
        },
        "lists": {
            "kix.abc": {
                "listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}
            }
        },
    }


def _doc_with_native_checkboxes(*items: tuple, revision_id: str = "rev-checkboxes") -> dict:
    """A doc whose body has one bullet paragraph per (text, nesting_level) in
    `items`, each resolving as a native BULLET_CHECKBOX glyph (glyphType
    GLYPH_TYPE_UNSPECIFIED — see docs_structure_parser._resolve_is_native_checkbox)."""
    content = []
    index = 1
    for text, nesting_level in items:
        end = index + len(text) + 1
        content.append(
            {
                "startIndex": index,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{"textRun": {"content": text + "\n"}}],
                    "bullet": {"listId": "kix.cb", "nestingLevel": nesting_level},
                },
            }
        )
        index = end
    return {
        "revisionId": revision_id,
        "body": {"content": content},
        "lists": {
            "kix.cb": {
                "listProperties": {
                    "nestingLevels": [
                        {"glyphType": "GLYPH_TYPE_UNSPECIFIED"},
                        {"glyphType": "GLYPH_TYPE_UNSPECIFIED"},
                    ]
                }
            }
        },
    }


class TestDefaultPathCheckboxRoundTrip:
    def test_pull_then_push_zero_edit_round_trip_is_a_noop_for_native_checkbox(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _native_checkbox_doc()
        fake_client.list_comments.return_value = []
        # Drive's HTML export for a native checkbox list item — no bracket
        # marker, no checkbox-specific class the converter interprets.
        fake_client.get_doc_content.return_value = (
            '<ul class="c1 lst-kix_abc-0 start">'
            '<li class="c2 li-bullet-0"><span>Whatsapp group</span></li>'
            "</ul>"
        )
        # The default path's checkbox-state recovery (#78) cross-references
        # this against the structural checkbox paragraphs and patches the
        # real `[ ]`/`[x]` marker in — a matching export here is what keeps
        # this test's pull at status "ok" instead of the fail-closed warning.
        fake_client.fetch_markdown_export.return_value = "- [ ] Whatsapp group\n"

        local = tmp_path / "doc.md"
        pull_result = backend.pull("doc-1", str(local))

        assert pull_result.status == "ok"
        pulled = local.read_text(encoding="utf-8")
        assert pulled == "- [ ] Whatsapp group"

        push_result = backend.push(str(local), "doc-1")

        assert push_result.status == "skipped"
        fake_client.batch_update.assert_not_called()


class TestPullCheckboxState:
    def test_pull_recovers_checked_and_unchecked_state_from_markdown_export(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_native_checkboxes(
            ("buy milk", 0), ("buy eggs", 0)
        )
        fake_client.get_doc_content.return_value = "<ul><li>buy milk</li><li>buy eggs</li></ul>"
        fake_client.fetch_markdown_export.return_value = "- [ ] buy milk\n- [x] buy eggs\n"

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "ok"
        content = local.read_text(encoding="utf-8")
        assert "- [ ] buy milk" in content
        assert "- [x] buy eggs" in content

    def test_pull_falls_back_to_unchecked_and_warns_on_checkbox_count_mismatch(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_native_checkboxes(
            ("buy milk", 0), ("buy eggs", 0)
        )
        html = "<ul><li>buy milk</li><li>buy eggs</li></ul>"
        fake_client.get_doc_content.return_value = html
        # Only one checklist line comes back — count disagrees with the two
        # native-checkbox paragraphs the structural parse found.
        fake_client.fetch_markdown_export.return_value = "- [x] buy milk\n"

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "warning"
        assert "checkbox" in (result.message or "").lower()
        content = local.read_text(encoding="utf-8")
        assert "[x]" not in content
        assert "[ ]" not in content

    def test_pull_falls_back_to_unchecked_and_warns_on_markdown_export_failure(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_native_checkboxes(("buy milk", 0))
        fake_client.get_doc_content.return_value = "<ul><li>buy milk</li></ul>"
        fake_client.fetch_markdown_export.side_effect = RuntimeError("transport failure")

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "warning"
        assert "checkbox" in (result.message or "").lower()
        content = local.read_text(encoding="utf-8")
        assert local.exists()
        assert "[x]" not in content

    def test_pull_without_native_checkboxes_never_calls_markdown_export(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.get_doc_content.return_value = "<p>Hello</p>"

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local))

        assert result.status == "ok"
        fake_client.fetch_markdown_export.assert_not_called()

    def test_pull_with_tab_id_never_calls_markdown_export(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Tab-scoped pull stays on the structural path — files.export cannot
        target a tab, so it must never even be attempted (criterion 1)."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()

        local = tmp_path / "doc.md"
        result = backend.pull("doc-1", str(local), tab_id="t.second")

        assert result.status == "ok"
        fake_client.fetch_markdown_export.assert_not_called()


class TestPushTabId:
    def test_push_with_tab_id_targets_that_tab_in_batch_update_requests(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("Second tab content\n\nNew paragraph\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", tab_id="t.second")

        assert result.status == "ok"
        fake_client.batch_update.assert_called_once()
        _args, kwargs = fake_client.batch_update.call_args
        assert kwargs["required_revision_id"] == "rev-tabs"
        requests = _args[1]
        assert requests
        for request in requests:
            for inner in request.values():
                if "location" in inner:
                    assert inner["location"]["tabId"] == "t.second"
                if "range" in inner:
                    assert inner["range"]["tabId"] == "t.second"

    def test_push_with_unknown_tab_id_returns_error_status(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("Anything\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", tab_id="t.nonexistent")

        assert result.status == "error"
        assert "t.nonexistent" in (result.message or "")
        fake_client.batch_update.assert_not_called()

    def test_push_without_tab_id_on_multi_tab_doc_targets_first_tab_and_warns(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _multi_tab_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("First tab content\n\nNew paragraph\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "warning"
        assert "Overview" in (result.message or "") and "Details" in (result.message or "")
        fake_client.batch_update.assert_called_once()
        _args, kwargs = fake_client.batch_update.call_args
        requests = _args[1]
        assert requests
        # No explicit tab_id was configured, but the doc has tabs, so the
        # implicit default (the first tab, per resolve_document_tab) is
        # still stamped explicitly onto every request — an omitted tabId
        # also defaults to the first tab per the Docs API, but being
        # explicit here means the write always lands where the plan (and
        # its warning) said it would.
        for request in requests:
            for inner in request.values():
                if "location" in inner:
                    assert inner["location"]["tabId"] == "t.first"
                if "range" in inner:
                    assert inner["range"]["tabId"] == "t.first"

    def test_push_without_tab_id_on_single_tab_legacy_doc_stays_ok(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Backward-compatible no-tab_id case, mirroring
        TestPushRevisionGuard.test_push_passes_fetched_revision_id_into_batch_update
        but making the no-tab_id-kwarg call explicit."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1", tab_id=None)

        assert result.status == "ok"
        fake_client.batch_update.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Honest reporting — push() must not claim parity it doesn't have
# ─────────────────────────────────────────────────────────────────────────────

def _paragraph_element(index: int, text: str) -> tuple[dict, int]:
    end = index + len(text) + 1
    return (
        {
            "startIndex": index,
            "endIndex": end,
            "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
        },
        end,
    )


def _doc_with_pinned_empty_paragraph(revision_id: str = "rev-pinned") -> dict:
    """A doc holding an empty paragraph that no push can ever remove.

    The empty paragraph's only character is the newline that anchors the
    section break after it, so _make_delete_requests trims its range to
    nothing and drops the request — while diff_summary still (correctly)
    reports it as a removal.
    """
    content = []
    index = 1
    for text in ("Intro", ""):
        element, index = _paragraph_element(index, text)
        content.append(element)
    content.append({"startIndex": index, "endIndex": index + 1, "sectionBreak": {}})
    index += 1
    for text in ("Alpha", ""):
        element, index = _paragraph_element(index, text)
        content.append(element)
    return {"revisionId": revision_id, "body": {"content": content}}


class TestPushReportsWhatItCouldNotDo:
    def test_styling_that_could_not_be_placed_is_reported(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Pass 2 declines to style a paragraph it can't find in the written
        doc. Silence there would mean links vanishing under a green "ok"."""
        backend, fake_client = make_backend()
        content = []
        index = 1
        for text in ("Intro", "something else entirely", ""):
            element, index = _paragraph_element(index, text)
            content.append(element)
        # The mocked client returns this same doc from the pass-2 re-fetch, so
        # the doc pass 2 sees never gained the "Gamma" paragraph pass 1 asked for.
        fake_client.get_document.return_value = {"revisionId": "rev-x",
                                                 "body": {"content": content}}
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("Intro\n\n[Gamma](https://example.com/g)\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "warning"
        assert "Gamma" in (result.message or "")
        assert "not applied" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# Pass 1 empty does not mean "nothing to do"
#
# The diff key is (style, text, is_list_item) — no marks — so a link-only edit
# produces zero diffs and zero pass-1 requests. `push()` used to return
# "No changes detected" before pass 2 ran, so adding a link wrote nothing and
# reported success as a green ✓. Bold, italic, monospace and an
# indentation-only change all failed the same way.
# ─────────────────────────────────────────────────────────────────────────────

def _doc_with_paragraph(text: str, revision_id: str = "rev-1") -> dict:
    """A one-paragraph doc whose indices match what DocsStructureParser expects."""
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 1 + len(text) + 1,
                    "paragraph": {
                        "elements": [{"textRun": {"content": text + "\n"}}],
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    },
                }
            ]
        },
    }


class TestStylingOnlyPush:
    def test_a_link_only_edit_is_applied_rather_than_reported_as_no_change(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """The reported bug: the link is known, and used to be silently dropped."""
        local = tmp_path / "doc.md"
        local.write_text("[Beta](https://example.com)\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_paragraph("Beta")
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok", result.message
        assert client.batch_update.call_count == 1, "expected exactly the styling pass"
        requests = client.batch_update.call_args[0][1]
        links = [
            r["updateTextStyle"]["textStyle"]["link"]
            for r in requests
            if "updateTextStyle" in r
            and "link" in r["updateTextStyle"].get("textStyle", {})
        ]
        assert links == [{"url": "https://example.com"}]

    def test_the_styling_pass_is_revision_guarded(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """A write with no pass 1 still may not clobber a concurrent edit."""
        local = tmp_path / "doc.md"
        local.write_text("[Beta](https://example.com)\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_paragraph("Beta", revision_id="rev-9")
        client.list_comments.return_value = []

        backend.push(str(local), "doc-1")

        assert client.batch_update.call_args.kwargs["required_revision_id"] == "rev-9"

    def test_an_unchanged_document_still_reports_no_changes_and_writes_nothing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """The other direction — the fix must not make every push a write.

        Without this, moving the "nothing to do" return below pass 2 could be
        satisfied by simply always writing, which is worse than the bug.
        """
        local = tmp_path / "doc.md"
        local.write_text("Beta\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_paragraph("Beta")
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        assert result.message == "No changes detected"
        client.batch_update.assert_not_called()

    def test_a_styling_only_push_does_not_re_fetch_the_document(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Pass 1 wrote nothing, so the document already in hand is still current."""
        local = tmp_path / "doc.md"
        local.write_text("**Beta**\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_paragraph("Beta")
        client.list_comments.return_value = []

        backend.push(str(local), "doc-1")

        assert client.get_document.call_count == 1

    def test_a_text_only_push_does_not_issue_an_extra_fetch(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """`needs_pass2` still gates the work, so pass 2 costs nothing when idle.

        Removing that gate to make pass 2 unconditional would add a GET to every
        text-only push — an easy regression to ship while fixing the styling bug,
        because no other assertion here would notice.
        """
        local = tmp_path / "doc.md"
        local.write_text("Gamma\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_paragraph("Beta")
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok", result.message
        assert client.batch_update.call_count == 1, "pass 1 only"
        assert client.get_document.call_count == 1, "no styling work, so no re-read"

    def test_a_text_edit_carrying_a_link_still_re_fetches_before_styling(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """When pass 1 writes, the indices have moved — pass 2 must re-read.

        Reusing the pre-write document here would style stale positions. The
        mock returns the *post-write* document on the second call, because a
        static mock would make pass 2 fail to align and would pass this test
        for the wrong reason.
        """
        local = tmp_path / "doc.md"
        local.write_text("[Gamma](https://example.com)\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.side_effect = [
            _doc_with_paragraph("Beta", revision_id="rev-before"),
            _doc_with_paragraph("Gamma", revision_id="rev-after"),
        ]
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok", result.message
        assert client.get_document.call_count == 2
        assert client.batch_update.call_count == 2
        # Each pass is guarded by the revision it was built against.
        guards = [c.kwargs["required_revision_id"] for c in client.batch_update.call_args_list]
        assert guards == ["rev-before", "rev-after"]


# ─────────────────────────────────────────────────────────────────────────────
# A blank paragraph is preserved and reported, not deleted (#17)
# ─────────────────────────────────────────────────────────────────────────────

def _doc_with_blank_paragraph(revision_id: str = "rev-1") -> dict:
    """Alpha / (blank) / Omega — the shape that used to lose its blank line."""
    content, index = [], 1
    for text in ("Alpha", "", "Omega"):
        end = index + len(text) + 1
        content.append({
            "startIndex": index,
            "endIndex": end,
            "paragraph": {
                "elements": [{"textRun": {"content": text + "\n"}}],
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            },
        })
        index = end
    return {"revisionId": revision_id, "body": {"content": content}}


class TestBlankParagraphIsPreserved:
    def test_a_zero_edit_push_over_a_blank_paragraph_writes_nothing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """The document's blank line survives a sync the user did not ask for."""
        local = tmp_path / "doc.md"
        local.write_text("Alpha\n\nOmega\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_blank_paragraph()
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        client.batch_update.assert_not_called()

    def test_the_blank_paragraph_is_named_in_the_result(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Preserving it silently would be the worse half of the trade."""
        local = tmp_path / "doc.md"
        local.write_text("Alpha\n\nOmega\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_blank_paragraph()
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        assert "blank paragraph" in (result.message or "")

    def test_a_real_edit_alongside_a_blank_paragraph_still_applies(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Projection must not swallow the edits either side of the blank line."""
        local = tmp_path / "doc.md"
        local.write_text("Alpha\n\nGamma\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_blank_paragraph()
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1")

        # The blank paragraph is residue markdown cannot express, so the push
        # now reports it as a warning rather than silently dropping it — but
        # the edit alongside it must still go through.
        assert result.status == "warning", result.message
        assert "blank paragraph" in result.message
        assert client.batch_update.call_count == 1
        texts = [
            r["insertText"]["text"]
            for r in client.batch_update.call_args[0][1]
            if "insertText" in r
        ]
        assert any("Gamma" in t for t in texts)


def _doc_with_blank_paragraph_adjacent_to_checkbox(revision_id: str = "rev-1") -> dict:
    """Buy milk (native checkbox) / (blank) / Omega — root cause 2 from
    issue #17: a blank paragraph immediately after a checkbox item."""
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 11,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "elements": [{"textRun": {"content": "Buy milk\n"}}],
                        "bullet": {"listId": "kix.abc", "nestingLevel": 0},
                    },
                },
                {
                    "startIndex": 11,
                    "endIndex": 12,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "elements": [{"textRun": {"content": "\n"}}],
                    },
                },
                {
                    "startIndex": 12,
                    "endIndex": 18,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "elements": [{"textRun": {"content": "Omega\n"}}],
                    },
                },
            ]
        },
        "lists": {
            "kix.abc": {
                "listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}
            }
        },
    }


class TestBlankParagraphAdjacentToCheckboxRoundTrip:
    """AC5 (issue #17, root cause 2): a blank paragraph next to a native
    checkbox paragraph must not turn a zero-edit round trip into a
    corrupting push. projection.py's Rule 1 already drops the blank
    paragraph from *both* sides of the diff before it's compared (see its
    docstring, which cites this exact issue), and that fix predates this
    ticket (commit d8b1b5f). No new production code is added here — this
    is a confirming regression test that root cause 2 is already closed.
    """

    def test_zero_edit_push_over_blank_paragraph_next_to_checkbox_is_a_noop(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        # Exactly what pull() would have rendered for this doc: project()
        # drops the blank paragraph and nodes_to_markdown renders the
        # checkbox with its synthetic "[ ] " prefix.
        local.write_text("- [ ] Buy milk\n\nOmega\n", encoding="utf-8")
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_blank_paragraph_adjacent_to_checkbox()
        client.list_comments.return_value = []

        result = backend.push(str(local), "doc-1", force=True)

        assert result.status == "skipped"
        client.batch_update.assert_not_called()


def _tabbed_doc_with_prior_force_push_text(revision_id: str = "rev-force-2") -> dict:
    """A native-checkbox paragraph whose text is already the literal
    "[x] ..." left behind by a prior force-push escape-hatch edit (AC2/AC3)
    — the bullet glyph is untouched, so is_native_checkbox is still True."""
    return {
        "revisionId": revision_id,
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 20,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                    "elements": [{"textRun": {"content": "[x] Whatsapp group\n"}}],
                                    "bullet": {"listId": "kix.abc", "nestingLevel": 0},
                                },
                            },
                        ]
                    },
                    "lists": {
                        "kix.abc": {
                            "listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}
                        }
                    },
                },
            }
        ],
    }


class TestSecondRoundTripAfterForcePush:
    """AC6 (issue #17): a doc that already carries literal bracket text
    baked in by a prior force-push must not have that state compound on a
    second pull→push cycle.

    render_nodes_to_markdown() unconditionally prepends the synthetic
    "- [ ] " marker to any is_native_checkbox paragraph regardless of what
    its text already contains, so pulling this doc renders the cosmetically
    doubled "- [ ] [x] Whatsapp group" rather than "- [x] Whatsapp group".
    That's a separate, narrower defect from this ticket's push-corruption
    bug: DocsRequestBuilder's _key() fix (commit 83cdb99) strips exactly one
    literal "[ ] " prefix off a target and matches the remainder against the
    real native-checkbox text, which folds this doubled text back to "no
    change" — so the push side stays safe, and a second round trip doesn't
    grow a third bracket.
    """

    def test_pull_push_pull_push_does_not_compound_prior_force_push_brackets(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_prior_force_push_text()
        client.list_comments.return_value = []

        first_pull = backend.pull("doc-1", str(local), tab_id="t.0")
        assert first_pull.status == "ok", first_pull.message
        first_content = local.read_text(encoding="utf-8")

        first_push = backend.push(str(local), "doc-1", tab_id="t.0", force=True)
        assert first_push.status == "skipped", first_push.message
        client.batch_update.assert_not_called()

        second_pull = backend.pull("doc-1", str(local), tab_id="t.0")
        assert second_pull.status == "ok", second_pull.message
        second_content = local.read_text(encoding="utf-8")

        # No compounding: the second pull renders identically to the first
        # (no extra brackets piled on), because push never sent a request
        # that could have changed the live doc's text in between.
        assert second_content == first_content
        assert second_content.count("[") == 2  # exactly "[ ]" + "[x]", never a third

        second_push = backend.push(str(local), "doc-1", tab_id="t.0", force=True)
        assert second_push.status == "skipped", second_push.message
        client.batch_update.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# pull() renders TITLE as a heading, so pull → push is a fixpoint
# ─────────────────────────────────────────────────────────────────────────────

def _tabbed_doc_with_style(style: str, text: str = "My Doc") -> dict:
    """A single-tab document whose one paragraph has the given named style."""
    return {
        "revisionId": "rev-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 1 + len(text) + 1,
                                "paragraph": {
                                    "elements": [{"textRun": {"content": text + "\n"}}],
                                    "paragraphStyle": {"namedStyleType": style},
                                },
                            }
                        ]
                    }
                },
            }
        ],
    }


class TestPullRendersUnwritableStyles:
    def test_pull_renders_a_title_as_a_heading(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Markdown has no TITLE syntax, so the renderer used to emit bare text.

        Bare text re-parses as NORMAL_TEXT, which made the very next push demote
        the title. Rendering `#` instead makes pull → push a fixpoint. This goes
        through pull() rather than calling project() directly, because the bug
        was in what the file on disk ended up containing.
        """
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_style("TITLE")
        client.list_comments.return_value = []

        result = backend.pull("doc-1", str(local), tab_id="t.0")

        assert result.status == "ok", result.message
        assert local.read_text().strip() == "# My Doc"

    def test_pull_renders_a_subtitle_as_a_second_level_heading(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_style("SUBTITLE")
        client.list_comments.return_value = []

        backend.pull("doc-1", str(local), tab_id="t.0")

        assert local.read_text().strip() == "## My Doc"

    def test_pull_then_push_over_a_title_writes_nothing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """The whole point, end to end through both public methods."""
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_style("TITLE")
        client.list_comments.return_value = []

        backend.pull("doc-1", str(local), tab_id="t.0")
        result = backend.push(str(local), "doc-1", tab_id="t.0")

        assert result.status == "skipped", result.message
        client.batch_update.assert_not_called()


def _tabbed_doc_with_pua_paragraph() -> dict:
    """A single-tab document with a real paragraph plus a lone PUA-glyph one.

    The PUA paragraph is indistinguishable, in the parsed API data, from
    content an author actually typed — project() turns it into
    `private_use_glyph` residue and drops it from the rendered markdown.
    """
    return {
        "revisionId": "rev-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 8,
                                "paragraph": {
                                    "elements": [{"textRun": {"content": "Intro\n"}}],
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                },
                            },
                            {
                                "startIndex": 8,
                                "endIndex": 10,
                                "paragraph": {
                                    "elements": [
                                        {"textRun": {"content": "\n", "textStyle": {}}}
                                    ],
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                },
                            },
                        ]
                    }
                },
            }
        ],
    }


class TestPullSurfacesResidue:
    def test_pull_warns_when_a_private_use_glyph_paragraph_is_dropped(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """A paragraph holding only a private-use character vanishes from the
        rendered markdown with no trace — pull() must say so rather than
        reporting `status="ok"` over content that silently disappeared.
        """
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_pua_paragraph()
        client.list_comments.return_value = []

        result = backend.pull("doc-1", str(local), tab_id="t.0")

        assert result.status == "warning", result.message
        assert "private-use glyph" in (result.message or "")
        assert local.read_text().strip() == "Intro"

    def test_pull_stays_ok_when_the_only_residue_is_a_mapped_style(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """A TITLE/SUBTITLE style is mapped to a heading, not dropped — this
        must stay a plain "ok", matching
        test_pull_renders_a_title_as_a_heading.
        """
        local = tmp_path / "doc.md"
        backend, client = make_backend()
        client.get_document.return_value = _tabbed_doc_with_style("TITLE")
        client.list_comments.return_value = []

        result = backend.pull("doc-1", str(local), tab_id="t.0")

        assert result.status == "ok", result.message


# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsBackend.create() — new-doc creation for `docspan map`
# ─────────────────────────────────────────────────────────────────────────────

class TestCreate:
    def test_create_calls_client_and_returns_doc_id_title_url(
        self, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, client = make_backend()
        client.create_document.return_value = {"documentId": "new-doc-1", "title": "My Doc"}

        result = backend.create("My Doc")

        client.create_document.assert_called_once_with("My Doc")
        assert result.doc_id == "new-doc-1"
        assert result.title == "My Doc"
        assert result.url == "https://docs.google.com/document/d/new-doc-1/edit"


# ─────────────────────────────────────────────────────────────────────────────
# pull_sectioned() — gdocs-sectioned-sync Epic 2: split a doc into one
# NN-slug.md file per split_level heading plus _manifest.yaml, always via the
# structural path, written atomically (temp dir + os.replace).
# ─────────────────────────────────────────────────────────────────────────────

def _heading_paragraph(text: str, heading_id: str, style: str = "HEADING_1") -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style, "headingId": heading_id},
            "elements": [{"textRun": {"content": text + "\n"}}],
        },
    }


def _body_paragraph(text: str) -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [{"textRun": {"content": text + "\n"}}],
        },
    }


def _with_real_indices(paragraphs: List[dict]) -> List[dict]:
    """Assign sequential startIndex/endIndex to a paragraph-dict list.

    `DocsStructureParser` defaults a paragraph's start/end index to 0 when
    the source dict omits `startIndex`/`endIndex` (real Docs API responses
    always include them). Every node in a doc built without this ends up
    with identical (0, 0) positions, which collapses `DocsRequestBuilder`'s
    deletion/reorder detection (position-based) into an empty request list
    even though the node *content* differs — a test-fixture gap, not a
    production bug (confirmed by reproducing the same "no changes detected"
    symptom via plain, unmodified `push()`). Real Docs paragraphs are
    contiguous, so a simple running offset over each paragraph's text
    length reproduces realistic positions.
    """
    indexed: List[dict] = []
    offset = 1
    for p in paragraphs:
        text = p["paragraph"]["elements"][0]["textRun"]["content"]
        length = len(text)
        p = {**p, "startIndex": offset, "endIndex": offset + length}
        indexed.append(p)
        offset += length
    return indexed


def _sectioned_doc(revision_id: str = "rev-sectioned") -> dict:
    """5 HEADING_1 sections plus preamble content — Story 2.1's own example."""
    content = [_body_paragraph("Preamble content.")]
    for i in range(1, 6):
        content.append(_heading_paragraph(f"Section {i}", heading_id=f"h.section{i}"))
        content.append(_body_paragraph(f"Body of section {i}."))
    content = _with_real_indices(content)
    return {"revisionId": revision_id, "body": {"content": content}}


def _bookmark_link_paragraph(text: str = "see it") -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"textRun": {"content": text, "textStyle": {"link": {"bookmarkId": "kix.b1"}}}},
                {"textRun": {"content": "\n", "textStyle": {}}},
            ],
        },
    }


def _sectioned_doc_with_bookmark_link(revision_id: str = "rev-sectioned-bookmark") -> dict:
    """One HEADING_1 section whose body contains a bookmark link —
    unreadable on this structural path (issue #38), same shape as
    _single_tab_doc_with_bookmark_link but split across section files."""
    content = [
        _heading_paragraph("Section 1", heading_id="h.section1"),
        _bookmark_link_paragraph(),
    ]
    content = _with_real_indices(content)
    return {"revisionId": revision_id, "body": {"content": content}}


class TestPullSectioned:
    def test_pull_sectioned_should_write_section_files_and_manifest_using_structural_path(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()

        local_dir = tmp_path / "doc"
        result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        assert result.status == "ok", result.message
        # Never falls back to Drive's HTML export — it can't be scoped to a
        # heading range.
        fake_client.get_doc_content.assert_not_called()

        written = sorted(p.name for p in local_dir.iterdir())
        assert written == [
            "00-preamble.md",
            "01-section-1.md",
            "02-section-2.md",
            "03-section-3.md",
            "04-section-4.md",
            "05-section-5.md",
            "_manifest.yaml",
        ]
        assert "Preamble content." in (local_dir / "00-preamble.md").read_text()
        section_3 = (local_dir / "03-section-3.md").read_text()
        assert "# Section 3" in section_3
        assert "Body of section 3." in section_3

        import yaml

        manifest = yaml.safe_load((local_dir / "_manifest.yaml").read_text())
        assert len(manifest["entries"]) == 6
        assert manifest["entries"][0]["heading_id"] == "__preamble__"
        assert manifest["entries"][3]["heading_id"] == "h.section3"
        assert manifest["entries"][3]["filename"] == "03-section-3.md"

    def test_pull_sectioned_should_warn_and_name_the_bookmark_link(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Same structural-path property as pull()'s tab-scoped branch: each
        section file's markdown is the parser's own output, so an
        unreadable link is genuinely absent from the file just written
        (issue #38)."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc_with_bookmark_link()

        local_dir = tmp_path / "doc"
        result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        assert result.status == "warning"
        assert "bookmark link" in (result.message or "")

    def test_pull_sectioned_should_return_error_for_unknown_tab_id(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()

        local_dir = tmp_path / "doc"
        result = backend.pull_sectioned(
            "doc-1", str(local_dir), split_level="HEADING_1", tab_id="t.nonexistent"
        )

        assert result.status == "error"
        assert "t.nonexistent" in (result.message or "")

    def test_pull_sectioned_should_return_error_when_split_level_absent_from_doc(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()

        local_dir = tmp_path / "doc"
        result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_2")

        assert result.status == "error"
        assert "HEADING_2" in (result.message or "")

    def test_pull_sectioned_should_leave_prior_directory_intact_when_write_fails_partway(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()

        local_dir = tmp_path / "doc"
        local_dir.mkdir()
        (local_dir / "00-preamble.md").write_text("prior preamble\n")
        (local_dir / "_manifest.yaml").write_text(
            "entries:\n- heading_id: __preamble__\n  slug: preamble\n  filename: 00-preamble.md\n"
        )

        from docspan.backends.google_docs import backend as backend_module

        call_count = {"n": 0}
        real_render = backend_module.render_nodes_to_markdown

        def _flaky_render(nodes):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("simulated write failure")
            return real_render(nodes)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(backend_module, "render_nodes_to_markdown", _flaky_render)
            result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        assert result.status == "error"
        # The original directory's prior contents are completely untouched —
        # no partial set of new section files, no stray temp directory
        # promoted into place.
        assert (local_dir / "00-preamble.md").read_text() == "prior preamble\n"
        assert sorted(p.name for p in local_dir.iterdir()) == [
            "00-preamble.md",
            "_manifest.yaml",
        ]
        # No leftover temp directories beside it either.
        siblings = [p.name for p in tmp_path.iterdir()]
        assert siblings == ["doc"]

    def test_pull_sectioned_should_atomically_swap_temp_directory_into_place_on_success(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()

        local_dir = tmp_path / "doc"
        local_dir.mkdir()
        (local_dir / "00-preamble.md").write_text("stale preamble\n")
        (local_dir / "_manifest.yaml").write_text(
            "entries:\n- heading_id: __preamble__\n  slug: preamble\n  filename: 00-preamble.md\n"
        )

        result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        assert result.status == "ok", result.message
        written = sorted(p.name for p in local_dir.iterdir())
        assert written == [
            "00-preamble.md",
            "01-section-1.md",
            "02-section-2.md",
            "03-section-3.md",
            "04-section-4.md",
            "05-section-5.md",
            "_manifest.yaml",
        ]
        # The new content replaced the stale file — no old content survives.
        assert (local_dir / "00-preamble.md").read_text() != "stale preamble\n"
        # No stray temp/backup directories left beside the target directory.
        siblings = [p.name for p in tmp_path.iterdir()]
        assert siblings == ["doc"]

    def test_pull_sectioned_should_write_one_comments_sidecar_per_section(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        fake_client.get_doc_info.return_value = {"name": "Sectioned Doc"}
        fake_client.get_comments.return_value = [
            {
                "id": "c-sec3",
                "author": {"displayName": "Reviewer"},
                "resolved": False,
                "quotedFileContent": {"value": "Body of section 3."},
                "content": "Question about section 3.",
            },
            {
                "id": "c-sec5",
                "author": {"displayName": "Reviewer"},
                "resolved": False,
                "quotedFileContent": {"value": "Body of section 5."},
                "content": "Question about section 5.",
            },
            {
                "id": "c-unmatched",
                "author": {"displayName": "Reviewer"},
                "resolved": False,
                "quotedFileContent": {"value": "text that appears nowhere in the doc"},
                "content": "Orphaned comment.",
            },
        ]

        local_dir = tmp_path / "doc"
        result = backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        assert result.status == "ok", result.message
        comment_sidecars = sorted(p.name for p in local_dir.glob("*.comments.md"))
        assert comment_sidecars == ["03-section-3.md.comments.md", "05-section-5.md.comments.md"]

        section_3_comments = (local_dir / "03-section-3.md.comments.md").read_text()
        assert "Question about section 3." in section_3_comments
        assert "Question about section 5." not in section_3_comments

        section_5_comments = (local_dir / "05-section-5.md.comments.md").read_text()
        assert "Question about section 5." in section_5_comments
        assert "Question about section 3." not in section_5_comments

        # The unmatched comment is surfaced as residue, not silently attached
        # to any section, and doesn't get its own sidecar either.
        assert "text that appears nowhere" not in section_3_comments
        assert "text that appears nowhere" not in section_5_comments
        for entry in local_dir.iterdir():
            if entry.name.endswith(".comments.md"):
                assert "c-unmatched" not in entry.read_text()


# ─────────────────────────────────────────────────────────────────────────────
# push_sectioned() — gdocs-sectioned-sync Epic 3: reassemble a sectioned
# mapping's section files (in manifest order) and reuse push()'s diff/
# request-emission tail unchanged (_execute_push).
# ─────────────────────────────────────────────────────────────────────────────


class TestPushSectioned:
    def test_push_sectioned_should_error_when_manifest_missing(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        local_dir = tmp_path / "doc"
        local_dir.mkdir()

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "error"
        assert "manifest" in (result.message or "").lower()
        fake_client.get_document.assert_not_called()
        fake_client.batch_update.assert_not_called()

    def test_push_sectioned_should_reassemble_sections_in_manifest_order_not_filesystem_order(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        # Manifest lists section 2 before section 1, but the *files* are
        # still named 01-section-1.md / 02-section-2.md (filesystem/glob
        # order would put section 1 first). The live doc already has
        # section 2 before section 1 (i.e. it matches manifest order) — if
        # push_sectioned reassembled by filename order instead of manifest
        # order, it would (wrongly) see a reorder diff and write something;
        # reassembling in manifest order produces zero diff.
        backend, fake_client = make_backend()
        local_dir = tmp_path / "doc"
        local_dir.mkdir()
        (local_dir / "01-section-1.md").write_text("# Section 1\n\nBody of section 1.\n")
        (local_dir / "02-section-2.md").write_text("# Section 2\n\nBody of section 2.\n")
        (local_dir / "_manifest.yaml").write_text(
            "entries:\n"
            "- heading_id: h.section2\n  slug: section-2\n  filename: 02-section-2.md\n"
            "- heading_id: h.section1\n  slug: section-1\n  filename: 01-section-1.md\n"
        )
        fake_client.get_document.return_value = {
            "revisionId": "rev-order",
            "body": {
                "content": [
                    _heading_paragraph("Section 2", heading_id="h.section2"),
                    _body_paragraph("Body of section 2."),
                    _heading_paragraph("Section 1", heading_id="h.section1"),
                    _body_paragraph("Body of section 1."),
                ]
            },
        }

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "skipped", result.message
        fake_client.batch_update.assert_not_called()

    def test_push_sectioned_should_push_modified_section_content(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        section_3 = local_dir / "03-section-3.md"
        section_3.write_text(
            section_3.read_text().replace(
                "Body of section 3.", "Body of section 3, updated."
            )
        )

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status in ("ok", "warning"), result.message
        assert fake_client.batch_update.call_count >= 1
        _, kwargs = fake_client.batch_update.call_args_list[0]
        assert kwargs["required_revision_id"] == "rev-sectioned"
        serialized = json.dumps(fake_client.batch_update.call_args_list[0][0][1])
        assert "updated" in serialized

    def test_push_sectioned_should_push_added_section(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        (local_dir / "06-section-6.md").write_text("# Section 6\n\nBody of section 6.\n")

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status in ("ok", "warning"), result.message
        assert fake_client.batch_update.call_count >= 1
        serialized = json.dumps(fake_client.batch_update.call_args_list[0][0][1])
        assert "Section 6" in serialized

    def test_push_sectioned_should_push_deleted_section(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        (local_dir / "03-section-3.md").unlink()

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status in ("ok", "warning"), result.message
        assert fake_client.batch_update.call_count >= 1

    def test_push_sectioned_reorder_should_preserve_heading_ids_via_in_place_move(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Named for validation.md's literal test name. The resolved design
        (see push_sectioned's docstring / _classify_section_reorder) found
        Google Docs' batchUpdate API has no "move paragraph" primitive
        anywhere in docs_request_builder.py's build() — only equal/delete/
        insert/replace opcodes exist. For a *pure* reorder (manifest order
        changed, section content itself untouched — the scenario here),
        docs_request_builder.py's `_repair` step recognizes the swapped
        run's content pairs as literally unchanged and folds them back to
        `equal` opcodes rather than delete+insert (verified directly via
        `DocsRequestBuilder._opcodes()`: the reordered current/target runs
        come back as two `equal` opcodes, not `delete`+`insert`). So
        heading_ids ARE preserved here — literally, because nothing is
        deleted or reinserted — and zero batch_update requests are emitted;
        the live document's paragraph order is left as-is (Docs has no
        notion of order independent of content identity). Because nothing
        is actually written, this is a true no-op push (status "skipped",
        no batch_update call, no reorder warning attached — see
        push_sectioned's docstring on why the warning is only attached to a
        push that writes something) rather than literal API-level move
        support, which does not exist.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        import yaml

        manifest_path = local_dir / "_manifest.yaml"
        raw = yaml.safe_load(manifest_path.read_text())
        entries = raw["entries"]
        # Swap section 1 and section 2's manifest entries (indices 1, 2 —
        # index 0 is the preamble) without touching the section files or
        # their filenames, simulating a user hand-editing the manifest to
        # reorder sections since the last pull.
        entries[1], entries[2] = entries[2], entries[1]
        manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False))

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "skipped", result.message
        assert fake_client.batch_update.call_count == 0

    def test_push_sectioned_reorder_with_content_edit_should_accept_heading_id_churn_and_warn(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """plan.md's Story 7.3 second Given/When/Then: a section that is both
        reordered *and* content-edited in the same push. ADR-002's Consequences
        (rung 3 of the fallback ladder, formally recorded after Task 3.2.2's
        spike found no Docs API move primitive) documents that this combination
        cannot be folded back to a no-op the way a pure reorder can — the
        differ sees genuine content change, emits an ordinary delete+insert,
        heading_id does not survive, and push_sectioned must surface a warning
        naming the reordered section(s) rather than silently losing that
        identity. This is the one acceptance case
        test_push_sectioned_reorder_should_preserve_heading_ids_via_in_place_move
        does not cover (it only exercises reorder with content untouched)."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        import yaml

        manifest_path = local_dir / "_manifest.yaml"
        raw = yaml.safe_load(manifest_path.read_text())
        entries = raw["entries"]
        # Swap section 1 and section 2's manifest entries, same as the
        # pure-reorder test — but this time also edit one of the swapped
        # sections' actual content, so the differ can't fold the pair back
        # to `equal`.
        entries[1], entries[2] = entries[2], entries[1]
        manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False))
        section_1_file = local_dir / "01-section-1.md"
        section_1_file.write_text(
            section_1_file.read_text() + "\nAn extra edited line.\n"
        )

        fake_client.batch_update.return_value = {}
        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "warning", result.message
        assert "reordered" in (result.message or "")
        # Unlike the pure-reorder no-op, the content edit forces an actual
        # write — this is the documented heading_id-churn path, not a skip.
        assert fake_client.batch_update.call_count > 0

    def test_push_sectioned_should_return_blocked_status_without_partial_batch_update_when_diff_too_expensive(
        self, tmp_path, monkeypatch, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        from docspan.backends.google_docs.docs_request_builder import (
            DiffTooExpensive,
            DocsRequestBuilder,
        )

        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        def _raise_too_expensive(*args: object, **kwargs: object) -> None:
            raise DiffTooExpensive("document", 6000, 3000)

        monkeypatch.setattr(DocsRequestBuilder, "build", _raise_too_expensive)

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "blocked"
        assert result.message == str(DiffTooExpensive("document", 6000, 3000))
        fake_client.batch_update.assert_not_called()

    def test_diff_too_expensive_status_diverges_between_push_and_push_sectioned_by_design(
        self, tmp_path, monkeypatch, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Locks in the intentional status divergence documented in
        push_sectioned's own docstring: the same DiffTooExpensive exception,
        raised from the same single guard point in docs_request_builder.py
        with no sectioned-specific override, is surfaced as `status="error"`
        by push() (TestDiffTooExpensiveSurfacesAsUserFacingError, historical
        behavior) but `status="blocked"` by push_sectioned() (a refused
        write, not an unexpected fault). Both code paths are exercised here
        side-by-side against the identical exception instance so a future
        change that accidentally unifies or flips either status fails loudly
        rather than only breaking whichever of the two pre-existing tests
        happens to run first.
        """
        from docspan.backends.google_docs.docs_request_builder import (
            DiffTooExpensive,
            DocsRequestBuilder,
        )

        def _raise_too_expensive(*args: object, **kwargs: object) -> None:
            raise DiffTooExpensive("document", 6000, 3000)

        # Non-sectioned push(): historical status="error".
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(DocsRequestBuilder, "build", _raise_too_expensive)
            legacy_result = backend.push(str(local), "doc-1")
        assert legacy_result.status == "error"
        assert legacy_result.message == str(DiffTooExpensive("document", 6000, 3000))
        fake_client.batch_update.assert_not_called()

        # Sectioned push_sectioned(): status="blocked" for the identical guard.
        sectioned_backend, sectioned_client = make_backend()
        sectioned_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        sectioned_backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(DocsRequestBuilder, "build", _raise_too_expensive)
            sectioned_result = sectioned_backend.push_sectioned(str(local_dir), "doc-1")
        assert sectioned_result.status == "blocked"
        assert sectioned_result.message == str(DiffTooExpensive("document", 6000, 3000))
        sectioned_client.batch_update.assert_not_called()

        # Same exception, deliberately different reported status — this is
        # the divergence being locked in, not an oversight.
        assert legacy_result.status != sectioned_result.status

    def test_push_sectioned_should_report_error_without_partial_state_when_batch_update_fails(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        section_3 = local_dir / "03-section-3.md"
        edited = section_3.read_text().replace(
            "Body of section 3.", "Body of section 3, updated."
        )
        section_3.write_text(edited)
        fake_client.batch_update.side_effect = RuntimeError("network exploded mid push")

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "error"
        assert "network exploded" in (result.message or "")
        assert result.url is None
        # Exactly one batch_update attempt — pass 1 raised, so pass 2 (and
        # any further write) never runs; no partial-apply-then-fail state.
        assert fake_client.batch_update.call_count == 1
        # Local section files are never touched by push_sectioned itself —
        # the edit made above is still exactly what was written.
        assert section_3.read_text() == edited

    def test_push_sectioned_pull_then_push_with_no_edits_should_produce_zero_diff(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Fixpoint invariant (plan.md/validation.md): pull -> push with zero
        local edits must be a true no-op — zero diff, zero batch_update
        calls. validation.md names this scenario
        tests/test_gdocs_push_pipeline.py::
        test_sectioned_pull_then_push_with_no_edits_should_produce_zero_diff,
        a file outside this task's declared scope
        (tests/test_google_docs_backend.py only); the scenario is
        implemented here instead so the gate is still covered, with the
        location/name mismatch flagged for follow-up.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "skipped", result.message
        fake_client.batch_update.assert_not_called()

    def test_push_sectioned_should_return_error_when_section_file_unreadable(
        self,
        tmp_path,
        make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]],
        monkeypatch,
    ) -> None:  # type: ignore[no-untyped-def]
        """A section file present in the manifest but unreadable at push
        time (e.g. a permissions problem) must surface as
        `PushResult(status="error", ...)`, matching the `ManifestStore.load`
        failure a few lines above — not an unhandled OSError bubbling out of
        push_sectioned.

        Simulates the unreadable file via `monkeypatch` on
        `pathlib.Path.read_text` rather than `chmod(0o000)`: the latter is a
        no-op when the test suite runs as root (common in CI containers),
        which made this test pass vacuously there.
        """
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        section_3 = local_dir / "03-section-3.md"
        real_read_text = pathlib.Path.read_text

        def _raising_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self == section_3:
                raise OSError("simulated permission denied")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", _raising_read_text)

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "error"
        assert "03-section-3.md" in (result.message or "") or "cannot push sectioned mapping" in (
            result.message or ""
        )
        fake_client.batch_update.assert_not_called()

    def test_push_sectioned_should_warn_when_two_sections_reference_same_image_filename_with_different_sources(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Two sections referencing an image with the same filename but a
        different underlying source must be surfaced as a push warning
        naming both originating sections and the shared filename, per
        plan.md's Observability Plan ("Two sections reference
        identically-named-but-different images at push time")."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _sectioned_doc()
        local_dir = tmp_path / "doc"
        backend.pull_sectioned("doc-1", str(local_dir), split_level="HEADING_1")

        (local_dir / "assets").mkdir()
        (local_dir / "other").mkdir()
        (local_dir / "assets" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        (local_dir / "other" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nsecond")

        section_1 = local_dir / "01-section-1.md"
        section_2 = local_dir / "02-section-2.md"
        section_1.write_text(section_1.read_text() + "\n![one](assets/diagram.png)\n")
        section_2.write_text(section_2.read_text() + "\n![two](other/diagram.png)\n")

        result = backend.push_sectioned(str(local_dir), "doc-1")

        assert result.status == "warning", result.message
        assert "diagram.png" in (result.message or "")
        assert "01-section-1.md" in (result.message or "")
        assert "02-section-2.md" in (result.message or "")


# ─────────────────────────────────────────────────────────────────────────────
# Epic 0 (gdocs-native-blockquotes) — live-Doc spike, re-runnable
#
# project_plans/gdocs-native-blockquotes/implementation/epic-0-spike-findings.md
# documents that BLOCKQUOTE_BORDER_MARKER/BLOCKQUOTE_INDENT_PT_PER_LEVEL below are an
# ENGINEERING DECISION PENDING LIVE VERIFICATION, not a captured live-Doc result: no
# batchUpdate/documents.get call was made against the real Google Docs API to produce
# tests/fixtures/blockquote_border_marker_spike.json. This test only replays that
# hand-built fixture through the mocked client boundary — it can confirm the fixture's
# internal shape is self-consistent, not that a real Doc echoes these bytes back.
# ─────────────────────────────────────────────────────────────────────────────

class TestEpic0LiveDocSpike:
    @pytest.mark.skip("requires live Docs API credentials")
    def test_live_doc_spike_should_ReproduceRecordedBorderBehavior_When_RerunAgainstFixture(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:  # type: ignore[no-untyped-def]
        """Re-runnable scaffold for Epic 0/Story 0.1's live-Doc spike.

        Skipped in CI because it requires a real, explicitly-authorized throwaway
        Google Doc and live OAuth credentials — see "How to actually run this spike"
        in epic-0-spike-findings.md for the steps a maintainer follows to unskip this.
        Once unskipped, this should send the fixture's `batch_update_request` against a
        real throwaway document, `documents.get` the same range back, and assert the
        real response's `paragraphStyle.borderLeft`/`indentStart` matches (or, if it
        diverges, that divergence becomes the new recorded fixture and this test is
        updated to match reality rather than the other way around).
        """
        fixtures_dir = pathlib.Path(__file__).parent / "fixtures"
        fixture = json.loads(
            (fixtures_dir / "blockquote_border_marker_spike.json").read_text()
        )

        client = make_client()
        client.batch_update("live-spike-doc-id", fixture["batch_update_request"]["requests"])

        # A real spike run replaces this mocked return_value with an actual
        # `documents.get` call against the throwaway Doc created in step 2 of
        # epic-0-spike-findings.md's runbook.
        client.docs_service.documents().get().execute.return_value = fixture[
            "documents_get_response_paragraph_style_echo"
        ]
        echoed = client.docs_service.documents().get().execute()

        assert (
            echoed["paragraphStyle"]["borderLeft"]
            == fixture["candidate_blockquote_border_marker"]
        )
        assert (
            echoed["paragraphStyle"]["indentStart"]["magnitude"]
            == fixture["candidate_blockquote_indent_pt_per_level"]
        )


# ─────────────────────────────────────────────────────────────────────────────
# Epic 1: Domain Model — Identity Fields (is_blockquote/quote_depth)
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockquoteIdentityFields:
    """Story 1.1: is_blockquote/quote_depth fields, invariant, marker constants."""

    def test_DocsParagraphNode_should_AcceptBlockquoteFields_When_ConstructedWithValidPair(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        node = DocsParagraphNode(
            style="NORMAL_TEXT", text="quoted", is_blockquote=True, quote_depth=2
        )

        assert node.is_blockquote is True
        assert node.quote_depth == 2

    def test_DocsParagraphNode_should_DefaultBlockquoteFieldsToFalseAndZero_When_Unset(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        node = DocsParagraphNode(style="NORMAL_TEXT", text="plain")

        assert node.is_blockquote is False
        assert node.quote_depth == 0

    @pytest.mark.parametrize("is_blockquote, quote_depth", [(False, 2), (True, 0)])
    def test_DocsParagraphNode_should_RaiseValueError_When_ConstructedWithIllegalBlockquotePair(
        self, is_blockquote: bool, quote_depth: int
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        with pytest.raises(ValueError):
            DocsParagraphNode(
                style="NORMAL_TEXT",
                text="x",
                is_blockquote=is_blockquote,
                quote_depth=quote_depth,
            )

    def test_DocsRequestBuilder_should_ImportSameMarkerObject_When_ComparedByIdentity(
        self,
    ) -> None:
        from docspan.backends.google_docs import (
            docs_request_builder as docs_request_builder_module,
        )
        from docspan.backends.google_docs import (
            docs_structure_parser as docs_structure_parser_module,
        )

        assert (
            docs_request_builder_module.BLOCKQUOTE_BORDER_MARKER
            is docs_structure_parser_module.BLOCKQUOTE_BORDER_MARKER
        )
        assert (
            docs_request_builder_module.BLOCKQUOTE_INDENT_PT_PER_LEVEL
            is docs_structure_parser_module.BLOCKQUOTE_INDENT_PT_PER_LEVEL
        )


class TestNodeKeyContentKeyBlockquoteIdentity:
    """Story 1.2: `_node_key` includes blockquote identity, `_content_key` doesn't."""

    def test__node_key_should_DifferByBlockquoteFields_When_TextIsIdentical(self) -> None:
        from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        builder = DocsRequestBuilder()
        plain = DocsParagraphNode(style="NORMAL_TEXT", text="See the docs")
        quoted = DocsParagraphNode(
            style="NORMAL_TEXT", text="See the docs", is_blockquote=True, quote_depth=1
        )

        assert builder._node_key(plain) != builder._node_key(quoted)

    def test__content_key_should_IgnoreBlockquoteFields_When_TextIsIdentical(self) -> None:
        from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        builder = DocsRequestBuilder()
        plain = DocsParagraphNode(style="NORMAL_TEXT", text="See the docs")
        quoted = DocsParagraphNode(
            style="NORMAL_TEXT", text="See the docs", is_blockquote=True, quote_depth=1
        )

        assert builder._content_key(plain) == builder._content_key(quoted)


class TestRepairDoesNotCrossPairBlockquoteAndPlainParagraph:
    """Story 1.3: `_structural_score`/`_prefer_structural_pairing` cross-doc pooling.

    Epic 1 Task 1 finding (Story 1.3): before this story, `_structural_score`
    inspected only `style`/`is_heading_style`/`is_list_item` — nothing there read
    blockquote identity, so two same-text, same-style, same-list-item paragraphs
    (one a blockquote, one not) pooled by `_repair`'s global `_content_key` pass
    scored identically on every existing term and could be assigned to each
    other's slot by list-position tie-break alone. Fixed by adding
    `is_blockquote`/`quote_depth` equality terms to `_structural_score`.
    """

    def test__repair_should_NotCrossPairBlockquoteAndPlainParagraph_When_TextIsIdentical(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

        builder = DocsRequestBuilder()

        plain_current = DocsParagraphNode(
            style="NORMAL_TEXT", text="See the docs", start_index=1, end_index=10
        )
        quote_current = DocsParagraphNode(
            style="NORMAL_TEXT",
            text="See the docs",
            is_blockquote=True,
            quote_depth=1,
            start_index=11,
            end_index=20,
        )
        current = [plain_current, quote_current]

        quote_target = DocsParagraphNode(
            style="NORMAL_TEXT", text="See the docs", is_blockquote=True, quote_depth=1
        )
        plain_target = DocsParagraphNode(style="NORMAL_TEXT", text="See the docs")
        target = [quote_target, plain_target]

        # Two standalone singleton "delete" candidates (current[0]=plain,
        # current[1]=blockquote) and two standalone singleton "insert" slots
        # (target[0]=blockquote, target[1]=plain) — the exact shape
        # `_repair`'s global content-key pooling produces when a blockquote
        # and a plain paragraph sharing identical text land in unrelated
        # opcodes elsewhere in the same document (see `_prefer_structural_pairing`
        # docstring). All four share one `_content_key` (text-only), so without
        # the blockquote-aware scoring terms this is fixing, the two slots tie
        # on every other term and resolve by list-position alone.
        pending = [
            ("delete", 0, 1, 0, 0),
            ("delete", 1, 2, 0, 0),
            ("insert", 0, 0, 0, 1),
            ("insert", 0, 0, 1, 2),
        ]
        origin = [0, 0, 0, 0]

        result = builder._prefer_structural_pairing(pending, origin, current, target)

        by_target_start = {op[3]: op for op in result}

        # target[0] (the blockquote) must resolve to current[1] (the
        # blockquote), not current[0] (the plain paragraph) — and vice versa.
        assert by_target_start[0][:3] == ("equal", 1, 2)
        assert by_target_start[1][:3] == ("equal", 0, 1)


class TestProjectionBlockquoteBlankLineCarveOut:
    """Story 2.5: `project()`'s blank-paragraph-drop rule must not drop an
    empty blockquote line — `MarkdownToParagraphParser` now emits a real,
    empty-text node with `is_blockquote=True` for one (unlike an ordinary
    blank line, which produces no node at all), so it is representable and
    the asymmetry the drop rule exists to paper over does not apply."""

    def test_projection_should_KeepEmptyBlockquoteParagraph_When_TextIsBlank(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.projection import project

        node = DocsParagraphNode(
            style="NORMAL_TEXT", text="", is_blockquote=True, quote_depth=1,
            start_index=1, end_index=2,
        )

        kept, residue = project([node])

        assert kept == [node]
        assert residue == []

    def test_projection_should_DropEmptyParagraph_When_TextIsBlankAndNotBlockquote(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.projection import project

        node = DocsParagraphNode(style="NORMAL_TEXT", text="", start_index=1, end_index=2)

        kept, residue = project([node])

        assert kept == []
        assert len(residue) == 1


class TestDetectBlockquoteDepth:
    """Story 3.1: `_detect_blockquote_depth`/`_parse_paragraph` read a live
    paragraph's `borderLeft`/`indentStart` back into `is_blockquote`/
    `quote_depth` — the pull-side counterpart of Epic 2's
    `_blockquote_paragraph_style_fields` (docs_request_builder.py), which
    writes exactly this shape on push.
    """

    def test__parse_paragraph_should_SetBlockquoteFields_When_BorderMatchesMarker(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import (
            BLOCKQUOTE_BORDER_MARKER,
            BLOCKQUOTE_INDENT_PT_PER_LEVEL,
            DocsStructureParser,
        )

        parser = DocsStructureParser()
        element = {
            "startIndex": 1,
            "endIndex": 10,
            "paragraph": {
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "borderLeft": BLOCKQUOTE_BORDER_MARKER,
                    "indentStart": {
                        "magnitude": BLOCKQUOTE_INDENT_PT_PER_LEVEL,
                        "unit": "PT",
                    },
                },
                "elements": [{"textRun": {"content": "quoted\n", "textStyle": {}}}],
            },
        }

        node = parser._parse_paragraph(element)

        assert node.is_blockquote is True
        assert node.quote_depth == 1

    def test__parse_paragraph_should_LeaveBlockquoteFalse_When_BorderDoesNotMatchMarker(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import (
            BLOCKQUOTE_INDENT_PT_PER_LEVEL,
            DocsStructureParser,
        )

        parser = DocsStructureParser()
        element = {
            "startIndex": 1,
            "endIndex": 10,
            "paragraph": {
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    # A human-applied or otherwise unrelated left border --
                    # different color than BLOCKQUOTE_BORDER_MARKER.
                    "borderLeft": {
                        "color": {"color": {"rgbColor": {"red": 1, "green": 0, "blue": 0}}},
                        "width": {"magnitude": 1, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                    "indentStart": {
                        "magnitude": BLOCKQUOTE_INDENT_PT_PER_LEVEL,
                        "unit": "PT",
                    },
                },
                "elements": [{"textRun": {"content": "not a quote\n", "textStyle": {}}}],
            },
        }

        node = parser._parse_paragraph(element)

        assert node.is_blockquote is False
        assert node.quote_depth == 0

    def test__detect_blockquote_depth_should_MatchOnSubfields_When_DocsEchoesExtraPaddingDefault(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import (
            BLOCKQUOTE_BORDER_MARKER,
            BLOCKQUOTE_INDENT_PT_PER_LEVEL,
            _detect_blockquote_depth,
        )

        # Docs is free to echo back a normalized `padding` docspan never
        # wrote explicitly -- detection must match on color/width/dashStyle
        # alone, not the whole `borderLeft` dict (see Story 3.1 Unresolved
        # Question 2).
        border_left = dict(BLOCKQUOTE_BORDER_MARKER)
        border_left["padding"] = {"magnitude": 99, "unit": "PT"}
        paragraph_style = {
            "borderLeft": border_left,
            "indentStart": {
                "magnitude": BLOCKQUOTE_INDENT_PT_PER_LEVEL * 2,
                "unit": "PT",
            },
        }

        assert _detect_blockquote_depth(paragraph_style) == 2


class TestLegacyBlockquotePassthrough:
    """Story 3.1b: a Doc pushed under the old literal-`>`-text scheme must
    still pull correctly unchanged -- no native-blockquote code path fires
    for a paragraph whose `paragraphStyle` carries no border/indent marker,
    even when its literal text looks like a quote.
    """

    def test_render_nodes_to_markdown_should_PreserveLiteralPrefix_When_ParagraphIsLegacyUnmigratedQuote(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

        node = DocsParagraphNode(style="NORMAL_TEXT", text="> legacy note")

        assert node.is_blockquote is False
        assert render_nodes_to_markdown([node]) == "> legacy note\n"

    def test_render_nodes_to_markdown_should_PreserveLiteralPrefix_When_ParagraphIsLegacyNestedQuote(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

        node = DocsParagraphNode(style="NORMAL_TEXT", text="> > legacy nested")

        assert node.is_blockquote is False
        assert render_nodes_to_markdown([node]) == "> > legacy nested\n"


class TestGroupBlockquoteRuns:
    """Story 3.2: `_group_blockquote_runs` becomes the sole outer grouping
    stage, partitioning a flat node list into plain/code/blockquote runs and
    composing with `_group_code_runs` so a fence entirely inside a quote
    nests correctly.
    """

    def test__group_blockquote_runs_should_GroupContiguousQuoteNodes_When_SequenceHasMixedNodes(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.nodes_to_markdown import _group_blockquote_runs

        n0 = DocsParagraphNode(style="NORMAL_TEXT", text="before")
        n1 = DocsParagraphNode(
            style="NORMAL_TEXT", text="first line", is_blockquote=True, quote_depth=1
        )
        n2 = DocsParagraphNode(
            style="NORMAL_TEXT", text="second line", is_blockquote=True, quote_depth=1
        )
        n3 = DocsParagraphNode(style="NORMAL_TEXT", text="after")

        result = _group_blockquote_runs([n0, n1, n2, n3])

        assert result == [
            ("node", n0),
            ("blockquote", 1, [("node", n1), ("node", n2)]),
            ("node", n3),
        ]

    def test__group_blockquote_runs_should_PreserveCodeLanguage_When_QuoteContainsFencedCodeBlock(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import (
            DocsParagraphNode,
            TextSpan,
        )
        from docspan.backends.google_docs.nodes_to_markdown import (
            FENCE_MARKER,
            _group_blockquote_runs,
        )

        marker = DocsParagraphNode(
            style="NORMAL_TEXT",
            text=FENCE_MARKER + "python",
            is_blockquote=True,
            quote_depth=1,
        )
        code = DocsParagraphNode(
            style="NORMAL_TEXT",
            text="print(1)",
            is_blockquote=True,
            quote_depth=1,
            spans=[TextSpan(text="print(1)", monospace=True)],
        )

        result = _group_blockquote_runs([marker, code])

        assert len(result) == 1
        kind, depth, inner_groups = result[0]
        assert kind == "blockquote"
        assert depth == 1
        assert len(inner_groups) == 1
        code_kind, lang, code_nodes = inner_groups[0]
        assert code_kind == "code"
        assert lang == "python"
        assert code_nodes == [code]


class TestBlockquoteNodeRenderer:
    """Story 3.3: `BlockquoteNodeRenderer` prefixes every inner line with
    `"> " * depth`, joining lines directly -- no blank line between them,
    since CommonMark reads a blank `> ` line as starting a new paragraph
    inside the same quote, not the multi-line source it represents.
    """

    def test_BlockquoteNodeRenderer_should_PrefixEachLine_When_RenderingPlainQuoteRun(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.nodes_to_markdown import (
            BlockquoteNodeRenderer,
            _group_code_runs,
        )

        n1 = DocsParagraphNode(
            style="NORMAL_TEXT", text="first line", is_blockquote=True, quote_depth=1
        )
        n2 = DocsParagraphNode(
            style="NORMAL_TEXT", text="second line", is_blockquote=True, quote_depth=1
        )
        inner_groups = _group_code_runs([n1, n2])

        result = BlockquoteNodeRenderer().render((1, inner_groups))

        assert result == "> first line\n> second line"

    def test_BlockquoteNodeRenderer_should_DoublePrefixLines_When_RenderingDepthTwoRun(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs.nodes_to_markdown import (
            BlockquoteNodeRenderer,
            _group_code_runs,
        )

        n1 = DocsParagraphNode(
            style="NORMAL_TEXT", text="first line", is_blockquote=True, quote_depth=2
        )
        n2 = DocsParagraphNode(
            style="NORMAL_TEXT", text="second line", is_blockquote=True, quote_depth=2
        )
        inner_groups = _group_code_runs([n1, n2])

        result = BlockquoteNodeRenderer().render((2, inner_groups))

        assert result == "> > first line\n> > second line"

    def test_render_nodes_to_markdown_should_CallGroupBlockquoteRunsAsOuterStage_When_SequenceHasMixedNodes(
        self,
    ) -> None:
        from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
        from docspan.backends.google_docs import nodes_to_markdown as n2m

        n0 = DocsParagraphNode(style="NORMAL_TEXT", text="before")
        n1 = DocsParagraphNode(
            style="NORMAL_TEXT", text="quoted", is_blockquote=True, quote_depth=1
        )
        nodes = [n0, n1]

        original = n2m._group_code_runs
        calls: list = []

        def spy(seq):
            calls.append(list(seq))
            return original(seq)

        with mock.patch.object(n2m, "_group_code_runs", side_effect=spy):
            n2m.render_nodes_to_markdown(nodes)

        # _group_code_runs must never see the raw, unpartitioned sequence
        # directly -- render_nodes_to_markdown calls _group_blockquote_runs
        # first, which only ever hands _group_code_runs a same-kind
        # sub-stretch (a plain run or a single blockquote run's nodes).
        assert nodes not in calls


class TestPushPullBlockquoteRoundtrip:
    """Story 3.3: round-tripping markdown containing a native blockquote
    through parse (push side) and render (pull side) must reproduce the
    original markdown byte-for-byte, for each of: a plain multi-paragraph
    quote, a nested quote, a list inside a quote, and a fenced code block
    inside a quote (language tag included).
    """

    @pytest.mark.parametrize(
        "markdown_text",
        [
            pytest.param("> first line\n>\n> second line\n", id="plain"),
            pytest.param("> outer\n> > inner\n", id="nested"),
            pytest.param("> - item one\n> - item two\n", id="list_in_quote"),
            pytest.param("> ```python\n> print(1)\n> ```\n", id="code_in_quote"),
        ],
    )
    def test_push_pull_roundtrip_should_ReproduceByteIdenticalMarkdown_When_QuoteIsPlainNestedListOrCode(
        self, markdown_text: str
    ) -> None:
        from docspan.backends.google_docs.markdown_to_paragraph_parser import (
            MarkdownToParagraphParser,
        )
        from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

        nodes = MarkdownToParagraphParser().parse(markdown_text)

        result = render_nodes_to_markdown(nodes)

        assert result == markdown_text
        if "```python" in markdown_text:
            assert "python" in result
