"""Unit tests for Google Docs table push + inline style/link push (no network)."""

from typing import List

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
    TableCell,
    TextSpan,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

parser = MarkdownToParagraphParser()
builder = DocsRequestBuilder()
structure = DocsStructureParser()

TABLE_MD = """| A | B |
| --- | --- |
| 1 | 2 |
| 3 | 4 |
"""


def _text_style(req: dict) -> dict:
    return req["updateTextStyle"]["textStyle"]


def _one_para_doc(text: str, start: int = 1) -> dict:
    """A minimal doc containing a single paragraph (as it looks after a pass-1 insert)."""
    return {"body": {"content": [{
        "startIndex": start, "endIndex": start + len(text) + 1,
        "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
    }]}}


# ─────────────────────────────────────────────────────────────────────────────
# Inline styles / links on insert
# ─────────────────────────────────────────────────────────────────────────────

def test_markdown_link_produces_link_span() -> None:
    nodes = parser.parse("See [the doc](https://example.com/x) now.")
    para = nodes[0]
    assert isinstance(para, DocsParagraphNode)
    link_spans = [s for s in para.spans if s.link]
    assert link_spans and link_spans[0].link == "https://example.com/x"
    assert link_spans[0].text == "the doc"


def test_pass1_defers_text_styling() -> None:
    # Pass 1 (build) inserts plain text only; styling is applied in pass 2.
    nodes = parser.parse("See [the doc](https://example.com/x) now.")
    reqs = builder.build([], nodes, 100)
    assert not any("updateTextStyle" in r for r in reqs)


def test_pass2_emits_link_text_style() -> None:
    target = parser.parse("See [the doc](https://example.com/x) now.")
    doc = _one_para_doc(target[0].text)
    reqs = builder.build_span_style_requests(doc, target)
    link_reqs = [r for r in reqs if "updateTextStyle" in r and "link" in _text_style(r)]
    assert link_reqs
    assert _text_style(link_reqs[0])["link"]["url"] == "https://example.com/x"


def test_bold_italic_code_spans_emitted() -> None:
    nodes = parser.parse("A **bold** and *italic* and `code` word.")
    spans = nodes[0].spans
    assert any(s.bold for s in spans)
    assert any(s.italic for s in spans)
    assert any(s.monospace for s in spans)


def test_plain_paragraph_has_no_span_style_requests() -> None:
    target = parser.parse("Just plain text.")
    doc = _one_para_doc(target[0].text)
    assert builder.build_span_style_requests(doc, target) == []


def test_link_style_range_matches_span_offset() -> None:
    # "pre " = 4 UTF-16 units; link text "L" spans indices 5..6 for a paragraph at index 1.
    target = parser.parse("pre [L](https://e.co) post")
    doc = _one_para_doc(target[0].text)
    reqs = builder.build_span_style_requests(doc, target)
    link_reqs = [r for r in reqs if "updateTextStyle" in r and "link" in _text_style(r)]
    assert link_reqs
    rng = link_reqs[0]["updateTextStyle"]["range"]
    assert rng["startIndex"] == 1 + len("pre ")
    assert rng["endIndex"] == rng["startIndex"] + len("L")


def _multi_para_doc(texts: List[str], start: int = 1) -> dict:
    """A doc with several plain paragraphs in a row, as it looks after a pass-1 insert."""
    content = []
    idx = start
    for text in texts:
        end = idx + len(text) + 1
        content.append({
            "startIndex": idx, "endIndex": end,
            "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
        })
        idx = end
    return {"body": {"content": content}}


