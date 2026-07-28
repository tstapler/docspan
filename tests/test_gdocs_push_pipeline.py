"""End-to-end tests of the Google Docs push pipeline, against a model of the API.

Everything else in the suite exercises one component at a time: the parser
tests hand it raw Docs JSON, the request-builder tests hand it hand-built
nodes with hand-picked indices. Nothing crosses the boundary, so the builder's
assumptions about what the parser produces — and about what the document looks
like *after* pass 1's requests are applied — were never checked against each
other. Both bugs this module exists to pin down live exactly there.

So these tests run the real chain:

    markdown  --MarkdownToParagraphParser-->  target nodes
    doc JSON  --DocsStructureParser-------->  current nodes
                --DocsRequestBuilder.build--> pass-1 requests
                --DocModel.apply------------> the document those requests produce
                --DocsRequestBuilder.build_span_style_requests--> pass-2 requests

``DocModel`` is a small executable model of the two Docs API rules that make
this hard, taken from the DeleteContentRangeRequest reference:

  * "Deleting the last newline character of a Body ... " is invalid.
  * "Deleting the newline character before a Table, TableOfContents or
    SectionBreak without deleting the element" is invalid.

It checks those rules rather than any particular arithmetic, so a *different*
wrong trim (one that handled tables but not section breaks, say) fails here
too — which is the thing the hand-set-the-flag-and-assert-the-number unit
tests in test_docs_request_builder.py cannot do.

Deliberate simplifications, so nothing here is read as more than it is:

  * A table, table of contents or section break occupies a single index unit.
    Real tables span many, and contain their own paragraphs. The rules above
    only care about the element's boundary, and the model and the JSON it
    generates agree with each other, so delete/insert index arithmetic is
    exercised faithfully; table *cell* indices are not modelled and pass-2
    cell filling is not tested here (test_gdocs_tables_and_styles.py covers it).
  * Reconstructing a document after applying requests keeps text and structure
    only — every rebuilt paragraph is NORMAL_TEXT. Paragraph-level styling is
    asserted on the emitted requests instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pytest

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    UNDELETABLE_BOUNDARY_KEYS,
    DocsParagraphNode,
    DocsStructureParser,
    TextSpan,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser

parser = MarkdownToParagraphParser()
structure = DocsStructureParser()
builder = DocsRequestBuilder()

# Non-paragraph elements are single private-use code points in the flat model.
SECTION_BREAK_UNIT = chr(0xE000)
TABLE_OF_CONTENTS_UNIT = chr(0xE001)
_FIRST_TABLE_UNIT = 0xE100  # tables get one private-use char each, so a delete
_LAST_TABLE_UNIT = 0xE1FF   # can't confuse one table with another

_UNIT_KIND = {
    SECTION_BREAK_UNIT: "sectionBreak",
    TABLE_OF_CONTENTS_UNIT: "tableOfContents",
}


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _is_table_unit(char: str) -> bool:
    return _FIRST_TABLE_UNIT <= ord(char) <= _LAST_TABLE_UNIT


def _unit_kind(char: str) -> Optional[str]:
    if _is_table_unit(char):
        return "table"
    return _UNIT_KIND.get(char)


class InvalidDeleteRange(AssertionError):
    """Raised when a request violates a modelled Docs API deletion rule.

    The real API answers the same situation with HTTP 400 "Invalid deletion
    range. Cannot delete the requested range." and rejects the *whole* batch.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Document units
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Para:
    text: str
    style: str = "NORMAL_TEXT"
    bullet: bool = False


@dataclass
class SectionBreak:
    pass


@dataclass
class TableOfContents:
    pass


@dataclass
class Table:
    rows: List[List[str]] = field(default_factory=list)


Unit = object  # Para | SectionBreak | TableOfContents | Table


