"""A fenced code block is one node per line, because a Doc paragraph holds one.

Emitting the whole block as a single node with embedded newlines made `push`
**delete and reinsert the entire block on every push, forever**. `insertText`
writes "\\nline one\\nline two", which Docs splits into N paragraphs, so every later
diff saw N document paragraphs against 1 markdown node.

Simulated through `tests/test_gdocs_push_pipeline.py`'s `DocModel`: pushes 2, 3 and
4 each emitted 6 requests — `deleteContentRange` plus `insertText` — and the
paragraph text came out **identical** every time. So the harm is not that the text
ends up wrong; it is:

* push was **never idempotent** for any document containing a fenced block;
* every push destroyed and recreated those paragraphs, so a comment anchored to a
  line of code was destroyed on a sync that changed nothing;
* pass 2 reported the block `unaligned` and emitted **zero** span requests, so the
  monospace styling was never applied at all — measured 0 span requests before the
  fix, 2 after, *for a block with no render glyph*. A native Google Docs code block
  does carry one, and `_align_for_styling` used to parse the document
  **unprojected** and key on `node.text`, so the glyph was present on one side and
  absent on the other and pass 2 still emitted zero. That was a third consumer of
  `.text` needing the projected view (`_align_for_styling` was the only remaining
  one — see issue #53 and `TestRenderPrefix.test_align_for_styling_projects_current`
  below); it is now fixed.

The preview reported N removals, which reads as content deletion and is
indistinguishable from content the author removed deliberately. That is what made
it look like data loss, and what kept it unnoticed. See issue #40.
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.projection import project

markdown = MarkdownToParagraphParser()
structure = DocsStructureParser()
builder = DocsRequestBuilder()


class TestCodeBlockLinesDoNotStealAHeadingOrBullet:
    """Splitting fenced blocks per line (#40/#41) makes issue #42 easy to reach.

    A code block contributes duplicate short lines in bulk (`}`, `);`, `EOF`,
    `pass`), and any one matching a heading or list item's text elsewhere in
    the document can share that node's anchor with the surrounding edit's
    insert group. Criterion 6 of #42: this must not demote the heading or
    steal the bullet.
    """

    def test_duplicate_short_code_lines_next_to_a_heading_edit_spare_the_heading(
        self,
    ) -> None:
        """A doc-start insert (a new fenced-code line) shares its anchor with the
        live heading's restyle group — the same collision as criterion 3/6 — while
        several duplicate short code-flavored lines (`}`, `);`, `EOF`, `pass`)
        sit unrelated and unchanged elsewhere in the document as decoys.

        `is_heading("heading")` alone doesn't discriminate the bug: pre-fix, the
        restyle request is still a *superset* of the heading's own range, so the
        heading's paragraph id ends up in the covered set and gets the right style
        regardless. What the corrupted (pre-insert) range actually does is bleed
        the restyle onto the *newly inserted* paragraph too, and — because the
        insert ran before the tied restyle — the live heading is left holding its
        *old* style. Both of those are what the assertions below catch.
        """
        from .test_heading_identity import ParagraphReplay

        replay = ParagraphReplay([
            ("pass", "HEADING_2", "heading", False),
            ("}", "NORMAL_TEXT", "decoyA", False),
            (");", "NORMAL_TEXT", "decoyB", False),
            ("EOF", "NORMAL_TEXT", "item", True),
            ("pass", "NORMAL_TEXT", "decoyD", False),
            ("tail", "NORMAL_TEXT", "tail", False),
        ])
        doc, end = replay.document()
        md = "NewCode\n\n### pass\n\n}\n\n);\n\n- EOF\n\npass\n\ntail\n"
        target, _ = project(markdown.parse(md))
        current, _ = project(structure.parse(doc))
        replay.apply(builder.build(current, target, end))

        assert replay.is_heading("heading"), (
            f"the live heading was restyled to {replay.style['heading']}, "
            "so its headingId is gone and every anchor to it is dead"
        )
        assert replay.style["heading"] == "HEADING_3"
        assert not replay.is_heading("inserted-1"), (
            f"the restyle range leaked onto the newly inserted code line "
            f"(style={replay.style.get('inserted-1')}), which means it was "
            "computed against coordinates the insert had already shifted"
        )
        assert replay.alive("item") and replay.bullet["item"], (
            "the live list item must survive as the bullet, not be swapped out "
            "for one of the duplicate decoy lines"
        )


def _doc_of_lines(*lines: str) -> tuple[dict, int]:
    """A document holding one paragraph per line — how Docs actually stores it."""
    content, idx = [], 1
    for text in lines:
        content.append({
            "startIndex": idx,
            "endIndex": idx + len(text) + 1,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
            },
        })
        idx += len(text) + 1
    return {"revisionId": "rev-1", "body": {"content": content}}, idx


class TestGranularity:
    def test_each_line_of_a_fenced_block_is_its_own_node(self) -> None:
        nodes = markdown.parse("```yaml\nkey: value\n  indented: yes\n```\n")
        # A literal, non-monospace marker line carries the fence language
        # (issue #45 AC1) ahead of the monospace code lines.
        assert [node.text for node in nodes] == ["```yaml", "key: value", "  indented: yes"]
        code_nodes = nodes[1:]
        # Every code line stays monospace, so the styling survives the split.
        assert all(node.spans[0].monospace for node in code_nodes)
        assert not nodes[0].spans

    def test_indentation_is_preserved(self) -> None:
        """`strip()` ate leading whitespace; in code that is meaning, not padding."""
        nodes = markdown.parse("```\n    deeply indented\n```\n")
        assert [node.text for node in nodes] == ["    deeply indented"]

    def test_no_top_level_node_carries_an_embedded_newline(self) -> None:
        """The invariant the bug violated. A Doc paragraph cannot hold a newline.

        `block_code` is parsed at three sites (top level, list items,
        blockquotes) and all three now share `_nodes_from_code_block` — see
        `TestFenceInAListItem` and `TestFenceInABlockQuote` below for the other
        two.
        """
        nodes = markdown.parse(
            "before\n\n```sh\none\ntwo\nthree\n```\n\n```py\nfour\n```\n\nafter\n"
        )
        assert all("\n" not in node.text for node in nodes if hasattr(node, "text"))


class TestFenceInAListItem:
    def test_a_fence_in_a_list_item_is_split_per_line(self) -> None:
        nodes = markdown.parse("- Steps:\n\n  ```sh\n  make build\n  make test\n  ```\n")

        heading = next(n for n in nodes if n.text == "Steps:")
        assert heading.is_list_item is True

        code_lines = [n for n in nodes if n.text in ("make build", "make test")]
        assert [n.text for n in code_lines] == ["make build", "make test"]
        for line in code_lines:
            assert line.is_list_item is True
            assert line.nesting_level == 0
            assert line.spans[0].monospace is True

        assert all("\n" not in n.text for n in nodes)

    def test_a_fence_at_the_start_of_a_list_item_emits_no_stray_node(self) -> None:
        """No prose precedes the fence — nothing should flush an empty node."""
        nodes = markdown.parse("- ```sh\n  make build\n  ```\n")
        assert [n.text for n in nodes] == ["make build"]

    def test_multiple_fences_in_one_list_item_stay_separate(self) -> None:
        nodes = markdown.parse(
            "- Steps:\n\n  ```sh\n  one\n  ```\n\n  ```sh\n  two\n  ```\n"
        )
        assert [n.text for n in nodes] == ["Steps:", "one", "two"]

    def test_prose_after_a_fence_in_a_list_item_is_not_glued_or_dropped(self) -> None:
        """The `spans = []` reset after emitting a fence's nodes must let a
        trailing sibling line re-accumulate on its own, not vanish or merge
        into the last code line."""
        nodes = markdown.parse(
            "- Steps:\n\n  ```sh\n  make build\n  ```\n\n  Done.\n"
        )
        assert [n.text for n in nodes] == ["Steps:", "make build", "Done."]
        trailing = next(n for n in nodes if n.text == "Done.")
        assert trailing.is_list_item is True
        assert trailing.spans == []

    def test_a_fence_nested_two_lists_deep_carries_its_nesting_level(self) -> None:
        nodes = markdown.parse(
            "- outer\n  - inner:\n\n    ```sh\n    cmd\n    ```\n"
        )
        code = next(n for n in nodes if n.text == "cmd")
        assert code.is_list_item is True
        assert code.nesting_level == 1

    def test_pushing_an_unchanged_document_with_a_listed_fence_emits_no_requests(self) -> None:
        """Mirrors #40's idempotence regression test, for the list-item site.

        Before this fix the fence fell through `_walk_list_items` as raw
        multi-line text glued onto the list item's own text, which pushed a
        `deleteContentRange` + `insertText` pair on every sync of an untouched
        document.
        """
        md = "- Steps:\n\n  ```sh\n  make build\n  make test\n  ```\n"
        doc, end = _doc_of_lines("Steps:", "make build", "make test")
        for element in doc["body"]["content"]:
            element["paragraph"]["bullet"] = {"listId": "list-1"}

        target, _ = project(markdown.parse(md))
        current, _ = project(structure.parse(doc))

        assert builder.build(current, target, end) == []


class TestFenceInABlockQuote:
    """gdocs-native-blockquotes Story 2.1 replaced the literal "> "-prefix
    scheme these tests originally pinned with `is_blockquote`/`quote_depth`
    tagging (native Docs border/indent styling instead of literal text) — see
    `_walk_block_quote` in markdown_to_paragraph_parser.py. Updated in place
    to assert the new node shape rather than the retired prefix text.
    """

    def test_a_fence_in_a_block_quote_is_tagged_per_line(self) -> None:
        nodes = markdown.parse("> Note:\n>\n> ```sh\n> kubectl get pods\n> kubectl logs -f\n> ```\n")

        note = next(n for n in nodes if n.text == "Note:")
        code_lines = [n for n in nodes if n.text.startswith("kubectl")]
        assert [n.text for n in code_lines] == ["kubectl get pods", "kubectl logs -f"]
        for line in code_lines:
            assert line.spans and line.spans[-1].monospace is True
            assert line.is_blockquote is True
            assert line.quote_depth == 1
        assert note.text == "Note:"
        assert note.is_blockquote is True

    def test_a_fence_two_quote_levels_deep_gets_quote_depth_two(self) -> None:
        nodes = markdown.parse("> > ```sh\n> > cmd\n> > ```\n")
        cmd = next(n for n in nodes if n.text == "cmd")
        assert cmd.is_blockquote is True
        assert cmd.quote_depth == 2

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Known cross-epic gap, not a regression: gdocs-native-blockquotes "
            "Epic 2 (push) tags markdown-side blockquote nodes with "
            "is_blockquote=True/quote_depth, but Epic 3 (pull — "
            "docs_structure_parser.py reading a live paragraph's "
            "borderLeft/indentStart back into those same fields) has not "
            "landed yet. `_doc_of_lines` builds a live-doc paragraph with no "
            "such styling, so `structure.parse` always produces "
            "is_blockquote=False on the current side, which now differs from "
            "the markdown target's is_blockquote=True and emits a restyle "
            "request. This will start passing once Epic 3 lands; if it does "
            "so unexpectedly (strict=True), remove this marker."
        ),
    )
    def test_pushing_a_document_with_a_quoted_fence_does_not_delete_it(self) -> None:
        """The item's core repro: a quoted fence already in the live doc must
        survive an unchanged push, not be diffed away as a removal.
        """
        md = "intro\n\n> Note:\n>\n> ```sh\n> kubectl get pods\n> kubectl logs -f\n> ```\n\ntail\n"
        doc, end = _doc_of_lines(
            "intro", "Note:", "kubectl get pods", "kubectl logs -f", "tail",
        )

        target, _ = project(markdown.parse(md))
        current, _ = project(structure.parse(doc))

        assert builder.build(current, target, end) == []

    def test_a_list_item_inside_a_block_quote_containing_a_fence(self) -> None:
        """Composition: fence -> list item -> blockquote, all three fixes at once."""
        nodes = markdown.parse(
            "> - Steps:\n>\n>   ```sh\n>   make build\n>   ```\n"
        )
        code = next(n for n in nodes if n.text == "make build")
        assert code.is_list_item is True
        assert code.is_blockquote is True
        assert code.spans and code.spans[-1].monospace is True

    def test_a_blank_line_in_a_quoted_fence_still_renders_as_an_empty_tagged_node(
        self,
    ) -> None:
        """A blank code line inside a top-level fence is dropped entirely
        (empty text, no span — `projection.py` removes it from both sides on
        `text == ""`). Inside a blockquote it survives instead, because
        `projection.py`'s Story 2.5 carve-out keeps any empty node tagged
        `is_blockquote=True` — a vanishing line mid-quote would otherwise
        break the blockquote's visual continuity.
        """
        nodes = markdown.parse("> ```sh\n> one\n>\n> two\n> ```\n")
        # Story 2.1 also fixed the language marker to fire inside a
        # blockquote (`emit_language_marker=True` at this call site), so the
        # fence's "sh" language now heads the node list here too.
        assert [n.text for n in nodes] == ["```sh", "one", "", "two"]
        blank = nodes[2]
        assert blank.spans == []
        assert blank.is_blockquote is True


class TestPushIsIdempotent:
    def test_an_unchanged_document_with_a_code_block_emits_no_requests(self) -> None:
        """The regression test for #40, stated as the property that was broken.

        Before the fix this emitted a delete plus a reinsert of the whole block on
        a document nobody had edited, and did so on every push forever.
        """
        md = "before\n\n```yaml\nkey: value\n  indented: yes\nafter\n```\n\ntail\n"
        # The literal, non-monospace "```yaml" marker line (issue #45 AC1) is
        # itself a real paragraph in the live document, ahead of the code lines.
        doc, end = _doc_of_lines(
            "before", "```yaml", "key: value", "  indented: yes", "after", "tail"
        )

        target, _ = project(markdown.parse(md))
        current, _ = project(structure.parse(doc))

        assert builder.build(current, target, end) == []

    def test_no_single_insert_writes_more_than_one_paragraph(self) -> None:
        """The mechanism, pinned at the request level.

        Docs starts a new paragraph at every newline in an `insertText`, so one
        insert carrying two lines creates two paragraphs that the next diff cannot
        match to their single source node. Each insert must therefore carry exactly
        one line and exactly one newline.

        Deliberately agnostic about *where* that newline sits: the builder appends
        with a trailing "\\n" and inserts mid-document with a leading one, and
        which applies depends on `doc_end_index`. Pinning the placement pinned an
        implementation detail — and the first version of this test used
        `doc_end_index=1`, which no real document reports (an empty Doc is 2) and
        which makes `build()` emit an insert at index 0 that the API rejects.

        Asserted on the requests rather than by replaying them: they are emitted
        highest-index-first so they compose, and reconstructing the resulting
        document here would be testing my model of Docs instead of the builder.
        """
        md = "```sh\nline one\nline two\nline three\n```\n"
        target, _ = project(markdown.parse(md))

        texts = [
            request["insertText"]["text"]
            for request in builder.build([], target, 2)
            if "insertText" in request
        ]
        assert sorted(text.strip("\n") for text in texts) == [
            "```sh", "line one", "line three", "line two",
        ]
        for text in texts:
            assert text.count("\n") == 1, text


# ─────────────────────────────────────────────────────────────────────────────
# The render glyph Docs puts in front of a native code block
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderPrefix:
    """The glyph Docs writes in front of a paragraph it renders itself.

    Recorded, not stripped. `.text` stays faithful to the document because two
    groups of consumers read it — the index arithmetic needs what the document
    contains, the diff and renderer need what the markdown says — and an earlier
    attempt to strip it at parse time made the parser lie to the first group.

    Both whole-paragraph delete ranges are wrong, verified against the live API on
    a throwaway copy of a real document:

    * `[34052,34069)` covers the glyph -> `Invalid deletion range. Cannot delete the
      requested range.` `batchUpdate` is atomic, so one such delete fails the whole
      push and the document cannot be synced at all (#47).
    * `[34053,34069)` skips the glyph -> **accepted**, and the orphaned glyph merges
      into the following paragraph, which came back reading `\ue907mappings:`. The
      next pull strips it and reports zero requests, so it is permanent and silent.
    """

    def _code_block_doc(self) -> tuple[dict, int]:
        """The shape a live document actually reports for a native code block.

        Taken from a real document: the glyph is its **own** leading textRun with an
        empty textStyle, content follows in monospace runs, and the block's chrome is
        a glyph-only paragraph carrying the border/shading style.
        """
        mono = {"fontSize": {"magnitude": 9, "unit": "PT"},
                "weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}}
        paragraphs = [
            [("Intro\n", {})],
            [("\ue907", {}), ("# cfg\n", mono)],
            [("\ue907\n", {})],
            [("Tail\n", {})],
        ]
        content, index = [], 1
        for runs in paragraphs:
            text = "".join(c for c, _ in runs)
            end = index + len(text.encode("utf-16-le")) // 2
            content.append({"startIndex": index, "endIndex": end, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": c, "textStyle": st}} for c, st in runs],
            }})
            index = end
        return {"revisionId": "rev-1", "body": {"content": content}}, index

    def test_the_parser_keeps_the_glyph_and_records_it(self) -> None:
        doc, _ = self._code_block_doc()
        node = structure.parse(doc)[1]
        assert node.render_prefix == "\ue907"
        assert node.text == "\ue907# cfg", "text must stay faithful to the document"
        assert "".join(span.text for span in node.spans) == node.text
        assert node.start_index == 7, "the index must not move; the API counted the glyph"

    def test_an_empty_leading_run_cannot_hide_the_glyph(self) -> None:
        """The shape that destroyed a character.

        Matching `lstrip` against the concatenated text and then walking spans
        stopped at the empty run, read it as "no glyph here", and reconciled the
        resulting length mismatch by trimming from the *end* — so `code line` came
        back as `code lin` with the glyph still attached. Matching per run cannot
        reach that state: an empty run is skipped, not treated as a terminator.
        """
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 12, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {"textRun": {"content": "", "textStyle": {}}},
                    {"textRun": {"content": "\ue907", "textStyle": {}}},
                    {"textRun": {"content": "code line\n", "textStyle": {}}},
                ]}}]}}
        node = structure.parse(doc)[0]
        assert node.render_prefix == "\ue907"
        assert node.text == "\ue907code line"
        assert "".join(span.text for span in node.spans) == node.text

    def test_an_authors_own_private_use_character_is_left_alone(self) -> None:
        """U+F8FF is the Apple logo. Nerd Fonts live in the PUA too.

        Treating any leading PUA as an artifact silently altered legitimate content:
        the character was eaten on read, so it never matched the markdown and push
        emitted a delete-and-reinsert that dropped it. An author types such a
        character *inside* a run with their text, so the run is not entirely PUA.
        """
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 15, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": "\uf8ff macOS notes\n", "textStyle": {}}}],
            }}]}}
        node = structure.parse(doc)[0]
        assert node.render_prefix == ""
        assert node.text == "\uf8ff macOS notes"

    def test_projection_hides_the_prefix_from_the_diff_but_keeps_the_flag(self) -> None:
        doc, _ = self._code_block_doc()
        kept, residue = project(structure.parse(doc))
        code = [n for n in kept if getattr(n, "render_prefix", "")]
        assert len(code) == 1
        assert code[0].text == "# cfg", "the diff must see what the markdown says"
        assert code[0].start_index == 8, "start_index advances so pass 2 still lands"
        assert "".join(s.text for s in code[0].spans) == "# cfg"
        assert code[0].render_prefix == "\ue907", "the builder needs this to spare the block"
        # The block's own chrome paragraph is unrepresentable and is dropped.
        assert [r.kind for r in residue] == ["private_use_glyph"]

    def test_a_glyph_only_paragraph_padded_with_spaces_is_still_dropped(self) -> None:
        """A chrome paragraph is "entirely PUA ignoring surrounding whitespace" (projection.py's
        Rule 1b docstring) — not just ignoring a trailing newline. `_is_all_private_use` used to
        strip only "\\n", so a glyph padded with spaces/tabs read as mixed content and fell
        through to the diff/delete path instead of being dropped as residue.
        """
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 5, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": "  \n", "textStyle": {}}}],
            }}]}}
        kept, residue = project(structure.parse(doc))
        assert kept == []
        assert [r.kind for r in residue] == ["private_use_glyph"]

    def test_a_prefix_glyph_followed_by_non_monospace_text_is_flagged_as_ambiguous(
        self,
    ) -> None:
        """An author's own PUA character can land alone in its leading run too.

        Nothing in the parsed API data distinguishes that from Docs' own chrome glyph
        — both are a lone PUA run with an empty `textStyle`. A real code block's first
        line is monospace, so when what follows the dropped prefix is not, this may be
        the author's own character being silently discarded rather than chrome, and it
        is reported instead of assumed safe.
        """
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 15, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {"textRun": {"content": "", "textStyle": {}}},
                    {"textRun": {"content": "bold notes\n", "textStyle": {"bold": True}}},
                ],
            }}]}}
        kept, residue = project(structure.parse(doc))
        assert [r.kind for r in residue] == ["ambiguous_code_prefix"]
        assert kept[0].text == "bold notes", "the paragraph is still kept, not dropped"

    def test_a_prefix_followed_by_a_non_courier_monospace_font_is_not_flagged(
        self,
    ) -> None:
        """"Courier"/"mono" alone missed every other font Docs' own code-block
        picker offers — a real code block set in Consolas tripped
        `ambiguous_code_prefix` on every single push.
        """
        mono = {"fontSize": {"magnitude": 9, "unit": "PT"},
                "weightedFontFamily": {"fontFamily": "Consolas", "weight": 400}}
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 15, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {"textRun": {"content": "", "textStyle": {}}},
                    {"textRun": {"content": "# cfg\n", "textStyle": mono}},
                ],
            }}]}}
        kept, residue = project(structure.parse(doc))
        assert residue == []
        assert kept[0].text == "# cfg"

    def test_an_unchanged_code_block_emits_nothing(self) -> None:
        """The #47 path: a document with a native code block must be pushable."""
        doc, end = self._code_block_doc()
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\n```\n# cfg\n```\n\nTail\n"))
        assert builder.build(current, target, end) == []

    def test_removing_the_code_line_deletes_its_text_and_spares_the_paragraph(self) -> None:
        """Neither whole-paragraph range is emitted — see the class docstring.

        The delete must start past the glyph, so the API accepts it, and stop before
        the newline, so the glyph is not orphaned onto the next paragraph.
        """
        doc, end = self._code_block_doc()
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\nTail\n"))
        deletes = [r["deleteContentRange"]["range"]
                   for r in builder.build(current, target, end)
                   if "deleteContentRange" in r]

        assert deletes == [{"startIndex": 8, "endIndex": 13}], (
            "8 skips the glyph at 7, which the API refuses to delete; 13 stops before "
            "the newline at 13, which would otherwise merge the glyph into 'Tail'"
        )

    def test_the_leftover_glyph_paragraph_does_not_make_push_repeat_itself(self) -> None:
        """What makes sparing the paragraph safe rather than merely non-destructive.

        The delete leaves a glyph-only paragraph. `project()` rule 1b drops that from
        both sides, so the next diff does not see it and does not try again — which is
        what a whole-paragraph delete could never achieve, the API having refused it.
        """
        doc, end = self._code_block_doc()
        after = {"revisionId": "rev-2", "body": {"content": [
            dict(el) for el in doc["body"]["content"]
        ]}}
        # The document as it stands once the delete above has been applied.
        after["body"]["content"][1] = {
            "startIndex": 7, "endIndex": 8, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": "\ue907\n", "textStyle": {}}}]}}
        # Recompute every index rather than patching the shift piecemeal.
        index = 1
        for element in after["body"]["content"]:
            text = "".join(r["textRun"]["content"] for r in element["paragraph"]["elements"])
            element["startIndex"] = index
            index += len(text.encode("utf-16-le")) // 2
            element["endIndex"] = index

        current, _ = project(structure.parse(after))
        target, _ = project(markdown.parse("Intro\n\nTail\n"))
        assert builder.build(current, target, index) == []

    def test_align_for_styling_projects_current(self) -> None:
        """Issue #53: pass 2 must strip the glyph from `current`, not just `target`.

        Every other `DocsStructureParser().parse()` call site in the google_docs
        backend feeds the result through `project()` before diffing (or is
        `preview_push`'s deliberately-unprojected exception, which never reaches
        this method). `_align_for_styling` was the one place that parsed the live
        doc raw: `target` (already projected by `backend.py`) read `# cfg`, but
        `current` still carried the glyph and read `# cfg`. `_alignment_key`
        is text-only, so that pair could never produce an "equal" opcode — the
        code line was reported in `unaligned_span_targets` and its monospace
        styling was silently dropped on every push of a document with a native
        code block. Confirmed against the pre-fix code (git stash) to emit
        `requests == []` and the node in `unaligned`; this asserts the fixed
        behavior.
        """
        doc, _ = self._code_block_doc()
        target, _ = project(markdown.parse("Intro\n\n```\n# cfg\n```\n\nTail\n"))
        requests = builder.build_span_style_requests(doc, target)
        unaligned = builder.unaligned_span_targets(doc, target)
        assert unaligned == []
        assert requests == [{
            "updateTextStyle": {
                "range": {"startIndex": 8, "endIndex": 13},
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}
                },
                "fields": "weightedFontFamily",
            }
        }]

    def test_align_surfaces_residue_from_its_own_current_parse(self) -> None:
        """The second half of #53's fix: `current`'s residue must reach the caller.

        `_align_for_styling` re-parses the live document post-pass-1 and used to
        discard that parse's residue outright (`current, _ = project(...)`).
        An `ambiguous_code_prefix` there — a paragraph whose leading character
        looks like Docs' code-block glyph but is not actually monospace — is
        exactly the residue kind `project()`'s docstring says is unsafe to drop
        silently, and pass 1's own edits are as capable of producing it as the
        original document is. `align()` is the public entry point `push()`
        calls, so this asserts on `Pass2Alignment.residue` rather than reaching
        into the private method directly.
        """
        doc = {"revisionId": "rev-1", "body": {"content": [{
            "startIndex": 1, "endIndex": 15, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {"textRun": {"content": "", "textStyle": {}}},
                    {"textRun": {"content": "bold notes\n", "textStyle": {"bold": True}}},
                ],
            }}]}}
        target, _ = project(markdown.parse("bold notes\n"))
        alignment = builder.align(doc, target)
        assert [r.kind for r in alignment.residue] == ["ambiguous_code_prefix"]

    def test_push_surfaces_pass_2_residue_from_its_own_reparse(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """The wiring, not just the pure function above: `push()` must forward it.

        `align()`'s residue (proven by the test above) only reaches a caller of
        `push()` if `backend.py` actually reads `alignment.residue` and threads
        it through `describe_residue` — that plumbing (`pass2_residue` in
        `GoogleDocsBackend.push`) had no coverage of its own: deleting all four
        touch points (the declaration, the assignment, and both
        `describe_residue(pass2_residue)` call sites) left the full suite green.
        This drives the real `push()` entry point against a document that
        produces an `ambiguous_code_prefix` residue on pass 2's own re-parse —
        not pass 1's — so dropping that wiring again fails a test instead of
        only a manual revert.
        """
        backend, fake_client = make_backend()
        before = {"revisionId": "rev-1", "body": {"content": []}}
        after = {"revisionId": "rev-2", "body": {"content": [{
            "startIndex": 1, "endIndex": 15, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {"textRun": {"content": "", "textStyle": {}}},
                    {"textRun": {"content": "bold notes\n", "textStyle": {"bold": True}}},
                ],
            }}]}}
        fake_client.get_document.side_effect = [before, after]
        local = tmp_path / "doc.md"
        local.write_text("**bold notes**\n", encoding="utf-8")

        result = backend.push(str(local), "doc-1")

        assert result.status in ("ok", "warning"), result.message
        assert "code-block chrome" in (result.message or ""), result.message


