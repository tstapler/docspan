"""Render mermaid diagram source to a PNG via mermaid-cli (`mmdc`).

Google Docs' insertInlineImage has no native mermaid or SVG support (see
image_source.py's SVG rejection), so a ```mermaid fence has to become a
raster image before it can be pushed. mermaid-cli wraps Puppeteer/Chromium
to do the actual rendering; there is no pure-Python mermaid renderer, and
none of this repo's existing dependencies cover it, so this shells out.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from docspan.core.xdg import cache_home

RENDER_TIMEOUT_SECONDS = 30

# mmdc's default output is sized to the diagram's natural CSS pixel bounding
# box (no upscaling), which Google Docs then inserts at close to that same
# small pixel size -- a multi-participant sequence diagram lands under
# 800x400px. `-s` is a pure supersampling multiplier (same logical layout,
# more pixels per unit), so this trades a larger PNG for a diagram that's
# both inserted bigger and stays sharp if a reader drags it larger still.
RENDER_SCALE = 3


class MermaidRenderError(Exception):
    """Raised when mermaid-cli is missing, fails, or produces no output."""


def _cache_dir() -> Path:
    return cache_home() / "mermaid"


def _by_hash_dir() -> Path:
    """Reverse-lookup cache: sha256(png bytes) -> original diagram source.

    Google Docs has no API-writable place to stash the diagram text on the
    inserted image itself (insertInlineImage only accepts uri/objectSize,
    and the API's Request union has no way to set an EmbeddedObject's
    title/description -- alt text is UI-only, verified against
    https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request).
    So the only place left to recover "this pulled image was originally a
    ```mermaid fence" is a local, content-addressed cache written at push
    time and consulted at pull time -- see pulled_image_recovery.py. Keyed
    by the rendered bytes (not the diagram text) because pull only ever has
    the bytes, never the source.
    """
    return cache_home() / "mermaid" / "by-hash"


def _record_reverse_lookup(diagram: str, png_bytes: bytes) -> None:
    """Best-effort: remember that these exact PNG bytes came from `diagram`.

    Never raises -- a failure to write this optional reverse-cache entry
    must not break the push it's piggybacking on. Idempotent: an existing
    entry for the same hash is left alone rather than rewritten.
    """
    try:
        path = _by_hash_dir() / f"{hashlib.sha256(png_bytes).hexdigest()}.mmd"
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(diagram)
            os.replace(tmp_name, path)
        except BaseException:
            os.unlink(tmp_name)
            raise
    except OSError:
        pass


def lookup_mermaid_source(png_bytes: bytes) -> Optional[str]:
    """Recover the original ```mermaid fence text for previously-rendered bytes.

    Returns None on a cache miss (bytes never rendered by this diagram, or
    rendered on a different machine/user that never pushed here) -- this is
    the expected, non-error outcome for any image this local cache doesn't
    know about, not a failure.
    """
    path = _by_hash_dir() / f"{hashlib.sha256(png_bytes).hexdigest()}.mmd"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


@lru_cache(maxsize=1)
def _mmdc_version() -> str:
    """Best-effort version fingerprint of the resolved mmdc binary.

    Folded into the cache key so an mermaid-cli upgrade (different layout
    engine/fonts, same diagram text) busts old cached PNGs instead of
    silently serving stale renders forever. Falls back to a fixed marker
    when there's no real binary (npx fallback) or --version fails, rather
    than raising -- a version we can't determine is still a cache key,
    just a less precise one.
    """
    binary = shutil.which("mmdc")
    if binary is None:
        return "npx-fallback"
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, timeout=5, check=False)
        return result.stdout.decode("utf-8", errors="replace").strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _cache_key(diagram: str) -> str:
    # RENDER_SCALE and the mmdc version are baked into the key alongside the
    # diagram text, since both change the rendered bytes for the same source.
    digest = hashlib.sha256(diagram.encode("utf-8"))
    digest.update(str(RENDER_SCALE).encode("utf-8"))
    digest.update(_mmdc_version().encode("utf-8"))
    return digest.hexdigest()


# Puppeteer's sandboxed Chromium needs extra namespace permissions that many
# containers/CI runners don't grant; `--no-sandbox` is the standard
# workaround (same one Puppeteer's own docs recommend for Docker/CI). Passed
# via a config file (mmdc's `-p`) rather than trying to smuggle Chromium args
# through mmdc's own CLI surface, which doesn't expose them directly.
_PUPPETEER_CONFIG = json.dumps({"args": ["--no-sandbox", "--disable-setuid-sandbox"]})


def _mmdc_command(input_path: str, output_path: str, puppeteer_config_path: str) -> List[str]:
    """Build the mermaid-cli invocation, preferring a real installed binary.

    Falls back to `npx --yes -p @mermaid-js/mermaid-cli mmdc` when `mmdc`
    isn't on PATH as a real executable -- in this project's dev environment
    it's only a shell alias to that same npx invocation, which subprocess
    (no shell) can't resolve.
    """
    base = [shutil.which("mmdc") or "mmdc"]
    if shutil.which("mmdc") is None:
        base = ["npx", "--yes", "-p", "@mermaid-js/mermaid-cli", "mmdc"]
    return base + [
        "-i", input_path,
        "-o", output_path,
        "-b", "white",
        "-p", puppeteer_config_path,
        "-s", str(RENDER_SCALE),
    ]


def render_mermaid_png(diagram: str, *, timeout: Optional[float] = None) -> bytes:
    """Render mermaid diagram source to PNG bytes.

    Raises MermaidRenderError on any failure (missing mermaid-cli, non-zero
    exit, timeout, no output produced) -- callers (image_source.py) are
    expected to catch this and surface it as a push warning, never let it
    crash the push.

    Renders are cached on disk keyed by a hash of the diagram text (plus
    RENDER_SCALE) under $XDG_CACHE_HOME/docspan/mermaid -- unchanged fences
    are the common case across repeat pushes, and skipping mmdc/Puppeteer
    entirely is the only way to make those instant rather than merely
    faster. A failed render is never cached, so a transient failure (e.g. a
    missing mmdc) doesn't stick once the diagram itself is fine.
    """
    cache_path = _cache_dir() / f"{_cache_key(diagram)}.png"
    if cache_path.is_file():
        data = cache_path.read_bytes()
        # Idempotent and cheap (checks is_file() before writing) -- covers a
        # PNG cached before the reverse-lookup cache existed.
        _record_reverse_lookup(diagram, data)
        return data

    data = _render_uncached(diagram, timeout=timeout)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file and rename into place: os.replace is
    # atomic, so a concurrent push racing on the same diagram never sees a
    # partially-written PNG the way a direct write_bytes() could expose.
    fd, tmp_name = tempfile.mkstemp(dir=cache_path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, cache_path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    _record_reverse_lookup(diagram, data)
    return data


def _render_uncached(diagram: str, *, timeout: Optional[float] = None) -> bytes:
    with tempfile.TemporaryDirectory(prefix="docspan-mermaid-") as tmpdir:
        tmp = Path(tmpdir)
        input_path = tmp / "diagram.mmd"
        output_path = tmp / "diagram.png"
        puppeteer_config_path = tmp / "puppeteer-config.json"
        input_path.write_text(diagram, encoding="utf-8")
        puppeteer_config_path.write_text(_PUPPETEER_CONFIG, encoding="utf-8")

        command = _mmdc_command(str(input_path), str(output_path), str(puppeteer_config_path))
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout or RENDER_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise MermaidRenderError(
                "mermaid-cli (mmdc) not found and npx is unavailable to fetch it"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MermaidRenderError(
                f"mermaid-cli timed out after {timeout or RENDER_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise MermaidRenderError(f"mermaid-cli failed: {stderr or 'no error output'}")
        if not output_path.is_file():
            raise MermaidRenderError("mermaid-cli reported success but produced no output file")
        return output_path.read_bytes()