class DocModel:
    """A Google Docs body, as a flat index space plus the rules that govern it."""

    def __init__(self, units: List[Unit]) -> None:
        self.units = list(units)

    # ── rendering ────────────────────────────────────────────────────────────

    def flat(self) -> Tuple[str, Dict[str, List[List[str]]]]:
        """Render the body as a string whose 0-based offset i is document index i+1."""
        chars: List[str] = []
        tables: Dict[str, List[List[str]]] = {}
        next_table = _FIRST_TABLE_UNIT
        for unit in self.units:
            if isinstance(unit, Para):
                chars.append(unit.text + "\n")
            elif isinstance(unit, SectionBreak):
                chars.append(SECTION_BREAK_UNIT)
            elif isinstance(unit, TableOfContents):
                chars.append(TABLE_OF_CONTENTS_UNIT)
            elif isinstance(unit, Table):
                char = chr(next_table)
                next_table += 1
                tables[char] = unit.rows
                chars.append(char)
            else:  # pragma: no cover - guard against a malformed fixture
                raise TypeError(f"unknown unit {unit!r}")
        return "".join(chars), tables

    def doc(self) -> dict:
        """Render as a Google Docs `documents.get` body, with real index arithmetic."""
        content: List[dict] = []
        index = 1
        for unit in self.units:
            if isinstance(unit, Para):
                end = index + _utf16_len(unit.text) + 1
                paragraph: dict = {
                    "elements": [{"textRun": {"content": unit.text + "\n"}}],
                    "paragraphStyle": {"namedStyleType": unit.style},
                }
                if unit.bullet:
                    paragraph["bullet"] = {"listId": "list-1", "nestingLevel": 0}
                content.append({"startIndex": index, "endIndex": end, "paragraph": paragraph})
            elif isinstance(unit, Table):
                end = index + 1
                content.append({
                    "startIndex": index,
                    "endIndex": end,
                    "table": {
                        "rows": len(unit.rows),
                        "columns": max((len(r) for r in unit.rows), default=0),
                        "tableRows": [
                            {"tableCells": [_cell(text) for text in row]} for row in unit.rows
                        ],
                    },
                })
            else:
                end = index + 1
                key = "sectionBreak" if isinstance(unit, SectionBreak) else "tableOfContents"
                content.append({"startIndex": index, "endIndex": end, key: {}})
            index = end
        return {"body": {"content": content}}

    def end_index(self) -> int:
        content = self.doc()["body"]["content"]
        return content[-1]["endIndex"] if content else 1

    def paragraph_texts(self) -> List[str]:
        return [u.text for u in self.units if isinstance(u, Para)]

    def text_at(self, start: int, end: int) -> str:
        """The document content a request range covers — how a range is judged."""
        flat, _ = self.flat()
        return flat[start - 1:end - 1]

    # ── applying a batch ─────────────────────────────────────────────────────

    def apply(self, requests: List[dict]) -> "DocModel":
        """Apply a batchUpdate request list in order, enforcing the API's rules.

        Requests arrive already sorted highest-index-first by build(); they are
        applied in exactly that order against the *current* state, which is what
        makes a wrong trim show up as an index error rather than as silence.
        """
        flat, tables = self.flat()
        next_table = max((ord(c) for c in tables), default=_FIRST_TABLE_UNIT - 1) + 1

        for request in requests:
            if "deleteContentRange" in request:
                rng = request["deleteContentRange"]["range"]
                start, end = rng["startIndex"], rng["endIndex"]
                _check_delete(flat, start, end)
                flat = flat[:start - 1] + flat[end - 1:]
            elif "insertText" in request:
                inner = request["insertText"]
                index = inner["location"]["index"]
                _check_insert(flat, index)
                flat = flat[:index - 1] + inner["text"] + flat[index - 1:]
            elif "insertTable" in request:
                inner = request["insertTable"]
                index = inner["location"]["index"]
                _check_insert(flat, index)
                char = chr(next_table)
                next_table += 1
                tables[char] = [[""] * inner["columns"] for _ in range(inner["rows"])]
                # "A newline character will be inserted before the inserted
                # table." (InsertTableRequest reference) — that newline is a
                # whole extra paragraph the caller never asked for.
                flat = flat[:index - 1] + "\n" + char + flat[index - 1:]
            # updateParagraphStyle / createParagraphBullets / deleteParagraphBullets /
            # updateTextStyle change no indices, so the model ignores them.

        return DocModel(_units_from_flat(flat, tables))