class TestRenderPrefixParticipatesInIdentity:
    """A prose paragraph and a code-block line with the same text are not the same node.

    `_node_key` used to be `(style, is_list_item, nesting_level, text)` and
    `_content_key` just `(text,)` — neither read `render_prefix`, so a Docs-rendered
    code line (style NORMAL_TEXT, monospace, glyph-prefixed) and a plain prose
    paragraph reading the same text (also NORMAL_TEXT once projected) produced
    identical keys. difflib and `_repair` then had no way to tell them apart: the
    prose paragraph could pair `equal` with the code line (trapping it inside the
    rendered block forever, no glyph change but wrong classification), or a
    `replace` spanning both could send `_make_insert_requests` a `delete_start`
    that `project()` had already advanced *past* the glyph — landing the insert
    inside the Docs-rendered block. See issue #54.
    """

    def _doc_prose_and_code_sharing_text(
        self, text: str, code_first: bool = False
    ) -> tuple[dict, int]:
        """A document with a plain prose paragraph and a rendered code line, same text.

        Same JSON shape as `TestRenderPrefix._code_block_doc`, with an
        extra plain paragraph reading the same text ahead of the code line
        (or after it, when `code_first` is set — see
        `test_replacing_a_code_lines_text_deletes_and_inserts_past_the_glyph`,
        which needs that ordering to actually put `_node_key`'s
        `_is_code_line` signal to work).
        """
        mono = {"fontSize": {"magnitude": 9, "unit": "PT"},
                "weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}}
        prose = [(text + "\n", {})]
        code = [("", {}), (text + "\n", mono)]
        code_chrome = [("\n", {})]
        if code_first:
            paragraphs = [
                [("Intro\n", {})],
                code,
                code_chrome,
                prose,
                [("Tail\n", {})],
            ]
        else:
            paragraphs = [
                [("Intro\n", {})],
                prose,
                code,
                code_chrome,
                [("Tail\n", {})],
            ]
        content, index = [], 1
        for runs in paragraphs:
            raw = "".join(c for c, _ in runs)
            end = index + len(raw.encode("utf-16-le")) // 2
            content.append({"startIndex": index, "endIndex": end, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": c, "textStyle": st}} for c, st in runs],
            }})
            index = end
        return {"revisionId": "rev-1", "body": {"content": content}}, index

    def test_node_key_distinguishes_prose_from_a_code_line_with_the_same_text(self) -> None:
        doc, _ = self._doc_prose_and_code_sharing_text("cfg")
        nodes = structure.parse(doc)
        prose, code = nodes[1], nodes[2]
        assert prose.text == "cfg" and code.text == "cfg"
        assert prose.render_prefix == "" and code.render_prefix == ""

        # Projection strips the glyph from `.text`, so once projected the two
        # nodes read identical text and style — exactly the collision the old
        # keys could not see past.
        kept, _ = project(nodes)
        projected_prose = next(n for n in kept if n.text == "cfg" and not n.render_prefix)
        projected_code = next(n for n in kept if n.text == "cfg" and n.render_prefix)
        assert projected_prose.style == projected_code.style == "NORMAL_TEXT"

        assert builder._node_key(projected_prose) != builder._node_key(projected_code)

        # `_content_key` deliberately stays text-only and does NOT distinguish
        # them — it only classifies a pairing `_node_key` has already made
        # (see `_content_key`'s docstring). `_node_key`'s split above is what
        # keeps this pair out of the same `replace` run in the first place, so
        # `_content_key` never gets a chance to conflate them.
        assert builder._content_key(projected_prose) == builder._content_key(projected_code)

    def test_replacing_a_code_lines_text_deletes_and_inserts_past_the_glyph(self) -> None:
        """Bullet 3's literal reproduction, arranged as a collision so it actually
        exercises `_node_key`'s `_is_code_line` split rather than only the
        index arithmetic in isolation.

        Same shared-text setup as
        `test_node_key_distinguishes_prose_from_a_code_line_with_the_same_text`,
        but with the code line ordered *before* the prose line
        (`code_first=True`). With `_is_code_line` reverted out of `_node_key`,
        difflib's positional matching binds the *code* node — not the prose
        one — to the target's unchanged `"cfg"` slot: the delete/insert meant
        for the changed code line then lands on the unrelated prose
        paragraph instead, and the actual Docs-rendered paragraph is left
        untouched, silently keeping its stale text. That is the #54 failure
        mode: a `replace` whose current side is a Docs-rendered paragraph
        must land relative to the index `project()` already advanced past
        the glyph, which cannot happen if the wrong node absorbs the edit in
        the first place. Confirmed by temporarily dropping `_is_code_line`
        from `_node_key`: this test fails (the prose paragraph's range gets
        overwritten and the code paragraph's stale text survives untouched).

        A separate, harder collision — where `_node_key` alone still cannot
        resolve which current node should absorb a *single* target slot — is
        exercised and resolved by
        `test_a_prose_line_repeating_a_code_lines_text_is_disambiguated_in_favor_of_the_code_line`
        below.
        """
        doc, end = self._doc_prose_and_code_sharing_text("cfg", code_first=True)
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\ncfg\n\n```\nnew_cfg\n```\n\nTail\n"))

        code_node = next(n for n in current if n.render_prefix)
        prose_node = next(n for n in current if n.text == "cfg" and not n.render_prefix)
        requests = builder.build(current, target, end)

        def edit_starts():
            for request in requests:
                for key in ("deleteContentRange", "insertText"):
                    if key not in request:
                        continue
                    rng = request[key].get("range") or {
                        "startIndex": request[key].get("location", {}).get("index")
                    }
                    start = rng.get("startIndex")
                    if start is not None:
                        yield start

        starts = list(edit_starts())

        # The prose paragraph reads "cfg" in both current and target — it must
        # not be touched at all, let alone absorb the code line's edit.
        assert not any(
            prose_node.start_index <= start < prose_node.end_index for start in starts
        ), f"a request rewrote the unrelated prose paragraph instead of the code line: {requests}"

        # The code paragraph's own text must actually change to "new_cfg" ...
        assert any(
            code_node.start_index <= start < code_node.end_index for start in starts
        ), f"the code paragraph's stale text was left untouched: {requests}"

        # ... and every edit inside it must start at or past the glyph
        # (at code_node.start_index - 1, since project() already advanced
        # past it): Docs refuses to delete the glyph itself, and an insert
        # there would land ahead of it.
        for start in starts:
            assert start >= code_node.start_index, (
                f"a request landed on or before the render_prefix glyph: {requests}"
            )

    def test_a_prose_line_repeating_a_code_lines_text_is_disambiguated_in_favor_of_the_code_line(
        self,
    ) -> None:
        """The multi-candidate correspondence gap (issue #68), now resolved.

        `_node_key` keeps a prose paragraph and a code-rendered paragraph
        apart *when they are the only two candidates for their own slots* (see
        `test_node_key_distinguishes_prose_from_a_code_line_with_the_same_text`).
        The harder case is a plain current paragraph and a real current
        code-rendered paragraph both reading the same text, with only one
        target slot (also that text) to match against: `_node_key` never
        marks a *target* node as code (markdown never sets `render_prefix`),
        so the plain current paragraph — whose key equals the target's — used
        to win the correspondence, leaving the actual code-rendered node an
        unpaired `delete`, outside the `replace` run `_repair` inspects and so
        beyond its content-key rescue.

        `_opcodes` now runs `_prefer_structural_pairing` a second time at the
        top level (`prefer_code_line=True`), which prefers a
        `render_prefix`-carrying candidate for a slot whose target node is
        itself an all-monospace fenced-code line (`_target_wants_code_line`).
        So the code-rendered node wins the slot, and the plain prose
        paragraph is the one deleted — asserted below both by exclusion (the
        code-rendered node's range is untouched) and by inclusion (the prose
        paragraph's exact range is the one that gets deleted), mirroring
        `test_replacing_a_code_lines_text_deletes_and_inserts_past_the_glyph`.
        """
        doc, end = self._doc_prose_and_code_sharing_text("cfg")
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\n```\ncfg\n```\n\nTail\n"))

        code_node = next(n for n in current if n.render_prefix)
        prose_node = next(n for n in current if n.text == "cfg" and not n.render_prefix)
        requests = builder.build(current, target, end)

        lands_inside_code_block = any(
            code_node.start_index <= next(
                (
                    v
                    for v in (
                        r.get("deleteContentRange", {}).get("range", {}).get("startIndex"),
                        r.get("insertText", {}).get("location", {}).get("index"),
                    )
                    if v is not None
                ),
                -1,
            ) < code_node.end_index
            for r in requests
        )
        assert not lands_inside_code_block, (
            "the code-rendered node's range should be left alone now that the "
            "top-level pass prefers it for the code slot"
        )

        deletes = [
            r["deleteContentRange"]["range"] for r in requests if "deleteContentRange" in r
        ]
        assert deletes == [
            {"startIndex": prose_node.start_index, "endIndex": prose_node.end_index}
        ], (
            "the plain prose paragraph — not the code-rendered one — should be "
            f"the one deleted: {requests}"
        )

    def test_target_wanting_code_degrades_gracefully_with_no_code_rendered_candidate(
        self,
    ) -> None:
        """`_target_wants_code_line` can be true with nothing to prefer.

        Two plain (non-`render_prefix`) duplicate paragraphs both read "cfg",
        and the target wants that slot to be a fenced-code line, but neither
        current candidate is actually Docs-rendered. The whole-document pass
        added for issue #68 must not invent a winner or misbehave when its
        `code_slot_ids` gate finds no `render_prefix` candidate for the slot —
        it should fall back to `_repair`'s ordinary positional pairing:
        the first "cfg" survives as the match, the second is deleted as a
        stale duplicate.
        """
        doc, end = _doc_of_lines("Intro", "cfg", "cfg", "Tail")
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\n```\ncfg\n```\n\nTail\n"))

        first, second = (n for n in current if n.text == "cfg")
        requests = builder.build(current, target, end)

        deletes = [
            r["deleteContentRange"]["range"] for r in requests if "deleteContentRange" in r
        ]
        assert deletes == [
            {"startIndex": second.start_index, "endIndex": second.end_index}
        ], (
            "with no code-rendered candidate to prefer, the first duplicate "
            f"should be kept and the second deleted: {requests}"
        )

    def test_a_code_rendered_candidate_wins_the_slot_among_three_duplicates(self) -> None:
        """The `code_slot_ids` preference holds with more than two candidates.

        Two plain "cfg" paragraphs and one real Docs-rendered "cfg" paragraph
        (glyph-prefixed, monospace) all share the same text, with a single
        target slot wanting code. The code-rendered candidate must win the
        slot regardless of how many plain duplicates compete for it, and both
        plain duplicates are deleted.
        """
        mono = {"fontSize": {"magnitude": 9, "unit": "PT"},
                "weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}}
        paragraphs = [
            [("Intro\n", {})],
            [("cfg\n", {})],
            [("cfg\n", {})],
            [("", {}), ("cfg\n", mono)],
            [("Tail\n", {})],
        ]
        content, index = [], 1
        for runs in paragraphs:
            raw = "".join(c for c, _ in runs)
            end = index + len(raw.encode("utf-16-le")) // 2
            content.append({"startIndex": index, "endIndex": end, "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": c, "textStyle": st}} for c, st in runs],
            }})
            index = end
        doc = {"revisionId": "rev-1", "body": {"content": content}}

        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\n```\ncfg\n```\n\nTail\n"))

        code_node = next(n for n in current if n.render_prefix)
        plain_nodes = [n for n in current if n.text == "cfg" and not n.render_prefix]
        assert len(plain_nodes) == 2
        requests = builder.build(current, target, index)

        lands_inside_code_block = any(
            code_node.start_index <= next(
                (
                    v
                    for v in (
                        r.get("deleteContentRange", {}).get("range", {}).get("startIndex"),
                        r.get("insertText", {}).get("location", {}).get("index"),
                    )
                    if v is not None
                ),
                -1,
            ) < code_node.end_index
            for r in requests
        )
        assert not lands_inside_code_block, (
            f"the code-rendered node among three candidates should be left alone: {requests}"
        )

        deletes = {
            (r["deleteContentRange"]["range"]["startIndex"], r["deleteContentRange"]["range"]["endIndex"])
            for r in requests if "deleteContentRange" in r
        }
        assert deletes == {(n.start_index, n.end_index) for n in plain_nodes}, (
            f"both plain duplicates — not the code-rendered node — should be deleted: {requests}"
        )

    def test_a_code_line_and_a_same_text_prose_node_in_different_runs_do_not_swap(
        self,
    ) -> None:
        """PR #70's whole-document pooling (see `_content_key`'s docstring) puts
        a code line and a same-text prose node from *unrelated* pre-repair runs
        into the same `_prefer_structural_pairing` candidate/slot pool whenever
        `_node_key` has already told them apart (issue #68) — unlike the
        harder, still-open gap pinned by
        `test_a_prose_line_repeating_a_code_lines_text_still_confuses_correspondence`
        above, where only one target slot exists at all.

        Here each node has its *own* target slot, in a separate run (split by
        an untouched "ANCHOR" paragraph), and the cross-run pairing is
        deliberately the higher-scoring one on raw structural similarity
        alone — so this only stays correct because `_prefer_structural_
        pairing`'s same-origin tie-break keeps each node paired with its own
        run's slot instead of reassigning the code line's target to the prose
        node (or vice versa) purely because of the shared `_content_key`.
        """
        current = [
            DocsParagraphNode(text="cfg", style="NORMAL_TEXT", is_list_item=False),
            DocsParagraphNode(text="ANCHOR", style="NORMAL_TEXT", is_list_item=False),
            DocsParagraphNode(
                text="cfg", style="NORMAL_TEXT", is_list_item=True, render_prefix=""
            ),
        ]
        target = [
            DocsParagraphNode(text="cfg", style="HEADING_2", is_list_item=True),
            DocsParagraphNode(text="ANCHOR", style="NORMAL_TEXT", is_list_item=False),
            DocsParagraphNode(text="cfg", style="HEADING_4", is_list_item=False),
        ]

        opcodes = builder._opcodes(current, target)

        def target_index_for(current_index: int) -> int:
            for _tag, ci1, ci2, cj1, cj2 in opcodes:
                if ci1 <= current_index < ci2 and cj2 - cj1 == ci2 - ci1:
                    return cj1 + (current_index - ci1)
            raise AssertionError(f"current index {current_index} not covered: {opcodes}")

        assert target_index_for(0) == 0, (
            f"the prose node's own target slot was handed to the code line "
            f"from a different run: {opcodes}"
        )
        assert target_index_for(2) == 2, (
            f"the code line's own target slot was handed to the prose node "
            f"from a different run: {opcodes}"
        )
