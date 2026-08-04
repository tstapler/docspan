"""Build Google Docs batchUpdate request lists from structural AST diffs."""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Iterator, List, Literal, Optional, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
    TableCell,
    TextSpan,
)
from docspan.backends.google_docs.heading_anchors import (
    _heading_texts_and_ids,
    heading_slug_to_id,
    is_anchor,
    is_heading_style,
    link_payload,
    slugify,
    slugify_all,
)

Node = Union[DocsParagraphNode, DocsTableNode]


@dataclass(frozen=True)
class Pass2Alignment:
    """Everything the three pass-2 consumers need, computed once per push.

    See DocsRequestBuilder.align() for why this is shared rather than recomputed:
    the recomputation sat inside pass 2's optimistic-concurrency window.
    """
    current: List[Node]
    pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]]
    unaligned: List[DocsParagraphNode]
    slug_to_id: dict
    known_ids: set


def _utf16_len(text: str) -> int:
    """Return the number of UTF-16 code units in text (surrogate pairs count as 2)."""
    return len(text.encode("utf-16-le")) // 2


def _body_content(doc: dict) -> list:
    """Return the body content list, handling tabs-based and legacy structures."""
    if "tabs" in doc and doc["tabs"]:
        body = doc["tabs"][0].get("documentTab", doc).get("body", {})
    elif "body" in doc:
        body = doc["body"]
    else:
        return []
    return body.get("content", [])


def _node_text(node: Node) -> str:
    """Human-readable text for a node, for DiffEntry/preview rendering.

    Tables have no single "text" — render cells as a pipe-joined grid so a
    table row that overlaps an open comment's quoted text can still be
    caught by CommentCrossReference (push_preview.find_high_risk_paragraphs)
    instead of being silently invisible to it.
    """
    if isinstance(node, DocsTableNode):
        return "\n".join(" | ".join(cell.text for cell in row) for row in node.rows)
    return node.text


def _node_style(node: Node) -> str:
    """Style label for a node, for DiffEntry/preview rendering."""
    if isinstance(node, DocsTableNode):
        return "TABLE"
    return node.style


def _node_is_native_checkbox(node: Node) -> bool:
    """Whether a node is a native BULLET_CHECKBOX glyph (paragraphs only)."""
    return isinstance(node, DocsParagraphNode) and node.is_native_checkbox


@dataclass
class DiffEntry:
    """One row of a human-oriented paragraph/table diff (see plan.md Story 1.2.1).

    Produced only for non-"unchanged" rows by DocsRequestBuilder.diff_summary().
    `current_is_native_checkbox` is copied from the current-side
    DocsParagraphNode.is_native_checkbox for "remove"/"change" entries only
    (an "add" entry has no current node, so it stays at its default False;
    a table entry is also always False, since checkboxes are a paragraph-only
    concept) — it feeds GlyphShapeCheck (push_preview.find_high_risk_paragraphs),
    never DocsRequestBuilder.build()'s equality/opcode logic.
    """
    kind: Literal["add", "remove", "change", "unchanged"]
    current_text: Optional[str]
    target_text: Optional[str]
    style: str
    current_is_native_checkbox: bool = False