def test_duplicate_text_paragraphs_do_not_misalign_styling() -> None:
    # Regression: a prior text-equality aligner scanned forward for the FIRST
    # current paragraph with matching text, so when two unstyled paragraphs share
    # the same text, the styled paragraph after them got matched to the wrong
    # (earlier) index, permanently shifting every later paragraph's styling one
    # slot off. The alignment is order-preserving and global (difflib over the
    # whole node sequence, DocsRequestBuilder._align_for_styling), so duplicates
    # keep their relative order and cannot absorb a later paragraph's match.
    target = parser.parse("dup\n\ndup\n\n**bold** line")
    doc = _multi_para_doc(["dup", "dup", "bold line"])
    reqs = builder.build_span_style_requests(doc, target)
    bold_reqs = [r for r in reqs if "updateTextStyle" in r and _text_style(r).get("bold")]
    assert len(bold_reqs) == 1
    rng = bold_reqs[0]["updateTextStyle"]["range"]
    # The third paragraph starts right after "dup\n" + "dup\n" (each 4 UTF-16 units).
    third_para_start = 1 + len("dup\n") + len("dup\n")
    assert rng["startIndex"] == third_para_start
    assert rng["endIndex"] == third_para_start + len("bold")


def test_mismatched_text_does_not_desync_later_paragraphs() -> None:
    # Regression: if a current paragraph's text doesn't byte-for-byte match its
    # target counterpart (e.g. a stray whitespace difference from upstream
    # parsing), the old aligner skipped forward searching for a match, which
    # desynced every subsequent paragraph's styling. difflib reports the
    # mismatched pair as a "replace" and the rest as "equal", so the mismatch is
    # contained to its own slot instead of shifting everything after it.
    target = parser.parse("mismatch\n\n**bold** line")
    doc = _multi_para_doc(["totally different text", "bold line"])
    reqs = builder.build_span_style_requests(doc, target)
    bold_reqs = [r for r in reqs if "updateTextStyle" in r and _text_style(r).get("bold")]
    assert len(bold_reqs) == 1
    rng = bold_reqs[0]["updateTextStyle"]["range"]
    second_para_start = 1 + len("totally different text\n")
    assert rng["startIndex"] == second_para_start


# ─────────────────────────────────────────────────────────────────────────────
# Markdown table -> node
# ─────────────────────────────────────────────────────────────────────────────

def test_markdown_table_parses_to_table_node() -> None:
    nodes = parser.parse(TABLE_MD)
    tables = [n for n in nodes if isinstance(n, DocsTableNode)]
    assert len(tables) == 1
    t = tables[0]
    assert [c.text for c in t.rows[0]] == ["A", "B"]
    assert [c.text for c in t.rows[1]] == ["1", "2"]
    assert [c.text for c in t.rows[2]] == ["3", "4"]
    assert t.num_rows == 3 and t.num_cols == 2


# ─────────────────────────────────────────────────────────────────────────────
# Table diffing (insert / equal / delete)
# ─────────────────────────────────────────────────────────────────────────────

