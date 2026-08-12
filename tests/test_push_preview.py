"""Unit tests for push_preview.py — find_high_risk_paragraphs(), render_high_risk(),
PushPreview.render(), and GoogleDocsClient.list_comments() (Epic 1.2, Story 1.2.2).

Shared `make_client`/`make_http_error` factory fixtures live in tests/conftest.py
(also used by tests/test_google_docs_backend.py).
"""
from __future__ import annotations

from typing import Callable

import pytest
from googleapiclient.errors import HttpError

from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.backends.google_docs.docs_request_builder import DiffEntry
from docspan.backends.google_docs.push_preview import (
    AtRiskComment,
    HighRiskParagraph,
    PushPreview,
    find_high_risk_paragraphs,
    render_high_risk,
)

# ─────────────────────────────────────────────────────────────────────────────
# GoogleDocsClient.list_comments()
# ─────────────────────────────────────────────────────────────────────────────

class TestListComments:
    def test_list_comments_excludes_resolved_comments(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        execute_mock = client.drive_service.comments.return_value.list.return_value.execute
        execute_mock.return_value = {
            "comments": [
                {
                    "id": "c1",
                    "content": "check this",
                    "quotedFileContent": {"value": "inner"},
                    "resolved": False,
                    "author": {"displayName": "Nora Sullivan"},
                },
                {
                    "id": "c2",
                    "content": "old, resolved",
                    "quotedFileContent": {"value": "whatever"},
                    "resolved": True,
                    "author": {"displayName": "Bekah"},
                },
            ]
        }

        comments = client.list_comments("doc-1")

        assert len(comments) == 1
        assert comments[0]["id"] == "c1"

    def test_list_comments_returns_open_comment_for_scratch_doc(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        """Mirrors Story 1.2.2's acceptance criterion — one open comment with
        quotedFileContent.value == "inner", one resolved comment excluded."""
        client = make_client()
        execute_mock = client.drive_service.comments.return_value.list.return_value.execute
        execute_mock.return_value = {
            "comments": [
                {
                    "id": "open-1",
                    "quotedFileContent": {"value": "inner"},
                    "resolved": False,
                    "author": {"displayName": "Nora Sullivan"},
                },
                {
                    "id": "resolved-1",
                    "quotedFileContent": {"value": "outer"},
                    "resolved": True,
                    "author": {"displayName": "Nora Sullivan"},
                },
            ]
        }

        comments = client.list_comments("scratch-doc-1")

        assert len(comments) == 1
        assert comments[0]["quotedFileContent"]["value"] == "inner"

    def test_list_comments_propagates_http_error_from_drive_service(
        self,
        make_client: Callable[[], GoogleDocsClient],
        make_http_error: Callable[[int, str], HttpError],
    ) -> None:
        """A 403 scope-denial HttpError must not be swallowed silently — it
        must surface so push()'s outer except Exception can turn it into
        PushResult(status="error", ...) rather than a false 'no comments'."""
        client = make_client()
        client.drive_service.comments.return_value.list.return_value.execute.side_effect = (
            make_http_error(403, "The user does not have sufficient permissions")
        )

        with pytest.raises(HttpError):
            client.list_comments("doc-1")

    def test_list_comments_returns_empty_list_when_no_comments(
        self, make_client: Callable[[], GoogleDocsClient]
    ) -> None:
        client = make_client()
        client.drive_service.comments.return_value.list.return_value.execute.return_value = {}
        assert client.list_comments("doc-1") == []


# ─────────────────────────────────────────────────────────────────────────────
# find_high_risk_paragraphs() — CommentCrossReference
# ─────────────────────────────────────────────────────────────────────────────

def test_find_high_risk_paragraphs_flags_changed_paragraph_with_open_comment() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="Casual gathering for dinner at 6:30pm Friday",
            target_text="Casual dinner at 6:30pm Friday",
            style="NORMAL_TEXT",
        )
    ]
    comments = [{"id": "c1", "quotedFileContent": {"value": "inner"}, "author": {"displayName": "Nora Sullivan"}}]

    result = find_high_risk_paragraphs(entries, comments)

    assert result == [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner at 6:30pm Friday",
            reasons=["comment"],
            comments=[AtRiskComment(id="c1", quoted_text="inner", author="Nora Sullivan")],
        )
    ]


