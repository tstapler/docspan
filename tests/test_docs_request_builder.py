"""Unit tests for DocsRequestBuilder — structural diff algorithm, no network."""

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode, TextSpan

DOC_END = 100


def _para(
    text: str,
    style: str = "NORMAL_TEXT",
    start: int = 1,
    end: int = 10,
    is_list_item: bool = False,
) -> DocsParagraphNode:
    return DocsParagraphNode(
        style=style, text=text, start_index=start, end_index=end, is_list_item=is_list_item
    )


builder = DocsRequestBuilder()


# ─────────────────────────────────────────────────────────────────────────────
# No-change cases
# ─────────────────────────────────────────────────────────────────────────────

def test_identical_docs_produce_no_requests() -> None:
    current = [_para("Hello", start=1, end=7)]
    target = [_para("Hello", start=1, end=7)]
    requests = builder.build(current, target, DOC_END)
    assert requests == []


def test_empty_to_empty_produces_no_requests() -> None:
    assert builder.build([], [], DOC_END) == []


# ─────────────────────────────────────────────────────────────────────────────
# Insert
# ─────────────────────────────────────────────────────────────────────────────

def test_insert_into_empty_doc() -> None:
    current: list = []
    target = [_para("New paragraph")]
    requests = builder.build(current, target, DOC_END)
    # Must produce at least one insert request
    assert any("insertText" in r for r in requests)


def test_insert_appended_paragraph() -> None:
    current = [_para("Existing", start=1, end=9)]
    target = [_para("Existing", start=1, end=9), _para("Appended", start=9, end=18)]
    requests = builder.build(current, target, DOC_END)
    assert any("insertText" in r for r in requests)


def test_mid_document_insert_does_not_merge_into_previous_paragraph() -> None:
    """Regression: inserting a new paragraph between two unchanged paragraphs
    used to target current[i1 - 1].end_index - 1 — the index of the PREVIOUS
    paragraph's own trailing newline character, not the index right after it.
    Inserting there splices the new text in before that newline, merging it
    onto the end of the previous paragraph and leaving a spurious extra blank
    paragraph behind (e.g. "A\\nC\\n" -> "AB\\n\\nC\\n" instead of "A\\nB\\nC\\n")."""
    # "A\n" occupies [1, 3); "C\n" occupies [3, 5).
    current = [_para("A", start=1, end=3), _para("C", start=3, end=5)]
    target = [_para("A", start=1, end=3), _para("B", start=0, end=0), _para("C", start=3, end=5)]
    requests = builder.build(current, target, doc_end_index=5)

    insert_requests = [r for r in requests if "insertText" in r]
    assert len(insert_requests) == 1
    # Must insert at index 3 (right after "A\n", i.e. current[0].end_index),
    # not index 2 (current[0].end_index - 1, the position of "A"'s own "\n").
    assert insert_requests[0]["insertText"]["location"]["index"] == 3
    assert insert_requests[0]["insertText"]["text"] == "B\n"


# ─────────────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_removed_paragraph() -> None:
    current = [_para("Keep", start=1, end=5), _para("Delete me", start=5, end=15)]
    target = [_para("Keep", start=1, end=5)]
    requests = builder.build(current, target, DOC_END)
    assert any("deleteContentRange" in r for r in requests)


def test_delete_all_paragraphs() -> None:
    current = [_para("Gone", start=1, end=5)]
    target: list = []
    requests = builder.build(current, target, DOC_END)
    assert any("deleteContentRange" in r for r in requests)


# ─────────────────────────────────────────────────────────────────────────────
# Replace
# ─────────────────────────────────────────────────────────────────────────────

def test_replace_paragraph_text() -> None:
    current = [_para("Old text", start=1, end=9)]
    target = [_para("New text", start=1, end=9)]
    requests = builder.build(current, target, DOC_END)
    # Replace = delete + insert
    assert any("deleteContentRange" in r for r in requests)
    assert any("insertText" in r for r in requests)


# ─────────────────────────────────────────────────────────────────────────────
# Ordering guarantee
# ─────────────────────────────────────────────────────────────────────────────

