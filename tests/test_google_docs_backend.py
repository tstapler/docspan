"""Tests for GoogleDocsClient.batch_update revision guard and GoogleDocsBackend.push()
conflict handling (RevisionGuard, Epic 1.1 of wedding-planning-workflow).

Mocks at the docs_service / drive_service boundary — no real network calls.

Shared `make_client`/`make_http_error`/`make_backend` factory fixtures live in
tests/conftest.py (also used by tests/test_push_preview.py).
"""
from __future__ import annotations

import json
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