def _populated_table_doc() -> dict:
    def cell(idx: int, text: str) -> dict:
        return {"content": [{
            "startIndex": idx, "endIndex": idx + len(text) + 1,
            "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
        }]}

    return {"body": {"content": [
        {"startIndex": 1, "endIndex": 60, "table": {"rows": 3, "columns": 2, "tableRows": [
            {"tableCells": [cell(4, "A"), cell(8, "B")]},
            {"tableCells": [cell(12, "1"), cell(16, "2")]},
            {"tableCells": [cell(20, "3"), cell(24, "4")]},
        ]}},
        {"startIndex": 60, "endIndex": 61, "paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
    ]}}


def test_live_table_parses_to_table_node() -> None:
    nodes = structure.parse(_populated_table_doc())
    tables = [n for n in nodes if isinstance(n, DocsTableNode)]
    assert len(tables) == 1
    assert [[c.text for c in row] for row in tables[0].rows] == [
        ["A", "B"], ["1", "2"], ["3", "4"],
    ]


def test_table_insert_emits_insert_table() -> None:
    nodes = parser.parse(TABLE_MD)
    reqs = builder.build([], nodes, 100)
    it = [r for r in reqs if "insertTable" in r]
    assert len(it) == 1
    assert it[0]["insertTable"]["rows"] == 3
    assert it[0]["insertTable"]["columns"] == 2


def test_unchanged_table_is_idempotent() -> None:
    current = structure.parse(_populated_table_doc())
    target = parser.parse(TABLE_MD)
    reqs = builder.build(current, target, 61)
    assert reqs == []


def _multi_paragraph_table_doc() -> dict:
    """A live table whose first cell holds two paragraphs (`content` elements),
    as issue #61's push -> pull -> push idempotency case."""

    def two_paragraph_cell(idx1: int, t1: str, idx2: int, t2: str) -> dict:
        return {"content": [
            {"startIndex": idx1, "endIndex": idx1 + len(t1) + 1,
             "paragraph": {"elements": [{"textRun": {"content": t1 + "\n"}}]}},
            {"startIndex": idx2, "endIndex": idx2 + len(t2) + 1,
             "paragraph": {"elements": [{"textRun": {"content": t2 + "\n"}}]}},
        ]}

    def one_paragraph_cell(idx: int, text: str) -> dict:
        return {"content": [{
            "startIndex": idx, "endIndex": idx + len(text) + 1,
            "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
        }]}

    return {"body": {"content": [
        {"startIndex": 1, "endIndex": 40, "table": {"rows": 1, "columns": 2, "tableRows": [
            {"tableCells": [
                two_paragraph_cell(4, "line one", 13, "line two"),
                one_paragraph_cell(23, "x"),
            ]},
        ]}},
        {"startIndex": 40, "endIndex": 41, "paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
    ]}}


def test_unchanged_multi_paragraph_table_is_idempotent() -> None:
    """AC2: an unmodified push -> pull -> push of a multi-paragraph-cell table must
    not generate a delete+insert (which would orphan any comment anchored in it).

    The rendered HTML round trip must reparse into a target whose diff key matches
    the live document's exactly — no requests at all.
    """
    current = structure.parse(_multi_paragraph_table_doc())
    rendered = render_nodes_to_markdown([n for n in current if isinstance(n, DocsTableNode)])
    target = parser.parse(rendered)
    reqs = builder.build(current, target, 41)
    assert reqs == []


def test_unmodified_multi_paragraph_table_has_no_high_risk_diff_entry() -> None:
    """A comment anchored inside a multi-paragraph cell must survive an unmodified
    push: `diff_summary` must report the table as `equal`, never `remove`/`change`,
    so `push_preview.find_high_risk_paragraphs` never even sees a delete candidate
    to weigh the comment's quoted text against.
    """
    current = structure.parse(_multi_paragraph_table_doc())
    rendered = render_nodes_to_markdown([n for n in current if isinstance(n, DocsTableNode)])
    target = parser.parse(rendered)
    entries, unchanged_count = builder.diff_summary(current, target)
    # The table itself must be `equal` (folded into unchanged_count); the one
    # remaining entry is the doc's own trailing blank paragraph, unrelated to
    # this fix — see test_unchanged_multi_paragraph_table_is_idempotent for the
    # request-level (build()) confirmation that no requests are ever emitted for it.
    assert not any("line one" in (e.current_text or "") for e in entries)
    assert unchanged_count >= 1

    # And the comment's anchor text — which spans the cell's paragraph break —
    # is still findable in the raw node text `_node_text`/DiffEntry machinery
    # would have compared against, unbroken by the HTML round trip.
    table_node = next(n for n in current if isinstance(n, DocsTableNode))
    assert "line one\nline two" in "\n".join(
        " | ".join(cell.text for cell in row) for row in table_node.rows
    )


def test_removed_table_emits_delete() -> None:
    current = structure.parse(_populated_table_doc())
    reqs = builder.build(current, [], 61)
    assert any("deleteContentRange" in r for r in reqs)


# ─────────────────────────────────────────────────────────────────────────────
# Pass 2 — cell fill
# ─────────────────────────────────────────────────────────────────────────────

def _empty_table_doc() -> dict:
    def empty_cell(idx: int) -> dict:
        return {"content": [{
            "startIndex": idx, "endIndex": idx + 1,
            "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
        }]}

    return {"body": {"content": [
        {"startIndex": 1, "endIndex": 30, "table": {"rows": 2, "columns": 2, "tableRows": [
            {"tableCells": [empty_cell(5), empty_cell(8)]},
            {"tableCells": [empty_cell(12), empty_cell(15)]},
        ]}},
    ]}}


def test_build_table_fill_requests_targets_cell_indices() -> None:
    target = [DocsTableNode(rows=[["A", "B"], ["1", "2"]])]
    reqs = builder.build_table_fill_requests(_empty_table_doc(), target)
    inserts = [r for r in reqs if "insertText" in r]
    pairs = [(r["insertText"]["location"]["index"], r["insertText"]["text"]) for r in inserts]
    # Sorted descending by index so earlier inserts don't shift later ones.
    assert pairs == [(15, "2"), (12, "1"), (8, "B"), (5, "A")]
    # Every filled cell also gets its paragraph style forced back to NORMAL_TEXT —
    # insertTable lets a new cell inherit the namedStyleType of an adjacent
    # heading, which otherwise renders table body text at heading size.
    style_resets = [r["updateParagraphStyle"] for r in reqs if "updateParagraphStyle" in r]
    assert all(r["paragraphStyle"] == {"namedStyleType": "NORMAL_TEXT"} for r in style_resets)
    assert {r["range"]["startIndex"] for r in style_resets} == {5, 8, 12, 15}


def test_fill_skips_when_no_target_tables() -> None:
    assert builder.build_table_fill_requests(_empty_table_doc(), []) == []


def test_fill_skips_populated_tables() -> None:
    target = [DocsTableNode(rows=[["A", "B"], ["1", "2"], ["3", "4"]])]
    # Already-populated table should not be re-filled.
    assert builder.build_table_fill_requests(_populated_table_doc(), target) == []


def _populated_cell(idx: int, text: str) -> dict:
    return {"content": [{
        "startIndex": idx, "endIndex": idx + len(text) + 1,
        "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]},
    }]}


def _empty_cell(idx: int) -> dict:
    return {"content": [{
        "startIndex": idx, "endIndex": idx + 1,
        "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
    }]}


def test_fill_pairs_by_document_order_not_by_emptiness_count() -> None:
    """Issue #59: pairing must not advance its target index only on empty tables.

    Live tables ``[populated "KEEP", empty]`` against 2 target tables — the old
    code advanced `ti` only inside the emptiness branch, so the lone empty
    (2nd) live table was paired with target *0* instead of target *1*. The
    correct pairing is by document-order position, unconditional on emptiness.
    """
    doc = {"body": {"content": [
        {"startIndex": 1, "endIndex": 10, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(4, "KEEP")]},
        ]}},
        {"startIndex": 10, "endIndex": 15, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_empty_cell(13)]},
        ]}},
    ]}}
    target = [DocsTableNode(rows=[["T0"]]), DocsTableNode(rows=[["T1"]])]
    reqs = builder.build_table_fill_requests(doc, target)
    assert reqs == [
        {"insertText": {"location": {"index": 13}, "text": "T1"}},
        {
            "updateParagraphStyle": {
                "range": {"startIndex": 13, "endIndex": 14},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        },
    ]


def test_fill_pairs_by_document_order_symmetric() -> None:
    """Same bug, opposite direction: ``[populated, empty, populated]`` against
    3 targets ``[T0, T1, T2]``.

    The old code advanced its target index only when it hit an *empty* live
    table, silently skipping populated ones without consuming a slot — so the
    lone empty table (2nd in document order) was paired with target 0 instead
    of target 1. Document-order pairing must give it target 1 regardless of
    the emptiness of the tables around it.
    """
    doc = {"body": {"content": [
        {"startIndex": 1, "endIndex": 10, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(4, "KEEP0")]},
        ]}},
        {"startIndex": 10, "endIndex": 15, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_empty_cell(13)]},
        ]}},
        {"startIndex": 15, "endIndex": 25, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(18, "KEEP2")]},
        ]}},
    ]}}
    target = [
        DocsTableNode(rows=[["T0"]]),
        DocsTableNode(rows=[["T1"]]),
        DocsTableNode(rows=[["T2"]]),
    ]
    reqs = builder.build_table_fill_requests(doc, target)
    assert reqs == [
        {"insertText": {"location": {"index": 13}, "text": "T1"}},
        {
            "updateParagraphStyle": {
                "range": {"startIndex": 13, "endIndex": 14},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        },
    ]


def test_style_pairs_tables_by_document_order() -> None:
    """The styling side shares `table_pairs` with the fill fix above, computed
    via content alignment rather than raw table position.

    The live document has an *extra* leading table (e.g. pre-existing content
    outside the pushed markdown) before an "Intro" paragraph and the two
    tables that actually correspond to `target`. Raw-position pairing (the
    pre-fix behavior: zip the first N live tables with the N target tables in
    order) grabs the extra table as if it were target 0, misaligning every
    later table by one and losing all styling. Content-aligned `table_pairs`
    matches "Intro" first via difflib, then correctly pairs the two tables
    that follow it with their two targets, leaving the unrelated leading
    table out of the pairing entirely.
    """
    doc = {"body": {"content": [
        {"startIndex": 1, "endIndex": 10, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(4, "ZZZ")]},
        ]}},
        {"startIndex": 10, "endIndex": 16, "paragraph": {
            "elements": [{"textRun": {"content": "Intro\n"}}],
        }},
        {"startIndex": 16, "endIndex": 25, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(19, "AAA")]},
        ]}},
        {"startIndex": 25, "endIndex": 34, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(28, "BBB")]},
        ]}},
    ]}}
    target = [
        DocsParagraphNode(style="NORMAL_TEXT", text="Intro"),
        DocsTableNode(rows=[[TableCell(text="AAA", spans=[TextSpan(text="AAA", bold=True)])]]),
        DocsTableNode(rows=[[TableCell(text="BBB", spans=[TextSpan(text="BBB", italic=True)])]]),
    ]
    reqs = builder.build_table_cell_span_requests(doc, target)
    by_index = {r["updateTextStyle"]["range"]["startIndex"]: _text_style(r) for r in reqs}
    assert by_index[19].get("bold") is True
    assert by_index[28].get("italic") is True


