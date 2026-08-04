"""Inline styling inside a table cell — including internal `#anchor` links.

Cells were plain `str`, so every mark inside one was dropped on the way in and
pass 2 never looked at a table at all. The visible consequence: a cross-reference
written inside a table cell rendered as dead text in the Doc, while the *identical*
reference one paragraph away resolved into a real heading link. Nothing reported
it, because the styling was already gone by the time anything could have noticed.

The field measurement that motivated this lives in the pull request, not here — a
count taken against one document on one day rots, and a test file is the wrong place
to keep something a reader cannot re-derive from the repository. What *is* checkable
is below.
"""
from __future__ import annotations

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsStructureParser,
    DocsTableNode,
    TableCell,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

markdown = MarkdownToParagraphParser()
structure = DocsStructureParser()
builder = DocsRequestBuilder()


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _cell(text: str, start: int, **style: object) -> dict:
    """A live table cell holding one paragraph of one run.

    `endIndex` is measured in **UTF-16 code units**, as Docs does. Using `len()` made
    the fixture unable to express any non-BMP case — and the one piece of UTF-16
    arithmetic this change adds is in the cell path, so a `len()` fixture left it
    asserted only by construction.
    """
    return {"content": [{
        "startIndex": start,
        "endIndex": start + _utf16_len(text) + 1,
        "paragraph": {"elements": [{"textRun": {"content": text + "\n", "textStyle": dict(style)}}]},
    }]}


def _doc_with_table(*cells: dict, heading: str = "A1 — Current state") -> dict:
    """A document holding a heading (so an anchor has something to resolve to) then a table."""
    heading_end = 1 + len(heading) + 1
    return {"revisionId": "rev-1", "body": {"content": [
        {"startIndex": 1, "endIndex": heading_end, "paragraph": {
            "paragraphStyle": {"namedStyleType": "HEADING_2", "headingId": "h.a1live"},
            "elements": [{"textRun": {"content": heading + "\n", "textStyle": {}}}]}},
        {"startIndex": heading_end, "endIndex": heading_end + 1, "table": {
            "rows": 1, "columns": len(cells),
            "tableRows": [{"tableCells": list(cells)}]}},
    ]}}


class TestCellsCarryTheirStyling:
    def test_a_link_in_a_markdown_cell_survives_parsing(self) -> None:
        node = markdown.parse("| ref | note |\n| --- | --- |\n| [A1](#a1) | x |\n")[0]
        cell = node.rows[1][0]
        assert cell.text == "A1"
        assert [(s.text, s.link) for s in cell.spans] == [("A1", "#a1")]

    def test_bold_and_code_in_a_cell_survive(self) -> None:
        node = markdown.parse("| a |\n| --- |\n| **b** and `c` |\n")[0]
        cell = node.rows[1][0]
        assert cell.text == "b and c"
        assert [(s.text, s.bold, s.monospace) for s in cell.spans] == [
            ("b", True, False), (" and ", False, False), ("c", False, True),
        ]

    def test_an_unstyled_cell_carries_no_spans(self) -> None:
        """Matching `DocsParagraphNode`: no marks means the plain-text path."""
        node = markdown.parse("| a |\n| --- |\n| plain |\n")[0]
        assert node.rows[1][0] == TableCell(text="plain", spans=[])

    def test_a_link_in_a_live_cell_survives_reading(self) -> None:
        doc = _doc_with_table(_cell("see A1", 30, link={"headingId": "h.a1live"}))
        cell = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)][0].rows[0][0]
        assert cell.text == "see A1"
        assert [(s.text, s.link) for s in cell.spans] == [("see A1", "#h.a1live")]

    def test_spans_concatenate_to_the_cell_text(self) -> None:
        """The invariant pass 2 walks. A cell's text is stripped; the spans must be too.

        The live run ends with the paragraph's newline and Docs pads cells with
        whitespace, so without trimming the spans by the same amount every range in
        the cell would be placed off by the width of the trim.
        """
        doc = _doc_with_table(_cell("  padded  ", 30, bold=True))
        cell = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)][0].rows[0][0]
        assert cell.text == "padded"
        assert "".join(s.text for s in cell.spans) == cell.text

    def test_a_string_cell_is_normalised_rather_than_left_to_crash(self) -> None:
        """Cells were `str`; a caller still passing one gets a `TableCell`.

        Without this the mistake surfaced as `AttributeError: 'str' object has no
        attribute 'text'` several frames inside request building, which says nothing
        about what actually went wrong.
        """
        node = DocsTableNode(rows=[["a", "b"]])
        assert node.rows == [[TableCell(text="a"), TableCell(text="b")]]


