"""Build Google Docs batchUpdate request lists from structural AST diffs."""
from __future__ import annotations

import difflib
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterator, List, Literal, Optional, Tuple, Union

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
from docspan.backends.google_docs.projection import Residue, project

Node = Union[DocsParagraphNode, DocsTableNode]

# difflib's opcode tuple. Named because it is now threaded through three
# functions (_opcodes, _repair, _coalesce) and `list` is invariant, so an
# inlined Literal in one signature and a bare `str` in the next do not unify.
Opcode = Tuple[Literal["replace", "delete", "insert", "equal"], int, int, int, int]

logger = logging.getLogger(__name__)

# Thresholds for _bounded_opcodes. Tuned against this file's own test fixtures
# (a 30-row table, the fenced-code-block fixtures in test_code_block_granularity.py)
# so ordinary documents never trip the guard, while a document built from a
# few thousand duplicate short lines/cells does.
_MAX_COMPARISON_CELLS = 4_000_000
_MAX_DUPLICATE_RUN = 60
_MIN_SIZE_FOR_DUPLICATE_CHECK = 150

# Must exceed `_structural_score`'s maximum possible value (currently 4: 2 for
# matching style + 1 for matching heading-ness + 1 for matching list-item-ness)
# so a code-rendered candidate always outranks a merely structurally-similar one.
_CODE_LINE_PREFERENCE_BONUS = 100


class DiffTooExpensive(Exception):
    """Raised instead of running SequenceMatcher on pathological duplicate-heavy input.

    `autojunk=False` is required for correctness (see `_opcodes`'s docstring
    and issue #54/#68) but it also reintroduces difflib's cubic-ish worst case
    once many short keys repeat — a document with a few thousand duplicate
    lines or table cells can otherwise hang push/pull for minutes.

    Refuses loudly rather than falling back to a positional/heuristic diff:
    `_repair` and `_prefer_structural_pairing` both depend on
    `get_opcodes()`'s exact partition guarantee, and a lookalike-popularity
    heuristic is exactly what reopened the headingId-mispairing bug PR #50/#67
    fixed. "Refuse, don't guess" matches this file's existing philosophy.
    """

    def __init__(self, context: str, size: int, max_duplicate_run: int):
        self.context = context
        self.size = size
        self.max_duplicate_run = max_duplicate_run
        super().__init__(
            f"Diff for {context} is too expensive to compute safely: {size} "
            f"nodes include a run of {max_duplicate_run} duplicate short "
            "lines/cells. Split the table or code block into smaller pieces "
            "and retry."
        )


def _bounded_opcodes(a_keys: List[Tuple], b_keys: List[Tuple], *, context: str) -> List[Opcode]:
    """The sole place any `autojunk=False` `SequenceMatcher` gets constructed.

    `_opcodes`, `_repair`'s inner per-run matcher and `_align_for_styling`'s
    pass-2 matcher all route through this one function so the pathological-
    input guard can never be applied at one call site and forgotten at
    another — the same failure class `_opcodes`'s docstring already warns
    about for build()/diff_summary() drifting apart.

    Trips (raises `DiffTooExpensive`, logging a WARNING first) on either:
    - a comparison matrix (`len(a_keys) * len(b_keys)`) large enough that
      even difflib's average-case cost is unsafe, or
    - a duplicate-key run dense enough to trigger the cubic-ish worst case,
      gated by a size floor so a handful of legitimately-repeated short
      values (a few blank paragraphs, a short status column) never trips it.

    Otherwise behaves exactly as the removed inline `SequenceMatcher(None,
    a_keys, b_keys, autojunk=False)` calls did — `autojunk` is never
    re-enabled and no popularity heuristic is substituted.
    """
    combined_len = len(a_keys) + len(b_keys)
    max_duplicate_run = max(Counter(a_keys + b_keys).values()) if combined_len else 0
    if len(a_keys) * len(b_keys) > _MAX_COMPARISON_CELLS or (
        combined_len >= _MIN_SIZE_FOR_DUPLICATE_CHECK and max_duplicate_run > _MAX_DUPLICATE_RUN
    ):
        logger.warning(
            "Refusing expensive diff for %s: %d + %d nodes, largest duplicate run %d",
            context,
            len(a_keys),
            len(b_keys),
            max_duplicate_run,
        )
        raise DiffTooExpensive(context, combined_len, max_duplicate_run)
    matcher = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    return matcher.get_opcodes()


