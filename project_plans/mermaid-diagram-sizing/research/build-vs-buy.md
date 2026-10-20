# Research: Build vs. Buy — Mermaid Diagram Sizing

## 1. Existing OSS library for "read PNG dims + fit-to-width scale"

No such library is warranted. Reading `Image.open(path).size` (Pillow) already gives
`(width_px, height_px)`; the fit-to-width computation is:

```python
scale = target_width_pt / native_width_px
height_pt = native_height_px * scale
```

That's the entire algorithm — three lines, no edge cases beyond guarding
`native_width_px == 0`. Searched for a "responsive image fit" or "aspect-ratio-preserving
resize" package in the Python ecosystem (e.g. anything wrapping this arithmetic as a
utility): nothing maintained exists that does *only* this without also pulling in actual
image resizing/re-encoding, layout engines, or CSS-object-fit polyfills, all of which are
overkill for computing two numbers to put in a Google Docs API request body.

**mmdc's `-w`/`-H` flags**: `mmdc --help` (confirmed via direct invocation, mmdc resolved
through the project's `npx -p @mermaid-js/mermaid-cli mmdc` alias) shows:

```
-w, --width [width]    Width of the page (default: 800)
-H, --height [height]  Height of the page (default: 600)
```

These set the Puppeteer **viewport** size mmdc renders into, not the output PNG's final
pixel dimensions — the documented behavior (and mermaid-cli's actual behavior) is to
auto-crop the rendered page to the diagram's content bounding box regardless of the
viewport size, so passing `-w 1560` does not reliably produce a 1560px-wide PNG; a small
diagram in a large viewport still crops down to its natural size. Because the output size
still depends on content, the current design (render at natural size with `RENDER_SCALE`
supersampling unchanged, then read the actual PNG dimensions after the fact and compute
`objectSize` from those) is correct and necessary — there's no mmdc flag that bypasses the
after-the-fact dimension read.

**Verdict: Not recommended (no library needed).** Hand-rolled arithmetic is correct here;
reaching for a library would be over-engineering a 3-line computation.

## 2. SaaS/managed API

Not applicable, as scoped in the requirements — no external service is a candidate for
"resize a locally-rendered PNG's declared dimensions in a Docs API request." No further
evaluation performed.

## 3. Pillow's `.size` vs. a bespoke PNG-header parser

Pillow's `Image.open(...).size` (lazy header read, no decode/resize needed just to get
dimensions) is the standard, battle-tested way to read image dimensions in Python and is
the obviously correct choice over hand-parsing PNG's IHDR chunk. Writing a bespoke binary
parser for one image format (even though PNG's width/height are a fixed 8-byte read at a
known offset) would only be justified if avoiding a dependency mattered — and it doesn't
here, per §4.

**Correction to requirements.md's premise**: the requirements doc states Pillow is
"already a dependency." That is not currently true — `grep -n "[Pp]illow" pyproject.toml
uv.lock` returns no matches, and the full `dependencies` list in `pyproject.toml` (typer,
rich, PyYAML, ruamel.yaml, pydantic, google-auth*, markdownify, requests, httpx,
python-dateutil, merge3, mistune) contains nothing that pulls in Pillow transitively
either. Implementation will need to add `Pillow` as a new direct dependency in
`pyproject.toml` before using `Image.open(...).size`. This doesn't change the
recommendation — Pillow is still the right choice — but the plan/task breakdown should
include a dependency-addition step, not assume it's free.

**Verdict: Recommended** — use Pillow for dimension reads only (no resize/re-encode
needed), with the caveat that it must be added as a new dependency.

## 4. Reuse/extend existing docspan image-handling code

Checked `src/docspan/backends/google_docs/image_source.py` (`_MAGIC_BYTES` dict at line 28,
`_sniff_mime_type` at line 217) — this only matches leading magic bytes (e.g.
`b"\x89PNG\r\n\x1a\n"` → `"image/png"`) to classify MIME type. It does not parse any
header fields beyond the signature, so there's no width/height extraction logic here to
extend; extending it to also parse IHDR would just be reimplementing what Pillow already
does robustly (and only for PNG — Pillow already handles this uniformly across formats).

Checked `pulled_image_recovery.py` — it deals with recovering original mermaid source text
from rendered-PNG bytes via the render cache (hash-keyed lookup), not with parsing pixel
dimensions from image bytes at all.

`docs_request_builder.py:2816-2819` already has the consuming side wired up
(`node.width_pt`/`node.height_pt` → `objectSize` with `unit: "PT"`), confirming the only
missing piece is computing those two `_pt` values from the rendered PNG — which is exactly
the arithmetic in §1, with dimensions sourced via Pillow per §3.

**Verdict: Not recommended (nothing to fork/extend)** — no existing width/height-parsing
code exists in the codebase to reuse; a small new helper (e.g. in `mermaid_renderer.py` or
adjacent to where the PNG bytes are produced) is the right shape, backed by Pillow.

## Summary

| Option | Verdict |
|---|---|
| OSS "fit-to-width" library | Not recommended — 3-line arithmetic, no gap to fill |
| mmdc `-w`/`-H` as a shortcut | Not viable — sets viewport, not output crop size; doesn't remove the need to read PNG dims after rendering |
| SaaS/managed API | N/A |
| Pillow `.size` for dimension reads | Recommended — but must be added as a new direct dependency (not currently present in `pyproject.toml`/`uv.lock`, contra requirements.md's assumption) |
| Fork/extend existing image code | Not recommended — `image_source.py`'s magic-byte sniffing and `pulled_image_recovery.py` don't parse dimensions; nothing to extend |
