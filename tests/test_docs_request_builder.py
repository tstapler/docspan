"""Unit tests for DocsRequestBuilder — structural diff algorithm, no network."""

from dataclasses import replace

import pytest

from docspan.backends.google_docs import docs_request_builder as docs_request_builder_module
from docspan.backends.google_docs.docs_request_builder import (
    DiffTooExpensive,
    DocsRequestBuilder,
)
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
)

DOC_END = 100


def _para(
    text: str,
    style: str = "NORMAL_TEXT",
    start: int = 1,
    end: int = 10,
    is_list_item: bool = False,
    precedes_structural_element: bool = False,
) -> DocsParagraphNode:
    return DocsParagraphNode(
        style=style,
        text=text,
        start_index=start,
        end_index=end,
        is_list_item=is_list_item,
        precedes_structural_element=precedes_structural_element,
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

def test_delete_stops_short_of_newline_anchoring_a_following_structural_element() -> None:
    """Regression: a paragraph directly followed by a Table, TableOfContents or
    SectionBreak owns the newline that anchors that element, and the Docs API
    rejects deleting it "without deleting the element" — batchUpdate fails
    atomically with `Invalid requests[N].deleteContentRange: Invalid deletion
    range. Cannot delete the requested range.`, so the whole push applies
    nothing. SectionBreak/TableOfContents are not parsed into nodes at all, so
    they can never be co-deleted; the delete must stop one index short."""
    # "Keep\n" is [1, 6); "Doomed\n" is [6, 13) and is followed by a section
    # break occupying [13, 14) — so index 12 is the anchoring newline.
    current = [
        _para("Keep", start=1, end=6),
        _para("Doomed", start=6, end=13, precedes_structural_element=True),
    ]
    target = [_para("Keep", start=1, end=6)]
    requests = builder.build(current, target, doc_end_index=40)

    delete_requests = [r for r in requests if "deleteContentRange" in r]
    assert len(delete_requests) == 1
    assert delete_requests[0]["deleteContentRange"]["range"] == {
        "startIndex": 6,
        "endIndex": 12,
    }


def test_replace_before_structural_element_keeps_the_anchoring_newline() -> None:
    """The same protection applies on a "replace" opcode, which is how an
    edited (rather than removed) paragraph reaches _make_delete_requests."""
    current = [_para("Old text", start=6, end=15, precedes_structural_element=True)]
    target = [_para("New text", start=0, end=0)]
    requests = builder.build(current, target, doc_end_index=40)

    delete_requests = [r for r in requests if "deleteContentRange" in r]
    assert len(delete_requests) == 1
    assert delete_requests[0]["deleteContentRange"]["range"]["endIndex"] == 14


def test_delete_trims_the_anchoring_newline_even_when_the_table_is_deleted_too() -> None:
    """Trimming stays unconditional when the following Table is deleted in the
    same batch. Requests are applied highest-index-first, so the table is
    already gone by the time the paragraph's delete runs and whatever followed
    the table has moved up against that newline — which may be another
    boundary. The table itself is still deleted whole."""
    table = DocsTableNode(rows=[["a", "b"]], start_index=13, end_index=25)
    current = [
        _para("Keep", start=1, end=6),
        _para("Doomed", start=6, end=13, precedes_structural_element=True),
        table,
    ]
    target = [_para("Keep", start=1, end=6)]
    requests = builder.build(current, target, doc_end_index=40)

    ranges = [r["deleteContentRange"]["range"] for r in requests if "deleteContentRange" in r]
    assert {"startIndex": 6, "endIndex": 12} in ranges
    assert {"startIndex": 13, "endIndex": 25} in ranges


def test_delete_does_not_exceed_doc_end() -> None:
    doc_end = 10
    current = [_para("Delete", start=1, end=10)]
    target: list = []
    requests = builder.build(current, target, doc_end)
    for r in requests:
        if "deleteContentRange" in r:
            end_idx = r["deleteContentRange"]["range"]["endIndex"]
            assert end_idx <= doc_end, f"Delete range {end_idx} exceeds doc_end {doc_end}"


def test_delete_bounds_does_not_compound_render_prefix_and_structural_trim() -> None:
    """Regression (#55): a render-glyph paragraph (#47) that is also the last
    paragraph before a Table/ToC/SectionBreak used to trim twice — once for
    each independent rule — eating the author's last character along with the
    newline. `# cfg` at [8, 14) is 5 units of text starting at 8, so only
    [8, 13) may be deleted; the old code produced [8, 12)."""
    node = _para("# cfg", start=8, end=14, precedes_structural_element=True)
    node = replace(node, render_prefix="")

    start, end, trimmed = DocsRequestBuilder._delete_bounds(node, doc_end_index=100)

    assert (start, end, trimmed) == (8, 13, True)


def test_replace_of_a_render_prefix_paragraph_does_not_add_a_second_newline() -> None:
    """Regression (#56): _make_delete_requests already spares a render-glyph
    paragraph's own newline (#47/#55), but build()'s "replace" branch used to
    write `target_text + "\\n"` regardless, adding a second newline on top of
    the one just protected — splitting the paragraph and leaving a stray empty
    one behind on every edit to that line."""
    current = [replace(_para("# cfg", start=8, end=14), render_prefix="")]
    target = [_para("# other", start=0, end=0)]
    requests = builder.build(current, target, doc_end_index=100)

    insert_requests = [r for r in requests if "insertText" in r]
    assert len(insert_requests) == 1
    assert insert_requests[0]["insertText"]["text"] == "\n# other"


def test_replace_of_a_multi_node_range_uses_the_last_node_for_spared_newline() -> None:
    """Regression (#56 follow-up, found in review of PR #48): when a replace
    spans multiple nodes, the newline that survives at delete_start once all
    the deletes run is spared by whichever node borders what comes after the
    range — the LAST deleted node, not the first. build() used to read the
    trim flag off current[i1] (the first node), so a trim flag set only on a
    later node in the range was ignored and a spurious second newline was
    inserted."""
    current = [
        _para("AAAA", start=1, end=6),
        _para("BB", start=6, end=9, precedes_structural_element=True),
    ]
    target = [_para("ZZZZZZ", start=0, end=0)]
    requests = builder.build(current, target, doc_end_index=100)

    insert_requests = [r for r in requests if "insertText" in r]
    assert len(insert_requests) == 1
    assert insert_requests[0]["insertText"]["text"] == "\nZZZZZZ"


def test_replace_of_a_multi_node_range_does_not_spare_newline_when_only_first_node_trims() -> None:
    """Mirror of the above: when the trim flag is on the FIRST node but not
    the last, the first node no longer borders what comes after the deleted
    range, so its newline must NOT be spared."""
    current = [
        _para("AAAA", start=1, end=6, precedes_structural_element=True),
        _para("BB", start=6, end=9),
    ]
    target = [_para("ZZZZZZ", start=0, end=0)]
    requests = builder.build(current, target, doc_end_index=100)

    insert_requests = [r for r in requests if "insertText" in r]
    assert len(insert_requests) == 1
    assert insert_requests[0]["insertText"]["text"] == "ZZZZZZ\n"


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



# ─────────────────────────────────────────────────────────────────────────────
# tab_id stamping (build()/_inject_tab_id) — multi-tab doc support
# ─────────────────────────────────────────────────────────────────────────────

def test_build_without_tab_id_omits_tab_id_from_requests() -> None:
    """Backward-compatible no-tab_id case: legacy (non-tabbed) docs must
    produce requests identical to pre-tabs-support behavior — no tabId key
    anywhere, since Location/Range's tabId is only meaningful on tabbed docs."""
    current: list = []
    target = [_para("New paragraph")]
    requests = builder.build(current, target, DOC_END, tab_id=None)
    assert requests
    for request in requests:
        for inner in request.values():
            assert "tabId" not in inner.get("location", {})
            assert "tabId" not in inner.get("range", {})


def test_build_with_tab_id_stamps_tab_id_onto_every_location_and_range() -> None:
    current: list = [_para("Existing", start=1, end=9)]
    target = [
        _para("Existing", start=1, end=9),
        _para("Appended", start=9, end=18),
    ]
    requests = builder.build(current, target, DOC_END, tab_id="t.second")
    assert requests
    saw_location_or_range = False
    for request in requests:
        for inner in request.values():
            if "location" in inner:
                assert inner["location"]["tabId"] == "t.second"
                saw_location_or_range = True
            if "range" in inner:
                assert inner["range"]["tabId"] == "t.second"
                saw_location_or_range = True
    assert saw_location_or_range


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


# ─────────────────────────────────────────────────────────────────────────────
# DiffTooExpensive guard — duplicate-heavy documents (backlog: bound difflib's
# SequenceMatcher(autojunk=False) blowup)
#
# These tests override the module's thresholds to small values so they stay
# fast and deterministic (count/complexity-based, not wall-clock timing) while
# still exercising the exact branch that trips on real few-thousand-line
# documents.
# ─────────────────────────────────────────────────────────────────────────────

def _small_thresholds(monkeypatch: pytest.MonkeyPatch, *, max_duplicate_run: int = 5, min_size: int = 10) -> None:
    monkeypatch.setattr(docs_request_builder_module, "_MAX_DUPLICATE_RUN", max_duplicate_run)
    monkeypatch.setattr(docs_request_builder_module, "_MIN_SIZE_FOR_DUPLICATE_CHECK", min_size)


def _duplicate_paragraphs(text: str, count: int, *, start: int = 1) -> list:
    nodes = []
    cursor = start
    for _ in range(count):
        end = cursor + len(text) + 1
        nodes.append(_para(text, start=cursor, end=end))
        cursor = end
    return nodes


def test_build_raises_diff_too_expensive_above_duplicate_run_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC0: a document whose duplicate-line run exceeds the threshold raises a
    clear error from build() instead of constructing the expensive matcher."""
    _small_thresholds(monkeypatch)
    current = _duplicate_paragraphs("dup", 6)
    target = _duplicate_paragraphs("dup", 6)
    with pytest.raises(DiffTooExpensive) as excinfo:
        builder.build(current, target, DOC_END)
    assert "too expensive" in str(excinfo.value)


def test_build_does_not_raise_below_duplicate_run_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5 (lower half of the boundary): a duplicate run short of the
    threshold must diff normally, not raise. `_bounded_opcodes` counts
    duplicates across `a_keys + b_keys` combined, so with equal current/target
    the combined run is 2x each side's paragraph count — use a small
    `min_size` so the duplicate-run branch is actually exercised rather than
    short-circuited by the size floor."""
    _small_thresholds(monkeypatch, max_duplicate_run=10, min_size=2)
    current = _duplicate_paragraphs("dup", 4)
    target = _duplicate_paragraphs("dup", 4)
    assert builder.build(current, target, DOC_END) == []


def test_threshold_boundary_n_minus_one_vs_n_only_changes_latency_not_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: crossing the boundary must only ever add a raise — the diff
    produced just below threshold must be the same *kind* of result (a plain,
    successful diff) whether the run is far below or exactly at threshold,
    with no discontinuity in what gets reported. `_bounded_opcodes` counts the
    duplicate run across `a_keys + b_keys` combined, so passing the same list
    as both current and target means N identical paragraphs on each side
    produce a combined run of 2N — the guard's condition is
    `max_duplicate_run > _MAX_DUPLICATE_RUN`, so N = floor(_MAX_DUPLICATE_RUN / 2)
    sits at the boundary and N+1 crosses it. `min_size` is set to 2 (well
    below every N used here) so the duplicate-run branch is always live,
    isolating the assertions to the boundary itself."""
    _small_thresholds(monkeypatch, max_duplicate_run=10, min_size=2)
    max_duplicate_run = docs_request_builder_module._MAX_DUPLICATE_RUN
    n_at_boundary = max_duplicate_run // 2

    far_below = _duplicate_paragraphs("dup", 1)
    entries_far_below, unchanged_far_below = builder.diff_summary(far_below, far_below)
    assert entries_far_below == []
    assert unchanged_far_below == len(far_below)

    at_boundary = _duplicate_paragraphs("dup", n_at_boundary)
    entries_at, unchanged_at = builder.diff_summary(at_boundary, at_boundary)
    assert entries_at == []
    assert unchanged_at == len(at_boundary)

    over = _duplicate_paragraphs("dup", n_at_boundary + 1)
    with pytest.raises(DiffTooExpensive):
        builder.diff_summary(over, over)


def test_ordinary_table_and_code_block_never_trip_guard() -> None:
    """AC1: realistic documents (a 30-row table, a normal-sized fenced code
    block) never trip the guard, using the real production thresholds."""
    table_current = DocsTableNode(
        rows=[[TableCell(text=f"Row {i} value", spans=[]) for _ in range(3)] for i in range(30)],
        start_index=1,
        end_index=500,
    )
    table_target = DocsTableNode(
        rows=[[TableCell(text=f"Row {i} value!", spans=[]) for _ in range(3)] for i in range(30)],
        start_index=1,
        end_index=500,
    )
    code_current = [
        _para(f"line {i} of code", start=500 + i * 20, end=500 + i * 20 + 18)
        for i in range(40)
    ]
    code_target = [
        _para(f"line {i} of code!", start=500 + i * 20, end=500 + i * 20 + 19)
        for i in range(40)
    ]

    # No DiffTooExpensive raised at production thresholds.
    builder.build([table_current] + code_current, [table_target] + code_target, DOC_END + 2000)
    builder.diff_summary([table_current] + code_current, [table_target] + code_target)


def test_build_and_diff_summary_raise_identically_on_pathological_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: build() and diff_summary() both route through the same _opcodes(),
    so they must never diverge on whether a document is pathological."""
    _small_thresholds(monkeypatch)
    current = _duplicate_paragraphs("dup", 6)
    target = _duplicate_paragraphs("dup", 6)

    with pytest.raises(DiffTooExpensive):
        builder.build(current, target, DOC_END)
    with pytest.raises(DiffTooExpensive):
        builder.diff_summary(current, target)


def test_guard_never_reenables_autojunk_or_a_popularity_heuristic() -> None:
    """AC3: below threshold, `_bounded_opcodes` must produce byte-identical
    opcodes to a direct `SequenceMatcher(None, ..., autojunk=False)` call —
    proof no popularity heuristic or autojunk=True fallback was substituted."""
    import difflib

    a_keys = [("a",), ("b",), ("a",), ("c",), ("a",)]
    b_keys = [("a",), ("a",), ("c",), ("b",), ("a",)]

    expected = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False).get_opcodes()
    actual = docs_request_builder_module._bounded_opcodes(a_keys, b_keys, context="test")
    assert actual == expected


