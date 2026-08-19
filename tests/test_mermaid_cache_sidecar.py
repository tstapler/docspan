"""Unit tests for mermaid_cache_sidecar (git-committed cross-machine mermaid cache)."""

from docspan.backends.google_docs import mermaid_cache_sidecar


def test_load_missing_sidecar_is_empty(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    assert mermaid_cache_sidecar.load(md_path) == {}


def test_record_then_lookup_round_trips(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    png_bytes = b"\x89PNG\r\n\x1a\nfake-render"
    diagram = "graph TD\n  A --> B"

    mermaid_cache_sidecar.record(md_path, png_bytes, diagram)

    assert mermaid_cache_sidecar.lookup(md_path, png_bytes) == diagram


def test_lookup_miss_returns_none(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    assert mermaid_cache_sidecar.lookup(md_path, b"never rendered") is None


def test_record_preserves_existing_entries(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    first_bytes, first_diagram = b"first-png-bytes", "graph TD\n  A --> B"
    second_bytes, second_diagram = b"second-png-bytes", "graph TD\n  X --> Y"

    mermaid_cache_sidecar.record(md_path, first_bytes, first_diagram)
    mermaid_cache_sidecar.record(md_path, second_bytes, second_diagram)

    assert mermaid_cache_sidecar.lookup(md_path, first_bytes) == first_diagram
    assert mermaid_cache_sidecar.lookup(md_path, second_bytes) == second_diagram


def test_sidecar_path_is_colocated_with_the_markdown_file(tmp_path) -> None:
    md_path = tmp_path / "argocd-crd-backed-client" / "README.md"
    assert mermaid_cache_sidecar.sidecar_path(str(md_path)) == tmp_path / (
        "argocd-crd-backed-client/README.md.mermaid-cache.yaml"
    )


def test_recording_the_same_entry_twice_does_not_rewrite_the_file(tmp_path) -> None:
    md_path = str(tmp_path / "doc.md")
    png_bytes, diagram = b"same-bytes", "graph TD\n  A --> B"

    mermaid_cache_sidecar.record(md_path, png_bytes, diagram)
    sidecar = mermaid_cache_sidecar.sidecar_path(md_path)
    first_mtime = sidecar.stat().st_mtime_ns

    mermaid_cache_sidecar.record(md_path, png_bytes, diagram)

    assert sidecar.stat().st_mtime_ns == first_mtime


def test_corrupt_sidecar_file_is_treated_as_empty(tmp_path) -> None:
    md_path = tmp_path / "doc.md"
    sidecar = mermaid_cache_sidecar.sidecar_path(str(md_path))
    sidecar.write_text("not: valid: yaml: [", encoding="utf-8")

    assert mermaid_cache_sidecar.load(str(md_path)) == {}
