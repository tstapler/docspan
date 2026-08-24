"""Unit tests for recover_pulled_images (pull-side base64/mermaid recovery).

See pulled_image_recovery.py's module docstring for why this exists: Drive's
HTML export (the default pull() path) inlines every embedded image as a
data:image/...;base64,... URI, including a pushed ```mermaid fence's
rendered diagram.
"""

import base64
import hashlib

from docspan.backends.google_docs import mermaid_cache_sidecar
from docspan.backends.google_docs.docs_structure_parser import DocsImageNode
from docspan.backends.google_docs.mermaid_renderer import _by_hash_dir
from docspan.backends.google_docs.pulled_image_recovery import (
    recover_pulled_images,
    recover_structural_mermaid_images,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _data_uri(data: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


def _seed_reverse_cache(tmp_path, monkeypatch, png_bytes: bytes, diagram: str) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    import hashlib

    path = _by_hash_dir() / f"{hashlib.sha256(png_bytes).hexdigest()}.mmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diagram, encoding="utf-8")


def test_cached_mermaid_render_is_restored_to_a_fence(tmp_path, monkeypatch) -> None:
    diagram = "graph TD\n  A --> B"
    png_bytes = _PNG_MAGIC + b"rendered-diagram-bytes"
    _seed_reverse_cache(tmp_path, monkeypatch, png_bytes, diagram)

    markdown = f"Some text.\n\n![mermaid diagram abc123]({_data_uri(png_bytes)})\n\nMore text.\n"
    image_nodes = [DocsImageNode(src="https://lh3.googleusercontent.com/temp1", alt="")]

    result = recover_pulled_images(markdown, image_nodes)

    assert result.mermaid_restored == 1
    assert result.base64_deflated == 0
    assert "```mermaid\ngraph TD\n  A --> B\n```" in result.markdown
    assert "base64" not in result.markdown


def test_uncached_image_falls_back_to_real_url_not_base64(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    png_bytes = _PNG_MAGIC + b"some-photo-nobody-rendered-locally"

    markdown = f"![a photo]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://lh3.googleusercontent.com/temp2", alt="")]

    result = recover_pulled_images(markdown, image_nodes)

    assert result.mermaid_restored == 0
    assert result.base64_deflated == 1
    assert result.markdown == "![a photo](https://lh3.googleusercontent.com/temp2)\n"
    assert "base64" not in result.markdown


def test_mismatched_counts_is_a_safe_no_op(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    png_bytes = _PNG_MAGIC + b"one-image"
    markdown = f"![only one]({_data_uri(png_bytes)})\n"
    # Two structural image nodes for one base64 match in the markdown --
    # the positional-correlation assumption doesn't hold here.
    image_nodes = [
        DocsImageNode(src="https://example.com/1", alt=""),
        DocsImageNode(src="https://example.com/2", alt=""),
    ]

    result = recover_pulled_images(markdown, image_nodes)

    assert result.markdown == markdown
    assert result.mermaid_restored == 0
    assert result.base64_deflated == 0


def test_no_images_is_a_safe_no_op(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    markdown = "Just plain text, no images at all.\n"

    result = recover_pulled_images(markdown, [])

    assert result.markdown == markdown
    assert result.mermaid_restored == 0
    assert result.base64_deflated == 0


def test_multiple_images_correlated_in_document_order(tmp_path, monkeypatch) -> None:
    diagram_a = "graph TD\n  A --> B"
    png_a = _PNG_MAGIC + b"diagram-a"
    _seed_reverse_cache(tmp_path, monkeypatch, png_a, diagram_a)
    png_b = _PNG_MAGIC + b"a-real-photo"

    markdown = (
        f"![first]({_data_uri(png_a)})\n\n"
        f"some text between images\n\n"
        f"![second]({_data_uri(png_b)})\n"
    )
    image_nodes = [
        DocsImageNode(src="https://example.com/a", alt=""),
        DocsImageNode(src="https://example.com/b", alt=""),
    ]

    result = recover_pulled_images(markdown, image_nodes)

    assert result.mermaid_restored == 1
    assert result.base64_deflated == 1
    assert "```mermaid\ngraph TD\n  A --> B\n```" in result.markdown
    assert "![second](https://example.com/b)" in result.markdown
    assert "base64" not in result.markdown


def test_falls_back_to_committed_sidecar_when_local_cache_is_empty(tmp_path, monkeypatch) -> None:
    """Simulates a pull on a different machine: the local XDG mermaid cache
    (mermaid_renderer.py) has never seen this diagram, but the committed
    sidecar (mermaid_cache_sidecar.py, next to the .md file) has -- because
    it was pushed from a different machine and the sidecar was committed."""
    other_xdg_home = tmp_path / "other-machines-xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(other_xdg_home))

    md_path = tmp_path / "argocd-crd-backed-client" / "README.md"
    md_path.parent.mkdir(parents=True)
    diagram = "sequenceDiagram\n  Caller->>API: request"
    png_bytes = _PNG_MAGIC + b"a-sequence-diagram-rendered-elsewhere"
    mermaid_cache_sidecar.record(str(md_path), png_bytes, diagram)

    markdown = f"![mermaid diagram xyz]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://lh3.googleusercontent.com/temp3", alt="")]

    result = recover_pulled_images(markdown, image_nodes, markdown_path=str(md_path))

    assert result.mermaid_restored == 1
    assert f"```mermaid\n{diagram}\n```" in result.markdown
    assert "base64" not in result.markdown


def test_no_markdown_path_means_no_sidecar_lookup(tmp_path, monkeypatch) -> None:
    """Without a markdown_path, there's nowhere to find a sidecar -- must
    degrade to the base64-deflated fallback, not raise."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    diagram = "graph TD\n  A --> B"
    png_bytes = _PNG_MAGIC + b"some-diagram"
    md_path = tmp_path / "doc.md"
    mermaid_cache_sidecar.record(str(md_path), png_bytes, diagram)

    markdown = f"![x]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://example.com/img", alt="")]

    result = recover_pulled_images(markdown, image_nodes)  # markdown_path omitted

    assert result.mermaid_restored == 0
    assert result.base64_deflated == 1
    assert result.markdown == "![x](https://example.com/img)\n"


def test_preserves_real_alt_text_from_the_export_on_fallback(tmp_path, monkeypatch) -> None:
    """Alt text is UI-settable in Docs even though the API can't write it --
    an author-set alt text should survive, sourced from the export's own
    markdown rather than the always-empty structural node.alt."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    png_bytes = _PNG_MAGIC + b"a-diagram-nobody-rendered-here"

    markdown = f"![architecture overview]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://example.com/img", alt="")]

    result = recover_pulled_images(markdown, image_nodes)

    assert result.markdown == "![architecture overview](https://example.com/img)\n"


