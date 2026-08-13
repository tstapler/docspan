"""Unit tests for Google Docs inline image support.

Covers the .backlog-context.md acceptance criteria not already exercised by
the pre-existing request-builder/pipeline test files:
  0. markdown image -> DocsImageNode
  1/2/3 request-builder insertInlineImage shape + ordering + index math
  4/8 image_source.py resolution: local upload, URL bypass, missing/oversized/
      unsupported-format rejection
  7. pull round-trip: inlineObjectElement -> DocsImageNode -> markdown
Criterion 5 (temp-file cleanup/retry) and 6 (gated live integration) live in
tests/test_google_docs_backend.py and are out of default-CI scope respectively.
"""

from dataclasses import replace

import pytest

from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsImageNode,
    DocsParagraphNode,
    DocsStructureParser,
)
from docspan.backends.google_docs.image_source import (
    LocalPathSource,
    UrlSource,
    build_source,
    resolve_document_images,
    resolve_images,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import (
    MarkdownToParagraphParser,
)
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown

DOC_END = 100


def _para(text: str, start: int = 1, end: int = 10) -> DocsParagraphNode:
    return DocsParagraphNode(style="NORMAL_TEXT", text=text, start_index=start, end_index=end)


# ─────────────────────────────────────────────────────────────────────────────
# Criterion 0: markdown -> DocsImageNode
# ─────────────────────────────────────────────────────────────────────────────


def test_bare_image_paragraph_parses_to_image_node() -> None:
    nodes = MarkdownToParagraphParser().parse("![a diagram](./local.png)\n")
    assert len(nodes) == 1
    assert isinstance(nodes[0], DocsImageNode)
    assert nodes[0].src == "./local.png"
    assert nodes[0].alt == "a diagram"


def test_image_mixed_with_running_text_is_not_an_image_node() -> None:
    nodes = MarkdownToParagraphParser().parse("before ![a](./x.png) after\n")
    assert len(nodes) == 1
    assert isinstance(nodes[0], DocsParagraphNode)


# ─────────────────────────────────────────────────────────────────────────────
# Criterion 1: insertInlineImage request shape
# ─────────────────────────────────────────────────────────────────────────────


def test_insert_bare_image_into_empty_doc_emits_insert_inline_image() -> None:
    builder = DocsRequestBuilder()
    target = [DocsImageNode(src="https://example.com/x.png", alt="x")]
    requests = builder.build([], target, DOC_END)

    image_requests = [r for r in requests if "insertInlineImage" in r]
    assert len(image_requests) == 1
    body = image_requests[0]["insertInlineImage"]
    assert body["uri"] == "https://example.com/x.png"
    assert "objectSize" not in body


def test_insert_image_with_dimensions_sets_object_size() -> None:
    builder = DocsRequestBuilder()
    target = [DocsImageNode(src="https://example.com/x.png", alt="x", width_pt=100.0, height_pt=50.0)]
    requests = builder.build([], target, DOC_END)

    image_requests = [r for r in requests if "insertInlineImage" in r]
    assert image_requests[0]["insertInlineImage"]["objectSize"] == {
        "height": {"magnitude": 50.0, "unit": "PT"},
        "width": {"magnitude": 100.0, "unit": "PT"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Criterion 2/3: ordering and index math around an inserted image
# ─────────────────────────────────────────────────────────────────────────────


def test_insert_image_before_appended_paragraph_does_not_desync_indices() -> None:
    """Regression for the documented [30,32) vs [31,33) off-by-one.

    Inserting an image ahead of a following paragraph must not shift that
    paragraph's own insertText/updateParagraphStyle onto the wrong index --
    each node's requests must target the index implied by its own position,
    not one that silently absorbed the image's 1-UTF-16-unit footprint.
    """
    builder = DocsRequestBuilder()
    current = [_para("Intro", start=1, end=7)]
    target = [
        _para("Intro", start=1, end=7),
        DocsImageNode(src="https://example.com/x.png", alt="x"),
        _para("Outro", start=0, end=0),
    ]
    requests = builder.build(current, target, DOC_END)

    insert_texts = [r["insertText"] for r in requests if "insertText" in r]
    image_requests = [r["insertInlineImage"] for r in requests if "insertInlineImage" in r]
    assert len(image_requests) == 1

    image_index = image_requests[0]["location"]["index"]
    # The image occupies exactly 1 UTF-16 unit; whatever gets inserted right
    # after it (the image's own boundary newline, or the following
    # paragraph's text) must be anchored at image_index + 1, never
    # image_index + 2 or image_index (both are the off-by-one failure mode).
    following_indices = sorted(
        t["location"]["index"] for t in insert_texts if t["location"]["index"] > image_index
    )
    assert following_indices[0] == image_index + 1


def test_insert_inline_image_group_ordered_against_an_unrelated_later_edit() -> None:
    """A batch with an image-insert group anchored earlier than an unrelated
    restyle group must write the later (higher-index) group first, so the
    image insertion doesn't shift the restyle's target range out from under
    it -- exercising criterion #2 against the real diff/build path rather
    than _extract_start_index in isolation (which is only ever used by pass
    2, and pass 2 never emits insertInlineImage)."""
    builder = DocsRequestBuilder()
    current = [
        _para("A", start=1, end=3),
        DocsParagraphNode(style="HEADING_1", text="Heading", start_index=3, end_index=11),
    ]
    target = [
        _para("A", start=1, end=3),
        DocsImageNode(src="https://example.com/x.png", alt="x"),
        DocsParagraphNode(style="NORMAL_TEXT", text="Heading", start_index=0, end_index=0),
    ]
    requests = builder.build(current, target, DOC_END)

    image_requests = [r for r in requests if "insertInlineImage" in r]
    restyle_requests = [r for r in requests if "updateParagraphStyle" in r]
    assert len(image_requests) == 1
    assert len(restyle_requests) == 1

    image_index = requests.index(image_requests[0])
    restyle_index = requests.index(restyle_requests[0])
    # The restyle targets the (higher, unshifted) live index 3-11 and must
    # be applied before the image insertion at index 3 shifts everything
    # after it -- so it comes first in the write-backwards request array.
    assert restyle_index < image_index


# ─────────────────────────────────────────────────────────────────────────────
# Criterion 4/8: image_source.py resolution
# ─────────────────────────────────────────────────────────────────────────────


def test_build_source_bypasses_upload_for_remote_url() -> None:
    source = build_source("/docs/page.md", "https://example.com/x.png")
    assert isinstance(source, UrlSource)
    assert source.url == "https://example.com/x.png"


def test_build_source_resolves_relative_path_against_markdown_directory(tmp_path) -> None:
    md_path = tmp_path / "sub" / "page.md"
    md_path.parent.mkdir()
    source = build_source(str(md_path), "./img.png")
    assert isinstance(source, LocalPathSource)
    assert source.path == str(tmp_path / "sub" / "img.png")


def test_local_png_is_uploaded_and_url_is_not(tmp_path) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    local_png = tmp_path / "img.png"
    local_png.write_bytes(png_bytes)

    calls = []

    def uploader(data: bytes, filename: str, mime_type: str) -> dict:
        calls.append((data, filename, mime_type))
        return {"file_id": "drive-123", "uri": "https://drive.example.com/drive-123"}

    sources = {
        "local": LocalPathSource(path=str(local_png)),
        "remote": UrlSource(url="https://example.com/x.png"),
    }
    resolved, errors = resolve_images(sources, uploader)

    assert not errors
    assert len(calls) == 1  # only the local image was uploaded
    assert resolved["local"].uri == "https://drive.example.com/drive-123"
    assert resolved["local"].temp_drive_file_id == "drive-123"
    assert resolved["remote"].uri == "https://example.com/x.png"
    assert resolved["remote"].temp_drive_file_id is None


def test_missing_local_file_produces_error_not_crash(tmp_path) -> None:
    sources = {"a": LocalPathSource(path=str(tmp_path / "does-not-exist.png"))}
    resolved, errors = resolve_images(sources, uploader=lambda *_: {"file_id": "x", "uri": "x"})
    assert not resolved
    assert len(errors) == 1
    assert "not found" in errors[0].reason


def test_oversized_local_file_produces_error_not_crash(tmp_path) -> None:
    from docspan.backends.google_docs.image_source import MAX_IMAGE_BYTES

    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1))
    sources = {"a": LocalPathSource(path=str(big))}
    resolved, errors = resolve_images(sources, uploader=lambda *_: {"file_id": "x", "uri": "x"})
    assert not resolved
    assert "exceeds" in errors[0].reason


def test_svg_local_file_is_rejected_as_unsupported(tmp_path) -> None:
    svg = tmp_path / "logo.svg"
    svg.write_bytes(b"<?xml version='1.0'?><svg></svg>")
    sources = {"a": LocalPathSource(path=str(svg))}
    resolved, errors = resolve_images(sources, uploader=lambda *_: {"file_id": "x", "uri": "x"})
    assert not resolved
    assert "SVG" in errors[0].reason


def test_unrecognized_format_is_rejected(tmp_path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_bytes(b"just some text, not an image")
    sources = {"a": LocalPathSource(path=str(junk))}
    resolved, errors = resolve_images(sources, uploader=lambda *_: {"file_id": "x", "uri": "x"})
    assert not resolved
    assert "not a recognized image format" in errors[0].reason


def test_resolve_document_images_is_positional_with_none_for_failures(tmp_path) -> None:
    ok_png = tmp_path / "ok.png"
    ok_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    md_path = tmp_path / "doc.md"
    md_path.write_text("placeholder")

    nodes = [
        DocsImageNode(src="./ok.png", alt="ok"),
        DocsImageNode(src="./missing.png", alt="missing"),
        DocsImageNode(src="https://example.com/remote.png", alt="remote"),
    ]

    def uploader(data: bytes, filename: str, mime_type: str) -> dict:
        return {"file_id": "drive-1", "uri": "https://drive.example.com/drive-1"}

    resolved, warnings, temp_ids = resolve_document_images(nodes, str(md_path), uploader)

    assert len(resolved) == 3
    assert resolved[0] is not None and resolved[0].src == "https://drive.example.com/drive-1"
    assert resolved[1] is None
    assert resolved[2] is not None and resolved[2].src == "https://example.com/remote.png"
    assert len(warnings) == 1
    assert "missing.png" in warnings[0]
    assert temp_ids == ["drive-1"]


# ─────────────────────────────────────────────────────────────────────────────
# Criterion 7: pull round-trip
# ─────────────────────────────────────────────────────────────────────────────


def _doc_with_inline_image() -> dict:
    return {
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 2,
                    "paragraph": {
                        "elements": [
                            {"inlineObjectElement": {"inlineObjectId": "kix.obj1"}},
                        ]
                    },
                },
                {
                    "startIndex": 2,
                    "endIndex": 3,
                    "paragraph": {
                        "elements": [{"textRun": {"content": "\n"}}]
                    },
                },
            ]
        },
        "inlineObjects": {
            "kix.obj1": {
                "inlineObjectProperties": {
                    "embeddedObject": {
                        "contentUri": "https://docs.google.com/image-content-uri",
                        "description": "a diagram",
                        "size": {
                            "width": {"magnitude": 100.0, "unit": "PT"},
                            "height": {"magnitude": 50.0, "unit": "PT"},
                        },
                    }
                }
            }
        },
    }


def test_pull_parses_inline_object_element_into_image_node() -> None:
    nodes = DocsStructureParser().parse(_doc_with_inline_image())
    image_nodes = [n for n in nodes if isinstance(n, DocsImageNode)]
    assert len(image_nodes) == 1
    node = image_nodes[0]
    assert node.src == "https://docs.google.com/image-content-uri"
    assert node.alt == "a diagram"
    assert node.object_id == "kix.obj1"


def test_pulled_image_node_round_trips_to_markdown() -> None:
    nodes = DocsStructureParser().parse(_doc_with_inline_image())
    markdown = render_nodes_to_markdown(nodes)
    assert "![a diagram](https://docs.google.com/image-content-uri)" in markdown
