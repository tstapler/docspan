"""A live heading must keep its style, because its `headingId` is the anchor.

Every internal cross-reference in a synced document resolves against a
`headingId` that Google Docs assigns when the heading is created. Restyle that
paragraph to NORMAL_TEXT and the id is gone: `[A1](#a1-current-state)` becomes a
dead link, and there is no error anywhere — push succeeds, the text is right, and
only the links are broken.

The cause was in the diff key. `_node_key` was text-only so that a *restyle*
would align rather than delete-and-reinsert, but that weakened correspondence to
"same text ⇒ same paragraph". Where a document heading's text repeats elsewhere
in the markdown, `difflib` could pair the heading with that other occurrence,
leaving the real heading to be restyled as body text.

Measured over 500 seeded single-block edits (`TestNoHeadingIsDemoted`): **8 lost a
heading before the split, 0 after** — 5 restyled to NORMAL_TEXT and 3 deleted
outright, across all three edit kinds. Fenced code blocks made it easy to reach —
one node per line fills the sequence with short generic strings (`}`, `pass`,
`Config`) — but the reproduction below uses **pure prose**, so it predates that
and is not specific to code.
"""
from __future__ import annotations

import random
from collections import Counter
from typing import Dict, List, Optional, Tuple

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsStructureParser
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.projection import project

from .test_gdocs_push_pipeline import DocModel, Para

markdown = MarkdownToParagraphParser()
structure = DocsStructureParser()
builder = DocsRequestBuilder()


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _assert_bmp_only(text: str, where: str) -> None:
    """Fail fast on non-BMP characters rather than silently desyncing indices.

    `ParagraphReplay` stores/mutates `self.chars`/`self.owner` as one entry per
    Python code point and derives lengths from that count, while `_utf16_len`
    (matching the real Docs API) counts UTF-16 code units. Those agree only for
    BMP text; a surrogate-pair character (emoji, astral-plane scripts) would
    make the harness's bookkeeping quietly diverge from the API it's modeling.
    Rewriting the harness to track UTF-16 code units is out of scope here.
    """
    for char in text:
        if ord(char) > 0xFFFF:
            raise ValueError(
                f"ParagraphReplay is BMP-only: {char!r} in {where} requires a "
                "UTF-16 surrogate pair, which this harness cannot represent."
            )