def test_restores_from_appendix_entries_alone_no_local_cache_no_sidecar(
    tmp_path, monkeypatch
) -> None:
    """Third recovery tier (mermaid_appendix.py): no XDG cache hit, no
    sidecar (markdown_path omitted entirely), only appendix_entries."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagram = "graph TD\n  A --> B"
    png_bytes = _PNG_MAGIC + b"a-diagram-only-known-via-the-appendix"
    appendix_entries = {hashlib.sha256(png_bytes).hexdigest(): diagram}

    markdown = f"![mermaid diagram]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://lh3.googleusercontent.com/temp4", alt="")]

    result = recover_pulled_images(markdown, image_nodes, appendix_entries=appendix_entries)

    assert result.mermaid_restored == 1
    assert f"```mermaid\n{diagram}\n```" in result.markdown
    assert "base64" not in result.markdown


def test_appendix_miss_falls_back_to_real_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    png_bytes = _PNG_MAGIC + b"not-in-the-appendix-either"
    appendix_entries = {"some-other-hash": "graph TD\n  X --> Y"}

    markdown = f"![a photo]({_data_uri(png_bytes)})\n"
    image_nodes = [DocsImageNode(src="https://example.com/photo", alt="")]

    result = recover_pulled_images(markdown, image_nodes, appendix_entries=appendix_entries)

    assert result.mermaid_restored == 0
    assert result.markdown == "![a photo](https://example.com/photo)\n"


def test_structural_recovery_restores_from_appendix_entries_alone(tmp_path, monkeypatch) -> None:
    """recover_structural_mermaid_images's own three-tier chain, appendix-only."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagram = "graph TD\n  A --> B"
    png_bytes = _PNG_MAGIC + b"fetched-over-http-and-only-in-the-appendix"
    appendix_entries = {hashlib.sha256(png_bytes).hexdigest(): diagram}

    markdown = "![a diagram](https://docs.google.com/content-uri-1)\n"
    image_nodes = [DocsImageNode(src="https://docs.google.com/content-uri-1", alt="")]

    def fake_fetch(url: str):
        assert url == "https://docs.google.com/content-uri-1"
        return png_bytes

    result = recover_structural_mermaid_images(
        markdown, image_nodes, fetch=fake_fetch, appendix_entries=appendix_entries
    )

    assert result.mermaid_restored == 1
    assert f"```mermaid\n{diagram}\n```" in result.markdown


def test_structural_recovery_fetch_failure_leaves_link_untouched(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    markdown = "![a diagram](https://docs.google.com/content-uri-2)\n"
    image_nodes = [DocsImageNode(src="https://docs.google.com/content-uri-2", alt="")]

    result = recover_structural_mermaid_images(
        markdown, image_nodes, fetch=lambda url: None, appendix_entries={}
    )

    assert result.mermaid_restored == 0
    assert result.markdown == markdown