class TestPassTwoStylesCells:
    def test_an_anchor_in_a_cell_becomes_a_heading_link(self) -> None:
        """The bug, stated as the property that was broken."""
        doc = _doc_with_table(_cell("A1", 30), _cell("note", 34))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[
                TableCell(text="A1", spans=markdown.parse(
                    "| x |\n| --- |\n| [A1](#a1--current-state) |\n")[0].rows[1][0].spans),
                TableCell(text="note"),
            ]]),
        ]

        links = [
            r["updateTextStyle"]["textStyle"]["link"]
            for r in builder.build_table_cell_span_requests(doc, target)
            if "link" in r["updateTextStyle"].get("textStyle", {})
        ]
        assert links == [{"headingId": "h.a1live"}], (
            "the anchor must resolve against the live heading's id, exactly as it "
            "does for the same reference written in a paragraph"
        )

    def test_the_range_lands_on_the_cell_text_and_not_the_padding(self) -> None:
        """The offset is searched for, not assumed to be the paragraph's start."""
        doc = _doc_with_table(_cell("  A1", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1** |\n")[0].rows[1][0].spans)]]),
        ]
        ranges = [r["updateTextStyle"]["range"]
                  for r in builder.build_table_cell_span_requests(doc, target)]
        # The cell paragraph starts at 30 and holds "  A1"; the text starts at 32.
        assert ranges == [{"startIndex": 32, "endIndex": 34}]

    def test_a_cell_whose_text_is_absent_is_reported_rather_than_guessed(self) -> None:
        """Refusing to guess is only safe if the refusal is loud.

        Happens when a concurrent edit changes the cell between pass 1 and pass 2.
        Aiming the range at that ordinal anyway would style whatever now sits there.
        """
        doc = _doc_with_table(_cell("something else", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1** |\n")[0].rows[1][0].spans)]]),
        ]
        assert builder.build_table_cell_span_requests(doc, target) == []
        assert builder.unplaced_table_cells(doc, target) == ["A1"]

    def test_an_offset_past_an_astral_character_is_measured_in_utf16_units(self) -> None:
        """Docs indices count UTF-16 units; an emoji is two, not one.

        Counting Python characters here would place the range one unit early for
        every astral character before the styled text — a wrong-but-valid range that
        styles the wrong characters and reports nothing.
        """
        doc = _doc_with_table(_cell("\U0001F600 A1", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1** |\n")[0].rows[1][0].spans)]]),
        ]
        ranges = [r["updateTextStyle"]["range"]
                  for r in builder.build_table_cell_span_requests(doc, target)]
        # "\U0001F600 " is 3 UTF-16 units (surrogate pair + space), not 2 characters.
        assert ranges == [{"startIndex": 33, "endIndex": 35}]

    def test_a_span_wider_than_the_cell_is_dropped_rather_than_spilling(self) -> None:
        """The bound is enforced, not trusted — the cell path's safety property.

        Without it a span longer than the cell writes a range past the cell's
        newline, into whatever the document holds next.
        """
        doc = _doc_with_table(_cell("A1", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1 and rather more text than fits** |\n"
            )[0].rows[1][0].spans)]]),
        ]
        assert builder.build_table_cell_span_requests(doc, target) == []

    def test_an_inline_object_in_the_cell_is_bailed_on_rather_than_mis_measured(self) -> None:
        """A non-textRun element has index width but no text.

        So `find`'s offset is no longer the document distance from `startIndex`.
        Measured before the bail-out: the range came out [30,32) for text sitting at
        [31,33) — it styled the image plus the first character, silently.
        """
        cell = _cell("A1", 30)
        cell["content"][0]["paragraph"]["elements"].insert(
            0, {"inlineObjectElement": {"inlineObjectId": "kix.img1"}}
        )
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1** |\n")[0].rows[1][0].spans)]]),
        ]
        doc = _doc_with_table(cell)
        assert builder.build_table_cell_span_requests(doc, target) == []
        assert builder.unplaced_table_cells(doc, target) == ["A1"]

    def test_a_target_cell_with_no_live_counterpart_is_reported(self) -> None:
        """Both generators stop when the *live* side runs out.

        So a target table, row or cell with no live counterpart was never yielded and
        never reported — the silent partial application `unaligned_span_targets`
        deliberately closed for paragraphs.
        """
        doc = _doc_with_table(_cell("A1", 30))
        spans = markdown.parse("| x |\n| --- |\n| **A1** |\n")[0].rows[1][0].spans
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[
                [TableCell(text="A1", spans=spans), TableCell(text="Owner", spans=spans)],
            ]),
        ]
        assert builder.unplaced_table_cells(doc, target) == ["Owner"]

    def test_a_dead_anchor_in_a_cell_is_reported(self) -> None:
        """The other half of the motivating bug.

        The resolving half is fixed by emitting the link; the non-resolving half was
        still exactly the original complaint — dead text in the document, reported by
        nothing — because `unresolved_anchor_links` tested only paragraphs and
        `_align_for_styling` skips tables.
        """
        doc = _doc_with_table(_cell("A1", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| [A1](#no-such-heading) |\n")[0].rows[1][0].spans)]]),
        ]
        # No link is written — that part was already right.
        assert [r for r in builder.build_table_cell_span_requests(doc, target)
                if "link" in r["updateTextStyle"].get("textStyle", {})] == []
        # And now it is reported.
        assert builder.unresolved_anchor_links(doc, target) == ["#no-such-heading"]

    def test_an_unstyled_table_costs_nothing(self) -> None:
        doc = _doc_with_table(_cell("A1", 30))
        target = [DocsTableNode(rows=[["A1"]])]
        assert builder.build_table_cell_span_requests(doc, target) == []


class TestRendering:
    def test_a_cell_link_renders_back_into_markdown(self) -> None:
        doc = _doc_with_table(_cell("A1", 30, link={"url": "https://example.com"}))
        nodes = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)]
        assert "[A1](https://example.com)" in render_nodes_to_markdown(nodes)

    def test_a_newline_in_a_cell_becomes_a_break_rather_than_a_new_row(self) -> None:
        """A cell holds a paragraph list; markdown's table syntax has no line break.

        An unescaped newline ends the *row*, so a two-paragraph cell reparses as a
        paragraph and the table is destroyed. Rendering spans made it worse before
        this: `**line one\nline two**` emitted a dangling `**` across the break.
        """
        node = DocsTableNode(rows=[[TableCell(text="line one\nline two"), TableCell(text="x")]])
        rendered = render_nodes_to_markdown([node])
        assert rendered.splitlines()[0] == "| line one<br>line two | x |"
        # Still one table, still two columns.
        assert isinstance(markdown.parse(rendered)[0], DocsTableNode)

    def test_a_parsed_cell_with_no_marks_carries_no_spans(self) -> None:
        """The parser path, not the markdown path.

        `test_an_unstyled_cell_carries_no_spans` covers `_cell_from_token`; this
        covers `_parse_cell`, whose comment claims the same thing and was untested.
        """
        doc = _doc_with_table(_cell("plain", 30))
        cell = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)][0].rows[0][0]
        assert cell == TableCell(text="plain", spans=[])

    def test_padding_cells_are_not_the_same_object(self) -> None:
        """`[TableCell()] * n` aliases one object, and `spans` is a mutable default.

        So styling one padded cell would style every one of them.
        """
        node = DocsTableNode(rows=[[TableCell(text="a")], [TableCell(text="b")]])
        node.rows[0].append(TableCell())
        node.rows[1].append(TableCell())
        assert node.rows[0][1] is not node.rows[1][1]

    def test_a_pipe_in_cell_text_is_escaped(self) -> None:
        """An unescaped `|` ends the cell, silently splitting the row.

        Reachable from a link whose URL carries one, which pass 1 will happily write.
        """
        node = DocsTableNode(rows=[[TableCell(text="a|b"), TableCell(text="c")]])
        rendered = render_nodes_to_markdown([node])
        assert rendered.splitlines()[0] == r"| a\|b | c |"
        # And it survives the round trip as one cell, not two.
        assert [c.text for c in markdown.parse(
            rendered + "\n| --- | --- |\n"
        )[0].rows[0]] == ["a|b", "c"]
