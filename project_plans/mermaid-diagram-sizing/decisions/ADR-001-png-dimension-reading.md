# ADR-001: Read PNG pixel dimensions via hand-parsed IHDR chunk, not Pillow

**Date**: 2026-09-18
**Status**: Accepted

## Context

`resolve_document_images()` (`src/docspan/backends/google_docs/image_source.py:246-308`)
needs the pixel width/height of a mermaid diagram's rendered PNG to compute
`width_pt`/`height_pt` for Google Docs' `insertInlineImage.objectSize`. mmdc
(mermaid-cli) always renders to PNG for this pipeline — never SVG or JPEG.

`requirements.md`'s Constraints section left this as an open Phase 3 decision
between two options:
1. Add **Pillow** as a new runtime dependency and use `Image.open(...).size`.
2. Hand-parse the PNG `IHDR` chunk with `struct.unpack` — no new dependency.

Phase 2 research surfaced a factual error in the original requirements
premise: Pillow is **not** currently a dependency of docspan (`pyproject.toml`
has no `Pillow`/`PIL` entry; confirmed by `grep` and by
`python3 -c "import PIL"` raising `ModuleNotFoundError` in the project's
environment). `image_source.py` itself already documents a deliberate
anti-Pillow policy for MIME detection:

```python
# Magic-byte sniffing instead of `imghdr` (removed in Python 3.13) or a new
# Pillow dependency -- covers the formats insertInlineImage actually supports.
```
(`image_source.py:25-26`)

`research/build-vs-buy.md` §3 recommends Pillow on general-purpose grounds
("standard, battle-tested"), while `research/stack.md` §1 and
`research/architecture.md`/`research/features.md` §3 recommend the IHDR parse
specifically because it matches this file's own stated precedent and the
problem is scoped to exactly one format.

## Decision

**Hand-parse the PNG `IHDR` chunk with `struct.unpack`.** No new dependency
is added.

PNG guarantees `IHDR` is the first chunk: 8-byte signature (already the magic
bytes `_MAGIC_BYTES` sniffs for), 4-byte chunk length, 4-byte `"IHDR"` tag,
then width and height as two big-endian `uint32`s (bytes 16-19 and 20-23).
This is a fixed-offset, ~10-line read with no parsing ambiguity — no chunk
CRC validation, palette handling, or interlacing logic is needed since only
width/height are read, never pixel data.

## Rationale

- **Consistency with existing, stated policy.** `image_source.py` already
  rejected Pillow once, in the same file, for the same class of reason
  (avoiding a heavyweight image library for a narrow byte-sniffing need).
  Reaching for Pillow now for a second, adjacent need in the same file would
  directly contradict a documented decision one function above it.
- **Scope match.** mermaid rendering in this codebase only ever produces PNG
  (`mermaid_renderer.py` shells out to `mmdc`, which always writes PNG for
  this pipeline) — there is no present or planned need to read dimensions
  from any other format. Pillow's format-agnostic decoder is unused
  generality here.
- **Dependency cost.** Pillow pulls in compiled bindings (`libjpeg`, `zlib`,
  platform wheels) for a single header-only read (`width`, `height` as two
  integers). A ~10-line `struct.unpack` avoids that footprint entirely with
  no loss of correctness for this narrow use.
- **Determinism.** Both approaches are equally deterministic for well-formed
  PNG bytes (per `research/pitfalls.md` §2) — this is not a tiebreaker, but
  confirms the lighter option loses nothing on the property that matters most
  for the diff-identity risk this feature must avoid.

## Consequences

- If a future feature needs to read dimensions from a non-PNG format (SVG,
  JPEG, etc.), it will need either a second hand-written parser or a
  reconsideration of this ADR — not a blocker today, since mermaid output is
  PNG-only, but worth a decision-owner note anywhere this constant/function
  is reused later.
- No `pyproject.toml` change is needed for this feature's dependency graph.
- `_png_pixel_dimensions()` must return `None` (not raise) on any byte string
  that isn't a valid, minimally-sized PNG with an `IHDR` chunk immediately
  following the signature, so a malformed/truncated input degrades to
  "no sizing computed" (today's behavior) rather than crashing a push — see
  `implementation/plan.md` Story 1.1.1.
