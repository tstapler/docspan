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

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsStructureParser,
    DocsTableNode,
    TableCell,
    TextSpan,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import (
    MarkdownToParagraphParser,
    _table_from_token,
)
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
    return _doc_with_rows([list(cells)], heading=heading)


def _doc_with_rows(rows: list, heading: str = "A1 — Current state") -> dict:
    """Same, for a table of more than one row.

    A markdown table always carries a header row, so anything driven through `push`
    needs a live table with a matching row count — otherwise the *target* has a row
    the document does not and `unplaced_table_cells` correctly reports the orphan,
    which looks like a code defect and is a fixture defect.
    """
    heading_end = 1 + len(heading) + 1
    return {"revisionId": "rev-1", "body": {"content": [
        {"startIndex": 1, "endIndex": heading_end, "paragraph": {
            "paragraphStyle": {"namedStyleType": "HEADING_2", "headingId": "h.a1live"},
            "elements": [{"textRun": {"content": heading + "\n", "textStyle": {}}}]}},
        {"startIndex": heading_end, "endIndex": heading_end + 1, "table": {
            "rows": len(rows), "columns": max((len(r) for r in rows), default=0),
            "tableRows": [{"tableCells": list(row)} for row in rows]}},
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

    def test_two_distinct_unplaceable_cells_with_the_same_text_are_both_counted(
        self,
    ) -> None:
        """The dedup that made the count wrong — nothing caught its return.

        An earlier version guarded the orphan sweep with `cell.text not in missed`, so
        two genuinely distinct affected cells collapsed into one entry and the message
        printed "1 table cell(s)" for two. Cell texts repeat constantly in these
        tables.
        """
        spans = markdown.parse("| x |\n| --- |\n| **Owner** |\n")[0].rows[1][0].spans
        doc = _doc_with_table(_cell("ref", 30))
        target = [DocsTableNode(rows=[
            [TableCell(text="ref")],
            [TableCell(text="Owner", spans=spans)],
            [TableCell(text="Owner", spans=spans)],
        ])]
        assert builder.unplaced_table_cells(doc, target) == ["Owner", "Owner"]

    def test_an_unstyled_cell_is_never_reported_as_unplaced(self) -> None:
        """The `if not cell.styled: continue` guard.

        Without it every cell in a table with no live counterpart is reported, so a
        plain table would raise a warning about formatting it never had.
        """
        doc = _doc_with_table(_cell("ref", 30))
        target = [DocsTableNode(rows=[[TableCell(text="ref")], [TableCell(text="plain")]])]
        assert builder.unplaced_table_cells(doc, target) == []

    def test_a_placed_cell_whose_spans_overflow_is_reported(self) -> None:
        """Placing is not the same as styling.

        `_span_requests_in` stops at the first span that would cross the cell's bound
        and emits nothing for it or anything after — so a cell could place, lose its
        styling, and be reported by nothing. `_spans_overflow` closes exactly this for
        paragraphs.
        """
        doc = _doc_with_table(_cell("A1", 30))
        target = [
            *markdown.parse("## A1 — Current state\n"),
            DocsTableNode(rows=[[TableCell(text="A1", spans=markdown.parse(
                "| x |\n| --- |\n| **A1 and rather more text than fits** |\n"
            )[0].rows[1][0].spans)]]),
        ]
        assert builder.build_table_cell_span_requests(doc, target) == []
        assert builder.unplaced_table_cells(doc, target) == ["A1"]

    def test_an_unstyled_table_costs_nothing(self) -> None:
        doc = _doc_with_table(_cell("A1", 30))
        target = [DocsTableNode(rows=[["A1"]])]
        assert builder.build_table_cell_span_requests(doc, target) == []


class TestPushReportsWhatItCouldNotStyle:
    """The wiring, asserted through `push` — not just the function that computes it.

    `unplaced_table_cells` was written, documented as push's report, and never called.
    Round 2 then found that wiring it up was itself untested: five separate mutations
    to `backend.py` — including deleting the call, dropping it from the "nothing to
    do" gate, and replacing the rendered message with `None` — all left the suite
    green. A report nothing asserts on is indistinguishable from no report.
    """

    def _doc_with_unstylable_cell(self) -> dict:
        """A live table whose cell holds a person chip, so it can never be placed.

        `_parse_cell` puts the chip's display name in `.text` but the chip
        contributes no `textRun`, so the cell text cannot be located by a text
        search — the shape of an "Owner" column in a real design doc.
        """
        cell = _cell("Ada owns A1", 40)
        cell["content"][0]["paragraph"]["elements"].insert(
            0, {"person": {"personProperties": {"name": "Ada", "email": "ada@example.com"}}}
        )
        return _doc_with_rows([[_cell("ref", 30)], [cell]])

    def _markdown(self) -> str:
        return (
            "## A1 — Current state\n\n"
            "| ref |\n| --- |\n| [Ada owns A1](#a1--current-state) |\n"
        )

    def test_push_warns_rather_than_reporting_success(self, make_backend, tmp_path) -> None:
        backend, client = make_backend()
        doc = self._doc_with_unstylable_cell()
        client.get_document.return_value = doc
        client.batch_update.return_value = {}
        client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text(self._markdown(), encoding="utf-8")
        result = backend.push(str(local), "doc-id")

        assert result.status == "warning", (
            f"a cell whose styling was dropped must not report success: {result}"
        )
        assert "table cell" in result.message
        assert "Ada owns A1" in result.message

    def test_the_remedy_is_not_a_false_promise_to_push_again(self) -> None:
        """A chip/image/multi-paragraph cell can never be placed.

        So "push again and the styling will land" was false, and because a warning
        exits non-zero the document would exit 1 forever while being told to retry.
        """
        message = GoogleDocsBackend._render_unplaced_cells(["Ada owns A1"])
        assert "pushing again places the styling" in message
        assert "will report this same warning" in message

    def test_a_push_that_dropped_cell_styling_is_never_reported_as_no_changes(
        self, make_backend, tmp_path
    ) -> None:
        """The "nothing to do" gate has to count unplaced cells.

        I first recorded this as untestable, having failed to build a fixture where
        pass 1 emits nothing while a styled cell is unplaceable — every attempt had
        pass 1 writing something, so the gate was never reached and the assertion
        passed for the wrong reason. A reviewer built it: put the chip's display name
        in its **own** element, so the live parsed text equals the markdown text and
        the text-only diff key gives pass 1 nothing to do, while `_cell_placement`
        still declines the cell for the non-`textRun` element beside it. My fixture
        prepended the chip to a run *already* holding the name, yielding
        `AdaAda owns A1`.

        Without the gate term this returns `status="skipped"` / "No changes detected"
        — a push that dropped a cell's formatting reporting that nothing happened.
        """
        backend, client = make_backend()
        chip_cell = {"content": [{
            "startIndex": 40, "endIndex": 52,
            "paragraph": {"elements": [
                {"person": {"personProperties": {"name": "Ada", "email": "ada@example.com"}}},
                {"textRun": {"content": " owns A1\n", "textStyle": {}}},
            ]},
        }]}
        client.get_document.return_value = _doc_with_rows([[_cell("ref", 30)], [chip_cell]])
        client.batch_update.return_value = {}
        client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text(self._markdown(), encoding="utf-8")
        result = backend.push(str(local), "doc-id")

        assert result.status != "skipped", (
            f"a push that dropped cell styling must not report no changes: {result}"
        )
        assert "table cell" in (result.message or "")

    def test_a_cell_that_can_be_placed_is_not_reported(self, make_backend, tmp_path) -> None:
        """The other side of the gate — no false warning on a healthy document."""
        backend, client = make_backend()
        client.get_document.return_value = _doc_with_rows(
            [[_cell("ref", 30)], [_cell("A1", 40)]]
        )
        client.batch_update.return_value = {}
        client.list_comments.return_value = []

        local = tmp_path / "doc.md"
        local.write_text(
            "## A1 — Current state\n\n| ref |\n| --- |\n| [A1](#a1--current-state) |\n",
            encoding="utf-8",
        )
        result = backend.push(str(local), "doc-id")
        assert "table cell" not in (result.message or "")


class TestRendering:
    def test_a_cell_link_renders_back_into_markdown(self) -> None:
        doc = _doc_with_table(_cell("A1", 30, link={"url": "https://example.com"}))
        nodes = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)]
        assert "[A1](https://example.com)" in render_nodes_to_markdown(nodes)

    def test_a_multi_paragraph_cell_round_trips_faithfully(self) -> None:
        """A Docs cell holds a paragraph list; markdown's pipe-table syntax has no
        cell-internal break.

        Two quiet encodings were tried and reverted (#51, #61): a bare `\\n` ends the
        row early and reparses as a stray paragraph; `<br>` keeps the row but breaks
        the diff key, so an *unmodified* push deletes and re-creates the table along
        with every comment anchored in it — and decoding `<br>` back on parse reopens
        the same hole for a cell whose author literally typed `<br>`.

        A table holding a multi-paragraph cell now renders as a raw HTML `<table>`
        block instead of pipe syntax: mistune tokenizes the whole block as one opaque
        string, so the internal `\\n` is never seen as a row terminator, and the cell
        round-trips exactly.
        """
        node = DocsTableNode(rows=[[TableCell(text="line one\nline two"), TableCell(text="x")]])
        rendered = render_nodes_to_markdown([node])
        assert "<table" in rendered

        parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
        assert len(parsed) == 1
        assert [c.text for c in parsed[0].rows[0]] == ["line one\nline two", "x"]

    def test_styling_in_a_later_paragraph_of_a_multi_paragraph_cell_is_reported_not_dropped(
        self,
    ) -> None:
        """`_cell_placement` only reads a live cell's first `content` element.

        A real two-paragraph cell has two `content` elements; styling that belongs to
        the second cannot be located in the first, so it must still surface via
        `unplaced_table_cells` rather than silently vanish. This is an existing,
        intentional limitation of the push-side placement logic (out of scope for the
        render/parse fix), and it must not regress into a silent drop.
        """
        two_paragraph = {"content": [
            {"startIndex": 30, "endIndex": 39, "paragraph": {
                "elements": [{"textRun": {"content": "line one\n", "textStyle": {}}}]}},
            {"startIndex": 39, "endIndex": 48, "paragraph": {
                "elements": [{"textRun": {"content": "line two\n", "textStyle": {}}}]}},
        ]}
        doc = _doc_with_table(two_paragraph)
        target = [DocsTableNode(rows=[[TableCell(text="line one\nline two", spans=markdown.parse(
            "| x |\n| --- |\n| **a** |\n")[0].rows[1][0].spans)]])]
        assert builder.unplaced_table_cells(doc, target) == ["line one\nline two"]

    def test_a_literal_br_a_person_typed_survives_untouched(self) -> None:
        """The input the decode destroyed. `HEAD~1` of that fix handled it correctly.

        A cell reading `see <br> tag` came back as `see \n tag`, so the diff key
        stopped matching and an unmodified push deleted and re-created the table. A
        cell holding only `<br>` came back empty — content destroyed outright.
        """
        for raw in ("see <br> tag", "<br>", "<br/>", "<BR>"):
            cell = markdown.parse(f"| x |\n| --- |\n| {raw} |\n")[0].rows[1][0]
            assert cell.text == raw, f"{raw!r} must survive parsing unchanged"

    def test_a_literal_table_tag_typed_in_a_single_paragraph_cell_survives_untouched(
        self,
    ) -> None:
        """A real `<table>` block is now a recognized construct — but only as its own
        top-level markdown block, not as text inside a pipe-table cell.

        A single-paragraph cell that literally reads `<table>` must stay an ordinary
        (escaped) pipe-table cell, not get reinterpreted as the start of an HTML table.
        """
        cell = markdown.parse("| x |\n| --- |\n| <table> |\n")[0].rows[1][0]
        assert cell.text == "<table>"

    def test_html_unsafe_characters_in_a_multi_paragraph_cell_round_trip(self) -> None:
        """`<`, `>`, `&` must be entity-escaped on render and decoded back on parse,
        with no corruption and no accidental tag injection."""
        raw_text = "a <b> & c\n<script>&x"
        node = DocsTableNode(rows=[[TableCell(text=raw_text), TableCell(text="y")]])
        rendered = render_nodes_to_markdown([node])
        assert "<script>" not in rendered.split("<table>", 1)[1].split("&x", 1)[0]

        parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
        assert parsed[0].rows[0][0].text == raw_text

    def test_a_cell_with_only_blank_paragraphs_round_trips_without_losing_the_table(self) -> None:
        """A cell holding an empty paragraph between two others (or only empty
        paragraphs) must not fracture the table's raw-HTML block.

        CommonMark ends an HTML block at the first blank line, even inside otherwise
        opaque raw HTML — an empty *interior* paragraph renders as a bare `\\n\\n`,
        which mistune's tokenizer treats as a block boundary, splitting one
        `block_html` token into two and silently dropping the rest of the table (see
        `_guard_blank_paragraph_lines`). A leading/trailing empty paragraph is exempt
        since it shares its physical line with a tag and is never actually blank.
        """
        node = DocsTableNode(rows=[[TableCell(text="\n\n"), TableCell(text="x")]])
        rendered = render_nodes_to_markdown([node])
        parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
        assert len(parsed) == 1
        assert [c.text for c in parsed[0].rows[0]] == ["\n\n", "x"]

        node2 = DocsTableNode(rows=[[TableCell(text="para one\n\npara two"), TableCell(text="y")]])
        rendered2 = render_nodes_to_markdown([node2])
        parsed2 = [n for n in markdown.parse(rendered2) if isinstance(n, DocsTableNode)]
        assert len(parsed2) == 1
        assert parsed2[0].rows[0][0].text == "para one\n\npara two"

    def test_a_styled_span_crossing_a_paragraph_boundary_does_not_leak_markers(self) -> None:
        """A bold/italic/monospace run can span a paragraph break (e.g. a user bolds
        the first line of a two-line cell) — the styling boundary and the paragraph
        boundary don't line up.

        Rendering that single span's markdown syntax (`**...**`) across the `\\n` and
        only then splitting on `\\n` leaves an unmatched `**` on each side, which
        mistune's inline parser can't pair — the decoded text comes back with literal
        `**` garbage baked in instead of the styling being applied per paragraph.
        Each paragraph must be rendered — and re-parsed — independently.
        """
        spans = [
            TextSpan(text="line one\n", bold=True),
            TextSpan(text="line two", bold=True),
        ]
        cell = TableCell(text="line one\nline two", spans=spans)
        node = DocsTableNode(rows=[[cell, TableCell(text="x")]])
        rendered = render_nodes_to_markdown([node])
        assert "**" not in rendered.split("<table>", 1)[1].replace("**line one**", "").replace(
            "**line two**", ""
        )

        parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
        decoded = parsed[0].rows[0][0]
        assert decoded.text == "line one\nline two"
        assert all(s.bold for s in decoded.spans if s.text != "\n")

    def test_a_real_zero_width_space_in_cell_text_is_not_mistaken_for_the_blank_guard(
        self,
    ) -> None:
        """The blank-paragraph guard (`_guard_blank_paragraph_lines`) marks an interior
        empty paragraph with a literal U+200B so it survives as a non-blank physical
        line. If a *real* cell paragraph is itself a stray U+200B — plausible,
        copy-pasted content from other editors carries these — it must not collide
        with the guard and get silently stripped to "" on decode.
        """
        zwsp = "​"
        for raw_text in (f"para one\n{zwsp}\npara two", f"{zwsp}\nsecond", f"first\n{zwsp}"):
            node = DocsTableNode(rows=[[TableCell(text=raw_text), TableCell(text="y")]])
            rendered = render_nodes_to_markdown([node])
            parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
            assert parsed[0].rows[0][0].text == raw_text, f"{raw_text!r} must survive unchanged"

    def test_a_document_mixing_pipe_and_html_tables_parses_both_correctly(self) -> None:
        """A document with a plain single-paragraph table followed by a multi-paragraph
        (HTML) table must parse both back correctly, in document order."""
        pipe_node = DocsTableNode(rows=[[TableCell(text="a"), TableCell(text="b")]])
        html_node = DocsTableNode(rows=[[TableCell(text="c\nd"), TableCell(text="e")]])
        rendered = render_nodes_to_markdown([pipe_node, html_node])

        parsed = [n for n in markdown.parse(rendered) if isinstance(n, DocsTableNode)]
        assert len(parsed) == 2
        assert [c.text for c in parsed[0].rows[0]] == ["a", "b"]
        assert [c.text for c in parsed[1].rows[0]] == ["c\nd", "e"]

    def test_a_parsed_cell_with_no_marks_carries_no_spans(self) -> None:
        """The parser path, not the markdown path.

        `test_an_unstyled_cell_carries_no_spans` covers `_cell_from_token`; this
        covers `_parse_cell`, whose comment claims the same thing and was untested.
        """
        doc = _doc_with_table(_cell("plain", 30))
        cell = [n for n in structure.parse(doc) if isinstance(n, DocsTableNode)][0].rows[0][0]
        assert cell == TableCell(text="plain", spans=[])

    def test_padding_cells_from_the_parser_are_distinct_objects(self) -> None:
        """`[TableCell()] * n` puts one object at every padded position.

        The earlier version of this test hand-appended two separately-constructed
        cells and asserted they differed — which is true of any two constructor calls
        and passes with the aliasing bug fully restored. It never reached
        `_table_from_token`. This drives the parser.

        Padding is unreachable through mistune (it rejects a ragged GFM table rather
        than padding it), so the ragged row is built as a bare `table_row` token, the
        one shape `cells_of` can still shorten.
        """
        def row(*texts: str) -> dict:
            return {"type": "table_row", "children": [
                {"type": "table_cell", "children": [{"type": "text", "raw": t}]} for t in texts
            ]}

        # One row short by **two** columns. Each row's padding is built by its own
        # expression, so `[TableCell()] * n` aliases *within* a row and not across
        # rows — measured. An earlier version of this test compared cells in
        # different rows and passed with the aliasing fully restored.
        node = _table_from_token({"type": "table", "children": [
            row("a", "b", "c"), row("d"),
        ]})
        assert [len(r) for r in node.rows] == [3, 3], "the short row must be padded"
        assert node.rows[1][1] is not node.rows[1][2], (
            "two padded cells in one row must not be the same object"
        )

        node.rows[1][1].spans.append(TextSpan(text="x"))
        assert node.rows[1][2].spans == [], (
            "touching one padded cell must not reach its neighbour"
        )

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
