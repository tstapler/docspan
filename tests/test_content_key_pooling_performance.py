"""AC7: global `_content_key` pooling must not regress performance on a
large, repetitive document.

There is no pre-existing "5000-paragraph fixture" to benchmark against —
grepping the repo for one turns up only two prose comments
(`docs_request_builder.py:1596`, `backend.py:369`) citing a historical,
uncommitted measurement of an *unrelated* pass-2 recomputation issue, not
`_content_key` pooling. This module is the fixture and benchmark AC7 asks
for, authored fresh.

`_prefer_structural_pairing`'s own docstring flags the risk this guards
against: pooling every `_content_key` across the whole document (PR #70) is
"still worst-case O(n^2) in the size of the largest duplicate-content group,
and that cost is no longer bounded by a single run. No size guard exists
yet". That group is sized by `_content_key` (text-only), while the
`DiffTooExpensive` guard's duplicate-run check is keyed on `_node_key`
(style-inclusive) — so a document where every node shares one *text* but
each has a distinct *style* can grow a huge `_content_key` pool while never
tripping the guard. That is exactly the shape built below: many small
groups, each anchored by a unique equal paragraph (so the outer diff can't
collapse everything into one giant replace run) and each containing a
same-text "dup" restyle pair whose style is unique to that group (so no
single `_node_key` repeats more than twice, far under `_MAX_DUPLICATE_RUN`),
while every "dup" node in the whole document shares one `_content_key` and
therefore one pool.
"""
from __future__ import annotations

import time
from typing import List, Tuple

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

builder = DocsRequestBuilder()


def _pooled_restyle_groups(
    group_count: int,
) -> Tuple[List[DocsParagraphNode], List[DocsParagraphNode]]:
    """`group_count` independent restyle-of-a-duplicate groups, all sharing
    one `_content_key` ("dup") but each with a group-unique style, separated
    by group-unique equal anchors."""
    current: List[DocsParagraphNode] = []
    target: List[DocsParagraphNode] = []
    for i in range(group_count):
        current.append(DocsParagraphNode(text=f"anchor_{i}", style="NORMAL_TEXT", is_list_item=False))
        current.append(DocsParagraphNode(text="dup", style=f"HEADING_2_{i}", is_list_item=False))
        current.append(DocsParagraphNode(text="dup", style=f"HEADING_2_{i}", is_list_item=False))
        target.append(DocsParagraphNode(text=f"anchor_{i}", style="NORMAL_TEXT", is_list_item=False))
        target.append(DocsParagraphNode(text="dup", style=f"HEADING_3_{i}", is_list_item=False))
        target.append(DocsParagraphNode(text="dup", style=f"HEADING_2_{i}", is_list_item=False))
    return current, target


def _fastest_of(current: List[DocsParagraphNode], target: List[DocsParagraphNode], repeats: int = 3) -> float:
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        builder._opcodes(current, target)
        best = min(best, time.perf_counter() - t0)
    return best


class TestContentKeyPoolingPerformance:
    """AC7: a large one-`_content_key`-group document still diffs correctly
    and within a reasonable, non-explosive time budget."""

    def test_a_large_pooled_document_resolves_every_group_without_destruction(self) -> None:
        current, target = _pooled_restyle_groups(600)

        opcodes = builder._opcodes(current, target)

        destructive = [op for op in opcodes if op[0] != "equal"]
        assert not destructive, (
            f"a pooled document with 600 independent restyle groups should "
            f"resolve every one in place; found destructive opcodes instead: "
            f"{destructive}"
        )

    def test_pooling_cost_does_not_explode_on_a_larger_document(self) -> None:
        # 150 groups (450 nodes/side) vs. 600 groups (1800 nodes/side) — 4x
        # the input on both sides, well under the `DiffTooExpensive` cell-
        # count guard (1800*1800 < _MAX_COMPARISON_CELLS) so the diff
        # actually reaches `_prefer_structural_pairing` instead of raising.
        small = _pooled_restyle_groups(150)
        large = _pooled_restyle_groups(600)

        small_time = _fastest_of(*small)
        large_time = _fastest_of(*large)

        # The pooling is documented as worst-case O(n^2) in the largest
        # `_content_key` group's size — a 4x input growth is expected to
        # cost roughly 16x, not a multiple order of magnitude more. This is
        # a regression backstop (catches an accidental O(n^3)-or-worse
        # change), not a claim that the current cost is sub-quadratic.
        assert large_time < max(small_time * 40, 5.0), (
            f"pooling cost grew far faster than the documented O(n^2) on "
            f"the largest _content_key group: {small_time:.4f}s at 150 "
            f"groups vs {large_time:.4f}s at 600 groups"
        )

    def test_a_realistic_large_document_completes_within_a_practical_budget(self) -> None:
        # An absolute backstop independent of the ratio check above: even
        # the largest fixture this test suite can build without tripping
        # the comparison-cell guard must resolve well within the time a
        # single push can reasonably spend recomputing a diff.
        current, target = _pooled_restyle_groups(600)

        elapsed = _fastest_of(current, target, repeats=1)

        assert elapsed < 5.0, (
            f"diffing a 1800-node/side pooled document took {elapsed:.2f}s, "
            f"too slow for a single push's diff step"
        )
