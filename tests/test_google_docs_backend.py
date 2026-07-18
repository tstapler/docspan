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