class ParagraphReplay:
    """Replays a batch tracking *which* paragraph owns each character.

    Distinct from `DocModel` on purpose, and composed with it rather than
    replacing it. `DocModel` answers "does this batch obey the API's index
    rules, and what text results" — it rebuilds its units from the flat string,
    so paragraph identity and style are gone by design. This answers the
    orthogonal question: after the batch, does the paragraph that carried the
    `headingId` still exist, and is it still a heading?

    That question cannot be answered from the request list alone. A range there
    may be in pre- or post-insert coordinates depending on how the requests were
    ordered, so a NORMAL_TEXT restyle overlapping a heading's *original* range
    may be a demotion or may be correctly targeting a paragraph inserted in
    front of it. Both readings look identical until the batch is applied in
    order. Every request must therefore be replayed, not inspected.

    `apply` delegates to `DocModel.apply` for the API rule checks, so the rules
    live in one place and an ordering bug still surfaces as `InvalidRange`.
    """

    def __init__(self, paragraphs: List[Tuple[str, str, str, bool]]) -> None:
        self.chars: List[str] = []
        self.owner: List[Optional[str]] = []
        self.style: Dict[str, str] = {}
        self.bullet: Dict[str, bool] = {}
        for text, style, pid, bullet in paragraphs:
            _assert_bmp_only(text, f"paragraph {pid!r}")
            self.style[pid], self.bullet[pid] = style, bullet
            for char in text + "\n":
                self.chars.append(char)
                self.owner.append(pid)

    def _pids(self) -> List[str]:
        seen, ordered = set(), []
        for pid in self.owner:
            if pid is not None and pid not in seen:
                seen.add(pid)
                ordered.append(pid)
        return ordered

    def text_of(self, pid: str) -> str:
        return "".join(c for c, o in zip(self.chars, self.owner) if o == pid).rstrip("\n")

    def alive(self, pid: str) -> bool:
        return pid in self.owner

    def is_heading(self, pid: str) -> bool:
        return self.alive(pid) and self.style[pid].startswith("HEADING")

    def document(self) -> Tuple[dict, int]:
        """Render as `documents.get` would, with a `headingId` on every heading."""
        content, index = [], 1
        for pid in self._pids():
            text = self.text_of(pid)
            style: dict = {"namedStyleType": self.style[pid]}
            if self.style[pid].startswith("HEADING"):
                style["headingId"] = "h." + pid
            paragraph: dict = {
                "paragraphStyle": style,
                "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
            }
            if self.bullet[pid]:
                paragraph["bullet"] = {"listId": "list-1", "nestingLevel": 0}
            end = index + _utf16_len(text) + 1
            content.append({"startIndex": index, "endIndex": end, "paragraph": paragraph})
            index = end
        return {"revisionId": "rev-1", "body": {"content": content}}, index

    def apply(self, requests: List[dict]) -> "ParagraphReplay":
        # Rule checking first, and against the same requests: DocModel raises
        # InvalidRange for an insert past the body or a style range applied
        # before the insert that creates it.
        DocModel([
            Para(self.text_of(pid), self.style[pid], self.bullet[pid]) for pid in self._pids()
        ]).apply(requests)

        inserts = 0
        for request in requests:
            if "deleteContentRange" in request:
                rng = request["deleteContentRange"]["range"]
                low, high = rng["startIndex"] - 1, rng["endIndex"] - 1
                del self.chars[low:high]
                del self.owner[low:high]
            elif "insertText" in request:
                index = request["insertText"]["location"]["index"] - 1
                text = request["insertText"]["text"]
                _assert_bmp_only(text, "an insertText request")
                inserts += 1
                # One fresh id per insert. Finer granularity is not needed: the
                # question is only ever "is this the paragraph that held the
                # headingId, or a new one?"
                pid = f"inserted-{inserts}"
                self.style[pid], self.bullet[pid] = "NORMAL_TEXT", False
                self.chars[index:index] = list(text)
                self.owner[index:index] = [pid] * len(text)
            elif "updateParagraphStyle" in request:
                rng = request["updateParagraphStyle"]["range"]
                named = request["updateParagraphStyle"]["paragraphStyle"].get("namedStyleType")
                if not named:
                    continue
                covered = range(rng["startIndex"] - 1, min(rng["endIndex"] - 1, len(self.owner)))
                for pid in {self.owner[i] for i in covered}:
                    if pid is not None:
                        self.style[pid] = named
        return self


def _replay_of(nodes: List[object]) -> Optional[ParagraphReplay]:
    """A document holding exactly `nodes`, one tracked paragraph each."""
    paragraphs = []
    for position, node in enumerate(nodes):
        if not hasattr(node, "text"):
            return None  # a table; paragraph identity is not the question there
        paragraphs.append((
            node.text, node.style, f"p{position + 1}", bool(getattr(node, "is_list_item", False)),
        ))
    return ParagraphReplay(paragraphs) if paragraphs else None


def _push(replay: ParagraphReplay, md: str) -> ParagraphReplay:
    doc, end = replay.document()
    target, _ = project(markdown.parse(md))
    current, _ = project(structure.parse(doc))
    return replay.apply(builder.build(current, target, end))


