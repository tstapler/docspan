"""Unit tests for Google Docs table push + inline style/link push (no network)."""

from typing import List

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser

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
    # Regression: a prior text-equality-based aligner matched the FIRST current
    # paragraph with matching text, so when two unstyled paragraphs share the same
    # text, the styled paragraph after them got matched to the wrong (earlier)
    # index, permanently shifting every later paragraph's styling one slot off.
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
    # desynced every subsequent paragraph's styling. Positional pairing is
    # immune to this since it never searches — it just zips index-for-index.
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
    assert t.rows[0] == ["A", "B"]
    assert t.rows[1] == ["1", "2"]
    assert t.rows[2] == ["3", "4"]
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
    assert tables[0].rows == [["A", "B"], ["1", "2"], ["3", "4"]]


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
    pairs = [(r["insertText"]["location"]["index"], r["insertText"]["text"]) for r in reqs]
    # Sorted descending by index so earlier inserts don't shift later ones.
    assert pairs == [(15, "2"), (12, "1"), (8, "B"), (5, "A")]


def test_fill_skips_when_no_target_tables() -> None:
    assert builder.build_table_fill_requests(_empty_table_doc(), []) == []


def test_fill_skips_populated_tables() -> None:
    target = [DocsTableNode(rows=[["A", "B"], ["1", "2"], ["3", "4"]])]
    # Already-populated table should not be re-filled.
    assert builder.build_table_fill_requests(_populated_table_doc(), target) == []