def test_find_high_risk_paragraphs_ignores_unchanged_paragraphs() -> None:
    """An open comment whose paragraph never appears as a remove/change
    DiffEntry (unchanged, or belongs to an unrelated paragraph) produces []."""
    entries = [
        DiffEntry(
            kind="change",
            current_text="An unrelated paragraph entirely",
            target_text="An unrelated paragraph, edited",
            style="NORMAL_TEXT",
        )
    ]
    comments = [{"quotedFileContent": {"value": "inner"}, "author": {"displayName": "Nora Sullivan"}}]

    assert find_high_risk_paragraphs(entries, comments) == []


def test_find_high_risk_paragraphs_flags_native_checkbox_glyph_paragraph_even_without_comment() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="[ ] Whatsapp group",
            target_text="[x] Whatsapp group",
            style="NORMAL_TEXT",
            current_is_native_checkbox=True,
        )
    ]

    result = find_high_risk_paragraphs(entries, comments=[])

    assert result == [
        HighRiskParagraph(
            paragraph_text="[ ] Whatsapp group",
            reasons=["native_glyph"],
        )
    ]


def test_find_high_risk_paragraphs_does_not_flag_ordinary_literal_checklist_paragraph() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="[ ] Whatsapp group",
            target_text="[x] Whatsapp group",
            style="NORMAL_TEXT",
            current_is_native_checkbox=False,
        )
    ]

    assert find_high_risk_paragraphs(entries, comments=[]) == []


def test_find_high_risk_paragraphs_combines_both_reasons_when_paragraph_has_open_comment_and_is_native_glyph() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="[ ] Whatsapp group discussion",
            target_text="[x] Whatsapp group discussion",
            style="NORMAL_TEXT",
            current_is_native_checkbox=True,
        )
    ]
    comments = [{"id": "c1", "quotedFileContent": {"value": "group"}, "author": {"displayName": "Bekah"}}]

    result = find_high_risk_paragraphs(entries, comments)

    assert len(result) == 1
    assert set(result[0].reasons) == {"comment", "native_glyph"}
    assert result[0].comments == [AtRiskComment(id="c1", quoted_text="group", author="Bekah")]


def test_find_high_risk_paragraphs_only_considers_remove_and_change_kinds() -> None:
    entries = [
        DiffEntry(kind="add", current_text=None, target_text="[ ] New item", style="NORMAL_TEXT"),
        DiffEntry(
            kind="unchanged",
            current_text="[ ] New item",
            target_text="[ ] New item",
            style="NORMAL_TEXT",
        ),
    ]
    comments = [{"quotedFileContent": {"value": "New item"}, "author": {"displayName": "Tyler"}}]
    assert find_high_risk_paragraphs(entries, comments) == []


def test_find_high_risk_paragraphs_ignores_comment_with_empty_quoted_content() -> None:
    entries = [
        DiffEntry(kind="remove", current_text="Anything at all", target_text=None, style="NORMAL_TEXT")
    ]
    comments = [{"quotedFileContent": {"value": ""}, "author": {"displayName": "Tyler"}}]
    assert find_high_risk_paragraphs(entries, comments) == []


def test_find_high_risk_paragraphs_collects_every_matching_comment_not_just_the_first() -> None:
    """Regression test: find_high_risk_paragraphs() must not stop at the first
    open comment that matches a paragraph — a paragraph can carry more than
    one open comment, and dropping the rest silently hid them from the
    warn-before-force message."""
    entries = [
        DiffEntry(
            kind="change",
            current_text="Casual gathering for dinner at 6:30pm Friday",
            target_text="Casual dinner at 6:30pm Friday",
            style="NORMAL_TEXT",
        )
    ]
    comments = [
        {"id": "c1", "quotedFileContent": {"value": "gathering"}, "author": {"displayName": "Nora Sullivan"}},
        {"id": "c2", "quotedFileContent": {"value": "6:30pm"}, "author": {"displayName": "Bekah"}},
    ]

    result = find_high_risk_paragraphs(entries, comments)

    assert len(result) == 1
    assert result[0].comments == [
        AtRiskComment(id="c1", quoted_text="gathering", author="Nora Sullivan"),
        AtRiskComment(id="c2", quoted_text="6:30pm", author="Bekah"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# render_high_risk()
# ─────────────────────────────────────────────────────────────────────────────

def test_render_high_risk_includes_comment_block_with_author_and_quoted_text() -> None:
    high_risk = [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner",
            reasons=["comment"],
            comments=[AtRiskComment(id="c1", quoted_text="inner", author="Nora Sullivan")],
        )
    ]
    rendered = render_high_risk(high_risk)
    assert "⚠ COMMENT AT RISK" in rendered
    assert "Nora Sullivan" in rendered
    assert "inner" in rendered
    assert "c1" in rendered
    assert "--force" in rendered


def test_render_high_risk_lists_every_at_risk_comment_not_just_the_first() -> None:
    high_risk = [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner",
            reasons=["comment"],
            comments=[
                AtRiskComment(id="c1", quoted_text="gathering", author="Nora Sullivan"),
                AtRiskComment(id="c2", quoted_text="dinner", author="Bekah"),
            ],
        )
    ]
    rendered = render_high_risk(high_risk)
    assert "Nora Sullivan" in rendered
    assert "Bekah" in rendered
    assert "c1" in rendered
    assert "c2" in rendered
    assert "gathering" in rendered
    assert "dinner" in rendered


