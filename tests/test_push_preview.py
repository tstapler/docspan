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
    HighRiskParagraph,
    PushPreview,
    find_churn_pairs,
    find_high_risk_paragraphs,
    render_churn_note,
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
    comments = [{"quotedFileContent": {"value": "inner"}, "author": {"displayName": "Nora Sullivan"}}]

    result = find_high_risk_paragraphs(entries, comments)

    assert result == [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner at 6:30pm Friday",
            reasons=["comment"],
            comment_quoted_text="inner",
            comment_author="Nora Sullivan",
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
            comment_quoted_text=None,
            comment_author=None,
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
    comments = [{"quotedFileContent": {"value": "group"}, "author": {"displayName": "Bekah"}}]

    result = find_high_risk_paragraphs(entries, comments)

    assert len(result) == 1
    assert set(result[0].reasons) == {"comment", "native_glyph"}
    assert result[0].comment_quoted_text == "group"
    assert result[0].comment_author == "Bekah"


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


# ─────────────────────────────────────────────────────────────────────────────
# render_high_risk()
# ─────────────────────────────────────────────────────────────────────────────

def test_render_high_risk_includes_comment_block_with_author_and_quoted_text() -> None:
    high_risk = [
        HighRiskParagraph(
            paragraph_text="Casual gathering for dinner",
            reasons=["comment"],
            comment_quoted_text="inner",
            comment_author="Nora Sullivan",
        )
    ]
    rendered = render_high_risk(high_risk)
    assert "⚠ COMMENT AT RISK" in rendered
    assert "Nora Sullivan" in rendered
    assert "inner" in rendered
    assert "--force" in rendered


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
            comment_quoted_text="group",
            comment_author="Bekah",
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
            comment_quoted_text="inner",
            comment_author="Nora Sullivan",
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


# ─────────────────────────────────────────────────────────────────────────────
# find_churn_pairs() / render_churn_note()
# ─────────────────────────────────────────────────────────────────────────────

def test_find_churn_pairs_matches_identical_text_from_same_run() -> None:
    remove = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    entries = [remove, add]

    pairs = find_churn_pairs(entries)

    assert pairs == [(remove, add)]


def test_find_churn_pairs_ignores_unrelated_non_adjacent_entries() -> None:
    """Mirrors the `_node_key`/`Config`-heading-collision failure mode
    (docs_request_builder.py:112-150) — identical short text from two
    unrelated opcode runs (different `edit_group`) must never be paired."""
    remove = DiffEntry(
        kind="remove", current_text="TODO", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    unrelated_change = DiffEntry(
        kind="change", current_text="Friday", target_text="Saturday", style="NORMAL_TEXT", edit_group=1
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="TODO", style="NORMAL_TEXT", edit_group=2
    )
    entries = [remove, unrelated_change, add]

    pairs = find_churn_pairs(entries)

    assert pairs == []


def test_find_churn_pairs_ignores_adjacent_entries_from_different_edit_groups() -> None:
    """`_prefer_structural_pairing` (docs_request_builder.py) can carve one
    "replace" run into a winning "equal" plus a same-text-elsewhere
    "delete"/"insert" pair with no "equal" opcode between them, so two
    genuinely unrelated remove/add entries can sit directly adjacent in the
    flat `entries` list. Scoping by `edit_group` (not adjacency) must still
    keep them apart even though nothing else separates them positionally."""
    remove = DiffEntry(
        kind="remove", current_text="TODO", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="TODO", style="NORMAL_TEXT", edit_group=1
    )
    entries = [remove, add]

    pairs = find_churn_pairs(entries)

    assert pairs == []


def test_find_churn_pairs_excludes_table_rows() -> None:
    remove = DiffEntry(kind="remove", current_text="Row A", target_text=None, style="TABLE", edit_group=0)
    add = DiffEntry(kind="add", current_text=None, target_text="Row A", style="TABLE", edit_group=0)

    pairs = find_churn_pairs([remove, add])

    assert pairs == []


def test_find_churn_pairs_1to1_matches_duplicate_text_without_double_counting() -> None:
    remove_a = DiffEntry(
        kind="remove", current_text="", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    remove_b = DiffEntry(
        kind="remove", current_text="", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add_a = DiffEntry(kind="add", current_text=None, target_text="", style="NORMAL_TEXT", edit_group=0)
    entries = [remove_a, remove_b, add_a]

    pairs = find_churn_pairs(entries)

    assert len(pairs) == 1
    assert pairs[0][1] is add_a


def test_find_churn_pairs_empty_entries_returns_no_pairs() -> None:
    assert find_churn_pairs([]) == []


def test_render_churn_note_mentions_comment_and_identity_loss() -> None:
    remove = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )

    note = render_churn_note([(remove, add)])

    assert "comment" in note.lower()
    assert "lost" in note.lower()


def test_push_preview_render_reports_churn_pair_as_rewritten_not_removal() -> None:
    remove = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    preview = PushPreview(entries=[remove, add], unchanged_count=0, high_risk=[], request_count=2)

    rendered = preview.render()

    assert "~ rewritten (no text change)" in rendered
    assert "- Same paragraph" not in rendered
    assert "0 removal(s)" in rendered
    assert "1 rewritten (no text change)" in rendered


def test_push_preview_render_still_shows_comment_at_risk_for_churned_paragraph() -> None:
    remove = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    high_risk = [
        HighRiskParagraph(
            paragraph_text="Same paragraph",
            reasons=["comment"],
            comment_quoted_text="Same paragraph",
            comment_author="Nora Sullivan",
        )
    ]
    preview = PushPreview(entries=[remove, add], unchanged_count=0, high_risk=high_risk, request_count=2)

    rendered = preview.render()

    assert "⚠ COMMENT AT RISK" in rendered
    assert "~ rewritten (no text change)" in rendered


def test_push_preview_render_distinguishes_churn_from_ordinary_entries_in_mixed_batch() -> None:
    """`render()`'s `id()`-based `churned_removes`/`churned_adds` filtering has
    only ever been exercised against entries=[remove, add] (just the churn
    pair itself). Feed it a mix of a churned pair and unrelated plain
    add/remove entries to confirm the id()-based lookup correctly leaves the
    unrelated entries alone instead of e.g. filtering by text or position."""
    churn_remove = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    churn_add = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    unrelated_remove = DiffEntry(
        kind="remove", current_text="Other text", target_text=None, style="NORMAL_TEXT", edit_group=1
    )
    unrelated_add = DiffEntry(
        kind="add", current_text=None, target_text="Other text", style="NORMAL_TEXT", edit_group=2
    )
    entries = [churn_remove, churn_add, unrelated_remove, unrelated_add]
    preview = PushPreview(entries=entries, unchanged_count=0, high_risk=[], request_count=4)

    rendered = preview.render()

    lines = rendered.splitlines()
    rewritten_lines = [line for line in lines if "~ rewritten (no text change)" in line]
    remove_lines = [line for line in lines if line.strip().startswith("- ")]
    add_lines = [line for line in lines if line.strip().startswith("+ ")]

    assert len(rewritten_lines) == 1
    assert remove_lines == ["  - Other text"]
    assert add_lines == ["  + Other text"]
    assert "1 removal(s)" in rendered
    assert "1 addition(s)" in rendered
    assert "1 rewritten (no text change)" in rendered


def test_find_churn_pairs_1to1_matches_two_full_pairs_without_double_claiming() -> None:
    """Extends the duplicate-text coverage above (2 removes + 1 add, one pair
    plus a leftover) to 2 removes + 2 adds with identical text, all in the
    same edit_group — every add must be claimed by exactly one remove, with
    no double-claiming and no add left unpaired."""
    remove_a = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    remove_b = DiffEntry(
        kind="remove", current_text="Same paragraph", target_text=None, style="NORMAL_TEXT", edit_group=0
    )
    add_a = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    add_b = DiffEntry(
        kind="add", current_text=None, target_text="Same paragraph", style="NORMAL_TEXT", edit_group=0
    )
    entries = [remove_a, remove_b, add_a, add_b]

    pairs = find_churn_pairs(entries)

    assert len(pairs) == 2
    claimed_adds = [pair[1] for pair in pairs]
    assert add_a in claimed_adds
    assert add_b in claimed_adds
    assert claimed_adds[0] is not claimed_adds[1]
    claimed_removes = [pair[0] for pair in pairs]
    assert remove_a in claimed_removes
    assert remove_b in claimed_removes
    assert claimed_removes[0] is not claimed_removes[1]
