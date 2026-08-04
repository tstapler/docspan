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

    def test_no_node_carries_an_embedded_newline(self) -> None:
        """The invariant the bug violated. A Doc paragraph cannot hold a newline."""
        nodes = markdown.parse(
            "before\n\n```sh\none\ntwo\nthree\n```\n\n```py\nfour\n```\n\nafter\n"
        )
        assert all("\n" not in node.text for node in nodes if hasattr(node, "text"))


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
        match to their single source node. Each insert must therefore carry
        exactly one line — the leading "\\n" opens the new paragraph, and there
        must be nothing after the content.

        Asserted on the requests rather than by replaying them: they are emitted
        highest-index-first so they compose, and reconstructing the resulting
        document here would be testing my model of Docs instead of the builder.
        """
        md = "```sh\nline one\nline two\nline three\n```\n"
        target, _ = project(markdown.parse(md))

        texts = [
            request["insertText"]["text"]
            for request in builder.build([], target, 1)
            if "insertText" in request
        ]
        assert sorted(texts) == ["\nline one", "\nline three", "\nline two"]
        for text in texts:
            assert text.count("\n") == 1, text
            assert text.startswith("\n"), text