@dataclass(frozen=True)
class Pass2Alignment:
    """Everything the three pass-2 consumers need, computed once per push.

    See DocsRequestBuilder.align() for why this is shared rather than recomputed:
    the recomputation sat inside pass 2's optimistic-concurrency window.
    """
    current: List[Node]
    pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]]
    unaligned: List[DocsParagraphNode]
    table_pairs: List[Tuple[int, DocsTableNode]]
    slug_to_id: dict
    known_ids: set
    residue: List[Residue]


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

        **Full identity** — style and bullet participate. This answers only
        *"which live paragraph is this markdown node about?"*, which is a different
        question from *"is this an in-place restyle or a rewrite?"*. `_content_key`
        answers the second, and `_repair` applies it.

        The key used to be text-only, so that a restyle would align rather than
        delete-and-reinsert. That worked while nodes were long and distinctive, but
        it weakened correspondence to "same text ⇒ same paragraph" — and once
        fenced code blocks became one node per line, the sequence filled with short
        generic strings (`}`, `pass`, `Config`, `Example`) that collide with
        headings and list items elsewhere in the document.

        The consequence was not cosmetic. A code line reading `Config` beside a
        live `# Config` heading paired with it, so push demoted the heading to
        NORMAL_TEXT and inserted a fresh one — destroying the `headingId` every
        internal anchor resolves against, and any comment anchored to it, while the
        preview said `change 'Config' -> 'Config'`. Reproduces with pure prose too
        (a heading whose text repeats as body text), so this predates the code-block
        split; the split made it easy to hit.

        Restyle-in-place is now re-admitted deliberately by `_repair` instead of
        falling out of a key that was too weak to tell paragraphs apart.

        `_is_code_line` (`bool(node.render_prefix)`) participates too: a
        paragraph inside a Docs-rendered block and a plain paragraph with
        otherwise-identical style/bullet/nesting/text are not the same
        paragraph to align against (issue #54). Without it, SequenceMatcher
        could pair a prose paragraph with a code-rendered one sharing its
        text — permanently trapping the prose inside the rendered block, or
        the reverse, or opening a `replace` across that boundary whose insert
        then lands at an index `project()` has already advanced past the
        glyph, landing it inside the Docs-rendered block instead of beside it.

        Only `_node_key` carries this signal, not `_content_key` — see the
        latter's docstring for why the split is necessary.
        """
        if isinstance(node, DocsTableNode):
            return ("__table__", tuple(tuple(self._cell_key(c) for c in row) for row in node.rows))
        return (
            "__para__",
            node.style,
            node.is_list_item,
            node.nesting_level,
            self._is_code_line(node),
            node.text,
        )

    @staticmethod
    def _is_code_line(node: DocsParagraphNode) -> bool:
        """Whether a paragraph belongs to a Docs-rendered block (e.g. a native code block).

        `render_prefix` is populated only by `DocsStructureParser`, when parsing a
        live document — a target node parsed from markdown never carries it, even
        for a fenced code line. That asymmetry is deliberate here: this only needs
        to split `_node_key`'s current-side alphabet so a prose paragraph and a
        rendered-block paragraph with identical text stop looking like the same
        node (issue #54). It does not need to make an unchanged code line's key
        equal its target's — `_content_key` stays text-only precisely so `_repair`
        can still fold that pairing back to `equal` once `_node_key` has separated
        it from any unrelated prose (see `_content_key`'s docstring).
        """
        return bool(node.render_prefix)

    @staticmethod
    def _target_wants_code_line(node: Node) -> bool:
        """Whether a *target* paragraph is itself a fenced-code line.

        `MarkdownToParagraphParser` never sets `render_prefix` (see
        `_is_code_line`'s docstring), but it does mark every span of a fenced
        code block's line `monospace=True`. That is the only signal available
        on the target side that a slot is "supposed to be code" — used solely
        to gate `_prefer_structural_pairing`'s top-level, current-side
        duplicate preference (issue #68) toward a `render_prefix`-carrying
        candidate. `all()` rather than `any()` so a prose paragraph that
        merely contains an inline `` `code` `` span among ordinary text does
        not trip this — only a paragraph that is entirely monospace, the
        shape a fenced-code line always has.
        """
        if not isinstance(node, DocsParagraphNode):
            return False
        return bool(node.spans) and all(s.monospace for s in node.spans)

    def _cell_key(self, cell: TableCell) -> Tuple:
        """Hashable full identity for a table cell, including inline styling.

        `TableCell` and `TextSpan` are plain (unfrozen, unhashable) dataclasses —
        `_node_key` needs a hashable key to feed `difflib.SequenceMatcher`, so this
        flattens a cell's text and spans into a tuple instead of hashing the
        objects themselves.
        """
        return (
            cell.text,
            tuple((s.text, s.bold, s.italic, s.link, s.monospace) for s in cell.spans),
        )

    def _content_key(self, node: Node) -> Tuple:
        """Identity ignoring everything editable in place — the old `_node_key`.

        Used only to classify a pairing that `_node_key` has already made: two nodes
        with the same content differ only by attributes `updateParagraphStyle`,
        `createParagraphBullets` and `deleteParagraphBullets` can change without
        touching the text, so the edit is a restyle and not a rewrite.

        Deliberately still text-only with respect to `render_prefix` — unlike
        `_node_key` — because `_content_key` also has to recognize an unchanged
        code line as content-equal so `_repair` can fold it back to `equal`
        (see `_node_key`'s docstring on why a raw `render_prefix` there means a
        current code line's `_node_key` never matches its own unchanged target).

        A stray `_content_key` collision between a prose and a code node with
        the same text is harmless *when they are the only two candidates for
        their own slots*: `_node_key` already keeps them apart there, so
        `_repair` — which only inspects the two sides of a single
        `_node_key`-identified `replace` run — never gets a run containing
        both to conflate. It is not harmless in general: when a plain current
        paragraph and a real code-rendered current paragraph both read the
        same text and only one target slot exists for that text, the *outer*
        `_node_key` matcher (not `_repair`) can still let the plain one win
        the correspondence and leave the code-rendered one an unpaired
        `delete` — a pre-existing gap now closed by `_opcodes`'s top-level
        `_prefer_structural_pairing(prefer_code_line=True)` pass, which
        re-scores that exact ambiguity across the whole document; see
        `test_a_prose_line_repeating_a_code_lines_text_is_disambiguated_in_favor_of_the_code_line`
        in `tests/test_code_block_granularity.py` and issue #68.
        """
        if isinstance(node, DocsTableNode):
            return ("__table__", tuple(tuple(c.text for c in row) for row in node.rows))
        return ("__para__", node.text)

    def _opcodes(
        self,
        current: List[Node],
        target: List[Node],
    ) -> List[Opcode]:
        """Build the single difflib.SequenceMatcher opcode list shared by
        build() and diff_summary().

        Both methods interpret the same opcodes differently on purpose
        (build() does whole-range replace, diff_summary() zips pairwise) —
        but they must see identical opcodes, since push()'s safety gate
        (high_risk, derived from diff_summary()'s classification) and the
        actual write (derived from build()'s classification) must never
        drift apart. This is the one place current_keys/target_keys get
        constructed; the matcher itself is built by `_bounded_opcodes`, the
        shared guard against difflib's duplicate-heavy worst case (see its
        docstring) — a `DiffTooExpensive` raised here propagates to both
        callers identically, so they can never diverge on whether a document
        is pathological either.

        `_repair` only disambiguates duplicate-content candidates *within* a
        single `replace` run it already identified. It cannot rescue a
        current node that `_node_key`'s top-level `SequenceMatcher` pass
        already bound elsewhere in the document — e.g. a plain paragraph and
        a real code-rendered paragraph reading the same text, competing for
        one target slot the plain paragraph wins purely because its key
        happens to match first (issue #68). `_prefer_structural_pairing`'s
        candidate/slot/greedy-assignment machinery generalizes to that
        top-level case unchanged (it already takes global indices and an
        `i1`/`j1` offset; passing `0, 0` and the whole opcode list just
        widens its view from "one run" to "the document"), so it is reused
        here rather than duplicated — with `prefer_code_line=True` to prefer
        a `render_prefix`-carrying candidate for a slot the target itself
        marks as code (`_target_wants_code_line`). `_coalesce` runs again
        afterward since the pass may re-tag opcodes that were previously
        merged.
        """
        current_keys = [self._node_key(n) for n in current]
        target_keys = [self._node_key(n) for n in target]
        opcodes = _bounded_opcodes(current_keys, target_keys, context="document")
        repaired = self._repair(opcodes, current, target)
        # The whole-document pass below only ever has work to do when some
        # target slot is itself a fenced-code line (`_target_wants_code_line`)
        # — see `_prefer_structural_pairing`'s `code_slot_ids` gate, which
        # otherwise `continue`s past every content-key group unconditionally.
        # Skipping the `by_key` grouping entirely for documents with no code
        # blocks avoids that whole-document-sized bookkeeping on every single
        # `_opcodes()`/`build()` call for the common case.
        if not any(self._target_wants_code_line(n) for n in target):
            return self._coalesce(repaired)
        resolved = self._prefer_structural_pairing(
            repaired, current, target, 0, 0, prefer_code_line=True,
        )
        return self._coalesce(resolved)

    def _repair(
        self,
        opcodes: List[Opcode],
        current: List[Node],
        target: List[Node],
    ) -> List[Opcode]:
        """Re-classify text-identical pairs inside a `replace` run as `equal`.

        `_node_key` includes style and bullet, so a paragraph that was only
        *restyled* now lands in a `replace` run — and `build()` answers a replace
        with delete-then-insert, which retypes the paragraph and destroys any
        comment anchored to it. That behaviour is what the old text-only key
        avoided, at the cost of correspondence (see `_node_key`).

        So the two concerns are separated: the key decides *correspondence*, and
        this decides *classification*. Where a replace run pairs nodes with equal
        `_content_key`, the edit is an in-place restyle and the opcode becomes
        `equal`, which `_make_style_update_requests` turns into the two or three
        in-place requests it actually needs.

        Leftovers on either side stay a `replace`/`insert`/`delete` for the part
        that genuinely differs, so nothing is silently dropped.

        Pairing nodes by their position within the run (same offset from the
        run's start on both sides) is not a correspondence relation — it just
        assumes the run has no internal insert/delete of its own. Where it
        does (e.g. a restyle sitting next to an unrelated deletion in the same
        run), a positional walk mispairs nodes: a live heading can end up
        "paired" with an unrelated line, so the heading looks like a rewrite
        and gets deleted-and-reinserted, destroying its headingId. So instead
        of walking positionally, a second SequenceMatcher (keyed on
        `_content_key`) finds the actual content correspondence within the
        run's own sub-ranges. `get_opcodes()` returns a partition of both
        inputs, so this can't assign two current nodes to the same target
        node.
        """
        repaired: List[Opcode] = []
        for tag, i1, i2, j1, j2 in opcodes:
            if tag != "replace":
                repaired.append((tag, i1, i2, j1, j2))
                continue
            cur_slice = current[i1:i2]
            tgt_slice = target[j1:j2]
            inner_opcodes = _bounded_opcodes(
                [self._content_key(n) for n in cur_slice],
                [self._content_key(n) for n in tgt_slice],
                context="replace-run",
            )
            pending: List[Opcode] = []
            for itag, ci1, ci2, tj1, tj2 in inner_opcodes:
                aci1, aci2 = i1 + ci1, i1 + ci2
                atj1, atj2 = j1 + tj1, j1 + tj2
                if itag == "equal":
                    # Real content correspondence inside the run -> restyle-in-place.
                    for off in range(aci2 - aci1):
                        pending.append(
                            ("equal", aci1 + off, aci1 + off + 1, atj1 + off, atj1 + off + 1)
                        )
                else:
                    # Genuinely different content in this sub-window -> real rewrite.
                    pending.append((itag, aci1, aci2, atj1, atj2))
            pending = self._prefer_structural_pairing(pending, cur_slice, tgt_slice, i1, j1)
            repaired.extend(self._coalesce(pending))
        return repaired

    def _prefer_structural_pairing(
        self,
        pending: List[Opcode],
        cur_slice: List[Node],
        tgt_slice: List[Node],
        i1: int,
        j1: int,
        prefer_code_line: bool = False,
    ) -> List[Opcode]:
        """Reassign ambiguous equal/delete pairings toward their structurally closest node.

        Two callers, two different scope contracts: `_repair` calls this locally,
        scoped to a single `replace` run it already identified
        (`prefer_code_line=False`, the default); `_opcodes` calls it once more
        over the *whole document* (`prefer_code_line=True`). `prefer_code_line`
        is what distinguishes the two — see its own paragraph below for what it
        changes about the scoring and the pool of eligible candidates.

        The inner `SequenceMatcher` in `_repair` treats every current node sharing
        a `_content_key` as interchangeable and pairs whichever ones it meets first
        with the target — typically by position. When several current nodes share
        text (a stale body paragraph and a live heading both reading "Setup"),
        that can restyle-in-place the wrong one and delete the live heading
        instead, destroying its `headingId`. Same thing happens, worse, when the
        *target* side also repeats the text (e.g. restyling one duplicate up to a
        heading and another down to a bullet): each ambiguous target then needs
        its own best-matching current node, not just the first one considered.

        So for every `_content_key` shared by more than one current node in this
        run, this treats it as a small assignment problem: each target position
        that currently has an "equal" pairing is a slot, every current node
        sharing the key (whether currently paired or currently "delete") is a
        candidate, and slots claim candidates greedily by structural similarity
        (style, heading-ness, list-item-ness), highest score first, ties going to
        a candidate's own existing slot so an already-fine pairing is not
        needlessly perturbed. Whichever candidates no slot claims become deletes.

        This only ever reassigns which current index a given target range maps
        to — the target ranges themselves, and every other opcode's indices, are
        untouched — so it cannot double-book or drop a target index. There is no
        list-order requirement to preserve: `build()` and `diff_summary()` both
        consume each opcode by its own absolute (i1, i2, j1, j2), and `_coalesce`
        only merges entries whose indices are exactly contiguous, so reordering
        which candidate owns which target range cannot corrupt either consumer.

        Generalization: a duplicate-content current node does not stop being a
        candidate just because the inner matcher happened to fold it into a
        multi-node "replace" block alongside other, genuinely different content
        in the same run (e.g. a live heading sitting next to an edited sentence,
        with the actual duplicate target slot won by a stray paragraph
        elsewhere in the run). Every current index inside a "replace" opcode is
        registered as a candidate the same way a singleton "delete" is; if one
        wins a slot, its parent "replace" opcode is structurally split
        afterward — the winning index is carved out as its own "equal", and
        whatever current indices remain keep the original, untouched target
        range (attached to the first surviving contiguous run; any other
        surviving run becomes a plain "delete", since the target content is
        already spoken for). If every current index in the block is claimed,
        the target range becomes a fresh "insert" anchored where the block used
        to be. This never touches a target index more than once and never
        drops one, so it cannot corrupt `build()`/`diff_summary()` the same way
        the singleton case cannot (see above).

        Scope note: only the *current* side of "replace"/"insert" opcodes is
        considered here. A duplicate *target* slot trapped inside a multi-node
        block (the symmetric case) is not decomposed — there is no existing
        "equal" opcode to use as the slot in that case, only a range with no
        established per-index correspondence to split by. That gap is open.

        `prefer_code_line` (used only by `_opcodes`'s top-level call, issue
        #68) adds a scoring bonus for a candidate that is itself a
        `render_prefix`-carrying code-rendered node when the slot's target
        node is itself a fenced-code-block line (`_target_wants_code_line`).
        Without the target-side gate, a plain paragraph that merely shares
        text with an unrelated code node elsewhere in the document (a
        legitimate, already-correct pairing — see
        `test_replacing_a_code_lines_text_deletes_and_inserts_past_the_glyph`)
        would be wrongly outranked by that unrelated code node; gating on
        what the target slot itself wants keeps this scoped to slots that
        actually are code.
        """
        expanded: List[Opcode] = []
        for tag, ci1, ci2, cj1, cj2 in pending:
            if tag == "delete" and ci2 - ci1 > 1:
                for idx in range(ci1, ci2):
                    expanded.append(("delete", idx, idx + 1, cj1, cj1))
            elif tag == "equal" and ci2 - ci1 > 1:
                stride = cj2 - cj1  # "equal" guarantees ci2-ci1 == cj2-cj1.
                for offset in range(ci2 - ci1):
                    idx = ci1 + offset
                    jdx = cj1 + offset if stride else cj1
                    expanded.append(("equal", idx, idx + 1, jdx, jdx + 1))
            else:
                expanded.append((tag, ci1, ci2, cj1, cj2))

        # A candidate id is either ("pos", position) — a singleton "equal"/
        # "delete" entry in `expanded`, matching the prior behavior — or
        # ("interior", position, idx) — a current index still trapped inside
        # the "replace" opcode at `position`. Only "pos" candidates that are
        # currently "equal" can be slots; "interior" candidates can only win.
        by_key: Dict[Tuple, List[Tuple]] = {}
        for pos, (tag, ci1, ci2, _cj1, _cj2) in enumerate(expanded):
            if tag in ("equal", "delete") and ci2 - ci1 == 1:
                by_key.setdefault(self._content_key(cur_slice[ci1 - i1]), []).append(("pos", pos))
            elif tag == "replace":
                for idx in range(ci1, ci2):
                    key = self._content_key(cur_slice[idx - i1])
                    by_key.setdefault(key, []).append(("interior", pos, idx))

        def _current_index(cid: Tuple) -> int:
            if cid[0] == "pos":
                return int(expanded[cid[1]][1])
            return int(cid[2])

        # position -> {idx: (target j1, target j2)} claimed out of a "replace" opcode
        extractions: Dict[int, Dict[int, Tuple[int, int]]] = {}

        for positions in by_key.values():
            slot_ids = [cid for cid in positions if cid[0] == "pos" and expanded[cid[1]][0] == "equal"]
            if not slot_ids or len(positions) < 2:
                continue

            if prefer_code_line:
                # The top-level call runs this over the *whole document*, not one
                # `_repair`-scoped run — so a content-key group here can span
                # slots `_repair` already resolved correctly and independently
                # (e.g. a live heading and a live bullet that both happen to read
                # "Setup"). Without this gate, re-scoring every slot in the group
                # lets an unrelated candidate's raw `_structural_score` outrank an
                # already-fine self-pair on a slot that was never in question
                # (see `test_duplicate_text_on_both_sides_still_saves_every_live_node`).
                # So only a slot whose target actually wants a code line is up for
                # grabs; every other slot's current candidate is pulled out of the
                # pool entirely rather than left in to compete for the code slot.
                code_slot_ids = [
                    sid for sid in slot_ids
                    if self._target_wants_code_line(tgt_slice[expanded[sid[1]][3] - j1])
                ]
                if not code_slot_ids:
                    continue
                non_code_positions = set(slot_ids) - set(code_slot_ids)
                positions = [cid for cid in positions if cid not in non_code_positions]
                slot_ids = code_slot_ids

            slot_targets = {sid: (expanded[sid[1]][3], expanded[sid[1]][4]) for sid in slot_ids}

            pair_scores = []
            for si, sid in enumerate(slot_ids):
                scj1, _scj2 = slot_targets[sid]
                target_node = tgt_slice[scj1 - j1]
                wants_code = prefer_code_line and self._target_wants_code_line(target_node)
                for ci, cid in enumerate(positions):
                    candidate_node = cur_slice[_current_index(cid) - i1]
                    score = self._structural_score(candidate_node, target_node)
                    if (
                        wants_code
                        and isinstance(candidate_node, DocsParagraphNode)
                        and self._is_code_line(candidate_node)
                    ):
                        score += _CODE_LINE_PREFERENCE_BONUS
                    pair_scores.append((score, sid == cid, si, ci, sid, cid))
            pair_scores.sort(key=lambda t: (-t[0], 0 if t[1] else 1, t[2], t[3]))

            assigned_candidate_for: Dict[Tuple, Tuple] = {}
            chosen_candidates = set()
            for _score, _self_pair, _si, _ci, sid, cid in pair_scores:
                if sid in assigned_candidate_for or cid in chosen_candidates:
                    continue
                assigned_candidate_for[sid] = cid
                chosen_candidates.add(cid)

            for sid, cid in assigned_candidate_for.items():
                if cid == sid:
                    continue
                scj1, scj2 = slot_targets[sid]
                if cid[0] == "pos":
                    _, cci1, cci2, _, _ = expanded[cid[1]]
                    expanded[cid[1]] = ("equal", cci1, cci2, scj1, scj2)
                else:
                    _, rpos, idx = cid
                    extractions.setdefault(rpos, {})[idx] = (scj1, scj2)

            for sid in slot_ids:
                if sid not in chosen_candidates:
                    _, sci1, sci2, scj1, _scj2 = expanded[sid[1]]
                    expanded[sid[1]] = ("delete", sci1, sci2, scj1, scj1)

        if not extractions:
            return expanded

        rebuilt: List[Opcode] = []
        new_equals: List[Opcode] = []
        for pos, (tag, ci1, ci2, cj1, cj2) in enumerate(expanded):
            claimed = extractions.get(pos)
            if not claimed:
                rebuilt.append((tag, ci1, ci2, cj1, cj2))
                continue
            for idx, (scj1, scj2) in claimed.items():
                new_equals.append(("equal", idx, idx + 1, scj1, scj2))
            remaining = []
            start = ci1
            for idx in sorted(claimed):
                if idx > start:
                    remaining.append((start, idx))
                start = idx + 1
            if start < ci2:
                remaining.append((start, ci2))
            if not remaining:
                rebuilt.append(("insert", ci1, ci1, cj1, cj2))
            else:
                (first_start, first_end), *rest = remaining
                rebuilt.append(("replace", first_start, first_end, cj1, cj2))
                for start, end in rest:
                    rebuilt.append(("delete", start, end, cj1, cj1))
        rebuilt.extend(new_equals)
        return rebuilt

    @staticmethod
    def _structural_score(node: Node, target_node: Node) -> int:
        """How closely `node`'s non-text attributes already match `target_node`'s.

        Used only to rank candidates in `_prefer_structural_pairing`.
        """
        if isinstance(node, DocsTableNode) or isinstance(target_node, DocsTableNode):
            return 0
        score = 0
        if node.style == target_node.style:
            score += 2
        if is_heading_style(node.style) == is_heading_style(target_node.style):
            score += 1
        if node.is_list_item == target_node.is_list_item:
            score += 1
        return score

    @staticmethod
    def _coalesce(
        opcodes: List[Opcode],
    ) -> List[Opcode]:
        """Merge adjacent same-tag opcodes, so downstream sees runs not singletons.

        `build()` treats a `replace` run as one range, and emitting N single-node
        replaces instead of one N-node replace would change the request shape.
        """
        merged: List[Opcode] = []
        for op in opcodes:
            if merged and merged[-1][0] == op[0] and merged[-1][2] == op[1] and merged[-1][4] == op[3]:
                tag, i1, _, j1, _ = merged[-1]
                merged[-1] = (tag, i1, op[2], j1, op[4])
            else:
                merged.append(op)
        return merged

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
                # "equal" means equal *text*: `_node_key` includes style and
                # bullet, so a restyle lands in a `replace` run, and `_repair`
                # re-tags it as `equal` on `_content_key`. Either way a restyle
                # (heading level, bullet on/off, nesting) arrives here rather
                # than as a replace. It is still a change the user asked
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

        # (anchor_index, is_insert, requests) — the requests for one node or
        # one insert group, the document index they are all written against,
        # and whether this group is an insert (vs. a restyle/delete against
        # pre-existing content).
        #
        # Ordering rule, stated once here because getting it wrong is silent:
        # groups are applied highest-anchor-first (so every edit runs against
        # coordinates nothing has shifted yet). Within a *tied* anchor,
        # non-insert groups (equal-restyle, delete) go first and insert
        # groups go last: every non-insert group's range was computed from
        # `current[...].start_index`, a pre-insert coordinate, so an insert
        # sharing that anchor must not run first — it would shift the range
        # out from under the paragraph it was meant for, corrupting whichever
        # node happens to land in the old range instead (e.g. #42: a live
        # heading demoted and the new paragraph promoted in its place, or a
        # bullet request landing on the wrong paragraph). The `replace`
        # opcode's delete-then-insert pair already got this right by
        # emission order alone; `is_insert` makes that same rule explicit
        # and general instead of accidental.
        #
        # This used to be one flat sort over every request's own startIndex,
        # which is only equivalent while every request in a group shares the
        # anchor. The append-past-the-last-node case broke that: its paragraph
        # sits one index *after* the insert point, so its updateParagraphStyle
        # carried a higher startIndex than the insertText it depends on and
        # sorted ahead of it — a style request against a range that did not
        # exist yet. The anchor is now carried explicitly instead of inferred.
        groups: List[Tuple[int, int, List[dict]]] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for ci, ti in zip(range(i1, i2), range(j1, j2)):
                    requests = self._make_style_update_requests(current[ci], target[ti])
                    if requests:
                        groups.append((current[ci].start_index, 0, requests))

            elif tag == "delete":
                for node in current[i1:i2]:
                    requests = self._make_delete_requests([node], doc_end_index)
                    if requests:
                        groups.append((node.start_index, 0, requests))

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
                    groups.append((insert_at, 1, requests))

            elif tag == "replace":
                delete_start = current[i1].start_index
                for node in current[i1:i2]:
                    requests = self._make_delete_requests([node], doc_end_index)
                    if requests:
                        groups.append((node.start_index, 0, requests))
                # render_prefix and precedes_structural_element both stop the
                # last deleted node's delete range short of a newline that
                # belongs to something else — chrome shared with a following
                # render-glyph paragraph, or the anchor for a following
                # Table/ToC/SectionBreak (see _delete_bounds) — and that
                # newline collapses down to sit at delete_start once the
                # delete runs. It's the last node, not the first, because
                # that's the one bordering what comes after the deleted
                # range once all the deletes in the range have run. The
                # insert therefore lands on an existing newline rather than
                # in front of a following paragraph, the same situation
                # `before_newline` exists for on the "insert" branch above;
                # writing `text + "\n"` there adds a *second* newline on top
                # of the one just protected, splitting the paragraph and
                # leaving a stray empty one behind on every such edit (#56).
                #
                # The doc_end_index clamp in _delete_bounds also spares a
                # newline — the paragraph's own terminator — when the
                # deleted range ends at the document's last paragraph.
                # `before_newline=True` is not the right fix there: that
                # writes "\ntext", which prepends a blank paragraph in
                # front of the clamp-spared newline instead of reusing it.
                # The insert must instead be bare text with no newline on
                # either side, so the clamp-spared newline is reused as the
                # new text's own terminator (see `bare_last` on
                # _make_insert_requests). `precedes_structural_element` is
                # mutually exclusive with this clamp (a following
                # Table/ToC/SectionBreak means last.end_index <
                # doc_end_index), but render_prefix is not — a render-glyph
                # paragraph that is also the doc's last paragraph keeps the
                # existing before_newline=True behavior (#48 takes
                # precedence over this narrower case).
                last = current[i2 - 1]
                spares_structural_newline = isinstance(last, DocsParagraphNode) and (
                    bool(last.render_prefix) or last.precedes_structural_element
                )
                spares_terminal_newline = (
                    not spares_structural_newline
                    and isinstance(last, DocsParagraphNode)
                    and bool(last.text)
                    and last.end_index >= doc_end_index
                )
                requests = self._make_insert_requests(
                    target[j1:j2],
                    delete_start,
                    before_newline=spares_structural_newline,
                    bare_last=spares_terminal_newline,
                )
                if requests:
                    # Same anchor as the first deleted node's group. is_insert=1
                    # places it after that delete group in the sort below, so
                    # the delete still runs before the insert that replaces it.
                    groups.append((delete_start, 1, requests))

        # Descending by anchor so later edits never shift an earlier one's
        # coordinates; at a tied anchor, non-insert groups (is_insert=0)
        # before insert groups (is_insert=1) — see the comment above.
        groups.sort(key=lambda group: (-group[0], group[1]))
        all_requests = [request for _anchor, _is_insert, requests in groups for request in requests]
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
        if isinstance(node, DocsParagraphNode) and node.render_prefix:
            # A paragraph inside a block Docs renders itself. Neither whole-paragraph
            # range works, verified against the live API on a copy of a real document:
            #
            #   [34052,34069)  covers the glyph  -> "Invalid deletion range. Cannot
            #                                       delete the requested range."
            #   [34053,34069)  skips the glyph   -> accepted, and the orphaned glyph
            #                                       merges into the *next* paragraph,
            #                                       which came back "mappings:"
            #
            # The first fails the whole atomic batch (#47); the second corrupts a
            # paragraph the author never touched, invisibly. So delete the text and
            # leave the paragraph: the author's line goes, the block keeps its shape,
            # and what remains is a glyph-only paragraph — the same shape Docs writes
            # for the block's own chrome, which `project()` rule 1b drops from both
            # sides of the diff. That is what keeps push idempotent rather than
            # retrying a delete it can never complete.
            #
            # `start` already skips the prefix: project() advanced it. Computed
            # directly from the text length rather than by decrementing `end`,
            # because `precedes_structural_element` (#55) can be true on the
            # same node — a render-glyph paragraph immediately before a Table,
            # ToC or SectionBreak — and two independent `end -= 1`s would trim
            # the range twice, deleting the author's last character along with
            # the newline.
            end = start + _utf16_len(node.text)
            trimmed = True
        elif isinstance(node, DocsParagraphNode) and node.precedes_structural_element:
            end -= 1
            trimmed = True
        if end >= doc_end_index:
            end = doc_end_index - 1
            trimmed = True
        return start, end, trimmed

    # ──────────────────────────────────────────────
    # Pass 2 — fill table cells from a re-fetched doc
    # ──────────────────────────────────────────────

    def build_table_fill_requests(
        self,
        doc: dict,
        target: List[Node],
        alignment: Optional["Pass2Alignment"] = None,
    ) -> List[dict]:
        """
        Emit insertText requests to fill empty tables created by a prior push (pass 1).

        Pairs live tables with ``target`` DocsTableNodes via `_paired_tables` — the
        same content-aligned, document-order pairing `build_table_cell_span_requests`
        uses — then reads real cell indices from ``doc`` so no index prediction is
        required. A live table that pairs but is not empty (already populated by an
        earlier push) is skipped for insertion, but still consumes its pairing slot,
        so a mix of populated and empty tables cannot shift a later table onto the
        wrong target.
        """
        target_tables = [n for n in target if isinstance(n, DocsTableNode)]
        if not target_tables:
            return []

        aligned = self._aligned(doc, target, alignment)
        inserts: List[Tuple[int, str]] = []
        for table, tnode in self._paired_tables(doc, aligned.table_pairs):
            if not self._table_is_empty(table):
                continue  # already populated (or a pre-existing content table)
            inserts.extend(self._cell_inserts(table, tnode))

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
        for table, tnode in self._paired_tables(doc, aligned.table_pairs):
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
        aligned = self._aligned(doc, target, alignment)
        missed: List[str] = []
        paired = {id(tnode): table for table, tnode in self._paired_tables(doc, aligned.table_pairs)}
        for tnode in target_tables:
            table = paired.get(id(tnode))
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
    def _paired_tables(
        doc: dict, table_pairs: List[Tuple[int, DocsTableNode]]
    ) -> Iterator[Tuple[dict, DocsTableNode]]:
        """Live tables paired with target tables via `_align_for_styling`'s content alignment.

        The single pairing shared by `build_table_fill_requests` and
        `build_table_cell_span_requests` — both used to compute their own
        (one advancing only past *empty* live tables, the other advancing
        unconditionally), which meant a live table's fill and its styling
        could disagree about which target table it corresponded to. `doc` is
        walked here, rather than in `_align_for_styling`, because
        `table_pairs`' ordinals are positions among tables in the *parsed*
        `current` list, and only the raw API dicts in `_body_content(doc)`
        carry the real cell indices `_cell_inserts`/`_table_is_empty`/
        `_cell_placement` need.

        A table has no id, and `_align_for_styling` keys every table on one
        sentinel (`_alignment_key`), so within a content-aligned "equal" run
        tables still pair by relative order — but that order now tracks
        paragraphs difflib finds inserted or removed around a table, unlike
        raw body position, which the previous version of this method used and
        which drifts whenever a concurrent edit shifts table counts between
        pass 1 and pass 2.
        """
        live_tables = [
            element["table"] for element in _body_content(doc) if element.get("table") is not None
        ]
        for ordinal, tnode in table_pairs:
            if ordinal >= len(live_tables):
                continue
            yield live_tables[ordinal], tnode

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
        current, pairs, unaligned, heading_pairs, residue, table_pairs = (
            self._align_for_styling(doc, target)
        )
        slug_to_id, known_ids = self._anchor_resolution(current, target, heading_pairs)
        return Pass2Alignment(
            current=current,
            pairs=pairs,
            unaligned=unaligned,
            table_pairs=table_pairs,
            slug_to_id=slug_to_id,
            known_ids=known_ids,
            residue=residue,
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
        List[Residue],
        List[Tuple[int, DocsTableNode]],
    ]:
        """Pair re-fetched document nodes with ``target`` nodes for pass-2 styling.

        Returns ``(current, pairs, unaligned, heading_pairs, residue,
        table_pairs)`` — the parsed document, pairs of (document node, target
        node) that are safe to style, the target paragraphs with spans that
        could not be paired, the same pairing restricted to target paragraphs
        that are headings, any residue from projecting this second, post-pass-1
        parse of the document, and the table pairing described below.

        ``table_pairs`` pairs a *live table ordinal* (its position among only
        the tables in ``current``, 0-based) with a target ``DocsTableNode``.
        An ordinal rather than the parsed ``DocsTableNode`` itself, because
        every table consumer (`_table_is_empty`, `_cell_inserts`,
        `_cell_placement`) needs the raw API dict from the re-fetched ``doc``,
        which `current` does not carry — the ordinal is what lets a caller find
        that dict back in `_body_content(doc)`. `_alignment_key` collapses
        every table to one sentinel key (see its docstring), so within a
        difflib "equal" run tables pair by position exactly like duplicate
        paragraph text does: order-preserving, but blind to which table's
        *cells* actually match. That is still strictly better than raw body
        position for this purpose, because it tracks paragraphs inserted or
        removed around a table rather than assuming the table's index in the
        body is stable.

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

        This method's own `current` used to parse the live doc unprojected
        (issue #53) while `target` was always projected, so a native
        code-block paragraph's PUA render prefix broke the `_alignment_key`
        text match and pass 2 silently skipped its styling. Every other
        `.text`/`.start_index` consumer reachable from `align()` — including
        `push_preview.find_high_risk_paragraphs`, which reads already-projected
        `current_nodes` from `_build_push_plan` — was audited and found
        unaffected; the full consumer-by-consumer inventory from that audit
        lives in PR #69's description rather than here, to keep this docstring
        from going stale as a permanent record of a one-time investigation.

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
        # Projected, like every other diff/render consumer in this file and in
        # backend.py's _build_push_plan/pull — NOT like `target` here, which
        # backend.py already projected before this method ever sees it (see
        # _alignment_key's docstring). Before this fix `current` was the one
        # exception: it parsed the live doc raw, so a native code-block
        # paragraph's `.text` still carried Docs' Private-Use-Area render
        # prefix while `target`'s never did (MarkdownToParagraphParser never
        # emits one). `_alignment_key` is text-only, so that pair could never
        # produce an "equal" opcode — pass 2 reported every such paragraph in
        # `unaligned_span_targets` and never applied its monospace styling
        # (issue #53). `project()` strips the prefix and advances
        # `start_index` past it (`_without_render_prefix`), which is exactly
        # what `_spans_overflow` and `_span_style_requests` need those fields
        # to mean. Unlike backend.py's default pull path (which discards this
        # same residue kind at `heading_id_to_slug(project(...)[0])`), this
        # residue is threaded through to Pass2Alignment.residue below: an
        # `ambiguous_code_prefix` here would be a paragraph pass 1's own edits
        # left looking like a code block without actually being monospace, and
        # that is not safe to drop silently (see projection.py's Residue
        # docstring) — it must reach the caller the same way the identical
        # residue kind on the original doc parse reaches plan.residue.
        current, current_residue = project(DocsStructureParser().parse(doc))
        opcodes = _bounded_opcodes(
            [self._alignment_key(n) for n in current],
            [self._alignment_key(n) for n in target],
            context="pass-2 styling",
        )

        # Ordinal of each table's position in `current`, among tables only —
        # what `table_pairs` reports instead of the parsed DocsTableNode, since
        # downstream table consumers need the raw dict at that ordinal in
        # `_body_content(doc)`, not the parsed node.
        table_ordinals: Dict[int, int] = {}
        table_count = 0
        for ci, node in enumerate(current):
            if isinstance(node, DocsTableNode):
                table_ordinals[ci] = table_count
                table_count += 1

        pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]] = []
        heading_pairs: List[Tuple[DocsParagraphNode, DocsParagraphNode]] = []
        table_pairs: List[Tuple[int, DocsTableNode]] = []
        aligned_target_indices = set()
        for tag, i1, i2, j1, j2 in opcodes:
            if tag != "equal":
                continue
            for ci, ti in zip(range(i1, i2), range(j1, j2)):
                cnode, tnode = current[ci], target[ti]
                aligned_target_indices.add(ti)
                if isinstance(cnode, DocsTableNode) or isinstance(tnode, DocsTableNode):
                    if isinstance(cnode, DocsTableNode) and isinstance(tnode, DocsTableNode):
                        table_pairs.append((table_ordinals[ci], tnode))
                    continue
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
        return current, pairs, unaligned, heading_pairs, current_residue, table_pairs

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
        requests = self.build_table_fill_requests(doc, target, alignment)
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
        self,
        nodes: List[Node],
        insert_at_index: int,
        before_newline: bool = False,
        bare_last: bool = False,
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

        ``bare_last`` is the mirror case: ``insert_at_index`` sits on a newline
        that terminates the *inserted* text rather than the preceding one — the
        deleted range's own terminal newline, spared by the doc_end_index clamp
        in ``_delete_bounds`` because it is undeletable. Writing either
        ``"text\\n"`` or ``"\\ntext"`` there would duplicate that newline; the
        text must go in bare, with no newline on either side, so the
        clamp-spared newline is reused as the new text's own terminator. It
        only ever applies to the last of ``nodes`` (the one bordering that
        clamp-spared newline once all nodes have been inserted) — earlier
        nodes in a multi-node replace are still followed by more inserted
        text, not by the spared newline, so they keep their own trailing
        ``"\\n"``. Mutually exclusive with ``before_newline``: both describe
        the same insert point, and only one boundary condition can hold.
        """
        assert not (before_newline and bare_last)
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

            is_bare = bare_last and node is nodes[-1]
            # The paragraph's own text always ends up as node.text + "\n",
            # except in bare mode, where the trailing "\n" already exists at
            # the insert point and must not be duplicated.
            if is_bare:
                text = node.text
            elif before_newline:
                text = "\n" + node.text
            else:
                text = node.text + "\n"
            requests.append({
                "insertText": {"location": {"index": insert_at_index}, "text": text}
            })
            paragraph_start = insert_at_index + 1 if before_newline else insert_at_index
            text_len = _utf16_len(node.text) if is_bare else _utf16_len(node.text + "\n")
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

        Emitted for `equal` opcodes, where every restyle lands — either because
        the text and attributes both match, or because `_repair` re-tagged a
        text-identical pair inside a `replace` run. Covers all three attributes the Docs API
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
