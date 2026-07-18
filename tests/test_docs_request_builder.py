"""Unit tests for DocsRequestBuilder — structural diff algorithm, no network."""

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

DOC_END = 100


def _para(text: str, style: str = "NORMAL_TEXT", start: int = 1, end: int = 10) -> DocsParagraphNode:
    return DocsParagraphNode(style=style, text=text, start_index=start, end_index=end)


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
