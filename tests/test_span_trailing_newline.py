"""A paragraph's terminating newline must not end up inside its spans.

Every Docs paragraph ends with "\\n" and it arrives inside the *last textRun's*
content, while `DocsParagraphNode.text` rstrips it. The rest of the pipeline
assumes the spans concatenate to exactly `.text` — pass 2 walks span lengths to
place ranges, and the markdown renderer concatenates them — so the disagreement
is not cosmetic. It only bites when the run carrying the newline also carries a
mark, which is the ordinary shape of a sentence ending in a link.
"""
from __future__ import annotations

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsStructureParser
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

structure = DocsStructureParser()
builder = DocsRequestBuilder()


def _doc(*runs: dict, text_len: int) -> dict:
    return {
        "revisionId": "rev-1",
        "body": {"content": [{
            "startIndex": 1,
            "endIndex": 1 + text_len + 1,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": list(runs),
            },
        }]},
    }


def _run(content: str, **style: object) -> dict:
    return {"textRun": {"content": content, "textStyle": dict(style)}}


class TestSpansMatchText:
    def test_spans_concatenate_to_exactly_text(self) -> None:
        node = structure.parse(_doc(
            _run("see "), _run("it\n", link={"url": "https://example.com"}), text_len=6
        ))[0]
        assert "".join(span.text for span in node.spans) == node.text == "see it"

    def test_an_empty_paragraph_has_no_spans(self) -> None:
        # Its only content is the newline, so there is no text to style and
        # nothing to render.
        node = structure.parse(_doc(_run("\n"), text_len=0))[0]
        assert node.spans == []

    def test_an_empty_run_is_dropped_rather_than_rendered_as_stray_marks(self) -> None:
        # Distinct from the newline case, which the trim removes on its own: an
        # empty textRun survives the trim, and a bold span holding no text
        # renders as "****".
        nodes = structure.parse(_doc(
            _run("kept"), _run("", bold=True), _run("\n"), text_len=4
        ))
        assert [span.text for span in nodes[0].spans] == ["kept"]
        assert render_nodes_to_markdown(nodes) == "kept\n"

    def test_an_unmarked_paragraph_is_unaffected(self) -> None:
        node = structure.parse(_doc(_run("plain text\n"), text_len=10))[0]
        assert [span.text for span in node.spans] == ["plain text"]

    def test_a_multi_byte_run_is_trimmed_by_the_newline_only(self) -> None:
        node = structure.parse(_doc(_run("café 🎉\n"), text_len=7))[0]
        assert [span.text for span in node.spans] == ["café 🎉"]


class TestRenderedMarkdown:
    def test_a_link_ending_a_paragraph_renders_as_a_link(self) -> None:
        # Before: "see [it\n](https://example.com)" — the newline inside the
        # link text, which re-parses as a literal "](https://example.com)" line.
        nodes = structure.parse(_doc(
            _run("see "), _run("it\n", link={"url": "https://example.com"}), text_len=6
        ))
        assert render_nodes_to_markdown(nodes) == "see [it](https://example.com)\n"

    def test_bold_ending_a_paragraph_renders_as_bold(self) -> None:
        nodes = structure.parse(_doc(_run("very "), _run("bold\n", bold=True), text_len=9))
        assert render_nodes_to_markdown(nodes) == "very **bold**\n"


class TestPassTwoNoLongerOverflows:
    def test_styling_survives_a_re_push_of_a_paragraph_ending_in_a_link(self) -> None:
        # The spans used to total one code unit more than the paragraph could
        # hold, so _spans_overflow reported it and pass 2 dropped the styling.
        doc = _doc(
            _run("see "), _run("it\n", link={"url": "https://example.com"}), text_len=6
        )
        target = structure.parse(doc)

        assert builder.unaligned_span_targets(doc, target) == []
        links = [
            request["updateTextStyle"]["textStyle"]["link"]
            for request in builder.build_span_style_requests(doc, target)
            if "link" in request["updateTextStyle"]["textStyle"]
        ]
        assert links == [{"url": "https://example.com"}]
