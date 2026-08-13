"""Render mermaid diagram source to a PNG via mermaid-cli (`mmdc`).

Google Docs' insertInlineImage has no native mermaid or SVG support (see
image_source.py's SVG rejection), so a ```mermaid fence has to become a
raster image before it can be pushed. mermaid-cli wraps Puppeteer/Chromium
to do the actual rendering; there is no pure-Python mermaid renderer, and
none of this repo's existing dependencies cover it, so this shells out.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

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
    """
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
