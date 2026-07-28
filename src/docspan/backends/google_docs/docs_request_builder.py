"""Build Google Docs batchUpdate request lists from structural AST diffs."""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
)

Node = Union[DocsParagraphNode, DocsTableNode]


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
        return "\n".join(" | ".join(row) for row in node.rows)
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
        """Key used by SequenceMatcher for comparing nodes."""
        if isinstance(node, DocsTableNode):
            return ("__table__", tuple(tuple(row) for row in node.rows))
        return ("__para__", node.style, node.text, node.is_list_item)

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
                unchanged_count += i2 - i1

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
                if i1 > 0:
                    insert_at = current[i1 - 1].end_index
                else:
                    insert_at = 1  # start of document body
                # Appending past the last node: current[i1 - 1] is the body's
                # final paragraph, so its end_index IS doc_end_index — one past
                # the last index an insert may name ("Index N must be less than
                # the end index of the referenced segment"). Step back onto the
                # body's terminal newline and write the new paragraph in front
                # of it. See _make_insert_requests(at_body_end=...) for why the
                # newline has to move to the front of the text.
                at_body_end = insert_at >= doc_end_index
                if at_body_end:
                    insert_at = doc_end_index - 1
                requests = self._make_insert_requests(
                    target[j1:j2], insert_at, at_body_end=at_body_end
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

    def unappliable_removals(
        self, current: List[Node], target: List[Node], doc_end_index: int
    ) -> List[DocsParagraphNode]:
        """Paragraphs the diff wants removed that no batchUpdate can remove.

        An empty paragraph pinned by the newline anchoring a Table,
        TableOfContents or SectionBreak has a delete range that trims to
        nothing, so _make_delete_requests drops the request entirely (see its
        `start >= end` branch). diff_summary() still reports the paragraph as a
        removal — correctly, since the document really does still contain it —
        and the two are therefore allowed to disagree. This is the bridge: it
        names exactly the paragraphs behind that disagreement, so push() can
        say so instead of reporting "No changes detected" about a document it
        knows still differs.

        The body's own final paragraph is deliberately excluded. Every Docs
        body ends with one, the API refuses to delete its newline, and
        MarkdownToParagraphParser can never emit a node for it — so it is a
        permanent property of the model rather than a difference, and counting
        it would make the warning fire on every push of every document.

        Only pure "delete" opcodes are inspected. A "replace" also runs its
        nodes through _make_delete_requests, but it always emits an insert as
        well, so it can never be the reason build() returned nothing.
        """
        unappliable: List[DocsParagraphNode] = []
        for tag, i1, i2, _j1, _j2 in self._opcodes(current, target):
            if tag != "delete":
                continue
            for node in current[i1:i2]:
                if not isinstance(node, DocsParagraphNode):
                    continue
                if node.end_index >= doc_end_index:
                    continue  # the body's terminal paragraph, not a difference
                start, end, _trimmed = self._delete_bounds(node, doc_end_index)
                if start >= end:
                    unappliable.append(node)
        return unappliable

    @staticmethod
    def _delete_bounds(node: Node, doc_end_index: int) -> Tuple[int, int, bool]:
        """The range a node's deleteContentRange may actually cover, and whether it was trimmed.

        Single source of truth for the two undeletable-newline rules described
        on _make_delete_requests. Both that method and unappliable_removals()
        need the answer, and they have to agree: the first drops a request when
        the range trims to nothing, and the second exists precisely to name the
        paragraphs behind those dropped requests. Computing the arithmetic twice
        let them disagree silently — an earlier version of unappliable_removals
        omitted the terminal-newline clamp, which happened to reach the same
        answer for the body's last paragraph and would have stopped doing so the
        moment anyone "tidied up" the duplication.
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

    def build_span_style_requests(self, doc: dict, target: List[Node]) -> List[dict]:
        """
        Emit updateTextStyle requests for inline styling (links/bold/italic/monospace).

        Runs against the re-fetched document so ranges use real post-insert indices.
        Nodes are paired by an order-preserving *content* alignment of the re-fetched
        document against ``target`` (see _align_for_styling), never by raw position.
        """
        if not any(isinstance(n, DocsParagraphNode) and n.spans for n in target):
            return []

        pairs, _unaligned = self._align_for_styling(doc, target)
        requests: List[dict] = []
        for cnode, tnode in pairs:
            requests.extend(self._span_style_requests(tnode, cnode))
        return requests

    def unaligned_span_targets(self, doc: dict, target: List[Node]) -> List[DocsParagraphNode]:
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
        pairs, unaligned = self._align_for_styling(doc, target)
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
        creates), and MarkdownToParagraphParser never emits an empty-text node,
        so a residue can never collide with a real target paragraph.
        """
        if isinstance(node, DocsTableNode):
            return ("__table__",)
        return ("__para__", node.text)

    def _align_for_styling(
        self, doc: dict, target: List[Node]
    ) -> Tuple[List[Tuple[DocsParagraphNode, DocsParagraphNode]], List[DocsParagraphNode]]:
        """Pair re-fetched document nodes with ``target`` nodes for pass-2 styling.

        Returns ``(pairs, unaligned)`` — pairs of (document node, target node)
        that are safe to style, and the target paragraphs with spans that could
        not be paired.

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

        unaligned = [
            node
            for index, node in enumerate(target)
            if isinstance(node, DocsParagraphNode)
            and node.spans
            and index not in aligned_target_indices
        ]
        return pairs, unaligned

    def build_second_pass_requests(
        self, doc: dict, target: List[Node], tab_id: Optional[str] = None
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
        requests += self.build_span_style_requests(doc, target)
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
                    text = node.rows[r][c]
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
        self, nodes: List[Node], insert_at_index: int, at_body_end: bool = False
    ) -> List[dict]:
        """
        Emit insert requests per node.

        Paragraphs: insertText + updateParagraphStyle (+ bullets). Inline text styling
        (links/bold/italic/monospace) is applied in pass 2 (build_span_style_requests),
        against real post-insert indices.
        Tables: insertTable (empty; filled in pass 2).

        All inserts share ``insert_at_index``; because the caller/build() sorts descending
        later, ordering inside a single insert group is preserved.

        ``at_body_end`` marks an append past the last node, where
        ``insert_at_index`` is the body's terminal newline rather than the start
        of a following paragraph. A paragraph is normally written as
        ``"text\\n"``, which relies on there being a paragraph boundary at the
        insert point to terminate. At the body's terminal newline there is none:
        the inserted text lands *inside* the final paragraph and
        ``"text\\n"`` would run "Alpha" and "Appended" together into
        ``"AlphaAppended"``, then leave a stray blank paragraph behind. Writing
        ``"\\ntext"`` instead uses the leading newline to close the existing
        final paragraph and the body's own terminal newline to close the new
        one. The paragraph therefore starts one index later than the insert
        point, which is what the ``+ 1`` below accounts for.
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
            text = "\n" + node.text if at_body_end else node.text + "\n"
            requests.append({
                "insertText": {"location": {"index": insert_at_index}, "text": text}
            })
            paragraph_start = insert_at_index + 1 if at_body_end else insert_at_index
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
        self, node: DocsParagraphNode, placement: DocsParagraphNode
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
        """
        requests: List[dict] = []
        offset = placement.start_index
        # The paragraph's last index is its newline; text lives strictly before it.
        limit = placement.end_index - 1 if placement.end_index else None
        for span in node.spans:
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
                attrs["link"] = span.link
            if span.monospace:
                attrs["monospace"] = True
            if attrs:
                requests.extend(self._make_text_style_requests(
                    span.text, attrs,
                    {"startIndex": offset, "endIndex": offset + span_len},
                ))
            offset += span_len
        return requests

    def _make_style_update_requests(self, current_node: Node, target_node: Node) -> List[dict]:
        """Emit updateParagraphStyle when a paragraph's style differs (text is equal)."""
        if isinstance(current_node, DocsTableNode) or isinstance(target_node, DocsTableNode):
            return []
        if current_node.style == target_node.style:
            return []
        return [{
            "updateParagraphStyle": {
                "range": {
                    "startIndex": current_node.start_index,
                    "endIndex": current_node.end_index,
                },
                "paragraphStyle": {"namedStyleType": target_node.style},
                "fields": "namedStyleType",
            }
        }]

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
            text_style["link"] = {"url": style_attrs["link"]}
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
