"""AC4: the restyle-destruction rate must be a committed, reproducible number.

PR #70's description measured this with a throwaway script exercising
`_opcodes()` directly ("2 words x 4 styles", lengths 2 and 3) and never
committed it: ~15.0% destructive at length 2 and ~36.1% at length 3 on
`main`, 0% on the fix. This module is that script, promoted to a permanent
test, with its own enumeration (every same-word-sequence, differing-style
pair, not just the PR's unspecified subset) rather than a copy of the PR's
numbers: 17.5% destructive at length 2 (168/960) and 38.9% at length 3
(12552/32256) on the reconstructed old behavior, 0% on the fix at both
lengths — close to the PR's figures, not identical, because the exact
enumeration behind those numbers was never committed either.

"Old" behavior is reconstructed directly rather than by reverting the fix:
`_repair` pre-#52 only re-tagged text-identical pairs *inside a `replace`
opcode run* (see `_old_style_repair` below, which mirrors exactly that scope)
and never pooled a standalone `insert`/`delete` pair across the rest of the
document. `_opcodes()`'s outer `SequenceMatcher` pass (`_node_key`-keyed) is
unchanged by the fix, so reusing it and swapping only the repair step isolates
the one behavior this backlog item changed.
"""
from __future__ import annotations

import difflib
import itertools
from typing import List

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode

builder = DocsRequestBuilder()

WORDS = ["A", "B"]
STYLES = ["HEADING_1", "HEADING_2", "HEADING_3", "NORMAL_TEXT"]


def _nodes(words: tuple, styles: tuple) -> List[DocsParagraphNode]:
    return [
        DocsParagraphNode(text=w, style=s, is_list_item=False)
        for w, s in zip(words, styles)
    ]


def _old_style_repair(
    opcodes: List[tuple],
    current: List[DocsParagraphNode],
    target: List[DocsParagraphNode],
) -> List[tuple]:
    """Pre-#52 `_repair`: content-identity resolved only within a `replace` run.

    A standalone `insert` or `delete` opcode (the exact shape difflib produces
    for a duplicated-then-restyled node, per this backlog item) passes through
    untouched — there is no whole-document pooling step to catch it.
    """
    result: List[tuple] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "replace":
            result.append((tag, i1, i2, j1, j2))
            continue
        cur_slice = current[i1:i2]
        tgt_slice = target[j1:j2]
        inner_opcodes = difflib.SequenceMatcher(
            None,
            [builder._content_key(n) for n in cur_slice],
            [builder._content_key(n) for n in tgt_slice],
            autojunk=False,
        ).get_opcodes()
        for itag, ci1, ci2, tj1, tj2 in inner_opcodes:
            aci1, aci2 = i1 + ci1, i1 + ci2
            atj1, atj2 = j1 + tj1, j1 + tj2
            if itag == "equal":
                for off in range(aci2 - aci1):
                    result.append(
                        ("equal", aci1 + off, aci1 + off + 1, atj1 + off, atj1 + off + 1)
                    )
            else:
                result.append((itag, aci1, aci2, atj1, atj2))
    return result


def _is_destructive(opcodes: List[tuple]) -> bool:
    return any(tag != "equal" for tag, *_ in opcodes)


def _restyle_shapes(length: int):
    """Every (current, target) pair sharing a word sequence but differing style.

    Sharing the word sequence is what makes this "restyle-only": nothing about
    the text changed, so a perfect diff would answer every shape with `equal`
    opcodes alone. Any other opcode in the result is diff machinery destroying
    paragraph identity for no textual reason.
    """
    for words in itertools.product(WORDS, repeat=length):
        for cur_styles in itertools.product(STYLES, repeat=length):
            for tgt_styles in itertools.product(STYLES, repeat=length):
                if cur_styles == tgt_styles:
                    continue
                yield _nodes(words, cur_styles), _nodes(words, tgt_styles)


def _measure(length: int):
    total = 0
    old_destructive = 0
    new_destructive = 0
    for current, target in _restyle_shapes(length):
        total += 1
        current_keys = [builder._node_key(n) for n in current]
        target_keys = [builder._node_key(n) for n in target]
        raw_opcodes = difflib.SequenceMatcher(
            None, current_keys, target_keys, autojunk=False
        ).get_opcodes()

        if _is_destructive(_old_style_repair(raw_opcodes, current, target)):
            old_destructive += 1
        if _is_destructive(builder._opcodes(current, target)):
            new_destructive += 1
    return total, old_destructive, new_destructive


class TestRestyleDestructionRate:
    """AC4: measure the restyle-destruction rate on old vs. fixed `_repair`."""

    def test_length_2_destruction_rate(self) -> None:
        total, old_destructive, new_destructive = _measure(2)
        old_rate = 100 * old_destructive / total
        assert old_destructive > 0, (
            f"expected the pre-#52 repair to destroy some restyle-only shapes "
            f"at length 2 (0/{total}) — the reconstructed old behavior no "
            f"longer reproduces the bug this backlog item is about"
        )
        assert new_destructive == 0, (
            f"fix regressed: {new_destructive}/{total} restyle-only shapes "
            f"({100 * new_destructive / total:.1f}%) still destroy paragraph "
            f"identity at length 2 (old rate was {old_rate:.1f}%)"
        )

    def test_length_3_destruction_rate(self) -> None:
        total, old_destructive, new_destructive = _measure(3)
        old_rate = 100 * old_destructive / total
        assert old_destructive > 0, (
            f"expected the pre-#52 repair to destroy some restyle-only shapes "
            f"at length 3 (0/{total}) — the reconstructed old behavior no "
            f"longer reproduces the bug this backlog item is about"
        )
        assert new_destructive == 0, (
            f"fix regressed: {new_destructive}/{total} restyle-only shapes "
            f"({100 * new_destructive / total:.1f}%) still destroy paragraph "
            f"identity at length 3 (old rate was {old_rate:.1f}%)"
        )