class TestTheLiveHeadingSurvives:
    def test_a_body_line_repeating_a_heading_does_not_steal_it(self) -> None:
        """The reproduction, in pure prose.

        The document has a real `Config` heading. The markdown keeps it and adds
        a body line that also reads `Config` *above* it — a lead-in, a summary
        row, a line of a code sample. With a text-only key difflib paired the
        heading with the body line and demoted the heading to NORMAL_TEXT,
        destroying `h.config` and every `#config` anchor with it.
        """
        replay = ParagraphReplay([
            ("Overview", "NORMAL_TEXT", "intro", False),
            ("Config", "HEADING_2", "heading", False),
            ("body", "NORMAL_TEXT", "tail", False),
        ])
        _push(replay, "Overview\n\nConfig\n\n## Config\n\nbody\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.text_of("heading") == "Config"

    def test_a_heading_duplicated_as_body_text_survives_with_its_heading_id_intact(self) -> None:
        """The literal repro from the bug report: a doc-start insert shares an
        anchor with the restyled live heading, and the document also has the
        heading's text duplicated as an unrelated body line.

        Document: `HEADING_2 'Overview'` / `NORMAL_TEXT 'Overview'` (the
        duplicate) / `'tail'`. Markdown restyles the heading to `###` and adds
        a new line above it, so the insert (anchored at doc-start, index 1)
        ties with the heading's `equal`-restyle group (also anchored at index
        1). `is_heading("heading")` alone doesn't discriminate the bug: even
        pre-fix, the restyle request still lands on the heading's paragraph id
        because it's a *superset* of the corrupted (pre-insert) range. What
        the corrupted range actually does is bleed the restyle onto the
        *newly inserted* paragraph too — asserting that paragraph is not also
        a heading is what catches it.
        """
        replay = ParagraphReplay([
            ("Overview", "HEADING_2", "heading", False),
            ("Overview", "NORMAL_TEXT", "dup", False),
            ("tail", "NORMAL_TEXT", "tail", False),
        ])
        _push(replay, "NewLine\n\n### Overview\n\nOverview\n\ntail\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_3"
        assert not replay.is_heading("inserted-1"), (
            f"the restyle range leaked onto the newly inserted paragraph "
            f"(style={replay.style.get('inserted-1')}), which means it was "
            "computed against coordinates the insert had already shifted"
        )
        doc, _ = replay.document()
        headings = [
            p["paragraph"]["paragraphStyle"]["headingId"]
            for p in doc["body"]["content"]
            if p["paragraph"]["paragraphStyle"].get("namedStyleType") == "HEADING_3"
        ]
        assert headings == ["h.heading"], "the original headingId must survive unchanged"

    def test_the_heading_is_restyled_in_place_rather_than_retyped(self) -> None:
        """A genuine restyle must stay an in-place edit.

        This is what the text-only key bought and what `_repair` has to keep: a
        `##` → `###` change must not delete the paragraph, or the `headingId`
        and any comment anchored to it go with it.
        """
        replay = ParagraphReplay([
            ("Config", "HEADING_2", "heading", False),
            ("body", "NORMAL_TEXT", "tail", False),
        ])
        requests = builder.build(
            project(structure.parse(replay.document()[0]))[0],
            project(markdown.parse("### Config\n\nbody\n"))[0],
            replay.document()[1],
        )

        assert not [r for r in requests if "deleteContentRange" in r], requests
        assert [
            r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
            for r in requests if "updateParagraphStyle" in r
        ] == ["HEADING_3"]
        replay.apply(requests)
        assert replay.style["heading"] == "HEADING_3"

    def test_a_restyle_survives_next_to_an_unrelated_deletion_in_the_same_run(self) -> None:
        """A restyle sharing a `replace` run with an unrelated delete must not mispair.

        `_node_key` mismatches both `Unique1` (dropped) and `Config` (restyled)
        against the single surviving target node, so difflib puts them in one
        `replace` run together. `_repair` used to walk that run pairwise by
        position — comparing `Unique1` (position 0) to `Config` (position 0 on
        the target side) — which is not a correspondence, just a coincidence of
        offset. That found no content match, so the *real* `Config` heading
        (position 1) fell off the end of the loop as a bare `delete`, while the
        target's `Config` came in as a fresh `insert`: the live heading, and its
        `headingId`, gone.
        """
        replay = ParagraphReplay([
            ("Unique1", "NORMAL_TEXT", "extra", False),
            ("Config", "HEADING_2", "heading", False),
            ("body", "NORMAL_TEXT", "tail", False),
        ])
        _push(replay, "### Config\n\nbody\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_3"

    def test_a_duplicate_text_sibling_does_not_steal_the_restyle(self) -> None:
        """Two current nodes share `_content_key`; only one target node matches.

        The document has both a plain `Setup` paragraph and a real `Setup`
        heading. The markdown restyles the heading to `###` and drops the plain
        paragraph. `_repair`'s inner `SequenceMatcher` sees two current nodes
        with the same content key ("Setup") and one target node with that key —
        an ambiguous pairing it used to resolve by picking whichever candidate it
        met first (the plain paragraph), restyling *it* into the heading and
        deleting the real one, taking `headingId` with it.
        """
        replay = ParagraphReplay([
            ("Setup", "NORMAL_TEXT", "body", False),
            ("Setup", "HEADING_2", "heading", False),
            ("tail", "NORMAL_TEXT", "tail", False),
        ])
        _push(replay, "### Setup\n\ntail\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_3"
        assert not replay.alive("body")

    def test_duplicate_text_on_both_sides_still_saves_every_live_node(self) -> None:
        """Both the current *and* target side repeat the content key.

        Four current nodes all read "Setup" (a stray body line, the real
        heading, the real bullet, and a stray HEADING_3) and the target keeps
        two of them restyled ("Setup" as `##` and "Setup" as a bullet). Every
        current node sharing a key is a candidate for every target sharing that
        key, not just whichever pairing the inner `SequenceMatcher` happens to
        walk into first — resolving the heading's slot must not be allowed to
        starve the bullet's slot (or vice versa) of the candidate that actually
        matches it.
        """
        replay = ParagraphReplay([
            ("Setup", "NORMAL_TEXT", "body", False),
            ("Setup", "HEADING_2", "heading", False),
            ("Setup", "BULLET", "bullet", True),
            ("Setup", "HEADING_3", "extra", False),
            ("tail", "NORMAL_TEXT", "tail", False),
        ])
        _push(replay, "## Setup\n\n- Setup\n\ntail\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_2"
        assert replay.alive("bullet") and replay.bullet["bullet"], (
            "the live bullet must survive as the bullet, not be swapped out "
            "for one of the other 'Setup' nodes"
        )
        assert not replay.alive("body")
        assert not replay.alive("extra")

    def test_duplicate_trapped_inside_a_replace_block_still_saves_the_heading(self) -> None:
        """The duplicate's real match lives outside the multi-node `replace` run.

        A stray "Setup" body paragraph sits *before* the live "Setup" heading;
        an unrelated paragraph ("Beta") sits *between* them and is also edited.
        The outer `SequenceMatcher` pairs the stray paragraph with the target's
        one "Setup" node as an "equal" — leaving the live heading and "Beta"
        together in one multi-node `replace` block, where `_repair` used to
        never look. `build()`'s replace branch then deletes the whole block
        outright, including the live heading and its `headingId`.
        """
        replay = ParagraphReplay([
            ("Alpha", "NORMAL_TEXT", "p1", False),
            ("Setup", "NORMAL_TEXT", "body", False),
            ("Beta", "NORMAL_TEXT", "p3", False),
            ("Setup", "HEADING_2", "heading", False),
        ])
        _push(replay, "AlphaX\n\n### Setup\n\nBetaX\n")

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_3"
        assert not replay.alive("body")


class TestNoHeadingIsDemoted:
    """Seeded sweep over single-block edits — the regime the bug lives in.

    The word list is short and generic on purpose: collisions are what make a
    text-only key mispair, so a vocabulary of distinctive sentences would never
    reach the defect. 8 of these 500 failed before the split and all pass after —
    but see the scope note: this measures *heading survival across a single-block
    edit*. The duplicate-text-sibling case (two current nodes sharing a
    `_content_key`, one target node) is now covered separately in
    `TestTheLiveHeadingSurvives.test_a_duplicate_text_sibling_does_not_steal_the_restyle`.
    Nor can the synthesized document contain a Private-Use render glyph, a chrome
    paragraph or a monospace run, so `render_prefix` is "" for every node here
    even though the word list is drawn from code fences — that gap is open.
    """

    WORDS = ["Config", "Example", "Setup", "body", "pass", "}", "key: value", "Notes", "A1"]

    def _blocks(self, rng: random.Random, count: int) -> List[str]:
        out = []
        for _ in range(count):
            kind, word = rng.choice(["h", "p", "p", "code", "list"]), rng.choice(self.WORDS)
            if kind == "h":
                out.append(f"{'#' * rng.randint(1, 3)} {word}")
            elif kind == "p":
                out.append(word)
            elif kind == "list":
                out.append(f"- {word}")
            else:
                lines = "\n".join(rng.choice(self.WORDS) for _ in range(rng.randint(1, 3)))
                out.append(f"```sh\n{lines}\n```")
        return out

    def test_a_single_block_edit_never_demotes_a_surviving_heading(self) -> None:
        damage = []
        for seed in range(500):
            rng = random.Random(seed)
            before = self._blocks(rng, rng.randint(3, 12))
            before_nodes, _ = project(markdown.parse("\n\n".join(before) + "\n"))
            replay = _replay_of(before_nodes)
            if replay is None:
                continue

            after = list(before)
            edit, at = rng.choice(["insert", "delete", "restyle"]), rng.randrange(len(after))
            if edit == "insert":
                after.insert(at, self._blocks(rng, 1)[0])
            elif edit == "delete":
                after.pop(at)
            else:
                after[at] = rng.choice(self.WORDS)  # a heading or fence becomes prose
            if not after:
                continue

            after_nodes, _ = project(markdown.parse("\n\n".join(after) + "\n"))
            _push(replay, "\n\n".join(after) + "\n")

            # Counted, not by set membership. A document may legitimately hold two
            # identical headings, and an edit that removes one *must* destroy one
            # id — asking "does some heading with these attributes still exist"
            # cannot tell that apart from destroying an id that should have
            # survived. The property is that at least as many originals survive as
            # the edit leaves room for.
            wanted = Counter((n.style, n.text) for n in after_nodes
                             if n.style.startswith("HEADING"))
            had = Counter((n.style, n.text) for n in before_nodes
                          if n.style.startswith("HEADING"))
            survived: Counter = Counter()
            for position, node in enumerate(before_nodes):
                if node.style.startswith("HEADING") and replay.is_heading(f"p{position + 1}"):
                    survived[(node.style, node.text)] += 1
            for key, count in wanted.items():
                expected = min(count, had[key])
                if survived[key] < expected:
                    style, text = key
                    lost = [f"p{i + 1}" for i, n in enumerate(before_nodes)
                            if (n.style, n.text) == key and not replay.is_heading(f"p{i + 1}")]
                    how = ("deleted" if any(not replay.alive(p) for p in lost)
                           else f"restyled to {replay.style[lost[0]]}")
                    damage.append(f"seed {seed} ({edit}): {text!r} {style} — "
                                  f"{expected} should have survived, {survived[key]} did ({how})")
                    break

        assert damage == [], f"{len(damage)} of 500 edits demoted a live heading:\n" + "\n".join(
            damage[:10]
        )

    def test_pushing_unchanged_content_emits_nothing(self) -> None:
        """Idempotence, over the same corpus.

        Guards the other direction: `_node_key` now carries style and bullet, so
        an over-strict key would make unchanged content look edited and rewrite
        the document on every sync.
        """
        noisy = []
        for seed in range(500):
            rng = random.Random(seed)
            nodes, _ = project(markdown.parse("\n\n".join(self._blocks(rng, rng.randint(3, 12))) + "\n"))
            replay = _replay_of(nodes)
            if replay is None:
                continue
            doc, end = replay.document()
            current, _ = project(structure.parse(doc))
            requests = builder.build(current, nodes, end)
            if requests:
                noisy.append(f"seed {seed}: {len(requests)} requests")

        assert noisy == [], f"{len(noisy)} unchanged documents emitted requests:\n" + "\n".join(
            noisy[:10]
        )
