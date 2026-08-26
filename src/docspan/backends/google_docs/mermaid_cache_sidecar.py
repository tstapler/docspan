"""Git-committed sidecar mapping a rendered mermaid PNG's hash to its source.

`mermaid_renderer.py`'s `lookup_mermaid_source()` cache lives under
`$XDG_CACHE_HOME` -- local-machine-only, so a teammate pulling the same doc
on a different machine never gets a `​```mermaid` fence restored, only the
deflated-but-still-just-an-image fallback `pulled_image_recovery.py` uses
when there's no cache hit. This sidecar closes that gap by persisting the
same hash -> diagram mapping next to the synced markdown file itself,
following the exact colocation convention `{file}.comments.md` already uses
(`core/paths.py`'s `COMMENTS_SUFFIX`) -- committed to the repo, not
gitignored, same as `pull_sectioned`'s `_manifest.yaml` (`manifest.py`'s own
docstring: "a committed file, not gitignored").
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

import yaml

MERMAID_CACHE_SUFFIX = ".mermaid-cache.yaml"


def sidecar_path(markdown_path: str) -> Path:
    return Path(str(markdown_path) + MERMAID_CACHE_SUFFIX)


def load(markdown_path: str) -> Dict[str, str]:
    """Read the sidecar's hash -> diagram map. Missing or corrupt -> {}."""
    path = sidecar_path(markdown_path)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, entries: Dict[str, str]) -> None:
    """Write `entries` to `path` via mkstemp + os.replace.

    Same pattern as `mermaid_renderer.py`'s `_record_reverse_lookup` and
    `render_mermaid_png`: a crash mid-write leaves the sibling temp file
    behind, never a half-written sidecar that `load()` would then have to
    treat as corrupt (and silently discard every prior entry for).
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(
                yaml.safe_dump(
                    entries, sort_keys=True, default_flow_style=False, allow_unicode=True
                )
            )
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def record(markdown_path: str, png_bytes: bytes, diagram: str) -> None:
    """Upsert one (hash -> diagram) entry, preserving whatever else is there.

    Best-effort and never raises: a push that successfully rendered and
    uploaded a mermaid diagram must not fail over this sidecar write, same
    "residue over crash" stance as the rest of this codebase.
    """
    record_many(markdown_path, {hashlib.sha256(png_bytes).hexdigest(): diagram})


def record_many(markdown_path: str, entries_to_record: Dict[str, str]) -> None:
    """Upsert many (hash -> diagram) entries with a single read-modify-write.

    Same best-effort/atomic-write contract as `record()`, but for callers
    (e.g. `image_source.py`'s push-time diagram resolution loop) that would
    otherwise call `record()` once per diagram node -- each call doing a
    full read-modify-write of the sidecar file, O(N) file I/O for an
    N-diagram doc. Skips the write entirely if nothing actually changed.
    """
    if not entries_to_record:
        return
    try:
        path = sidecar_path(markdown_path)
        entries = load(markdown_path)
        if all(entries.get(key) == diagram for key, diagram in entries_to_record.items()):
            return  # already current -- skip the no-op rewrite/diff churn
        entries.update(entries_to_record)
        _atomic_write(path, entries)
    except OSError:
        pass


def lookup(markdown_path: str, png_bytes: bytes) -> Optional[str]:
    return load(markdown_path).get(hashlib.sha256(png_bytes).hexdigest())