def test_repairs_inner_matcher_shares_the_same_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: `_repair`'s inner per-replace-run matcher (keyed on `_content_key`,
    called from inside `_repair` rather than through `_opcodes`) trips the
    same guard as the outer matcher. Calling `_repair` directly with a single
    synthetic `replace` opcode isolates the inner `_bounded_opcodes` call from
    the outer one, so this proves the inner call site itself is guarded, not
    just that the outer call raised first."""
    _small_thresholds(monkeypatch)
    dup_current = _duplicate_paragraphs("dup", 6)
    dup_target = _duplicate_paragraphs("dup!", 6)
    opcodes = [("replace", 0, len(dup_current), 0, len(dup_target))]

    with pytest.raises(DiffTooExpensive):
        builder._repair(opcodes, dup_current, dup_target)


def test_align_for_styling_shares_the_same_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: `_align_for_styling`'s pass-2 matcher trips the same guard as the
    other two call sites."""
    _small_thresholds(monkeypatch)
    dup_text = "dup line\n"
    content = []
    index = 1
    for _ in range(6):
        content.append({
            "startIndex": index,
            "endIndex": index + len(dup_text),
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": dup_text, "textStyle": {}}}],
            },
        })
        index += len(dup_text)
    doc = {"revisionId": "rev-1", "body": {"content": content}}

    target = _duplicate_paragraphs("dup line!", 6)

    with pytest.raises(DiffTooExpensive):
        builder.align(doc, target)


# AC6 (push()/pull() surface DiffTooExpensive as PushResult(status="error"),
# never an uncaught traceback) is covered end-to-end in
# tests/test_google_docs_backend.py::TestDiffTooExpensiveSurfacesAsUserFacingError,
# which has the make_backend/fake_client fixtures needed to drive push()
# through its full call path.


def test_guard_overhead_is_sub_quadratic_in_input_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC7: the guard's own pre-check (Counter + a multiply) must be cheap
    enough that both a document at the threshold and one four times larger
    raise immediately — proof this is a count-based guard, not something that
    itself scales like the algorithm it is protecting against."""
    _small_thresholds(monkeypatch)
    max_duplicate_run = docs_request_builder_module._MAX_DUPLICATE_RUN

    small = _duplicate_paragraphs("dup", max_duplicate_run + 1)
    large = _duplicate_paragraphs("dup", (max_duplicate_run + 1) * 4)

    for doc in (small, large):
        with pytest.raises(DiffTooExpensive) as excinfo:
            builder.build(doc, doc, DOC_END + 10000)
        assert excinfo.value.size == len(doc) * 2
