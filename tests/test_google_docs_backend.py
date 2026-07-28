"""Tests for GoogleDocsClient.batch_update revision guard and GoogleDocsBackend.push()
conflict handling (RevisionGuard, Epic 1.1 of wedding-planning-workflow).

Mocks at the docs_service / drive_service boundary — no real network calls.

Shared `make_client`/`make_http_error`/`make_backend` factory fixtures live in
tests/conftest.py (also used by tests/test_push_preview.py).
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

from docspan.backends.base import PushResult
from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.client import GoogleDocsClient

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
    def test_unappliable_difference_is_reported_instead_of_no_changes(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """"No changes detected" over a doc that provably differs is a false
        parity claim — the user is told they are in sync and stops looking."""
        backend, fake_client = make_backend()
        fake_client.get_document.return_value = _doc_with_pinned_empty_paragraph()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("Intro\n\nAlpha\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "warning"
        assert "No changes detected" not in (result.message or "")
        assert "still differs" in (result.message or "")
        assert result.message.startswith("1 blank paragraph")
        fake_client.batch_update.assert_not_called()

    def test_the_bodys_own_terminal_paragraph_is_not_reported_as_a_difference(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:  # type: ignore[no-untyped-def]
        """Every Docs body ends with a paragraph markdown cannot express, and
        diff_summary honestly calls it a removal. If push() treated that as an
        unappliable difference, every push of every already-synced document
        would warn — so the terminal paragraph must be excluded by name."""
        backend, fake_client = make_backend()
        content = []
        index = 1
        for text in ("Intro", "Alpha", ""):
            element, index = _paragraph_element(index, text)
            content.append(element)
        fake_client.get_document.return_value = {"revisionId": "rev-y",
                                                 "body": {"content": content}}
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("Intro\n\nAlpha\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "skipped"
        assert result.message == "No changes detected"
        fake_client.batch_update.assert_not_called()

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