class DocsRequestBuilder:
    """Diff two node ASTs and produce minimal Google Docs batchUpdate requests."""

    def _node_key(self, node: Node) -> Tuple:
        """Identity used by SequenceMatcher to align the two node sequences.

        **Text only.** Paragraph-level attributes — namedStyleType, bullet,
        nesting level — deliberately do NOT participate, because they are
        *editable in place*: `updateParagraphStyle`, `createParagraphBullets` and
        `deleteParagraphBullets` change them without touching the text.

        Including them made a restyle look like a different paragraph, so
        difflib reported `replace` and build() answered with delete-then-insert.
        Changing `## Sec` to `### Sec` therefore deleted the paragraph and
        retyped it — 5 requests, and any comment anchored to it destroyed —
        while the preview said `change 'Sec' -> 'Sec'`, identical text on both
        sides, which tells the reader nothing about what is about to happen.

        With text as the whole identity, a restyle is an `equal` opcode carrying
        a paragraph-attribute difference, which _make_style_update_requests
        turns into the two or three in-place requests it actually needs.

        Two paragraphs with the same text and different styles now align with
        each other. That is the point: they *are* the same paragraph, restyled.
        """
        if isinstance(node, DocsTableNode):
            return ("__table__", tuple(tuple(c.text for c in row) for row in node.rows))
        return ("__para__", node.text)

    def _opcodes(
        self,
        current: List[Node],
        target: List[Node],
    ) -> List[Tuple[Literal["replace", "delete", "insert", "equal"], int, int, int, int]]:
        """Build the single difflib.SequenceMatcher opcode list shared by
        build() and diff_summary().

        Both methods interpret the same opcodes differently on purpose
        (build() does whole-range replace, diff_summary() zips pairwise) —
        but they must see identical opcodes, since push()'s safety gate
        (high_risk, derived from diff_summary()'s classification) and the
        actual write (derived from build()'s classification) must never
        drift apart. This is the one place current_keys/target_keys/
        SequenceMatcher get constructed.
        """
        current_keys = [self._node_key(n) for n in current]
        target_keys = [self._node_key(n) for n in target]
        matcher = difflib.SequenceMatcher(None, current_keys, target_keys, autojunk=False)
        return matcher.get_opcodes()

    def diff_summary(
        self,
        current: List[Node],
        target: List[Node],
    ) -> Tuple[List[DiffEntry], int]:
        """
        Produce a human-oriented diff summary of current vs. target nodes.

        Reuses the same _opcodes() machinery build() uses (a second pass over
        matcher.get_opcodes(), not a separate diff algorithm), per plan.md
        Story 1.2.1. Table nodes are included (not skipped) so
        CommentCrossReference/GlyphShapeCheck still see any paragraph *or*
        table row about to be deleted/replaced.

        Args:
            current: Nodes parsed from the live Google Doc.
            target:  Nodes parsed from the local markdown file.

        Returns:
            (entries, unchanged_count) — entries contains only non-"unchanged"
            rows; unchanged_count is a plain int for the summary line.
        """
        entries: List[DiffEntry] = []
        unchanged_count = 0

        for tag, i1, i2, j1, j2 in self._opcodes(current, target):
            if tag == "equal":
                # "equal" means equal *text* — _node_key is text-only, so a
                # restyle (heading level, bullet on/off, nesting) lands here
                # rather than as a replace. It is still a change the user asked
                # for and push() will write, so it has to be reported; counting
                # it as unchanged would make --dry-run claim nothing happens
                # while push emits updateParagraphStyle.
                for ci, ti in zip(range(i1, i2), range(j1, j2)):
                    if self._restyles(current[ci], target[ti]):
                        entries.append(
                            DiffEntry(
                                kind="change",
                                current_text=_node_text(current[ci]),
                                target_text=_node_text(target[ti]),
                                style=_node_style(target[ti]),
                                current_is_native_checkbox=_node_is_native_checkbox(
                                    current[ci]
                                ),
                            )
                        )
                    else:
                        unchanged_count += 1

            elif tag == "delete":
                for node in current[i1:i2]:
                    entries.append(
                        DiffEntry(
                            kind="remove",
                            current_text=_node_text(node),
                            target_text=None,
                            style=_node_style(node),
                            current_is_native_checkbox=_node_is_native_checkbox(node),
                        )
                    )

            elif tag == "insert":
                for node in target[j1:j2]:
                    entries.append(
                        DiffEntry(
                            kind="add",
                            current_text=None,
                            target_text=_node_text(node),
                            style=_node_style(node),
                        )
                    )

            elif tag == "replace":
                cur_slice = current[i1:i2]
                tgt_slice = target[j1:j2]
                common = min(len(cur_slice), len(tgt_slice))
                for cur_node, tgt_node in zip(cur_slice[:common], tgt_slice[:common]):
                    entries.append(
                        DiffEntry(
                            kind="change",
                            current_text=_node_text(cur_node),
                            target_text=_node_text(tgt_node),
                            style=_node_style(cur_node),
                            current_is_native_checkbox=_node_is_native_checkbox(cur_node),
                        )
                    )
                # Length mismatch (e.g. one checklist line split into two) —
                # treat the leftovers as plain adds/removes rather than
                # raising or truncating silently.
                for extra_cur in cur_slice[common:]:
                    entries.append(
                        DiffEntry(
                            kind="remove",
                            current_text=_node_text(extra_cur),
                            target_text=None,
                            style=_node_style(extra_cur),
                            current_is_native_checkbox=_node_is_native_checkbox(extra_cur),
                        )
                    )
                for extra_tgt in tgt_slice[common:]:
                    entries.append(
                        DiffEntry(
                            kind="add",
                            current_text=None,
                            target_text=_node_text(extra_tgt),
                            style=_node_style(extra_tgt),
                        )
                    )

        return entries, unchanged_count

    def build(
        self,
        current: List[Node],
        target: List[Node],
        doc_end_index: int,
        tab_id: Optional[str] = None,
    ) -> List[dict]:
        """
        Build a minimal list of batchUpdate request dicts (pass 1).

        Tables are inserted empty here; call build_table_fill_requests() after re-fetching
        the document to populate their cells (pass 2).

        Args:
            current: Nodes parsed from the live Google Doc.
            target:  Nodes parsed from the local markdown file.
            doc_end_index: endIndex of the last body element (used to protect the terminal
                newline that Docs API requires).
            tab_id: When the doc has tabs, the tabId every request's
                location/range should target (from tabs.resolve_document_tab).
                None for legacy (non-tabbed) docs — Location/Range's tabId
                field is only meaningful on a tabbed doc.

        Returns:
            List of request dicts sorted by descending startIndex (write-backwards).
        """
        opcodes = self._opcodes(current, target)

        # (anchor_index, requests) — the requests for one node or one insert
        # group, and the document index they are all written against.
        #
        # Ordering rule, stated once here because getting it wrong is silent:
        # groups are applied highest-anchor-first (so every edit runs against
        # coordinates nothing has shifted yet), and within a group in emission
        # order (so an insert precedes the styling of what it inserted).
        #
        # This used to be one flat sort over every request's own startIndex,
        # which is only equivalent while every request in a group shares the
        # anchor. The append-past-the-last-node case broke that: its paragraph
        # sits one index *after* the insert point, so its updateParagraphStyle
        # carried a higher startIndex than the insertText it depends on and
        # sorted ahead of it — a style request against a range that did not
        # exist yet. The anchor is now carried explicitly instead of inferred.
        groups: List[Tuple[int, List[dict]]] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for ci, ti in zip(range(i1, i2), range(j1, j2)):
                    requests = self._make_style_update_requests(current[ci], target[ti])
                    if requests:
                        groups.append((current[ci].start_index, requests))

            elif tag == "delete":
                for node in current[i1:i2]:
                    requests = self._make_delete_requests([node], doc_end_index)
                    if requests:
                        groups.append((node.start_index, requests))

            elif tag == "insert":
                previous = current[i1 - 1] if i1 > 0 else None
                insert_at = previous.end_index if previous else 1
                # `previous.end_index` is normally the first index of the
                # following paragraph, which is exactly where a new paragraph
                # belongs. Two cases have no following paragraph there, and in
                # both the index one step back is an existing newline that the
                # new paragraph has to be written in *front* of:
                #
                # 1. Appending past the last node. `previous` is the body's
                #    final paragraph, so its end_index IS doc_end_index — one
                #    past the last index an insert may name ("Index N must be
                #    less than the end index of the referenced segment").
                #
                # 2. Inserting directly before a Table, TableOfContents or
                #    SectionBreak (#22). `previous.end_index` is the boundary
                #    element's own start index. An insert there is not inside
                #    any paragraph — "Text must be inserted inside the bounds
                #    of an existing Paragraph" (InsertTextRequest) — and for a
                #    table it lands in the first cell instead of the body.
                #    DocsStructureParser drops those elements, so
                #    precedes_structural_element is the only trace of them.
                #
                # See _make_insert_requests(before_newline=...) for why the
                # newline then has to move to the front of the inserted text.
                at_body_end = insert_at >= doc_end_index
                before_boundary = (
                    isinstance(previous, DocsParagraphNode)
                    and previous.precedes_structural_element
                )
                if at_body_end:
                    insert_at = doc_end_index - 1
                elif before_boundary:
                    insert_at -= 1
                requests = self._make_insert_requests(
                    target[j1:j2],
                    insert_at,
                    before_newline=at_body_end or before_boundary,
                )
                if requests:
                    groups.append((insert_at, requests))

            elif tag == "replace":
                delete_start = current[i1].start_index
                for node in current[i1:i2]:
                    requests = self._make_delete_requests([node], doc_end_index)
                    if requests:
                        groups.append((node.start_index, requests))
                requests = self._make_insert_requests(target[j1:j2], delete_start)
                if requests:
                    # Same anchor as the first deleted node's group, and emitted
                    # after it, so the delete runs before the insert that
                    # replaces it.
                    groups.append((delete_start, requests))

        # Stable, so groups sharing an anchor keep the order above.
        groups.sort(key=lambda group: group[0], reverse=True)
        all_requests = [request for _anchor, requests in groups for request in requests]
        self._inject_tab_id(all_requests, tab_id)
        return all_requests

    @staticmethod
    def _delete_bounds(node: Node, doc_end_index: int) -> Tuple[int, int, bool]:
        """The range a node's deleteContentRange may actually cover, and whether it was trimmed.

        Single source of truth for the two undeletable-newline rules described
        on _make_delete_requests.

        It briefly had a second caller, `unappliable_removals()`, which named the
        paragraphs whose delete request this method drops. That method is gone:
        `projection.project()` now removes empty paragraphs from both sides of
        the diff, and an empty paragraph was the only node whose range could trim
        to nothing (verified exhaustively over every document shape up to four
        paragraphs), so nothing is left for it to report.
        """
        start = node.start_index
        end = node.end_index
        trimmed = False
        if isinstance(node, DocsParagraphNode) and node.precedes_structural_element:
            end -= 1
            trimmed = True
        if end >= doc_end_index:
            end = doc_end_index - 1
            trimmed = True
        return start, end, trimmed

    # ──────────────────────────────────────────────
    # Pass 2 — fill table cells from a re-fetched doc
    # ──────────────────────────────────────────────

    def build_table_fill_requests(self, doc: dict, target: List[Node]) -> List[dict]:
        """
        Emit insertText requests to fill empty tables created by a prior push (pass 1).

        Matches the empty tables in the re-fetched document (in document order) to the
        DocsTableNodes in ``target`` (in order), reading real cell indices from ``doc`` so
        no index prediction is required.
        """
        target_tables = [n for n in target if isinstance(n, DocsTableNode)]
        if not target_tables:
            return []

        inserts: List[Tuple[int, str]] = []
        ti = 0
        for element in _body_content(doc):
            table = element.get("table")
            if table is None:
                continue
            if not self._table_is_empty(table):
                continue  # already populated (or a pre-existing content table)
            if ti >= len(target_tables):
                break
            inserts.extend(self._cell_inserts(table, target_tables[ti]))
            ti += 1

        # Insert highest index first so earlier inserts don't shift later cell indices.
        inserts.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"insertText": {"location": {"index": idx}, "text": text}}
            for idx, text in inserts
            if text
        ]

    def build_table_cell_span_requests(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[dict]:
        """Emit updateTextStyle for inline styling inside table cells.

        Pass 2 only ever walked paragraphs, so every mark inside a cell was dropped
        — and an internal `#anchor` cross-reference written in a cell rendered as
        dead text while the identical reference in a paragraph resolved. Nothing
        reported it, because the styling was lost one layer earlier, when cells were
        plain `str`.

        Runs against the re-fetched document, like the paragraph path, because a
        cell's real indices only exist once pass 1 has written it and heading ids
        only exist once the headings do.

        Cells are located, not predicted: the target cell's text is searched for
        inside the live cell's text and the offset taken from where it is found. A
        cell whose text is not there gets no requests at all — see
        `unplaced_table_cells` — rather than a range aimed at whatever happened to
        sit at that ordinal.
        """
        target_tables = [n for n in target if isinstance(n, DocsTableNode)]
        if not any(cell.styled for t in target_tables for row in t.rows for cell in row):
            return []

        aligned = self._aligned(doc, target, alignment)
        requests: List[dict] = []
        for table, tnode in self._paired_tables(doc, target_tables):
            for live, cell in self._paired_cells(table, tnode):
                if not cell.styled:
                    continue
                placed = self._cell_placement(live, cell)
                if placed is None:
                    continue
                start, limit = placed
                requests.extend(self._span_requests_in(
                    cell.spans, start, limit, aligned.slug_to_id, aligned.known_ids,
                ))
        return requests

    def unplaced_table_cells(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[str]:
        """Styled cells pass 2 could not place, so push can say so out loud.

        The loud half of the same trade `unaligned_span_targets` makes for
        paragraphs: refusing to guess is only safe if the refusal is reported.

        Walks the **target** grid outermost and looks the live cell up, rather than
        walking the live side and reconciling afterwards. Every styled target cell is
        then visited exactly once, so nothing needs deduplicating — and an earlier
        version deduped on `cell.text`, collapsing two genuinely distinct affected
        cells into one entry and understating the count in a message that prints it.

        Scope of that, measured rather than assumed: the dedup guarded only the
        orphan sweep, so it misreported only when the target and live *shapes*
        differed. Two duplicate-text cells that both had live counterparts were
        already counted exactly. So it was narrower than "the normal case", which is
        how an earlier version of this docstring put it.

        The inversion also removes a quadratic `text not in missed` scan from the
        window between pass 2's `get_document` and its `batch_update`. Measured over
        6400 styled unplaceable cells: 238 ms before, 10 ms after — but only with
        *distinct* cell texts. With one repeated text the old scan was ~12 ms, since
        the list it scanned never grew. Both numbers are real and they bound the win
        from either side. That window is the one `align()` **narrows** by 43% (its
        docstring calls itself a correctness lever wearing a performance hat), because
        a concurrent edit inside it costs the user a revisionId conflict on a document
        pass 1 has already changed.
        """
        target_tables = [n for n in target if isinstance(n, DocsTableNode)]
        if not target_tables:
            return []
        live_tables = self._live_tables(doc, len(target_tables))
        missed: List[str] = []
        for position, tnode in enumerate(target_tables):
            table = live_tables[position] if position < len(live_tables) else None
            rows = table.get("tableRows", []) if table else []
            for r, row in enumerate(tnode.rows):
                live_cells = rows[r].get("tableCells", []) if r < len(rows) else []
                for c, cell in enumerate(row):
                    if not cell.styled:
                        continue
                    live = live_cells[c] if c < len(live_cells) else None
                    placed = self._cell_placement(live, cell) if live is not None else None
                    if placed is None:
                        missed.append(cell.text)
                        continue
                    # A cell can place and still lose its styling: _span_requests_in
                    # stops at the first span that would cross the cell's bound and
                    # emits nothing for it or anything after. Reporting only the
                    # placement failure would leave that a silent partial
                    # application — the case unaligned_span_targets calls "the
                    # failure mode this whole pass exists to remove" and closes for
                    # paragraphs via _spans_overflow.
                    start, limit = placed
                    if limit is not None and start + sum(
                        _utf16_len(span.text) for span in cell.spans
                    ) > limit:
                        missed.append(cell.text)
        return missed

    @staticmethod
    def _live_tables(doc: dict, limit: int) -> List[dict]:
        """The first `limit` tables in body order — the pairing both cell passes use."""
        tables: List[dict] = []
        if limit <= 0:
            return tables
        for element in _body_content(doc):
            table = element.get("table")
            if table is not None:
                tables.append(table)
                if len(tables) >= limit:
                    break
        return tables

    @staticmethod
    def _paired_tables(
        doc: dict, target_tables: List[DocsTableNode]
    ) -> Iterator[Tuple[dict, DocsTableNode]]:
        """Live tables paired with target tables, in document order.

        Order is the only correspondence available: a table has no id, and
        `_align_for_styling` keys a table on its whole cell grid, so a table whose
        cells changed does not align at all.

        This is **not** the same pairing `build_table_fill_requests` uses — that one
        advances only past *empty* live tables, so it pairs the Nth empty table with
        the Nth target table. This pairs the Nth table outright, because by the time
        styling runs pass 1 has filled them and "empty" no longer identifies them.

        Raw body position is weaker than the content alignment
        `build_span_style_requests` uses for paragraphs, and the gap is real: if a
        concurrent edit adds a table between pass 1 and pass 2 the counts shift and a
        stale table can be styled. `_cell_placement`'s text search catches that only
        when the two tables' cell texts differ, and headers like "Status" or "Owner"
        repeat constantly. Narrow, but the same window `_cell_placement` bails on.
        """
        index = 0
        for element in _body_content(doc):
            table = element.get("table")
            if table is None:
                continue
            if index >= len(target_tables):
                return
            yield table, target_tables[index]
            index += 1

    @staticmethod
    def _paired_cells(
        table: dict, tnode: DocsTableNode
    ) -> Iterator[Tuple[dict, TableCell]]:
        """Live cell dicts paired with target cells, by row and column."""
        for r, row in enumerate(table.get("tableRows", [])):
            if r >= len(tnode.rows):
                return
            for c, cell in enumerate(row.get("tableCells", [])):
                if c >= len(tnode.rows[r]):
                    break
                yield cell, tnode.rows[r][c]

    @staticmethod
    def _cell_placement(live: dict, cell: TableCell) -> Optional[Tuple[int, Optional[int]]]:
        """Where `cell`'s text starts in the live document, and its hard upper bound.

        Returns None when the text is not in the cell's first content paragraph, so
        the caller emits nothing. Two reasons that happens and both are unsafe to
        guess through: the cell holds different text (a concurrent edit between
        pass 1 and pass 2), or its content is spread over more than one paragraph.

        The offset is *searched for* rather than assumed to be the paragraph's
        start, because `TableCell.text` is stripped on both sides while the live
        paragraph keeps its whitespace — assuming the start would shift every range
        in the cell by the width of the leading whitespace.
        """
        content = live.get("content", [])
        if not content:
            return None
        paragraph = content[0].get("paragraph")
        start_index = content[0].get("startIndex")
        end_index = content[0].get("endIndex")
        if paragraph is None or start_index is None or end_index is None:
            return None
        elements = paragraph.get("elements", [])
        if any(pe.get("textRun") is None for pe in elements):
            # An inlineObjectElement, footnoteReference, person chip, richLink,
            # equation or page break carries index width but contributes nothing to
            # the joined text, so `find`'s offset is no longer the document distance
            # from `startIndex`. Measured on an image before "A1\n": the range came
            # out [30,32) where the text sits at [31,33) — it styled the image plus
            # the first character, and reported nothing.
            #
            # Deliberately unconditional on *position*, not just on a leading
            # element. A trailing footnote leaves the offset correct and is declined
            # anyway, because distinguishing the safe placements means computing the
            # offset from each element's own `startIndex` — the proper fix, and index
            # arithmetic that needs replay verification rather than reasoning.
            #
            # `_parse_cell` supports `person` chips, so an @-mention in an "Owner"
            # column lands here. Its styling was already lost before this bail (the
            # chip's display name is in `.text` but not in the joined runs, so `find`
            # failed anyway); what the bail adds is that the caller now reports it.
            return None
        text = "".join(pe["textRun"].get("content", "") for pe in elements)
        offset = text.find(cell.text)
        if offset < 0:
            return None
        # end_index - 1 is the paragraph's newline; text lives strictly before it.
        return start_index + _utf16_len(text[:offset]), end_index - 1

    def align(self, doc: dict, target: List[Node]) -> "Pass2Alignment":
        """Parse ``doc``, pair it with ``target``, and resolve anchors — once.

        The three pass-2 consumers below each need the same alignment, and each
        used to compute its own: three full `DocsStructureParser.parse` calls and
        three full `SequenceMatcher` runs per push, two of every result thrown
        away. Measured on a 5000-paragraph document, that put **+43%** on the
        window between pass 2's `get_document` and its `batch_update` — the
        window in which a concurrent edit invalidates the revisionId and costs
        the user a conflict on a document pass 1 has already changed. So this is
        a correctness lever wearing a performance hat.

        `push()` calls this once and hands the result to all three. Each still
        accepts ``alignment=None`` and computes its own, so a caller with only a
        document and a target — every existing test — keeps working.
        """
        current, pairs, unaligned, heading_pairs = self._align_for_styling(doc, target)
        slug_to_id, known_ids = self._anchor_resolution(current, target, heading_pairs)
        return Pass2Alignment(
            current=current,
            pairs=pairs,
            unaligned=unaligned,
            slug_to_id=slug_to_id,
            known_ids=known_ids,
        )

    def _aligned(
        self, doc: dict, target: List[Node], alignment: Optional["Pass2Alignment"]
    ) -> "Pass2Alignment":
        return alignment if alignment is not None else self.align(doc, target)

    def build_span_style_requests(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[dict]:
        """
        Emit updateTextStyle requests for inline styling (links/bold/italic/monospace).

        Runs against the re-fetched document so ranges use real post-insert indices.
        Nodes are paired by an order-preserving *content* alignment of the re-fetched
        document against ``target`` (see _align_for_styling), never by raw position.
        """
        if not any(isinstance(n, DocsParagraphNode) and n.spans for n in target):
            return []

        aligned = self._aligned(doc, target, alignment)
        pairs, slug_to_id, known_ids = aligned.pairs, aligned.slug_to_id, aligned.known_ids
        requests: List[dict] = []
        for cnode, tnode in pairs:
            requests.extend(self._span_style_requests(tnode, cnode, slug_to_id, known_ids))
        return requests

    def unaligned_span_targets(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[DocsParagraphNode]:
        """Target paragraphs carrying inline styling that pass 2 could not place.

        Pass 2 refuses to guess: a target paragraph whose text it cannot find,
        in order, in the re-fetched document gets no updateTextStyle at all
        rather than one aimed at whatever paragraph happened to sit at the same
        ordinal. That is the safe half of the trade; this method is the loud
        half — push() reports these so "your links silently didn't apply" can't
        pass for success. Empty list is the normal case.

        Two distinct causes, both reported here because they have the same
        consequence for the reader of the document:

        1. The paragraph could not be aligned at all (its text is not in the
           written document, in order).
        2. The paragraph aligned, but its spans do not fit inside it
           (_spans_overflow) — so _span_style_requests clamps and drops them.
           Reporting only case 1 would leave case 2 as a silent partial
           application, which is the failure mode this whole pass exists to
           remove.
        """
        if not any(isinstance(n, DocsParagraphNode) and n.spans for n in target):
            return []
        aligned = self._aligned(doc, target, alignment)
        pairs, unaligned = aligned.pairs, aligned.unaligned
        overflowed = [
            tnode for cnode, tnode in pairs if self._spans_overflow(tnode, cnode)
        ]
        if not overflowed:
            return unaligned
        # Preserve target order; a node can only be in one of the two lists.
        reported = {id(node) for node in unaligned} | {id(node) for node in overflowed}
        return [
            node
            for node in target
            if isinstance(node, DocsParagraphNode) and id(node) in reported
        ]

    def unresolved_anchor_links(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[str]:
        """Internal anchors pass 2 could not point at a heading, in use order.

        The loud half of _span_style_requests' refusal to write a link with no
        target, and push()'s **primary** report — not a residue catcher. Every
        cause lands here: the author typo'd the anchor, the heading was renamed
        or deleted, or the document reports the heading with no `headingId`.
        heading_anchors.unresolved_anchors() covers the same ground for
        ``--dry-run`` only; push() does not call it and does not gate on it.

        Reported rather than raised: aborting would discard every other
        paragraph's inline styling for one bad link, and the next push would
        abort identically.

        **Table cells are walked too.** They hold spans now, so a dead anchor can
        live in one — and it used to be reported by nothing at all: the guard below
        tested only paragraphs, and `_align_for_styling` skips tables, so a cell
        anchor never reached `pairs`. That left the exact condition this whole pass
        exists to remove — a reference rendered as dead text in the document with a
        green tick over it. Cells are read straight off the target rather than from
        `pairs`, because a table aligns on its whole cell grid and so does not pair
        at all once any cell changed; an anchor that names no heading is unresolved
        whether or not its table could be located.
        """
        styled_paragraph = any(isinstance(n, DocsParagraphNode) and n.spans for n in target)
        styled_cell = any(cell.styled for n in target if isinstance(n, DocsTableNode)
                          for row in n.rows for cell in row)
        if not styled_paragraph and not styled_cell:
            return []
        aligned = self._aligned(doc, target, alignment)
        pairs, slug_to_id, known_ids = aligned.pairs, aligned.slug_to_id, aligned.known_ids
        unresolved: List[str] = []

        def collect(spans: List[TextSpan]) -> None:
            for span in spans:
                if not span.link or not is_anchor(span.link):
                    continue
                if link_payload(span.link, slug_to_id, known_ids) is None:
                    if span.link not in unresolved:
                        unresolved.append(span.link)

        for _cnode, tnode in pairs:
            collect(tnode.spans)
        for node in target:
            if isinstance(node, DocsTableNode):
                for row in node.rows:
                    for cell in row:
                        collect(cell.spans)
        return unresolved

    @staticmethod
    def _spans_overflow(node: DocsParagraphNode, placement: DocsParagraphNode) -> bool:
        """True when ``node``'s spans cannot all fit inside ``placement``'s text.

        _span_style_requests walks the spans with a cumulative offset and stops
        at the first one that would cross ``placement``'s trailing newline, so
        "some span gets dropped" is exactly "the spans are longer in total than
        the paragraph can hold". Checked as a total here rather than by
        re-walking, so the two can't drift.
        """
        if not node.spans or not placement.end_index:
            return False
        available = (placement.end_index - 1) - placement.start_index
        return sum(_utf16_len(span.text) for span in node.spans) > available

    @staticmethod
    def _alignment_key(node: Node) -> Tuple:
        """Identity used to align a re-fetched document against ``target`` in pass 2.

        Deliberately coarser than _node_key():

        * A table's *cells* are still empty at this point (pass 1 emits an
          empty insertTable; pass 2 fills it), so tables can only align on
          being-a-table.
        * namedStyleType and bullet are excluded because pass 2 applies *text*
          styling only — a paragraph whose paragraph-level style didn't land is
          still the right paragraph to put a link on.

        Text is therefore the whole identity of a paragraph here. That is what
        makes the alignment safe against the residue nodes pass 1 leaves
        behind: every one of them is an *empty* paragraph (a delete trimmed to
        protect an anchoring newline, or the implicit newline insertTable
        creates). MarkdownToParagraphParser *can* now emit an empty-text node — a
        blank line inside a fenced code block — so this holds because backend.py
        hands pass 2 already-projected nodes, not because the parser cannot
        produce one. The protection is projection, not the parser,
        so a residue can never collide with a real target paragraph.
        """
        if isinstance(node, DocsTableNode):
            return ("__table__",)
        return ("__para__", node.text)

    def _align_for_styling(
        self, doc: dict, target: List[Node]
    ) -> Tuple[
        List[Node],
        List[Tuple[DocsParagraphNode, DocsParagraphNode]],
        List[DocsParagraphNode],
        List[Tuple[DocsParagraphNode, DocsParagraphNode]],
    ]:
        """Pair re-fetched document nodes with ``target`` nodes for pass-2 styling.

        Returns ``(current, pairs, unaligned, heading_pairs)`` — the parsed
        document, pairs of (document node, target node) that are safe to style,
        the target paragraphs with spans that could not be paired, and the same
        pairing restricted to target paragraphs that are headings.

        ``current`` is returned rather than re-parsed by callers because parsing
        a large document twice per push to learn the same thing is pure cost.

        ``heading_pairs`` is what lets an anchor be resolved correctly. The slug
        an author wrote `#foo` against is a fact about *their markdown* — its
        duplicate suffix depends on the headings before it **in the markdown** —
        while the `headingId` to link to is a fact about the *document*. Pairing
        the two here is the only place both are known. Deriving slugs from the
        document instead goes wrong in two ways that produce a wrong link rather
        than an error: a heading the document holds but the markdown does not
        shifts every later duplicate suffix, and a Docs `TITLE`/`SUBTITLE` is not
        a `HEADING_*` at all, so a doc title could never be an anchor target
        even though `projection.project()` and `#`/`##` both treat it as one.

        Why not zip(): pass 1 does **not** guarantee the re-fetched document
        matches ``target`` node-for-node. It leaves an empty paragraph behind
        whenever a delete had to be trimmed to preserve the newline anchoring a
        Table/TableOfContents/SectionBreak (_make_delete_requests), and
        insertTable implicitly creates a newline of its own. Either one shifts
        every later node by a slot, and positional pairing then applies each
        paragraph's styling to its neighbour — silently, since a wrong range is
        still a valid range. That converts an atomically-rejected batch into a
        written-but-corrupted document, which is strictly worse.

        Why difflib and not a search-forward matcher: an earlier
        text-equality aligner scanned ahead for the first paragraph with
        matching text and so drifted permanently once anything didn't match
        byte-for-byte (a duplicated line matched the earlier copy; an unmatched
        line consumed the wrong successor). difflib.SequenceMatcher instead
        computes one global, order-preserving alignment over the whole
        sequence, so duplicates stay in their original relative order and an
        unmatched node is skipped rather than absorbed. Only nodes inside
        "equal" runs are paired at all: an "equal" run means the document's
        paragraph text is byte-identical to the target's, which is exactly the
        precondition the span offsets in _span_style_requests assume. Anything
        difflib reports as replace/insert/delete is ambiguous, so it is left
        unstyled and reported by unaligned_span_targets().
        """
        current = DocsStructureParser().parse(doc)
        matcher = difflib.SequenceMatcher(
            None,
            [self._alignment_key(n) for n in current],
            [self._alignment_key(n) for n in target],
            autojunk=False,
        )

        pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]] = []
        heading_pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]] = []
        aligned_target_indices = set()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                continue
            for ci, ti in zip(range(i1, i2), range(j1, j2)):
                cnode, tnode = current[ci], target[ti]
                if isinstance(cnode, DocsTableNode) or isinstance(tnode, DocsTableNode):
                    continue
                aligned_target_indices.add(ti)
                if tnode.spans:
                    pairs.append((cnode, tnode))
                if is_heading_style(tnode.style):
                    heading_pairs.append((cnode, tnode))

        unaligned = [
            node
            for index, node in enumerate(target)
            if isinstance(node, DocsParagraphNode)
            and node.spans
            and index not in aligned_target_indices
        ]
        return current, pairs, unaligned, heading_pairs

    def _anchor_resolution(
        self, current: List[Node], target: List[Node],
        heading_pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]],
    ) -> Tuple[dict, set]:
        """(slug -> headingId, every headingId the document reports).

        Slugs are computed over the **target's** headings in target order, so a
        duplicate suffix means what it means in the author's markdown, and each
        is mapped to the id of the document paragraph it aligned with. The
        document's own headings are folded in underneath for anchors that point
        at a heading this push does not contain (a partial push), and the id set
        makes a bare `#h.abc123` from an earlier pull resolve verbatim.

        A slug the markdown owns but that resolves to no id is **deleted**, not
        left holding the document-derived value. This is the difference between
        a reported dead anchor and a silent link to the wrong heading, and the
        wrong-heading case is reachable two ways:

        * the target heading landed outside a difflib `equal` run, so it has no
          pair. `## Overview / ## Overview / ## Details` against a document
          holding only `Overview, Details`: difflib pairs the markdown's *second*
          Overview with the document's only one, leaving the *first* unpaired.
          The seed's `overview -> h.first` therefore survives — so `#overview`
          (the author's first) and `#overview-1` (the second, correctly paired to
          the same paragraph) both resolved to `h.first`, and two anchors that
          name different headings pointed at one. Measured pre-fix:
          `{'overview': 'h.first', 'details': 'h.details', 'overview-1':
          'h.first'}`. It is the *unpaired* anchor whose entry is stale, not the
          suffixed one; the seed can never produce the key `overview-1` at all,
          since the document's lone Overview slugs without a suffix;
        * the paired document paragraph reports no `headingId`, so a *different*
          document heading whose own literal text slugs to `intro-1` keeps that
          key and captures an anchor that meant "my second `## Intro`".

        Neither was reported: `unaligned_span_targets` filters on `node.spans`
        and a plain heading has none, and `unresolved_anchor_links` only sees
        anchors that resolve to *nothing* — a wrong resolution is still a
        resolution. Both now surface as dead anchors.

        **An inherited entry survives three tests, all facts about the document.**
        Each was added because the previous set let a wrong link through:

        1. its *base* slug must equal the target heading's base slug. Not raw text
           — comparing text dropped every slug-preserving edit (`Rollout Plan`
           renamed to `Rollout plan`, a trailing space, added punctuation), 24
           links lost against 0 gained. Not the whole slug either: markdown
           `## Intro 1` (base `intro-1`) must not inherit the document's `intro-1`,
           which is the second of its two `Intro` headings (base `intro`).
        2. the document must hold exactly **one** heading with that base slug.
           Base equality alone is not enough: `Q&A` and `QA` both slug to `qa`, so
           markdown `## QA` whose own paragraph reports no id inherited `Q&A`'s
           id and the reader landed in the wrong section — green ✓, no warning.
           When two document headings share a base there is no way to tell which
           one the author meant, so this refuses rather than guesses.
        3. its id must not already be claimed by a heading this push paired, or
           two anchors naming different headings resolve to one id. This narrows
           one shape of that; it does not establish the general invariant, because
           a stale document-only entry colliding with a paired id is never
           inspected here.

        What all three preserve: a document holding one `Intro` and markdown
        holding one `## Intro` still resolves when difflib leaves the heading
        unpaired. Without that, a heading plainly present got a dead-anchor
        warning `unaligned_span_targets` could not even explain, since it filters
        on `node.spans` and a heading has none.
        """
        slug_to_id = dict(heading_slug_to_id(current))  # document-only headings
        paired_document_node = {id(tnode): cnode for cnode, tnode in heading_pairs}
        target_headings = [
            node for node in target
            if isinstance(node, DocsParagraphNode) and is_heading_style(node.style)
        ]
        # slug -> the text of the document heading that produced it, so an
        # inherited entry can be checked against what the author actually wrote.
        document_pairs = _heading_texts_and_ids(current)
        # slug -> the base slug of the document heading that produced it, plus how
        # many document headings share each base. Both are needed; see the
        # docstring's three tests.
        document_slug_base = {
            slug: slugify(text)
            for slug, (text, heading_id) in zip(
                slugify_all(text for text, _ in document_pairs), document_pairs
            )
            if heading_id
        }
        document_base_counts: dict = {}
        for text, _heading_id in document_pairs:
            base = slugify(text)
            document_base_counts[base] = document_base_counts.get(base, 0) + 1
        unpaired: List[Tuple[str, str]] = []
        for slug, tnode in zip(
            slugify_all(node.text for node in target_headings), target_headings
        ):
            cnode = paired_document_node.get(id(tnode))
            heading_id = getattr(cnode, "heading_id", None) if cnode is not None else None
            if heading_id:
                slug_to_id[slug] = heading_id
            else:
                unpaired.append((slug, tnode.text))

        # An inherited entry is only trustworthy on two counts, and both are facts
        # about the document rather than about the markdown:
        unpaired_slugs = {slug for slug, _ in unpaired}
        paired_ids = {
            heading_id
            for slug, heading_id in slug_to_id.items()
            if slug not in unpaired_slugs
        }
        for slug, tnode_text in unpaired:
            inherited = slug_to_id.get(slug)
            if inherited is None:
                continue
            base = slugify(tnode_text)
            # 1. Same base slug, or the suffix means something different on each
            #    side (markdown `## Intro 1` vs the document's second `Intro`).
            # 2. And exactly one document heading with that base, or there is no
            #    way to tell which of them the author meant (`Q&A` / `QA`).
            if (
                document_slug_base.get(slug) != base
                or document_base_counts.get(base, 0) != 1
            ):
                del slug_to_id[slug]
                continue
            # 2. Its id must not already be claimed by a heading this push paired.
            #    This narrows one shape of that collision; it does not establish
            #    the general invariant, because a *stale document-only* entry
            #    colliding with a paired id is never inspected here. Reachable
            #    today: a doc with two identical headings and markdown with one
            #    sends both `#intro` and `#intro-1` to the second.
            #    markdown `## Overview / ## Overview` against a document holding
            #    one `Overview` leaves the first unpaired with matching text, and
            #    without this both `#overview` and `#overview-1` land on it.
            if inherited in paired_ids:
                del slug_to_id[slug]
        known_ids = {
            node.heading_id
            for node in current
            if isinstance(node, DocsParagraphNode) and node.heading_id
        }
        return slug_to_id, known_ids

    def build_second_pass_requests(
        self,
        doc: dict,
        target: List[Node],
        tab_id: Optional[str] = None,
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[dict]:
        """
        Combined pass-2 requests: table cell fills + inline text styling.

        Both read indices from the re-fetched ``doc``; the combined list is applied
        highest-index-first so cell inserts don't invalidate other ranges.

        Args:
            tab_id: Same as build()'s tab_id — stamped onto every request's
                location/range when the doc has tabs. ``doc`` here is expected
                to already be tab-resolved (tabs.resolve_document_tab) so
                build_table_fill_requests()/build_span_style_requests() read
                the right tab's content; this parameter only affects which
                tab the *requests* are addressed to.
        """
        requests = self.build_table_fill_requests(doc, target)
        requests += self.build_span_style_requests(doc, target, alignment)
        # Cell styling reads indices from `doc`, i.e. from *before* the fills above
        # are applied. A table that already holds its text — every table on a
        # second or later push — is placed correctly. A table this push is
        # *creating* is still empty here, so `_cell_placement` cannot find the
        # cell's text and emits nothing; `unplaced_table_cells` reports those, so
        # the gap is loud rather than a silent half-application. Styling a
        # brand-new table's cells needs the post-fill index, which is predictable
        # but is index arithmetic that has to be verified by replay, not reasoned
        # about — left for its own change.
        requests += self.build_table_cell_span_requests(doc, target, alignment)
        requests.sort(key=lambda r: self._extract_start_index(r), reverse=True)
        self._inject_tab_id(requests, tab_id)
        return requests

    @staticmethod
    def _table_is_empty(table: dict) -> bool:
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                for element in cell.get("content", []):
                    paragraph = element.get("paragraph")
                    if paragraph is None:
                        continue
                    for pe in paragraph.get("elements", []):
                        run = pe.get("textRun")
                        if run and run.get("content", "").strip():
                            return False
        return True

    @staticmethod
    def _cell_inserts(table: dict, node: DocsTableNode) -> List[Tuple[int, str]]:
        """Pair each cell's first-content startIndex with the target cell text."""
        pairs: List[Tuple[int, str]] = []
        rows = table.get("tableRows", [])
        for r, row in enumerate(rows):
            cells = row.get("tableCells", [])
            for c, cell in enumerate(cells):
                content = cell.get("content", [])
                if not content:
                    continue
                idx = content[0].get("startIndex")
                if idx is None:
                    continue
                text = ""
                if r < len(node.rows) and c < len(node.rows[r]):
                    text = node.rows[r][c].text
                if text:
                    pairs.append((idx, text))
        return pairs

    # ──────────────────────────────────────────────
    # Request factories
    # ──────────────────────────────────────────────

    def _make_delete_requests(self, nodes: List[Node], doc_end_index: int) -> List[dict]:
        """Emit one deleteContentRange per node, protecting undeletable newlines.

        Two of the document's newlines can't be deleted on their own, and a
        node's [start_index, end_index) range covers both cases:

        1. The body's terminal newline (``doc_end_index - 1``).
        2. The newline immediately before a Table, TableOfContents or
           SectionBreak — the Docs API rejects deleting it "without deleting
           the element" (DeleteContentRangeRequest reference) with "Invalid
           deletion range. Cannot delete the requested range.", failing the
           whole batch atomically. A paragraph's trailing newline IS that
           newline whenever one of those elements follows it
           (DocsParagraphNode.precedes_structural_element).

        Both cases stop one index short rather than dropping the request, so
        the paragraph's text still goes away and only an empty paragraph is
        left behind holding the boundary open.

        Case 2 trims unconditionally, even when the following element is a
        Table that this same batch deletes. Co-deleting looks like it would
        make the newline safe, but the deletes are separate requests applied
        highest-index-first, so by the time the paragraph's request runs the
        table is already gone and whatever followed the table — possibly
        another boundary — has moved up against that newline. Keeping the
        rule unconditional costs one leftover empty paragraph and removes the
        need to reason about boundary chains at all.

        A trimmed delete is preceded by _residue_normalize_requests() so the
        paragraph that survives is a plain empty one, not an empty heading or
        an empty bullet.
        """
        requests = []
        for node in nodes:
            start, end, trimmed = self._delete_bounds(node, doc_end_index)
            if start >= end:
                # Nothing left to delete — an already-empty paragraph pinned by
                # a boundary or by the body's terminal newline. Emitting the
                # normalisation alone would make every push rewrite a paragraph
                # it can never remove, so push would never be idempotent.
                continue
            if trimmed and isinstance(node, DocsParagraphNode):
                requests.extend(self._residue_normalize_requests(node))
            requests.append({
                "deleteContentRange": {
                    "range": {"startIndex": start, "endIndex": end}
                }
            })
        return requests

    @staticmethod
    def _residue_normalize_requests(node: DocsParagraphNode) -> List[dict]:
        """Reset the paragraph a trimmed delete will leave behind to a plain empty one.

        namedStyleType and bullet live on the *paragraph*, and a trimmed delete
        removes only the paragraph's text — so without this the residue keeps
        them. Deleting an HEADING_2 that sat above a table would leave an empty
        HEADING_2 in the document outline, and deleting a bullet would leave an
        empty bullet; a tab-scoped pull then renders either as a literal "## " /
        "- " line that re-parses into a real node and leaks back into the
        markdown.

        Ranges use the paragraph's original, pre-delete coordinates and are
        emitted *before* the deleteContentRange for the same paragraph. build()
        sorts requests by descending startIndex with a stable sort, so requests
        sharing this paragraph's startIndex keep their emission order, and
        everything at a higher index has already been applied — the original
        coordinates are therefore still valid when these run.
        """
        requests: List[dict] = []
        paragraph_range = {"startIndex": node.start_index, "endIndex": node.end_index}
        if node.style != "NORMAL_TEXT":
            requests.append({
                "updateParagraphStyle": {
                    "range": dict(paragraph_range),
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "fields": "namedStyleType",
                }
            })
        if node.is_list_item:
            requests.append({"deleteParagraphBullets": {"range": dict(paragraph_range)}})
        return requests

    def _make_insert_requests(
        self, nodes: List[Node], insert_at_index: int, before_newline: bool = False
    ) -> List[dict]:
        """
        Emit insert requests per node.

        Paragraphs: insertText + updateParagraphStyle (+ bullets). Inline text styling
        (links/bold/italic/monospace) is applied in pass 2 (build_span_style_requests),
        against real post-insert indices.
        Tables: insertTable (empty; filled in pass 2).

        All inserts share ``insert_at_index``; because the caller/build() orders
        request groups rather than individual requests, emission order inside a
        single insert group is preserved.

        ``before_newline`` says ``insert_at_index`` points at an existing newline
        that terminates the preceding paragraph, rather than at the first index
        of a following paragraph. build() sets it for the two cases where there
        is no following paragraph to insert in front of — appending past the last
        node, and inserting directly before a Table/TableOfContents/SectionBreak.

        It changes which side of the text carries the newline. A paragraph is
        normally written ``"text\\n"``, which relies on a paragraph boundary at
        the insert point to terminate it. Landing on an existing newline instead
        puts the text *inside* the preceding paragraph, so ``"text\\n"`` would
        run "Alpha" and "Appended" together into ``"AlphaAppended"`` and leave a
        stray blank paragraph behind. Writing ``"\\ntext"`` uses the leading
        newline to close the preceding paragraph and the newline already at
        ``insert_at_index`` to close the new one:

            body       'Intro\\nAlpha\\n'  + insert at 12
            "text\\n"   -> 'Intro\\nAlphaAppended\\n\\n'
            "\\ntext"   -> 'Intro\\nAlpha\\nAppended\\n'

        The new paragraph therefore starts one index later than the insert point,
        which is what the ``+ 1`` below accounts for. Without it the
        updateParagraphStyle range begins on the preceding paragraph's newline
        and Docs applies namedStyleType to both paragraphs.
        """
        requests: List[dict] = []
        for node in reversed(nodes):
            if isinstance(node, DocsTableNode):
                requests.append({
                    "insertTable": {
                        "location": {"index": insert_at_index},
                        "rows": max(node.num_rows, 1),
                        "columns": max(node.num_cols, 1),
                    }
                })
                continue

            # The paragraph's own text always ends up as node.text + "\n"; only
            # which side of it carries the newline in the insert differs.
            text = "\n" + node.text if before_newline else node.text + "\n"
            requests.append({
                "insertText": {"location": {"index": insert_at_index}, "text": text}
            })
            paragraph_start = insert_at_index + 1 if before_newline else insert_at_index
            text_len = _utf16_len(node.text + "\n")
            paragraph_range = {
                "startIndex": paragraph_start,
                "endIndex": paragraph_start + text_len,
            }
            requests.append({
                "updateParagraphStyle": {
                    "range": paragraph_range,
                    "paragraphStyle": {"namedStyleType": node.style},
                    "fields": "namedStyleType",
                }
            })
            if node.is_list_item:
                requests.append({
                    "createParagraphBullets": {
                        "range": paragraph_range,
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                })
            else:
                # An inserted paragraph inherits the bullet of the paragraph it
                # split. Inserts run in reverse at a shared index, so pushing
                # markdown whose first list item follows a heading splits that
                # already-bulleted paragraph and the heading comes back with a
                # `bullet` set — styled as a heading *and* rendered as a list
                # item, because updateParagraphStyle only writes
                # namedStyleType and never clears a bullet.
                #
                # Unconditional, for two reasons. Nothing here knows what the
                # live paragraph at insert_at_index looks like, so "only when
                # needed" is not knowable at build time; and
                # deleteParagraphBullets on a paragraph with no bullet is a
                # no-op, so emitting it always keeps push idempotent.
                #
                # Emitted after the insert and inside the same group, so by the
                # time the *next* node is inserted at this index the paragraph
                # it splits is already clean — which is why one request per
                # node is enough rather than needing to re-clear earlier ones.
                requests.append({
                    "deleteParagraphBullets": {"range": paragraph_range}
                })
        return requests

    def _span_style_requests(
        self,
        node: DocsParagraphNode,
        placement: DocsParagraphNode,
        slug_to_id: Optional[dict] = None,
        known_ids: Optional[set] = None,
    ) -> List[dict]:
        """Emit updateTextStyle for each styled span of ``node``, placed inside ``placement``.

        ``placement`` is the live document paragraph _align_for_styling paired
        ``node`` with; its [start_index, end_index) is the hard boundary. Span
        offsets are derived from ``node``'s span *texts*, which is only exactly
        right when the two texts agree — the alignment guarantees that, but the
        bound is enforced here anyway rather than trusted, so a length
        disagreement (a paragraph holding a smart chip, an inline object, or
        trailing whitespace the markdown parser stripped from .text but kept in
        .spans) can only ever cost styling inside this paragraph. It can never
        spill a range into the next one.

        ``slug_to_id``/``known_ids`` resolve internal anchors against the
        re-fetched document's headings — heading ids only exist once the
        headings do, which is why this cannot happen in pass 1. An anchor that
        resolves to nothing gets **no link at all**: the span keeps its other
        marks and unresolved_anchor_links() reports it. It is never degraded to
        a `url` link holding a `#fragment`, which would put something in the
        document a reader can click and land nowhere. Nothing rejects the push
        first — this and unresolved_anchor_links() are the only checks on the
        write path.
        """
        # The paragraph's last index is its newline; text lives strictly before it.
        return self._span_requests_in(
            node.spans,
            placement.start_index,
            placement.end_index - 1 if placement.end_index else None,
            slug_to_id,
            known_ids,
        )

    def _span_requests_in(
        self,
        spans: List[TextSpan],
        start: int,
        limit: Optional[int],
        slug_to_id: Optional[dict] = None,
        known_ids: Optional[set] = None,
    ) -> List[dict]:
        """Place `spans` starting at `start`, never writing at or past `limit`.

        Extracted so a table cell can reuse it. The two callers differ only in how
        they locate the run of text — a paragraph's own index range, or a cell's
        first content paragraph — and everything that matters is here: the bound is
        enforced rather than trusted, and an anchor that resolves to nothing yields
        no link rather than a `url` holding a `#fragment` a reader cannot follow.
        """
        requests: List[dict] = []
        offset = start
        for span in spans:
            span_len = _utf16_len(span.text)
            if span_len == 0:
                continue
            if limit is not None and offset + span_len > limit:
                break  # offsets only grow — everything after this is out of bounds too
            attrs: dict = {}
            if span.bold:
                attrs["bold"] = True
            if span.italic:
                attrs["italic"] = True
            if span.link:
                payload = link_payload(span.link, slug_to_id, known_ids)
                if payload is not None:
                    attrs["link"] = payload
                # else: the anchor names no heading in the written document, so
                # there is nothing to point at. The span keeps its other marks
                # and unresolved_anchor_links() reports the anchor, rather than
                # this either writing a `url` link to a "#fragment" the Doc
                # cannot follow or aborting the whole pass and discarding every
                # other paragraph's styling.
            if span.monospace:
                attrs["monospace"] = True
            if attrs:
                requests.extend(self._make_text_style_requests(
                    span.text, attrs,
                    {"startIndex": offset, "endIndex": offset + span_len},
                ))
            offset += span_len
        return requests

    @staticmethod
    def _restyles(current_node: Node, target_node: Node) -> bool:
        """Whether two same-text nodes differ in a paragraph attribute.

        The single predicate shared by diff_summary (which must report a restyle)
        and _make_style_update_requests (which must emit one). Keeping them on one
        definition is what stops the preview and the write from disagreeing about
        whether anything is happening.
        """
        if isinstance(current_node, DocsTableNode) or isinstance(target_node, DocsTableNode):
            return False
        # Deliberately NOT nesting_level. CreateParagraphBulletsRequest derives
        # the level from leading tabs in the paragraph's *text*, not from any
        # paragraph attribute, so re-issuing the preset cannot move a paragraph
        # between levels — it would be a request that quietly does nothing.
        # Changing nesting is therefore a text edit, not a restyle, and is left
        # as a known gap rather than papered over with a no-op request.
        return (
            current_node.style != target_node.style
            or current_node.is_list_item != target_node.is_list_item
        )

    def _make_style_update_requests(self, current_node: Node, target_node: Node) -> List[dict]:
        """Restyle a paragraph in place — same text, different paragraph attributes.

        Emitted for `equal` opcodes, which since _node_key became text-only is
        where every restyle now lands. Covers all three attributes the Docs API
        can change without rewriting the text:

        * namedStyleType, via updateParagraphStyle
        * bullet on/off, via createParagraphBullets / deleteParagraphBullets

        This is what makes those edits non-destructive. The alternative — the
        delete-then-insert build() emits for a `replace` — retypes the paragraph,
        which costs more requests and destroys any comment anchored to it.

        List *nesting* is not here on purpose. CreateParagraphBulletsRequest
        derives the level from leading tabs in the paragraph's text, so
        re-issuing the preset cannot move a paragraph between levels; emitting it
        for a nesting change would be a request that silently does nothing.
        Changing nesting is a text edit, and it stays a known gap rather than a
        no-op dressed up as a fix.
        """
        if isinstance(current_node, DocsTableNode) or isinstance(target_node, DocsTableNode):
            return []

        requests: List[dict] = []
        paragraph_range = {
            "startIndex": current_node.start_index,
            "endIndex": current_node.end_index,
        }

        if current_node.style != target_node.style:
            requests.append({
                "updateParagraphStyle": {
                    "range": dict(paragraph_range),
                    "paragraphStyle": {"namedStyleType": target_node.style},
                    "fields": "namedStyleType",
                }
            })

        became_list = target_node.is_list_item and not current_node.is_list_item
        stopped_being_list = current_node.is_list_item and not target_node.is_list_item

        if stopped_being_list:
            requests.append({"deleteParagraphBullets": {"range": dict(paragraph_range)}})
        elif became_list:
            requests.append({
                "createParagraphBullets": {
                    "range": dict(paragraph_range),
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })

        return requests

    def _make_text_style_requests(
        self, text: str, style_attrs: dict, range_dict: dict
    ) -> List[dict]:
        """Emit updateTextStyle with a specific FieldMask (never '*')."""
        fields = []
        text_style: dict = {}

        if "bold" in style_attrs:
            fields.append("bold")
            text_style["bold"] = style_attrs["bold"]
        if "italic" in style_attrs:
            fields.append("italic")
            text_style["italic"] = style_attrs["italic"]
        if "link" in style_attrs:
            fields.append("link")
            link = style_attrs["link"]
            # Callers may hand over a ready-made Link union member — the
            # `headingId` form an internal anchor resolves to (heading_anchors)
            # has no `url` at all. A bare string stays the URL case.
            text_style["link"] = link if isinstance(link, dict) else {"url": link}
        if style_attrs.get("monospace"):
            fields.append("weightedFontFamily")
            text_style["weightedFontFamily"] = {"fontFamily": "Courier New", "weight": 400}

        if not fields:
            return []

        return [{
            "updateTextStyle": {
                "range": range_dict,
                "textStyle": text_style,
                "fields": ",".join(fields),
            }
        }]

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _inject_tab_id(requests: List[dict], tab_id: Optional[str]) -> None:
        """Stamp `tabId` onto every request's `location`/`range` dict, in place.

        Per the Docs API, Location and Range messages each carry their own
        optional `tabId`; omitting it defaults the request to the document's
        first tab. A no-op when `tab_id` is None (legacy, non-tabbed docs).
        """
        if not tab_id:
            return
        for request in requests:
            for inner in request.values():
                if not isinstance(inner, dict):
                    continue
                if "location" in inner:
                    inner["location"]["tabId"] = tab_id
                if "range" in inner:
                    inner["range"]["tabId"] = tab_id

    @staticmethod
    def _extract_start_index(request: dict) -> int:
        """Extract the primary startIndex from any request dict for sorting."""
        for key in (
            "deleteContentRange",
            "insertText",
            "insertTable",
            "updateParagraphStyle",
            "createParagraphBullets",
            "deleteParagraphBullets",
            "updateTextStyle",
        ):
            if key in request:
                inner = request[key]
                if "range" in inner:
                    return inner["range"].get("startIndex", 0)
                if "location" in inner:
                    return inner["location"].get("index", 0)
        return 0
