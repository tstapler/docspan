"""Unit tests for rendering/pushing ```mermaid fences as Google Docs inline images.

A ```mermaid fence is parsed into a DocsImageNode carrying its raw diagram
text (mermaid_source), rendered to a PNG at resolve time via an injected
renderer (never a real mermaid-cli subprocess in these tests), and uploaded
through the same image_source.py pipeline as any other image.
"""

import hashlib
import struct

from docspan.backends.google_docs import mermaid_cache_sidecar
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import DocsImageNode, DocsParagraphNode
from docspan.backends.google_docs.image_source import (
    MermaidSource,
    _mermaid_image_size_pt,
    _png_pixel_dimensions,
    resolve_document_images,
    resolve_images,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.mermaid_renderer import (
    MermaidRenderError,
    _mmdc_command,
    lookup_mermaid_source,
    render_mermaid_png,
)

from .conftest import minimal_png as _minimal_png

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MERMAID_MD = """```mermaid
graph TD
  A --> B
```
"""


def _fake_uploader(data: bytes, filename: str, mime_type: str) -> dict:
    return {"file_id": "temp123", "uri": "https://drive.example.com/temp123"}


def _fake_renderer(diagram: str) -> bytes:
    return _minimal_png()


# ─────────────────────────────────────────────────────────────────────────────
# markdown -> DocsImageNode
# ─────────────────────────────────────────────────────────────────────────────


def test_mermaid_fence_parses_to_image_node_with_source() -> None:
    nodes = MarkdownToParagraphParser().parse(MERMAID_MD)
    assert len(nodes) == 1
    assert isinstance(nodes[0], DocsImageNode)
    assert nodes[0].mermaid_source == "graph TD\n  A --> B"
    assert nodes[0].alt.startswith("mermaid diagram ")


def test_identical_diagrams_produce_the_same_alt() -> None:
    a = MarkdownToParagraphParser().parse(MERMAID_MD)[0]
    b = MarkdownToParagraphParser().parse(MERMAID_MD)[0]
    assert a.alt == b.alt


def test_different_diagrams_produce_different_alts() -> None:
    a = MarkdownToParagraphParser().parse(MERMAID_MD)[0]
    other = "```mermaid\ngraph TD\n  X --> Y\n```\n"
    b = MarkdownToParagraphParser().parse(other)[0]
    assert a.alt != b.alt


def test_non_mermaid_fence_is_unaffected() -> None:
    nodes = MarkdownToParagraphParser().parse("```python\nprint(1)\n```\n")
    assert all(isinstance(n, DocsParagraphNode) for n in nodes)


# ─────────────────────────────────────────────────────────────────────────────
# resolve_images / resolve_document_images with an injected renderer
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_images_renders_mermaid_source_via_injected_renderer() -> None:
    sources = {"0": MermaidSource(diagram="graph TD\n  A --> B")}
    resolved, errors = resolve_images(sources, _fake_uploader, renderer=_fake_renderer)
    assert errors == []
    assert resolved["0"].uri == "https://drive.example.com/temp123"
    assert resolved["0"].temp_drive_file_id == "temp123"


def test_resolve_document_images_uses_mermaid_source_over_src(tmp_path) -> None:
    node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")
    out, warnings, temp_ids, mermaid_entries = resolve_document_images(
        [node], str(tmp_path / "doc.md"), _fake_uploader, renderer=_fake_renderer
    )
    assert warnings == []
    assert out[0].src == "https://drive.example.com/temp123"
    assert temp_ids == ["temp123"]
    expected_hash = hashlib.sha256(_fake_renderer("graph TD\n  A --> B")).hexdigest()
    assert mermaid_entries == [(expected_hash, "graph TD\n  A --> B")]


def test_resolve_document_images_records_mermaid_source_in_committed_sidecar(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")

    resolve_document_images([node], md_path, _fake_uploader, renderer=_fake_renderer)

    png_bytes = _fake_renderer("graph TD\n  A --> B")
    assert mermaid_cache_sidecar.lookup(md_path, png_bytes) == "graph TD\n  A --> B"


def test_resolve_document_images_does_not_record_non_mermaid_images(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    real_image = tmp_path / "photo.png"
    real_image.write_bytes(_PNG_MAGIC + b"a-real-photo")
    node = DocsImageNode(alt="a photo", src="photo.png")

    resolve_document_images([node], md_path, _fake_uploader)

    assert mermaid_cache_sidecar.load(md_path) == {}


def test_mermaid_render_failure_is_a_warning_not_a_crash(tmp_path) -> None:
    def _failing_renderer(diagram: str) -> bytes:
        raise MermaidRenderError("mermaid-cli not found")

    node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")
    out, warnings, temp_ids, mermaid_entries = resolve_document_images(
        [node], str(tmp_path / "doc.md"), _fake_uploader, renderer=_failing_renderer
    )
    assert out == [None]
    assert len(warnings) == 1
    assert "mermaid render failed" in warnings[0]
    assert temp_ids == []
    assert mermaid_entries == []


# ─────────────────────────────────────────────────────────────────────────────
# PNG dimension reading
# ─────────────────────────────────────────────────────────────────────────────


def test_png_pixel_dimensions_reads_valid_ihdr() -> None:
    ihdr_body = struct.pack(">IIBBBBB", 2400, 1200, 8, 2, 0, 0, 0)
    data = _PNG_MAGIC + struct.pack(">I", len(ihdr_body)) + b"IHDR" + ihdr_body

    assert _png_pixel_dimensions(data) == (2400, 1200)


def test_png_pixel_dimensions_returns_none_for_fake_png_without_ihdr() -> None:
    data = _PNG_MAGIC + "graph TD\n  A --> B".encode("utf-8")

    assert _png_pixel_dimensions(data) is None


def test_png_pixel_dimensions_returns_none_for_truncated_bytes() -> None:
    assert _png_pixel_dimensions(_PNG_MAGIC) is None


def test_png_pixel_dimensions_returns_none_for_empty_bytes() -> None:
    assert _png_pixel_dimensions(b"") is None


def test_png_pixel_dimensions_returns_none_for_zero_width_or_height() -> None:
    """A malformed/adversarial IHDR with a zero dimension must not be treated
    as valid -- width==0 or height==0 breaks the downstream scale = target/width
    division (image_source.py's own explicit guard against this)."""
    zero_width = struct.pack(">IIBBBBB", 0, 1200, 8, 2, 0, 0, 0)
    zero_height = struct.pack(">IIBBBBB", 2400, 0, 8, 2, 0, 0, 0)

    for ihdr_body in (zero_width, zero_height):
        data = _PNG_MAGIC + struct.pack(">I", len(ihdr_body)) + b"IHDR" + ihdr_body
        assert _png_pixel_dimensions(data) is None


# ─────────────────────────────────────────────────────────────────────────────
# mermaid image sizing (px -> pt, filled to CONTENT_WIDTH_PT)
# ─────────────────────────────────────────────────────────────────────────────


def test_mermaid_image_size_pt_scales_2400x1200_at_render_scale_3_to_468x234() -> None:
    assert _mermaid_image_size_pt(_minimal_png(2400, 1200)) == (468.0, 234.0)


def test_mermaid_image_size_pt_is_independent_of_render_scale_value(monkeypatch) -> None:
    monkeypatch.setattr("docspan.backends.google_docs.image_source.RENDER_SCALE", 3)
    at_scale_3 = _mermaid_image_size_pt(_minimal_png(2400, 1200))

    monkeypatch.setattr("docspan.backends.google_docs.image_source.RENDER_SCALE", 6)
    at_scale_6 = _mermaid_image_size_pt(_minimal_png(4800, 2400))

    assert at_scale_3 == at_scale_6 == (468.0, 234.0)


def test_mermaid_image_size_pt_returns_none_for_malformed_png() -> None:
    data = _PNG_MAGIC + b"not a real png"

    assert _mermaid_image_size_pt(data) is None


def test_mermaid_image_size_pt_upscales_small_diagram_preserving_aspect_ratio() -> None:
    assert _mermaid_image_size_pt(_minimal_png(800, 400)) == (468.0, 234.0)


def test_mermaid_image_size_pt_is_deterministic_across_repeated_calls() -> None:
    png_bytes = _minimal_png(2400, 1200)

    first = _mermaid_image_size_pt(png_bytes)
    second = _mermaid_image_size_pt(png_bytes)

    assert first == second == (468.0, 234.0)


# ─────────────────────────────────────────────────────────────────────────────
# wiring _mermaid_image_size_pt into resolve_document_images
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_document_images_sets_width_and_height_for_mermaid_image(tmp_path) -> None:
    node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")

    out, warnings, _temp_ids, _mermaid_entries = resolve_document_images(
        [node],
        str(tmp_path / "doc.md"),
        _fake_uploader,
        renderer=lambda diagram: _minimal_png(2400, 1200),
    )

    assert warnings == []
    assert out[0].width_pt == 468.0
    assert out[0].height_pt == 234.0


def test_resolve_document_images_leaves_width_and_height_none_for_non_mermaid_image(
    tmp_path,
) -> None:
    real_image = tmp_path / "photo.png"
    real_image.write_bytes(_minimal_png(2400, 1200))
    node = DocsImageNode(alt="a photo", src="photo.png")

    out, warnings, _temp_ids, _mermaid_entries = resolve_document_images(
        [node], str(tmp_path / "doc.md"), _fake_uploader
    )

    assert warnings == []
    assert out[0].width_pt is None
    assert out[0].height_pt is None


def test_resolve_document_images_preserves_explicit_width_and_height_on_mermaid_node(
    tmp_path,
) -> None:
    node = DocsImageNode(
        alt="mermaid diagram abc123",
        mermaid_source="graph TD\n  A --> B",
        width_pt=100.0,
        height_pt=50.0,
    )

    out, warnings, _temp_ids, _mermaid_entries = resolve_document_images(
        [node],
        str(tmp_path / "doc.md"),
        _fake_uploader,
        renderer=lambda diagram: _minimal_png(2400, 1200),
    )

    assert warnings == []
    assert out[0].width_pt == 100.0
    assert out[0].height_pt == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# Epic 1.3: fixture repair + end-to-end / regression coverage
# ─────────────────────────────────────────────────────────────────────────────


def test_fake_renderer_output_is_a_valid_png_with_known_dimensions() -> None:
    """_fake_renderer must return real, IHDR-bearing PNG bytes (Story 1.3.1) --
    not the old _PNG_MAGIC + diagram_bytes stub, which _png_pixel_dimensions
    can't parse."""
    assert _png_pixel_dimensions(_fake_renderer("graph TD\n  A --> B")) == (2400, 1200)


def test_mermaid_image_gets_sized_and_centered_on_push(tmp_path) -> None:
    """End-to-end: parse -> resolve_document_images() -> the request
    builder's image-insert branch -> the emitted insertInlineImage carries
    the fit-to-content-width objectSize and the paragraph is centered."""
    parsed = MarkdownToParagraphParser().parse(MERMAID_MD)
    assert len(parsed) == 1

    out, warnings, _temp_ids, _mermaid_entries = resolve_document_images(
        parsed, str(tmp_path / "doc.md"), _fake_uploader, renderer=_fake_renderer
    )
    assert warnings == []
    node = out[0]
    assert node is not None

    requests = DocsRequestBuilder()._make_insert_requests([node], insert_at_index=10)

    image_requests = [r["insertInlineImage"] for r in requests if "insertInlineImage" in r]
    assert len(image_requests) == 1
    assert image_requests[0]["objectSize"] == {
        "height": {"magnitude": 234.0, "unit": "PT"},
        "width": {"magnitude": 468.0, "unit": "PT"},
    }

    style_requests = [
        r["updateParagraphStyle"] for r in requests if "updateParagraphStyle" in r
    ]
    assert len(style_requests) == 1
    assert style_requests[0]["paragraphStyle"]["alignment"] == "CENTER"


def test_repeated_mermaid_push_has_stable_node_key(tmp_path) -> None:
    """Two independent resolve_document_images() calls against the same
    diagram source must yield identical _node_key() tuples, so the diff
    engine treats a second, unchanged push as a no-op (idempotency)."""
    builder = DocsRequestBuilder()

    def _resolve_once() -> DocsImageNode:
        parsed = MarkdownToParagraphParser().parse(MERMAID_MD)
        out, warnings, _temp_ids, _mermaid_entries = resolve_document_images(
            parsed, str(tmp_path / "doc.md"), _fake_uploader, renderer=_fake_renderer
        )
        assert warnings == []
        return out[0]

    first_key = builder._node_key(_resolve_once())
    second_key = builder._node_key(_resolve_once())

    assert first_key == second_key
    assert first_key == ("__image__", first_key[1], 468.0, 234.0)


def test_repush_of_already_sized_mermaid_image_is_a_safe_noop() -> None:
    """Regression (Story 1.3.3): re-pushing a mermaid diagram whose pulled
    width_pt/height_pt differs from what this feature would now compute for
    the same diagram (same alt) is a safe no-op -- zero requests, no crash --
    per the Known Limitation documented in plan.md (the diff engine's
    _content_key for an image is alt-only, so _repair folds the mismatched
    sizes back to "equal", and _make_style_update_requests explicitly no-ops
    for DocsImageNode)."""
    builder = DocsRequestBuilder()
    pulled = DocsImageNode(
        alt="mermaid diagram abc123",
        width_pt=100.0,
        height_pt=50.0,
        mermaid_source=None,
    )
    target = DocsImageNode(
        alt="mermaid diagram abc123",
        width_pt=468.0,
        height_pt=234.0,
        mermaid_source="graph TD\n  A --> B",
    )

    requests = builder.build([pulled], [target], doc_end_index=100)

    assert requests == []


# ─────────────────────────────────────────────────────────────────────────────
# mermaid_renderer.py command construction
# ─────────────────────────────────────────────────────────────────────────────


def test_mmdc_command_falls_back_to_npx_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    command = _mmdc_command("in.mmd", "out.png", "puppeteer.json")
    assert command[:4] == ["npx", "--yes", "-p", "@mermaid-js/mermaid-cli"]
    assert "in.mmd" in command
    assert "out.png" in command


def test_mmdc_command_uses_real_binary_when_present(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/mmdc")
    command = _mmdc_command("in.mmd", "out.png", "puppeteer.json")
    assert command[0] == "/usr/local/bin/mmdc"


# ─────────────────────────────────────────────────────────────────────────────
# render_mermaid_png disk cache
# ─────────────────────────────────────────────────────────────────────────────


def _counting_uncached_renderer(calls: list) -> "callable":
    def _render(diagram: str, *, timeout=None) -> bytes:
        calls.append(diagram)
        return _PNG_MAGIC + diagram.encode("utf-8")

    return _render


def test_render_mermaid_png_reuses_cache_for_identical_diagram(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls: list = []
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer(calls),
    )
    diagram = "graph TD\n  A --> B"

    first = render_mermaid_png(diagram)
    second = render_mermaid_png(diagram)

    assert first == second == _PNG_MAGIC + diagram.encode("utf-8")
    assert calls == [diagram]  # second call was a cache hit, no re-render


def test_render_mermaid_png_distinct_diagrams_do_not_collide(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls: list = []
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer(calls),
    )

    a = render_mermaid_png("graph TD\n  A --> B")
    b = render_mermaid_png("graph TD\n  X --> Y")

    assert a != b
    assert calls == ["graph TD\n  A --> B", "graph TD\n  X --> Y"]


def test_render_mermaid_png_does_not_cache_a_failed_render(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls: list = []

    def _flaky(diagram: str, *, timeout=None) -> bytes:
        calls.append(diagram)
        if len(calls) == 1:
            raise MermaidRenderError("mermaid-cli not found")
        return _PNG_MAGIC + diagram.encode("utf-8")

    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached", _flaky
    )
    diagram = "graph TD\n  A --> B"

    try:
        render_mermaid_png(diagram)
        assert False, "expected MermaidRenderError on first call"
    except MermaidRenderError:
        pass

    result = render_mermaid_png(diagram)
    assert result == _PNG_MAGIC + diagram.encode("utf-8")
    assert calls == [diagram, diagram]  # failure wasn't cached, second call re-rendered


def test_render_mermaid_png_cache_key_changes_with_render_scale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._mmdc_version", lambda: "1.0.0"
    )
    calls: list = []
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer(calls),
    )
    diagram = "graph TD\n  A --> B"

    render_mermaid_png(diagram)
    monkeypatch.setattr("docspan.backends.google_docs.mermaid_renderer.RENDER_SCALE", 5)
    render_mermaid_png(diagram)

    assert calls == [diagram, diagram]  # scale change busts the cache, not a hit


# ─────────────────────────────────────────────────────────────────────────────
# reverse lookup: rendered bytes -> original diagram source (pull-side recovery)
# ─────────────────────────────────────────────────────────────────────────────


def test_lookup_mermaid_source_recovers_diagram_after_render(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer([]),
    )
    diagram = "graph TD\n  A --> B"

    png_bytes = render_mermaid_png(diagram)

    assert lookup_mermaid_source(png_bytes) == diagram


def test_lookup_mermaid_source_recovers_diagram_on_cache_hit_too(tmp_path, monkeypatch) -> None:
    """A second render_mermaid_png call (disk-cache hit, no re-render) must
    still populate the reverse lookup -- it's the only call site that knows
    the (bytes, diagram) pairing, so a cache-hit call that skipped this
    would leave the reverse lookup permanently empty for that diagram."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer([]),
    )
    diagram = "graph TD\n  A --> B"

    render_mermaid_png(diagram)
    # Simulate starting fresh with only the forward PNG cache surviving
    # (e.g. an XDG_CACHE_HOME populated before this feature existed).
    by_hash_dir = tmp_path / "docspan" / "mermaid" / "by-hash"
    for f in by_hash_dir.glob("*.mmd"):
        f.unlink()
    assert not any(by_hash_dir.glob("*.mmd"))

    png_bytes = render_mermaid_png(diagram)  # disk-cache hit, not a fresh render

    assert lookup_mermaid_source(png_bytes) == diagram


def test_lookup_mermaid_source_returns_none_on_miss(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert lookup_mermaid_source(b"\x89PNG\r\n\x1a\nnot a real render") is None


def test_render_mermaid_png_cache_key_changes_with_mmdc_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls: list = []
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._render_uncached",
        _counting_uncached_renderer(calls),
    )
    diagram = "graph TD\n  A --> B"

    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._mmdc_version", lambda: "1.0.0"
    )
    render_mermaid_png(diagram)
    monkeypatch.setattr(
        "docspan.backends.google_docs.mermaid_renderer._mmdc_version", lambda: "2.0.0"
    )
    render_mermaid_png(diagram)

    assert calls == [diagram, diagram]  # mmdc upgrade busts the cache, not a hit
