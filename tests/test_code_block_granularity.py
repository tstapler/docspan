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
  fix, 2 after.

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

class TestRenderGlyphIsNormalizedAway:
    """U+E907 is a rendering artifact, not content.

    Docs writes it at the start of a paragraph it renders itself. Markdown has no
    syntax for one, so the two codecs `project()` exists to reconcile were drawing
    from different alphabets and the diff read the glyph as an authored difference.

    Docs then **refuses** any `deleteContentRange` covering such a paragraph, and
    `batchUpdate` is atomic — so one refused delete failed the whole push and a
    document containing a native code block could not be pushed at all, however
    unrelated the edit. Measured on a real design doc: 56 requests, HTTP 400,
    nothing written. See issue #47.
    """

    GLYPH = ""

    def _para(self, runs: list[dict], start: int = 1) -> dict:
        text = "".join(r["textRun"]["content"] for r in runs)
        return {
            "startIndex": start,
            "endIndex": start + len(text),
            "paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                          "elements": runs},
        }

    def test_a_leading_glyph_is_stripped_from_text_and_spans(self) -> None:
        doc = {"revisionId": "r", "body": {"content": [self._para([
            {"textRun": {"content": f"{self.GLYPH}# markgate.yaml\n", "textStyle": {}}},
        ])]}}
        node = structure.parse(doc)[0]
        assert node.text == "# markgate.yaml"
        assert "".join(s.text for s in node.spans) == node.text

    def test_start_index_advances_with_the_strip(self) -> None:
        """The index risk. The API counted the glyph; if `start_index` does not move
        with it, every span in the paragraph is placed one unit early."""
        doc = {"revisionId": "r", "body": {"content": [self._para([
            {"textRun": {"content": f"{self.GLYPH}see ", "textStyle": {}}},
            {"textRun": {"content": "bold", "textStyle": {"bold": True}}},
            {"textRun": {"content": " now\n", "textStyle": {}}},
        ])]}}
        node = structure.parse(doc)[0]
        assert node.text == "see bold now"
        assert node.start_index == 2, "the glyph occupied index 1"
        # 'bold' sits at [6,10) in the document; the offset must agree.
        assert node.start_index + len("see ") == 6

    def test_a_glyph_only_paragraph_becomes_empty_and_is_projected_away(self) -> None:
        """Then rule 1 drops it from *both* sides, so the diff never sees it."""
        doc = {"revisionId": "r", "body": {"content": [self._para([
            {"textRun": {"content": f"{self.GLYPH}\n", "textStyle": {}}},
        ])]}}
        node = structure.parse(doc)[0]
        assert node.text == ""
        kept, residue = project([node])
        assert kept == []
        assert [r.kind for r in residue] == ["empty_paragraph"]

    def test_no_delete_is_emitted_for_a_glyph_paragraph(self) -> None:
        """The property that was broken: the push must not ask Docs to delete it."""
        doc = {"revisionId": "r", "body": {"content": [
            self._para([{"textRun": {"content": "intro\n", "textStyle": {}}}], start=1),
            self._para([{"textRun": {"content": f"{self.GLYPH}# cfg\n",
                                     "textStyle": {}}}], start=7),
        ]}}
        target, _ = project(markdown.parse("intro\n\n```yaml\n# cfg\n```\n"))
        current, _ = project(structure.parse(doc))

        requests = builder.build(current, target, 14)
        assert not [r for r in requests if "deleteContentRange" in r], requests
