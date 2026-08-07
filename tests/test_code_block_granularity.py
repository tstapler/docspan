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

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsStructureParser
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.projection import project

markdown = MarkdownToParagraphParser()
structure = DocsStructureParser()
builder = DocsRequestBuilder()


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
        assert [node.text for node in nodes] == ["key: value", "  indented: yes"]
        # Every line stays monospace, so the styling survives the split.
        assert all(node.spans[0].monospace for node in nodes)

    def test_indentation_is_preserved(self) -> None:
        """`strip()` ate leading whitespace; in code that is meaning, not padding."""
        nodes = markdown.parse("```\n    deeply indented\n```\n")
        assert [node.text for node in nodes] == ["    deeply indented"]

    def test_no_top_level_node_carries_an_embedded_newline(self) -> None:
        """The invariant the bug violated. A Doc paragraph cannot hold a newline.

        Scoped to **top-level** blocks on purpose. `block_code` is parsed in three
        places and only this one is fixed: a fence inside a list item still yields
        one node with newlines (and concatenates onto the list text), and one
        inside a blockquote is dropped entirely. Both are byte-identical before
        this change, so neither is a regression — but an unqualified `all(...)`
        over a document with no list in it reads as a guarantee that does not hold,
        which is worse than no test.
        """
        nodes = markdown.parse(
            "before\n\n```sh\none\ntwo\nthree\n```\n\n```py\nfour\n```\n\nafter\n"
        )
        assert all("\n" not in node.text for node in nodes if hasattr(node, "text"))

    def test_a_fence_in_a_list_item_is_still_unsplit(self) -> None:
        """Pins the known gap so it cannot be mistaken for fixed.

        Delete this test when the split moves into a helper shared by all three
        parse sites; until then it is the honest statement of scope.
        """
        nodes = markdown.parse("- Steps:\n\n  ```sh\n  make build\n  make test\n  ```\n")
        assert any("\n" in node.text for node in nodes if hasattr(node, "text"))


class TestPushIsIdempotent:
    def test_an_unchanged_document_with_a_code_block_emits_no_requests(self) -> None:
        """The regression test for #40, stated as the property that was broken.

        Before the fix this emitted a delete plus a reinsert of the whole block on
        a document nobody had edited, and did so on every push forever.
        """
        md = "before\n\n```yaml\nkey: value\n  indented: yes\nafter\n```\n\ntail\n"
        doc, end = _doc_of_lines("before", "key: value", "  indented: yes", "after", "tail")

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
            "line one", "line three", "line two",
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
        pinned and out of scope for this fix; see
        `test_a_prose_line_repeating_a_code_lines_text_still_confuses_correspondence`
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

    def test_a_prose_line_repeating_a_code_lines_text_still_confuses_correspondence(self) -> None:
        """A known residual gap, pinned rather than silently left undiscovered.

        `_node_key` now keeps a prose paragraph and a code-rendered paragraph
        apart *when they are the only two candidates for their own slots* (see
        `test_node_key_distinguishes_prose_from_a_code_line_with_the_same_text`).
        It cannot resolve the harder case where a plain current paragraph and a
        real current code-rendered paragraph both read the same text, and only
        one target slot (also that text) exists to match against: `_node_key`
        never marks a *target* node as code (markdown never sets
        `render_prefix`), so the plain current paragraph — whose key equals the
        target's — wins the correspondence, and the actual code-rendered node
        is left an unpaired `delete`, outside the `replace` run `_repair`
        inspects and so beyond its content-key rescue.

        This reproduces identically on unmodified `origin/main` (confirmed
        before this fix), so it predates issue #54 and is not something a key
        signal alone can fix — it needs `_prefer_structural_pairing`-style
        disambiguation lifted to the top-level correspondence matcher, which is
        outside this fix's scope. Tracked in issue #68. Pinned here as
        documented, not silently reintroduced.
        """
        doc, end = self._doc_prose_and_code_sharing_text("cfg")
        current, _ = project(structure.parse(doc))
        target, _ = project(markdown.parse("Intro\n\n```\ncfg\n```\n\nTail\n"))

        code_node = next(n for n in current if n.render_prefix)
        requests = builder.build(current, target, end)

        lands_inside_code_block = any(
            code_node.start_index <= (
                r.get("deleteContentRange", {}).get("range", {}).get("startIndex")
                or r.get("insertText", {}).get("location", {}).get("index")
                or -1
            ) < code_node.end_index
            for r in requests
        )
        assert lands_inside_code_block, (
            "if this starts failing, the multi-candidate correspondence gap "
            "described above has been fixed — replace this pin with a real "
            "assertion that the code block is left alone"
        )
