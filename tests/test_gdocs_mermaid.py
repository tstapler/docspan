"""Unit tests for rendering/pushing ```mermaid fences as Google Docs inline images.

A ```mermaid fence is parsed into a DocsImageNode carrying its raw diagram
text (mermaid_source), rendered to a PNG at resolve time via an injected
renderer (never a real mermaid-cli subprocess in these tests), and uploaded
through the same image_source.py pipeline as any other image.
"""

from docspan.backends.google_docs.docs_structure_parser import DocsImageNode, DocsParagraphNode
from docspan.backends.google_docs.image_source import (
    MermaidSource,
    resolve_document_images,
    resolve_images,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import MarkdownToParagraphParser
from docspan.backends.google_docs.mermaid_renderer import (
    MermaidRenderError,
    _mmdc_command,
    render_mermaid_png,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MERMAID_MD = """```mermaid
graph TD
  A --> B
```
"""


def _fake_uploader(data: bytes, filename: str, mime_type: str) -> dict:
    return {"file_id": "temp123", "uri": "https://drive.example.com/temp123"}


def _fake_renderer(diagram: str) -> bytes:
    return _PNG_MAGIC + diagram.encode("utf-8")


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
    out, warnings, temp_ids = resolve_document_images(
        [node], str(tmp_path / "doc.md"), _fake_uploader, renderer=_fake_renderer
    )
    assert warnings == []
    assert out[0].src == "https://drive.example.com/temp123"
    assert temp_ids == ["temp123"]


def test_mermaid_render_failure_is_a_warning_not_a_crash(tmp_path) -> None:
    def _failing_renderer(diagram: str) -> bytes:
        raise MermaidRenderError("mermaid-cli not found")

    node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")
    out, warnings, temp_ids = resolve_document_images(
        [node], str(tmp_path / "doc.md"), _fake_uploader, renderer=_failing_renderer
    )
    assert out == [None]
    assert len(warnings) == 1
    assert "mermaid render failed" in warnings[0]
    assert temp_ids == []


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
