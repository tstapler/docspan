"""Sectioned-sync manifest (`_manifest.yaml`) read/write.

Repository-pattern store for the ordered list of `SectionManifestEntry`
records that a sectioned mapping's directory owns. The manifest is the
authoritative order and identity map for a sectioned mapping's sections —
see `project_plans/gdocs-sectioned-sync/implementation/plan.md`'s Domain
Glossary and Pattern Decisions sections.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import tempfile
from typing import List, Optional

import yaml

# `_manifest.yaml`, deliberately NOT `.md` — sectioned pull/push glob
# `*.md` to find section files, and if the manifest were markdown-suffixed
# it would get silently picked up as a section file. Keep this constant in
# sync anywhere the filename is referenced instead of hardcoding the string.
MANIFEST_FILENAME = "_manifest.yaml"

# Sentinel `heading_id` for the pre-first-heading "preamble" section, which
# has no real Google Docs heading and therefore no API-assigned heading_id.
# Real Google Docs heading_ids are always API-assigned opaque strings and are
# never developer-chosen, so this sentinel can never collide with one. It is
# stable across pulls (see `SectionManifestEntry.heading_id` docstring).
PREAMBLE_HEADING_ID = "__preamble__"


class ManifestError(Exception):
    """Raised for manifest load/parse failures, wrapping the underlying cause."""


@dataclasses.dataclass
class SectionManifestEntry:
    """One ordered record in a sectioned mapping's `_manifest.yaml`.

    Attributes:
        heading_id: Google Docs' persistent per-heading-paragraph identifier
            (see `docs_structure_parser.py`'s `DocsParagraphNode.heading_id`),
            used as this section's stable identity key across pulls. **Special
            case**: the pre-first-heading "preamble" section has no real
            heading and therefore no Docs-API-assigned id; it uses the fixed
            sentinel value `"__preamble__"` (`PREAMBLE_HEADING_ID`) instead.
            This sentinel is a `heading_id`-typed field holding a
            non-Docs value by convention — real Google Docs heading_ids are
            always API-assigned and never equal to this literal string, so
            there is no ambiguity, but callers must not mistake
            `"__preamble__"` for a real Docs id when reasoning about identity.
        slug: URL/filename-safe slug derived from the section's heading text
            (via `heading_anchors.slugify()`), used to build `filename`.
        filename: The `NN-slug.md` file this section's content lives in,
            relative to the sectioned mapping's directory.
        title: Optional freeform display title (e.g. the raw heading text)
            for human-facing output; not used for identity matching.
    """

    heading_id: str
    slug: str
    filename: str
    title: Optional[str] = None


def _validate_filename(filename: object, manifest_path: str, index: int) -> str:
    """Reject a manifest entry `filename` that could escape the mapping's directory.

    A `_manifest.yaml` is a committed file, not gitignored, so a crafted
    entry (e.g. `filename: "../../../../.ssh/authorized_keys"`) must never
    be allowed to flow into the `os.path.join`/`open`/`os.rename` calls in
    `core/orchestrator.py` that trust this field as a plain relative
    filename within the mapping's section directory.
    """
    if not isinstance(filename, str) or not filename:
        raise ManifestError(
            f"manifest {manifest_path!r} entry {index} has an invalid filename: {filename!r}"
        )
    if os.path.isabs(filename):
        raise ManifestError(
            f"manifest {manifest_path!r} entry {index} has an absolute filename: {filename!r}"
        )

    section_dir = os.path.abspath(os.path.dirname(manifest_path))
    resolved = os.path.abspath(os.path.join(section_dir, filename))
    if os.path.commonpath([section_dir, resolved]) != section_dir:
        raise ManifestError(
            f"manifest {manifest_path!r} entry {index} has a filename that "
            f"escapes the mapping directory: {filename!r}"
        )
    return filename


class ManifestStore:
    """Repository for reading/writing a sectioned mapping's `_manifest.yaml`."""

    @staticmethod
    def load(path: str) -> List[SectionManifestEntry]:
        """Load manifest entries from ``path`` in file order (no re-sorting).

        Raises:
            ManifestError: if the file is missing, isn't valid YAML, or
                doesn't have the expected shape — never a raw YAML parser
                traceback or `KeyError`/`TypeError` bubbling to the caller.
        """
        manifest_path = pathlib.Path(path)
        try:
            raw_text = manifest_path.read_text()
        except OSError as exc:
            raise ManifestError(f"could not read manifest {path!r}: {exc}") from exc

        try:
            raw = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ManifestError(f"manifest {path!r} is not valid YAML: {exc}") from exc

        if raw is None:
            return []

        entries_raw = raw.get("entries") if isinstance(raw, dict) else raw
        if not isinstance(entries_raw, list):
            raise ManifestError(
                f"manifest {path!r} is malformed: expected a list of entries, "
                f"got {type(entries_raw).__name__}"
            )

        entries: List[SectionManifestEntry] = []
        for i, item in enumerate(entries_raw):
            if not isinstance(item, dict):
                raise ManifestError(
                    f"manifest {path!r} entry {i} is malformed: expected a mapping, "
                    f"got {type(item).__name__}"
                )
            try:
                filename = _validate_filename(item["filename"], path, i)
                entries.append(
                    SectionManifestEntry(
                        heading_id=item["heading_id"],
                        slug=item["slug"],
                        filename=filename,
                        title=item.get("title"),
                    )
                )
            except KeyError as exc:
                raise ManifestError(
                    f"manifest {path!r} entry {i} is missing required field {exc}"
                ) from exc

        return entries

    @staticmethod
    def save(path: str, entries: List[SectionManifestEntry]) -> None:
        """Atomically write ``entries`` to ``path`` (temp-file-in-same-dir + os.replace).

        Mirrors `config.py`'s `save_config`: writing to a temp file in the
        same directory then swapping it into place with `os.replace` means a
        crash mid-write can never leave a partially-written manifest on
        disk — inspecting the directory afterward always finds either the
        complete old manifest or the complete new one.
        """
        manifest_path = pathlib.Path(path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        raw = {
            "entries": [
                {
                    k: v
                    for k, v in (
                        ("heading_id", e.heading_id),
                        ("slug", e.slug),
                        ("filename", e.filename),
                        ("title", e.title),
                    )
                    if v is not None
                }
                for e in entries
            ]
        }

        fd, tmp_path = tempfile.mkstemp(
            dir=str(manifest_path.parent),
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                yaml.safe_dump(raw, f, sort_keys=False)
            os.replace(tmp_path, manifest_path)
        except Exception:
            os.unlink(tmp_path)
            raise