def test_render_high_risk_includes_native_glyph_block() -> None:
    high_risk = [
        HighRiskParagraph(paragraph_text="[ ] Whatsapp group", reasons=["native_glyph"])
    ]
    rendered = render_high_risk(high_risk)
    assert "⚠ NATIVE CHECKBOX GLYPH" in rendered
    assert "[ ] Whatsapp group" in rendered
    assert "--force" in rendered


def test_render_high_risk_renders_both_blocks_for_combined_reasons() -> None:
    high_risk = [
        HighRiskParagraph(
            paragraph_text="[ ] Whatsapp group",
            reasons=["comment", "native_glyph"],
            comments=[AtRiskComment(id="c1", quoted_text="group", author="Bekah")],
        )
    ]
    rendered = render_high_risk(high_risk)
    assert "⚠ COMMENT AT RISK" in rendered
    assert "⚠ NATIVE CHECKBOX GLYPH" in rendered


# ─────────────────────────────────────────────────────────────────────────────
# PushPreview.render()
# ─────────────────────────────────────────────────────────────────────────────

def test_push_preview_render_shows_checklist_toggle() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="[ ] Splitwise",
            target_text="[x] Splitwise",
            style="NORMAL_TEXT",
        )
    ]
    preview = PushPreview(entries=entries, unchanged_count=12, high_risk=[], request_count=3)
    rendered = preview.render()
    assert "~ [ ] Splitwise → [x] Splitwise" in rendered
    assert "12 unchanged" in rendered


def test_push_preview_render_includes_high_risk_warning() -> None:
    entries = [
        DiffEntry(
            kind="change",
            current_text="Casual gathering for dinner",
            target_text="Casual dinner",
            style="NORMAL_TEXT",
        )
    ]
    high_risk = [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner",
            reasons=["comment"],
            comments=[AtRiskComment(id="c1", quoted_text="inner", author="Nora Sullivan")],
        )
    ]
    preview = PushPreview(entries=entries, unchanged_count=0, high_risk=high_risk, request_count=2)
    rendered = preview.render()
    assert "⚠ COMMENT AT RISK" in rendered


def test_push_preview_render_notes_mixed_checklist_and_other_edits() -> None:
    entries = [
        DiffEntry(kind="change", current_text="[ ] Splitwise", target_text="[x] Splitwise", style="NORMAL_TEXT"),
        DiffEntry(
            kind="change",
            current_text="Friday 6:30pm: rehearsal dinner",
            target_text="Friday 7pm: rehearsal dinner",
            style="NORMAL_TEXT",
        ),
    ]
    preview = PushPreview(entries=entries, unchanged_count=0, high_risk=[], request_count=4)
    rendered = preview.render()
    assert "mixes 1 checklist toggle(s) with 1 other edit(s)" in rendered


def test_push_preview_render_no_mixed_note_when_all_checklist() -> None:
    entries = [
        DiffEntry(kind="change", current_text="[ ] Splitwise", target_text="[x] Splitwise", style="NORMAL_TEXT"),
    ]
    preview = PushPreview(entries=entries, unchanged_count=0, high_risk=[], request_count=2)
    rendered = preview.render()
    assert "mixes" not in rendered