def test_requests_sorted_descending_by_start_index() -> None:
    current = [_para("A", start=1, end=3), _para("B", start=3, end=6), _para("C", start=6, end=9)]
    target = [_para("A", start=1, end=3), _para("X", start=3, end=6), _para("C", start=6, end=9)]
    requests = builder.build(current, target, DOC_END)
    if len(requests) >= 2:
        indices = []
        for r in requests:
            if "deleteContentRange" in r:
                indices.append(r["deleteContentRange"]["range"]["startIndex"])
            elif "insertText" in r:
                indices.append(r["insertText"]["location"]["index"])
        # Should be sorted descending
        assert indices == sorted(indices, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal newline protection
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_does_not_exceed_doc_end() -> None:
    doc_end = 10
    current = [_para("Delete", start=1, end=10)]
    target: list = []
    requests = builder.build(current, target, doc_end)
    for r in requests:
        if "deleteContentRange" in r:
            end_idx = r["deleteContentRange"]["range"]["endIndex"]
            assert end_idx <= doc_end, f"Delete range {end_idx} exceeds doc_end {doc_end}"


# ─────────────────────────────────────────────────────────────────────────────
# Style-only change
# ─────────────────────────────────────────────────────────────────────────────

def test_heading_style_change_emits_style_request() -> None:
    current = [_para("Title", style="HEADING_1", start=1, end=6)]
    target = [_para("Title", style="HEADING_2", start=1, end=6)]
    requests = builder.build(current, target, DOC_END)
    assert any("updateParagraphStyle" in r for r in requests)


def test_same_style_no_style_request() -> None:
    current = [_para("Same", style="HEADING_1", start=1, end=5)]
    target = [_para("Same", style="HEADING_1", start=1, end=5)]
    requests = builder.build(current, target, DOC_END)
    assert not any("updateParagraphStyle" in r for r in requests)


# ─────────────────────────────────────────────────────────────────────────────
# Checklist round-trip (literal-text scheme — see ADR-001)
# ─────────────────────────────────────────────────────────────────────────────

def test_checklist_toggle_produces_replace_with_disc_bullet_not_checkbox() -> None:
    """Toggling `[ ]` -> `[x]` on an otherwise-unchanged list item must be
    diffed exactly like any other single-line text edit: one delete + one
    insert, with the bullet preset staying BULLET_DISC_CIRCLE_SQUARE — never
    BULLET_CHECKBOX (ADR-001's Pattern Decision: checklist state is never
    written as a native checkbox glyph)."""
    current = [_para("[ ] Splitwise", start=50, end=65, is_list_item=True)]
    target = [_para("[x] Splitwise", start=50, end=65, is_list_item=True)]
    # doc_end_index == current node's end_index so the terminal-newline
    # clamp in _make_delete_requests applies, matching plan.md Story 2.1.3's
    # worked example: deleteContentRange clamps to [50, 64).
    requests = builder.build(current, target, doc_end_index=65)

    delete_requests = [r for r in requests if "deleteContentRange" in r]
    insert_requests = [r for r in requests if "insertText" in r]
    bullet_requests = [r for r in requests if "createParagraphBullets" in r]

    assert len(delete_requests) == 1
    assert delete_requests[0]["deleteContentRange"]["range"] == {
        "startIndex": 50,
        "endIndex": 64,
    }

    assert len(insert_requests) == 1
    assert insert_requests[0]["insertText"]["text"] == "[x] Splitwise\n"

    assert len(bullet_requests) == 1
    assert bullet_requests[0]["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
    assert not any(
        r.get("createParagraphBullets", {}).get("bulletPreset") == "BULLET_CHECKBOX"
        for r in requests
    )


# ─────────────────────────────────────────────────────────────────────────────
# Known gap: link/style loss on edited paragraphs
# (feature-gap-report.md item 4 — _make_text_style_requests is dead code)
# ─────────────────────────────────────────────────────────────────────────────

def _para_spans(
    text: str,
    spans: list,
    style: str = "NORMAL_TEXT",
    start: int = 1,
    end: int = 10,
    is_list_item: bool = False,
) -> DocsParagraphNode:
    return DocsParagraphNode(
        style=style,
        text=text,
        start_index=start,
        end_index=end,
        is_list_item=is_list_item,
        spans=spans,
    )


def test_edited_paragraph_with_link_style_loses_text_style_request_confirming_gap() -> None:
    """Pins the documented gap in feature-gap-report.md item 4:
    `_make_text_style_requests` (docs_request_builder.py:287-323) is a fully
    implemented method for emitting `updateTextStyle` requests, but it is
    dead code — `_make_insert_requests` (the only method that writes new
    paragraph content on a "replace"/"insert" diff opcode) never calls it.

    This test asserts the CURRENT (broken) behavior on purpose: a "replace"
    opcode on a paragraph whose target carries a link span and a bold span
    produces `insertText` with the flattened plain text, but NO
    `updateTextStyle` request for either span. Per validation.md's Requirement
    -> Test Mapping (In-scope 3), this is a "pin the known gap" regression
    test — not a bug fix. It is explicitly out of scope to wire
    `_make_text_style_requests` into `_make_insert_requests` this cycle (see
    requirements.md Out of Scope and feature-gap-report.md item 4).

    This test is meant to start FAILING the moment someone fixes the
    underlying dead-code issue in a future cycle — that's the point: it
    should surface either a regression (formatting silently lost again after
    being fixed) or a fix (formatting requests now emitted), never silently
    pass either way."""
    current = [_para("Check the schedule before Friday", start=1, end=34)]
    target = [
        _para_spans(
            "See the day plan for details",
            spans=[
                TextSpan(text="day plan", link="https://example.com/day-plan"),
                TextSpan(text="details", bold=True),
            ],
            start=1,
            end=34,
        )
    ]

    requests = builder.build(current, target, doc_end_index=31)

    insert_requests = [r for r in requests if "insertText" in r]
    style_requests = [r for r in requests if "updateTextStyle" in r]

    assert len(insert_requests) == 1
    assert insert_requests[0]["insertText"]["text"] == "See the day plan for details\n"

    # The gap: no updateTextStyle request is emitted for the link/bold spans,
    # even though the target node carries them. When this starts failing,
    # someone has wired _make_text_style_requests into _make_insert_requests
    # — update/remove this test and feature-gap-report.md item 4 accordingly.
    assert style_requests == []


# ─────────────────────────────────────────────────────────────────────────────
# diff_summary() — human-oriented dry-run diff (plan.md Story 1.2.1)
# ─────────────────────────────────────────────────────────────────────────────

def _para_ncb(
    text: str,
    is_native_checkbox: bool = False,
    style: str = "NORMAL_TEXT",
    start: int = 1,
    end: int = 10,
    is_list_item: bool = False,
) -> DocsParagraphNode:
    return DocsParagraphNode(
        style=style,
        text=text,
        start_index=start,
        end_index=end,
        is_list_item=is_list_item,
        is_native_checkbox=is_native_checkbox,
    )


def test_diff_summary_reports_unchanged_count_and_skips_equal_rows() -> None:
    current = [
        _para("Housing: Bekah has the lake house", start=1, end=10),
        _para("Old text", start=10, end=20),
    ]
    target = [
        _para("Housing: Bekah has the lake house", start=1, end=10),
        _para("New text", start=10, end=20),
    ]
    entries, unchanged_count = builder.diff_summary(current, target)
    assert unchanged_count == 1
    assert len(entries) == 1
    assert entries[0].kind == "change"


def test_diff_summary_classifies_checklist_toggle_as_change() -> None:
    current = [_para("[ ] Splitwise", is_list_item=True)]
    target = [_para("[x] Splitwise", is_list_item=True)]
    entries, unchanged_count = builder.diff_summary(current, target)
    assert unchanged_count == 0
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "change"
    assert entry.current_text == "[ ] Splitwise"
    assert entry.target_text == "[x] Splitwise"
    assert entry.style == "NORMAL_TEXT"
    assert entry.current_is_native_checkbox is False


def test_diff_summary_classifies_new_paragraph_as_add() -> None:
    current: list = []
    target = [_para("Brand new paragraph")]
    entries, unchanged_count = builder.diff_summary(current, target)
    assert unchanged_count == 0
    assert len(entries) == 1
    assert entries[0].kind == "add"
    assert entries[0].current_text is None
    assert entries[0].target_text == "Brand new paragraph"
    assert entries[0].current_is_native_checkbox is False


def test_diff_summary_classifies_removed_paragraph_as_remove() -> None:
    current = [_para("Gone now")]
    target: list = []
    entries, unchanged_count = builder.diff_summary(current, target)
    assert unchanged_count == 0
    assert len(entries) == 1
    assert entries[0].kind == "remove"
    assert entries[0].current_text == "Gone now"
    assert entries[0].target_text is None


def test_diff_summary_copies_current_is_native_checkbox_from_current_side_only() -> None:
    """current_is_native_checkbox is copied from the current-side node only —
    an "add" entry (no current node) always stays False, and the target
    side's own is_native_checkbox (if any) is never consulted."""
    current = [_para_ncb("[ ] Whatsapp group", is_native_checkbox=True, is_list_item=True)]
    target = [_para_ncb("[x] Whatsapp group", is_native_checkbox=False, is_list_item=True)]
    entries, unchanged_count = builder.diff_summary(current, target)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "change"
    assert entry.current_text == "[ ] Whatsapp group"
    assert entry.target_text == "[x] Whatsapp group"
    assert entry.current_is_native_checkbox is True


def test_diff_summary_handles_empty_current_and_target_without_raising() -> None:
    entries, unchanged_count = builder.diff_summary([], [])
    assert entries == []
    assert unchanged_count == 0


def test_replace_with_unequal_current_and_target_length_does_not_raise() -> None:
    """A 'replace' opcode where current/target paragraph-range lengths differ
    (e.g. one checklist line split into two) is handled as extra add/remove
    entries, not an IndexError/zip truncation bug."""
    current = [_para("Only one paragraph here", start=1, end=25)]
    target = [
        _para("Split into", start=1, end=11),
        _para("two paragraphs", start=11, end=25),
    ]
    entries, unchanged_count = builder.diff_summary(current, target)
    kinds = sorted(e.kind for e in entries)
    assert kinds == ["add", "change"]
    assert unchanged_count == 0