def test_unplaced_table_cells_uses_content_aligned_pairing() -> None:
    """`unplaced_table_cells` must use the same `table_pairs` alignment as the
    fill/style passes above, not the old raw-position pairing.

    Same fixture as `test_style_pairs_tables_by_document_order`: an unrelated
    leading table before the "Intro" paragraph. Raw-position pairing (the
    pre-fix behavior, reintroduced if this function pairs tables itself
    instead of sharing `table_pairs`) matches that leading table against
    target 0 and shifts every later table by one, so neither "AAA" nor "BBB"
    is ever found in its paired live cell and both get reported missed.
    Content-aligned pairing correctly matches AAA<->AAA and BBB<->BBB, so
    nothing is missed.
    """
    doc = {"body": {"content": [
        {"startIndex": 1, "endIndex": 10, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(4, "ZZZ")]},
        ]}},
        {"startIndex": 10, "endIndex": 16, "paragraph": {
            "elements": [{"textRun": {"content": "Intro\n"}}],
        }},
        {"startIndex": 16, "endIndex": 25, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(19, "AAA")]},
        ]}},
        {"startIndex": 25, "endIndex": 34, "table": {"rows": 1, "columns": 1, "tableRows": [
            {"tableCells": [_populated_cell(28, "BBB")]},
        ]}},
    ]}}
    target = [
        DocsParagraphNode(style="NORMAL_TEXT", text="Intro"),
        DocsTableNode(rows=[[TableCell(text="AAA", spans=[TextSpan(text="AAA", bold=True)])]]),
        DocsTableNode(rows=[[TableCell(text="BBB", spans=[TextSpan(text="BBB", italic=True)])]]),
    ]
    assert builder.unplaced_table_cells(doc, target) == []