def _cell(text: str) -> dict:
    return {"content": [{"paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}}]}


def _check_insert(flat: str, index: int) -> None:
    if not 1 <= index <= len(flat):
        raise InvalidDeleteRange(f"insert index {index} outside body [1, {len(flat)}]")


def _check_delete(flat: str, start: int, end: int) -> None:
    """Enforce the DeleteContentRangeRequest rules this codebase can violate."""
    if start < 1 or end <= start:
        raise InvalidDeleteRange(f"degenerate range [{start}, {end})")
    if end > len(flat) + 1:
        raise InvalidDeleteRange(f"range [{start}, {end}) runs past the body")
    if end > len(flat):
        raise InvalidDeleteRange(
            f"range [{start}, {end}) deletes the body's terminal newline at {len(flat)}"
        )
    for index in range(start, end):
        if flat[index - 1] != "\n":
            continue
        following = index + 1
        if following > len(flat):
            continue
        kind = _unit_kind(flat[following - 1])
        if kind is None:
            continue
        if following >= end:  # the boundary element itself survives this delete
            raise InvalidDeleteRange(
                f"range [{start}, {end}) deletes the newline at {index}, which anchors "
                f"the {kind} at {following}, without deleting the {kind}"
            )


def _units_from_flat(flat: str, tables: Dict[str, List[List[str]]]) -> List[Unit]:
    units: List[Unit] = []
    buffer: List[str] = []
    for char in flat:
        kind = _unit_kind(char)
        if kind is None:
            buffer.append(char)
            if char == "\n":
                units.append(Para("".join(buffer[:-1])))
                buffer = []
            continue
        if buffer:  # pragma: no cover - a paragraph must end before a boundary
            raise AssertionError("boundary element not preceded by a newline")
        if kind == "table":
            units.append(Table(rows=tables[char]))
        elif kind == "sectionBreak":
            units.append(SectionBreak())
        else:
            units.append(TableOfContents())
    if buffer:  # pragma: no cover - a well-formed body ends with a newline
        raise AssertionError("body did not end with a newline")
    return units


def _pass1(model: DocModel, markdown: str) -> Tuple[List[dict], list]:
    target = parser.parse(markdown)
    current = structure.parse(model.doc())
    return builder.build(current, target, model.end_index()), target


def _boundary_ranges(doc: dict) -> List[Tuple[str, int, int]]:
    return [
        (key, element["startIndex"], element["endIndex"])
        for element in doc["body"]["content"]
        for key in UNDELETABLE_BOUNDARY_KEYS
        if key in element
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios — each is (live document, local markdown)
# ─────────────────────────────────────────────────────────────────────────────

LINK_MARKDOWN = "Intro\n\nAlpha\n\n[Beta](https://example.com)\n"

SCENARIOS: List[Tuple[str, DocModel, str]] = [
    (
        "paragraph removed above a section break",
        DocModel([Para("Intro"), Para("Doomed"), SectionBreak(),
                  Para("Alpha"), Para("Beta"), Para("")]),
        LINK_MARKDOWN,
    ),
    (
        "paragraph removed above a table",
        DocModel([Para("Intro"), Para("Doomed"), Table([["A", "B"]]),
                  Para("Alpha"), Para("Beta"), Para("")]),
        "Intro\n\n| A | B |\n| --- | --- |\n\nAlpha\n\n[Beta](https://example.com)\n",
    ),
    (
        "paragraph removed above a table of contents",
        DocModel([Para("Intro"), Para("Doomed"), TableOfContents(),
                  Para("Alpha"), Para("Beta"), Para("")]),
        LINK_MARKDOWN,
    ),
    (
        "heading removed above a table",
        DocModel([Para("Intro"), Para("Old heading", style="HEADING_2"), Table([["A"]]),
                  Para("Alpha"), Para("")]),
        "Intro\n\n| A |\n| --- |\n\nAlpha\n",
    ),
    (
        "paragraph edited directly above a section break",
        DocModel([Para("Intro"), Para("Old text"), SectionBreak(), Para("Beta"), Para("")]),
        "Intro\n\nNew text\n\n[Beta](https://example.com)\n",
    ),
    (
        "boundary elements back to back",
        DocModel([Para("Doomed"), SectionBreak(), SectionBreak(), Para("Alpha"), Para("")]),
        "Alpha\n",
    ),
    (
        "document opens on a doomed paragraph above a boundary",
        DocModel([Para("Doomed"), TableOfContents(), Para("Alpha"), Para("")]),
        "Alpha\n",
    ),
    (
        "table removed along with the paragraph above it",
        DocModel([Para("Intro"), Para("Doomed"), Table([["A", "B"]]), Para("Alpha"), Para("")]),
        "Intro\n\nAlpha\n",
    ),
    (
        "everything before a boundary replaced",
        DocModel([Para("One"), Para("Two"), Para("Three"), SectionBreak(),
                  Para("Beta"), Para("")]),
        "Uno\n\nDos\n\n[Beta](https://example.com)\n",
    ),
    (
        "table inserted where the live doc has none",
        DocModel([Para("Intro"), Para("Alpha"), Para("Beta"), Para("")]),
        "Intro\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nAlpha\n\n"
        "[Beta](https://example.com)\n",
    ),
]

SCENARIO_IDS = [name for name, _model, _markdown in SCENARIOS]


# ─────────────────────────────────────────────────────────────────────────────
# The delete-range rule
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("_name", "model", "markdown"), SCENARIOS, ids=SCENARIO_IDS)
def test_no_delete_ends_on_the_newline_anchoring_a_boundary(
    _name: str, model: DocModel, markdown: str
) -> None:
    """The invariant, stated as a rule rather than as an expected integer.

    A deleteContentRange whose endIndex equals a Table/TableOfContents/
    SectionBreak's startIndex has, by definition, deleted the newline
    immediately before that element as its last index — which the API allows
    only when the element itself is deleted too, and an endIndex that stops at
    the element's startIndex never covers it.
    """
    doc = model.doc()
    requests, _target = _pass1(model, markdown)
    boundaries = _boundary_ranges(doc)

    for request in requests:
        if "deleteContentRange" not in request:
            continue
        rng = request["deleteContentRange"]["range"]
        for kind, boundary_start, boundary_end in boundaries:
            if rng["endIndex"] != boundary_start:
                continue
            covers_element = (
                rng["startIndex"] <= boundary_start and boundary_end <= rng["endIndex"]
            )
            assert covers_element, (
                f"delete [{rng['startIndex']}, {rng['endIndex']}) ends on the newline "
                f"anchoring the {kind} at {boundary_start} without deleting the {kind}"
            )


@pytest.mark.parametrize(("_name", "model", "markdown"), SCENARIOS, ids=SCENARIO_IDS)
def test_every_pass1_batch_is_accepted_by_the_api_rule_model(
    _name: str, model: DocModel, markdown: str
) -> None:
    """Stronger than the endIndex check: replay the batch against the rules.

    Applies the requests in the order build() sorted them, against the state
    each previous request left behind, so a range that only becomes illegal
    after an earlier delete shifted the document is caught too.
    """
    requests, _target = _pass1(model, markdown)
    model.apply(requests)  # raises InvalidDeleteRange if any rule is broken


def test_the_rule_model_rejects_an_untrimmed_delete() -> None:
    """Guard on the guard: the model must actually fail the thing it models.

    Without this, a model that accepted everything would make the two tests
    above pass vacuously.
    """
    model = DocModel([Para("Intro"), Para("Doomed"), SectionBreak(), Para("Alpha"), Para("")])
    # "Doomed" is [7, 14); 13 is the newline anchoring the section break at 14.
    untrimmed = [{"deleteContentRange": {"range": {"startIndex": 7, "endIndex": 14}}}]
    with pytest.raises(InvalidDeleteRange, match="anchors the sectionBreak"):
        model.apply(untrimmed)


def test_the_rule_model_rejects_deleting_the_body_terminal_newline() -> None:
    model = DocModel([Para("Alpha"), Para("")])
    with pytest.raises(InvalidDeleteRange, match="terminal newline"):
        model.apply([{"deleteContentRange": {"range": {"startIndex": 7, "endIndex": 8}}}])


# ─────────────────────────────────────────────────────────────────────────────
# Pass 2 — styling must land on the paragraph it was written for
# ─────────────────────────────────────────────────────────────────────────────

def _link_ranges(requests: List[dict]) -> List[dict]:
    return [
        r["updateTextStyle"]["range"]
        for r in requests
        if "updateTextStyle" in r and "link" in r["updateTextStyle"]["textStyle"]
    ]


@pytest.mark.parametrize(
    ("_name", "model", "markdown"),
    [s for s in SCENARIOS if "[Beta]" in s[2]],
    ids=[name for name, _m, markdown in SCENARIOS if "[Beta]" in markdown],
)
def test_pass2_link_lands_on_the_text_it_was_written_for(
    _name: str, model: DocModel, markdown: str
) -> None:
    """The defect, asserted on content instead of on indices.

    Pass 1 does not leave the document matching ``target`` node-for-node: a
    delete trimmed to protect an anchoring newline leaves an empty paragraph
    where a node used to be, and insertTable adds a newline of its own. Pairing
    the re-fetched document with ``target`` by position then shifts every later
    paragraph by a slot, and `[Beta](…)`'s link is written over the *previous*
    paragraph's first four characters — a perfectly valid range, applied to the
    wrong words, with nothing raised. This asserts the covered characters, so it
    fails on the corruption itself rather than on an arithmetic expectation.
    """
    requests, target = _pass1(model, markdown)
    after = model.apply(requests)

    second_pass = builder.build_span_style_requests(after.doc(), target)
    ranges = _link_ranges(second_pass)

    assert len(ranges) == 1, "expected exactly one link to be styled"
    covered = after.text_at(ranges[0]["startIndex"], ranges[0]["endIndex"])
    assert covered == "Beta"


def test_pass2_skips_a_paragraph_it_cannot_place_rather_than_guessing() -> None:
    """A target paragraph whose text isn't in the written doc gets no styling.

    Both halves matter: nothing is emitted for the unplaceable paragraph, and
    the *other* paragraphs are still styled correctly rather than being dragged
    out of alignment by it.
    """
    after = DocModel([Para("Intro"), Para("something else entirely"),
                      Para("Beta"), Para("")])
    target = parser.parse(
        "Intro\n\n[Gamma](https://example.com/g)\n\n[Beta](https://example.com/b)\n"
    )

    requests = builder.build_span_style_requests(after.doc(), target)
    covered = [
        after.text_at(r["startIndex"], r["endIndex"]) for r in _link_ranges(requests)
    ]
    assert covered == ["Beta"]

    unaligned = builder.unaligned_span_targets(after.doc(), target)
    assert [n.text for n in unaligned] == ["Gamma"]


def test_pass2_never_writes_a_range_past_the_paragraph_it_targets() -> None:
    """Last line of defence: a range may not cross into the next paragraph.

    Span offsets come from the target's span *texts*, so a paragraph whose
    document length disagrees with its text length (a smart chip, an inline
    object) could otherwise push a range into its neighbour. The alignment
    should make that unreachable; this checks the bound holds regardless.
    """
    after = DocModel([Para("Intro"), Para("Beta"), Para("Gamma"), Para("")])
    target = parser.parse("Intro\n\n[Beta](https://example.com)\n\nGamma\n")

    requests = builder.build_span_style_requests(after.doc(), target)
    paragraphs = [
        (e["startIndex"], e["endIndex"]) for e in after.doc()["body"]["content"]
    ]
    for rng in _link_ranges(requests):
        assert any(
            start <= rng["startIndex"] and rng["endIndex"] <= end - 1
            for start, end in paragraphs
        ), f"range {rng} is not contained in a single paragraph's text"


def test_pass2_drops_a_span_longer_than_the_paragraph_it_was_matched_to() -> None:
    """The bound above, exercised where it actually binds.

    ``test_pass2_never_writes_a_range_past_the_paragraph_it_targets`` cannot
    fail for the reason the clamp exists: it uses a document whose paragraph
    text equals the target's, so the range is naturally contained and removing
    the clamp changes nothing (verified by mutation — the whole suite still
    passed with the clamp deleted).

    The clamp is for the case the alignment is *supposed* to make unreachable:
    a target paragraph whose spans are longer than its own ``text``. That is
    reachable in principle — ``text`` is what the alignment matches on, while
    the offsets come from the spans — so it is built by hand here rather than
    through the markdown parser. Without the clamp the link range runs past
    this paragraph's newline and lands on the next paragraph's first
    characters, silently.

    Both halves are asserted: the range is not written, *and* the paragraph is
    reported. A clamp on its own would only convert corruption into a silent
    no-op, which is the same trade this PR's second commit was written to undo.
    """
    after = DocModel([Para("Intro"), Para("Beta"), Para("Gamma"), Para("")])
    target = [
        DocsParagraphNode(style="NORMAL_TEXT", text="Intro"),
        DocsParagraphNode(
            style="NORMAL_TEXT",
            text="Beta",  # aligns against the document's "Beta"
            # ...but the spans claim more characters than the paragraph holds.
            spans=[TextSpan(text="Beta and then some", link="https://example.com")],
        ),
        DocsParagraphNode(style="NORMAL_TEXT", text="Gamma"),
    ]

    requests = builder.build_span_style_requests(after.doc(), target)

    assert _link_ranges(requests) == [], (
        "an over-long span must be dropped, not written past its paragraph"
    )
    assert [n.text for n in builder.unaligned_span_targets(after.doc(), target)] == [
        "Beta"
    ], "a dropped span must be reported, not silently skipped"


# ─────────────────────────────────────────────────────────────────────────────
# The paragraph a trimmed delete leaves behind
# ─────────────────────────────────────────────────────────────────────────────

def test_trimmed_delete_normalizes_the_residue_out_of_the_outline() -> None:
    """Deleting a heading above a table must not leave an empty heading.

    namedStyleType lives on the paragraph, and the trim removes only the text —
    so without an explicit reset the residue stays an HEADING_2 and shows up in
    the document outline, and a tab-scoped pull renders it as a literal "## "
    line that re-parses into a real node.
    """
    model = DocModel([Para("Intro"), Para("Old heading", style="HEADING_2"),
                      Table([["A"]]), Para("Alpha"), Para("")])
    requests, _target = _pass1(model, "Intro\n\n| A |\n| --- |\n\nAlpha\n")

    # "Old heading" is [7, 19); the table is at 19.
    normalize = [
        i for i, r in enumerate(requests)
        if "updateParagraphStyle" in r
        and r["updateParagraphStyle"]["range"]["startIndex"] == 7
        and r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
    ]
    delete = [
        i for i, r in enumerate(requests)
        if "deleteContentRange" in r
        and r["deleteContentRange"]["range"]["startIndex"] == 7
    ]
    assert normalize and delete
    assert normalize[0] < delete[0], "the reset must run before the text is deleted"
    model.apply(requests)


def test_trimmed_delete_clears_the_residues_bullet() -> None:
    """Same for a bullet: an empty bullet is still a visible bullet."""
    model = DocModel([Para("Intro"), Para("doomed item", bullet=True), SectionBreak(),
                      Para("Alpha"), Para("")])
    requests, _target = _pass1(model, "Intro\n\nAlpha\n")

    bullets = [
        r for r in requests
        if "deleteParagraphBullets" in r
        and r["deleteParagraphBullets"]["range"]["startIndex"] == 7
    ]
    assert bullets, "the residue keeps its bullet unless it is explicitly cleared"
    assert requests.index(bullets[0]) < next(
        i for i, r in enumerate(requests) if "deleteContentRange" in r
    )
    model.apply(requests)


def test_an_untouched_residue_produces_no_requests_so_push_stays_idempotent() -> None:
    """The residue can't be removed, so pushing twice must not keep rewriting it.

    A second push sees the empty paragraph as a removal it cannot express; it
    must emit nothing at all rather than re-issuing the style reset forever.
    """
    model = DocModel([Para("Intro"), Para(""), SectionBreak(), Para("Alpha"), Para("")])
    requests, _target = _pass1(model, "Intro\n\nAlpha\n")
    assert requests == []


def test_an_unremovable_difference_is_still_reported_by_diff_summary() -> None:
    """...and the diff must keep reporting it, so push() can say so honestly.

    diff_summary() feeds the open-comment/checkbox safety gate, so it has to
    over-report rather than be filtered down to match build(). The mismatch is
    real; push() is what has to stop calling it "No changes detected".
    """
    model = DocModel([Para("Intro"), Para(""), SectionBreak(), Para("Alpha"), Para("")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n")
    entries, _unchanged = builder.diff_summary(current, target)
    # The pinned empty paragraph and the body's terminal one — neither is
    # deletable, both are honestly reported as removals.
    assert entries and all(e.kind == "remove" for e in entries)
    assert builder.build(current, target, model.end_index()) == []


def test_unappliable_removals_names_the_pinned_paragraph_only() -> None:
    """The bridge push() needs: which removals will survive this push.

    diff_summary() over-reports on purpose (it gates the comment/checkbox
    safety check), and it counts the body's own terminal paragraph, which
    markdown can never express. Warning on that would fire for every push of
    every document, so it has to be excluded by name rather than by luck.
    """
    model = DocModel([Para("Intro"), Para(""), SectionBreak(), Para("Alpha"), Para("")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n")

    entries, _unchanged = builder.diff_summary(current, target)
    unappliable = builder.unappliable_removals(current, target, model.end_index())

    assert len(entries) == 2  # the pinned paragraph and the body's last one
    assert len(unappliable) == 1
    # The pinned one is the empty paragraph at [7, 8), not the terminal one.
    assert unappliable[0].start_index == 7


def test_a_document_that_already_matches_has_no_unappliable_removals() -> None:
    """A synced doc must stay silent — its terminal paragraph is not a diff."""
    model = DocModel([Para("Intro"), Para("Alpha"), Para("")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n")
    assert builder.build(current, target, model.end_index()) == []
    assert builder.unappliable_removals(current, target, model.end_index()) == []


def test_a_removal_that_can_be_applied_is_not_reported_as_unappliable() -> None:
    """The paragraph the trim is about to empty is still deletable text — it
    only becomes unappliable on the *next* push, once it is empty."""
    model = DocModel([Para("Intro"), Para("Doomed"), SectionBreak(),
                      Para("Alpha"), Para("")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n")
    assert builder.unappliable_removals(current, target, model.end_index()) == []


# ─────────────────────────────────────────────────────────────────────────────
# Appending past the last node (#21)
# ─────────────────────────────────────────────────────────────────────────────

def test_appending_past_the_last_node_stays_inside_the_body() -> None:
    """Issue #21: an append aimed one index too far, rejected by the API.

    build()'s insert branch places an insert at ``current[i1 - 1].end_index``.
    When the insert is *past the last node*, that node is the body's final
    paragraph and its end_index IS ``doc_end_index`` — one past the last legal
    insert position, so the API answers "Index N must be less than the end
    index of the referenced segment" and rejects the whole batch.

    It survived because it is masked whenever the document ends with an empty
    paragraph: the diff then pairs the new text against that paragraph, so the
    opcode is a *replace* (which inserts at a start_index, always in range)
    rather than an insert. This document deliberately ends with a paragraph
    that has text, which is the ordinary shape of a Google Doc body.

    Asserted through DocModel so it is the API's rule that fails the test, not
    an arithmetic expectation.
    """
    model = DocModel([Para("Intro"), Para("Alpha")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n\nAppended\n")

    requests = builder.build(current, target, model.end_index())

    after = model.apply(requests)  # raises if the insert index is out of range
    assert [p.text for p in structure.parse(after.doc())] == [
        "Intro",
        "Alpha",
        "Appended",
    ]


def test_an_appended_heading_does_not_restyle_the_paragraph_above_it() -> None:
    """The tail insert's paragraph range must cover only the new paragraph.

    Writing ``"\\ntext"`` puts the paragraph one index later than the insert
    point. If the accompanying updateParagraphStyle keeps using the insert point
    as its start, its range begins on the *previous* paragraph's terminal
    newline — so Docs applies namedStyleType to both paragraphs and appending a
    heading silently turns the paragraph above it into a heading too.

    DocModel deliberately ignores styling requests (they move no indices), so
    this is asserted against the emitted range rather than the rebuilt document.
    """
    model = DocModel([Para("Intro"), Para("Alpha")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n\n## Appended\n")

    requests = builder.build(current, target, model.end_index())
    after = model.apply(requests)

    styles = [
        r["updateParagraphStyle"] for r in requests if "updateParagraphStyle" in r
    ]
    assert len(styles) == 1
    assert styles[0]["paragraphStyle"]["namedStyleType"] == "HEADING_2"

    appended = [p for p in structure.parse(after.doc()) if p.text == "Appended"]
    assert len(appended) == 1
    assert (styles[0]["range"]["startIndex"], styles[0]["range"]["endIndex"]) == (
        appended[0].start_index,
        appended[0].end_index,
    ), "the style range must be exactly the appended paragraph"


def test_appending_to_a_document_that_ends_with_a_blank_paragraph_still_works() -> None:
    """The masking case from #21 — must keep working, and must stay a replace.

    The trailing "" is the body's own terminal paragraph. Every Docs body has
    one and markdown cannot express it, so it is expected in the result rather
    than a sign the append went wrong.
    """
    model = DocModel([Para("Intro"), Para("Alpha"), Para("")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nAlpha\n\nAppended\n")

    after = model.apply(builder.build(current, target, model.end_index()))
    assert [p.text for p in structure.parse(after.doc())] == [
        "Intro",
        "Alpha",
        "Appended",
        "",
    ]


def test_a_mid_document_insert_still_lands_after_the_preceding_paragraph() -> None:
    """The clamp must not move a mid-document insert.

    ``c74bea2`` removed a ``- 1`` here deliberately: for an insert in the
    middle, ``current[i1 - 1].end_index`` is the first index of the *following*
    paragraph, which is where the new paragraph belongs. Clamping that too
    would put the new text inside the preceding paragraph.
    """
    model = DocModel([Para("Intro"), Para("Omega")])
    current = structure.parse(model.doc())
    target = parser.parse("Intro\n\nMiddle\n\nOmega\n")

    after = model.apply(builder.build(current, target, model.end_index()))
    assert [p.text for p in structure.parse(after.doc())] == [
        "Intro",
        "Middle",
        "Omega",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Whole-pipeline shape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("_name", "model", "markdown"), SCENARIOS, ids=SCENARIO_IDS)
def test_pass1_writes_every_target_paragraph_into_the_document(
    _name: str, model: DocModel, markdown: str
) -> None:
    """Whatever else pass 1 leaves behind, the markdown's paragraphs are all there."""
    requests, target = _pass1(model, markdown)
    after = model.apply(requests)
    written = after.paragraph_texts()
    for node in target:
        if hasattr(node, "text") and node.text:
            assert node.text in written, f"{node.text!r} missing from {written!r}"


@pytest.mark.parametrize(("_name", "model", "markdown"), SCENARIOS, ids=SCENARIO_IDS)
def test_pass1_leaves_nothing_but_empty_paragraphs_behind(
    _name: str, model: DocModel, markdown: str
) -> None:
    """Every extra paragraph pass 1 leaves is empty — which is what makes the
    pass-2 content alignment sound, since markdown can never produce an empty
    paragraph node for one to be confused with."""
    requests, target = _pass1(model, markdown)
    after = model.apply(requests)
    expected = [n.text for n in target if hasattr(n, "text")]
    extra = list(after.paragraph_texts())
    for text in expected:
        if text in extra:
            extra.remove(text)
    assert all(text == "" for text in extra), f"unexpected leftover paragraphs: {extra!r}"
