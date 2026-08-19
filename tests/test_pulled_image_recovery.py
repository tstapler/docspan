"""Unit tests for recover_pulled_images (pull-side base64/mermaid recovery).

See pulled_image_recovery.py's module docstring for why this exists: Drive's
HTML export (the default pull() path) inlines every embedded image as a
data:image/...;base64,... URI, including a pushed ```mermaid fence's
rendered diagram.
"""

import base64

from docspan.backends.google_docs.docs_structure_parser import DocsImageNode
from docspan.backends.google_docs.mermaid_renderer import _by_hash_dir
from docspan.backends.google_docs.pulled_image_recovery import recover_pulled_images

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
