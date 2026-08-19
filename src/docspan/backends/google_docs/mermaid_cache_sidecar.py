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


def record(markdown_path: str, png_bytes: bytes, diagram: str) -> None:
    """Upsert one (hash -> diagram) entry, preserving whatever else is there.

    Best-effort and never raises: a push that successfully rendered and
    uploaded a mermaid diagram must not fail over this sidecar write, same
    "residue over crash" stance as the rest of this codebase.
    """
    try:
        key = hashlib.sha256(png_bytes).hexdigest()
        entries = load(markdown_path)
        if entries.get(key) == diagram:
            return  # already current -- skip the no-op rewrite/diff churn
        entries[key] = diagram
        sidecar_path(markdown_path).write_text(
            yaml.safe_dump(entries, sort_keys=True, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def lookup(markdown_path: str, png_bytes: bytes) -> Optional[str]:
    return load(markdown_path).get(hashlib.sha256(png_bytes).hexdigest())
