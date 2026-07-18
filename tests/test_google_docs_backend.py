"""Tests for GoogleDocsClient.batch_update revision guard and GoogleDocsBackend.push()
conflict handling (RevisionGuard, Epic 1.1 of wedding-planning-workflow).

Mocks at the docs_service / drive_service boundary — no real network calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from docspan.backends.base import PushResult
from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.config import GoogleDocsConfig


def _make_http_error(status: int, message: str) -> HttpError:
    """Build an HttpError with a fake httplib2-style response and body."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, message.encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsClient.batch_update — writeControl.requiredRevisionId
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchUpdateRevisionGuard:
    def _make_client(self) -> GoogleDocsClient:
        client = GoogleDocsClient.__new__(GoogleDocsClient)
        client.docs_service = MagicMock()
        client.drive_service = MagicMock()
        return client

    def test_batch_update_includes_write_control_when_required_revision_id_given(self) -> None:
        client = self._make_client()
        execute_mock = client.docs_service.documents.return_value.batchUpdate.return_value.execute
        execute_mock.return_value = {"documentId": "doc-1"}

        requests = [{"insertText": {"location": {"index": 1}, "text": "hi"}}]
        client.batch_update("doc-1", requests, required_revision_id="ALm37abc")

        _, kwargs = client.docs_service.documents.return_value.batchUpdate.call_args
        assert kwargs["documentId"] == "doc-1"
        assert kwargs["body"]["requests"] == requests
        assert kwargs["body"]["writeControl"] == {"requiredRevisionId": "ALm37abc"}

    def test_batch_update_omits_write_control_when_required_revision_id_is_none(self) -> None:
        client = self._make_client()
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
    def _make_backend(self) -> tuple[GoogleDocsBackend, MagicMock]:
        backend = GoogleDocsBackend(GoogleDocsConfig())
        fake_client = MagicMock()
        backend._client = fake_client
        return backend, fake_client

    def test_push_passes_fetched_revision_id_into_batch_update(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "ok"
        assert fake_client.batch_update.call_count == 1
        args, kwargs = fake_client.batch_update.call_args
        assert args[0] == "doc-1"
        assert kwargs["required_revision_id"] == "ALm37abc"

    def test_push_returns_conflict_status_with_friendly_message_on_stale_revision(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = _make_http_error(
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

    def test_push_returns_error_status_for_non_revision_http_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = _make_http_error(500, "Internal server error")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "error"
        assert result.message != "The doc changed since your last pull — run `docspan pull` again"

    def test_push_returns_error_status_for_generic_exception(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
        fake_client.get_document.return_value = _empty_doc(revision_id="ALm37abc")
        fake_client.batch_update.side_effect = RuntimeError("network exploded")

        local = tmp_path / "doc.md"
        local.write_text("# Some content\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status == "error"
        assert "network exploded" in (result.message or "")

    def test_push_does_not_call_batch_update_when_no_changes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
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
    def _make_backend(self) -> tuple[GoogleDocsBackend, MagicMock]:
        backend = GoogleDocsBackend(GoogleDocsConfig())
        fake_client = MagicMock()
        backend._client = fake_client
        return backend, fake_client

    def test_preview_push_never_calls_batch_update_even_when_high_risk(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
        fake_client.get_document.return_value = _checkbox_glyph_doc()
        fake_client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text("- [x] Whatsapp group\n", encoding="utf-8")

        preview = backend.preview_push(str(local), "doc-1")

        assert len(preview.high_risk) == 1
        assert preview.high_risk[0].reasons == ["native_glyph"]
        fake_client.batch_update.assert_not_called()

    def test_push_blocks_on_high_risk_using_exactly_one_fetch_it_performed_itself(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
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

    def test_push_force_true_proceeds_using_revision_id_from_its_own_fetch(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
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
# CommentCountBackstop (plan.md Task 1.2.3c/1.2.3d)
# ─────────────────────────────────────────────────────────────────────────────

class TestCommentCountBackstop:
    def _make_backend(self) -> tuple[GoogleDocsBackend, MagicMock]:
        backend = GoogleDocsBackend(GoogleDocsConfig())
        fake_client = MagicMock()
        backend._client = fake_client
        return backend, fake_client

    def test_push_appends_comment_count_dropped_warning_when_post_push_count_is_lower(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
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

    def test_push_message_has_no_drop_warning_when_comment_count_unchanged(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        backend, fake_client = self._make_backend()
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
