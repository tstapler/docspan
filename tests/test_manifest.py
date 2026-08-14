"""Tests for the sectioned-sync manifest store (gdocs-sectioned-sync Epic 1, Story 1.2)."""

from __future__ import annotations

import pytest

from docspan.backends.google_docs import manifest as manifest_module
from docspan.backends.google_docs.manifest import (
    ManifestError,
    ManifestStore,
    SectionManifestEntry,
)


def test_manifest_store_load_returns_entries_in_file_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    manifest_path = tmp_path / "_manifest.yaml"
    manifest_path.write_text(
        "entries:\n"
        "  - heading_id: __preamble__\n"
        "    slug: preamble\n"
        "    filename: 00-preamble.md\n"
        "  - heading_id: h.zzz\n"
        "    slug: zebra\n"
        "    filename: 01-zebra.md\n"
        "  - heading_id: h.aaa\n"
        "    slug: apple\n"
        "    filename: 02-apple.md\n"
    )

    entries = ManifestStore.load(str(manifest_path))

    assert [e.filename for e in entries] == [
        "00-preamble.md",
        "01-zebra.md",
        "02-apple.md",
    ]
    assert entries[0].heading_id == "__preamble__"
    assert entries[1].slug == "zebra"


def test_manifest_store_load_raises_on_malformed_yaml(tmp_path) -> None:  # type: ignore[no-untyped-def]
    manifest_path = tmp_path / "_manifest.yaml"
    manifest_path.write_text("entries: [this is not: valid: yaml: at all")

    with pytest.raises(ManifestError):
        ManifestStore.load(str(manifest_path))


def test_manifest_store_load_raises_clear_error_on_missing_required_field(tmp_path) -> None:  # type: ignore[no-untyped-def]
    manifest_path = tmp_path / "_manifest.yaml"
    manifest_path.write_text(
        "entries:\n"
        "  - heading_id: h.aaa\n"
        "    slug: apple\n"
    )

    with pytest.raises(ManifestError):
        ManifestStore.load(str(manifest_path))


def test_manifest_store_save_is_atomic_under_simulated_crash(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    manifest_path = tmp_path / "_manifest.yaml"

    original_entries = [
        SectionManifestEntry(heading_id="__preamble__", slug="preamble", filename="00-preamble.md"),
        SectionManifestEntry(heading_id="h.aaa", slug="apple", filename="01-apple.md"),
    ]
    ManifestStore.save(str(manifest_path), original_entries)
    original_text = manifest_path.read_text()

    def _crash_before_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated crash before os.replace")

    monkeypatch.setattr(manifest_module.os, "replace", _crash_before_replace)

    new_entries = [
        SectionManifestEntry(heading_id="h.bbb", slug="banana", filename="00-banana.md"),
    ]
    with pytest.raises(OSError):
        ManifestStore.save(str(manifest_path), new_entries)

    # Original manifest is untouched — never a partial/corrupt file.
    assert manifest_path.read_text() == original_text
    reloaded = ManifestStore.load(str(manifest_path))
    assert [e.filename for e in reloaded] == ["00-preamble.md", "01-apple.md"]

    # No stray temp file left behind in the directory.
    leftover_tmp = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover_tmp == []
