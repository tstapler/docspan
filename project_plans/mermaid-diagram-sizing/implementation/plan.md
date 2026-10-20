# Implementation Plan: mermaid-diagram-sizing

**Feature**: Auto-size and center mermaid-diagram inline images pushed to Google Docs, using the rendered PNG's real pixel dimensions instead of leaving `objectSize` unset.
**Date**: 2026-09-18
**Status**: Ready for implementation
**ADRs**: [ADR-001: Read PNG pixel dimensions via hand-parsed IHDR chunk, not Pillow](../decisions/ADR-001-png-dimension-reading.md)

---

## Step 0.5 — Alternatives considered

1. **Compute size in `resolve_document_images()`, read PNG dims via hand-parsed IHDR chunk** (chosen). Strength: sits at the one point in the codebase that already holds the rendered PNG bytes (`image_source.py:298`, `result.rendered_bytes`) before the node reaches the diff/builder, and needs no new dependency. Weakness: introduces a second small binary-parsing helper alongside `_sniff_mime_type`'s magic-byte table — one more piece of "hand-rolled format knowledge" in this file (mitigated: PNG's IHDR layout never changes and is fully covered by ADR-001's rationale).
2. **Compute size in `docs_request_builder.py` at insert time instead.** Strength: keeps all `objectSize`/layout math in the one file that already builds `insertInlineImage`. Weakness: the request builder never sees raw rendered bytes today (only `node.src`, a Drive URI) — moving sizing there would mean either threading `rendered_bytes` through `DocsImageNode` (a wider, unnecessary API change touching the pull-side dataclass too) or fetching the image back from Drive to measure it, adding a network round-trip. Rejected.
3. **Add Pillow and compute size via `Image.open(...).size`.** Strength: standard, well-tested image library; trivial one-liner. Weakness: new native-code dependency for a single PNG-header read, contradicting `image_source.py`'s own documented anti-Pillow precedent (see ADR-001). Rejected — recorded in the Pattern Decisions table and ADR-001.

Approach 1 is adopted; approaches 2 and 3 are the rejected alternatives referenced in Pattern Decisions below.

---

## Domain Glossary
*(Ubiquitous language — every domain term that appears as a type, method, or variable name.)*

| Term | Definition | Notes |
|------|-----------|-------|
| `CONTENT_WIDTH_PT` | Module-level float constant `468.0` — Google Docs' default Letter-page content width in points (6.5in × 72pt/in, 1in margins each side). The fit target for a mermaid image's `width_pt`. | New constant, `image_source.py`. Accepted approximation — doesn't read a doc's actual `documentStyle` margins (see requirements.md Rabbit Holes). |
| `_PT_PER_CSS_PX` | Module-level float constant `0.75` — the CSS reference-pixel-to-point conversion (96 CSS px/in ÷ 72pt/in). | New constant, `image_source.py`. |
| `_png_pixel_dimensions()` | Function `(data: bytes) -> Optional[Tuple[int, int]]` — reads `(width_px, height_px)` from a PNG's `IHDR` chunk by fixed byte offset; returns `None` if `data` isn't a well-formed PNG with `IHDR` immediately following the signature. | New function, `image_source.py`. Never raises. |
| `_mermaid_image_size_pt()` | Function `(png_bytes: bytes) -> Optional[Tuple[float, float]]` — computes `(width_pt, height_pt)` for a mermaid-rendered PNG: divides out `RENDER_SCALE`, converts logical px to pt via `_PT_PER_CSS_PX`, then scales both axes by one shared factor so the image's width becomes exactly `CONTENT_WIDTH_PT` while preserving aspect ratio. Returns `None` when `_png_pixel_dimensions()` does. | New function, `image_source.py`. |
| `RENDER_SCALE` | *(Existing, reused, not renamed.)* mmdc's supersampling multiplier (`3`) — same logical CSS layout, more raster pixels per unit. Must be divided out before any pt conversion. | `mermaid_renderer.py:33`; imported into `image_source.py`. |
| `DocsImageNode.mermaid_source` | *(Existing, reused, not renamed.)* Raw ```` ```mermaid ```` fence text carried on the node from parse time through `resolve_document_images()` to `docs_request_builder.py`. Doubles as this feature's "is this a mermaid image" signal for both sizing and paragraph-centering gates. | `docs_structure_parser.py:419`. |
| `DocsImageNode.width_pt` / `.height_pt` | *(Existing, reused, not renamed.)* Optional floats already wired into `insertInlineImage`'s `objectSize` by `docs_request_builder.py:2816-2820`. This feature is the first push-side code path to populate them (today only the pull side sets them). | `docs_structure_parser.py:417-418`. |

---

## Pattern Decisions

| Component | Pattern Chosen | Source | Alternative Rejected | Reason |
|-----------|---------------|--------|---------------------|--------|
| PNG dimension reading | Hand-parsed fixed-offset `IHDR` read via `struct.unpack` | Existing codebase convention (`_sniff_mime_type`'s magic-byte table) | Pillow `Image.open(...).size` | New native-code dependency for a single-format header read this file's own comment already says was avoided once; see ADR-001. |
| Size computation (`_mermaid_image_size_pt`) | Transaction Script (PoEAA) — a pure function doing linear arithmetic, no domain object | Fowler, PoEAA | Domain Model (a `MermaidImageSize` class with methods) | Three-line scale-and-round arithmetic doesn't carry enough behavior to justify a class; matches this file's existing plain-function helper style (`_read_local`, `_sniff_mime_type`). |
| `(width_px, height_px)` / `(width_pt, height_pt)` return values | Plain `Tuple[int, int]` / `Tuple[float, float]` | type-driven-design considered, then rejected | Dedicated `PngDimensions`/`PointSize` newtypes | Each value is consumed once, immediately, at a single call site one function away — no cross-entity ID-confusion risk (the newtype example's actual justification) exists here; a newtype would be ceremony with no bug it prevents. Matches this file's existing `Tuple[bytes, str]` return style (`_render_mermaid`, `_read_local`). |
| Mermaid-paragraph CENTER alignment gating | Guard clause on the existing `node.mermaid_source is not None` field | — | New `DocsImageNode.is_centered`/`.alignment` field | `mermaid_source` already survives `resolve_document_images()`'s `replace(node, src=result.uri)` (dataclasses.replace preserves every field not explicitly overridden) all the way into `docs_request_builder.py` — it's already exactly the signal needed. A second field would duplicate information the node already carries. |
| Dimension-read failure handling | Optional-return ("null object") — return `None`, caller skips sizing | Existing codebase convention (`_sniff_mime_type() -> Optional[str]`) | Raise a new `PngParseError` exception | Matches this file's existing `Optional[...]`-return convention for "couldn't classify this data" rather than adding a new exception type to `resolve_document_images()`'s try/except-to-push-warning residue pattern, for a path ADR-001 argues should never fire against real `mmdc` output — a silent `None` fallback (preserving today's "no `objectSize` sent" behavior) is proportionate. |

---

## Tech Debt Disposition
*(Per `research/architecture.md` §4.)*

| Area | Existing Issue | Disposition | Justification |
|------|----------------|--------------|----------------|
| `image_source.py` / `docs_request_builder.py` mermaid-image path | None — `research/architecture.md` explicitly found no SOLID/Clean/DDD violation here: the hypothesized "two separate mermaid-image code paths" doesn't exist, and `resolve_document_images()` already threads `rendered_bytes` through to exactly the seam this feature needs. | Extend as-is | The touched area is stable, single-path, and this change adds one more `if`-gated computation at an existing seam rather than another instance of any known violation — `architecture.md`'s own disposition for this area, restated here per the planning prompt's Step 3.5 instruction not to skip the section even when the answer is "no debt." |

---

## Migration Plan
N/A — no schema or data changes. `width_pt`/`height_pt` are computed values written into an existing `DocsImageNode` dataclass field and an existing Google Docs API request shape; nothing persists outside a single push's in-memory node list and the API request payload.

## Observability Plan
- **Logs**: none added. A dimension-read failure (defensive-only per ADR-001; expected to never fire against real `mmdc` output) silently skips sizing rather than emitting a new log line — proportionate to a near-zero-likelihood path, per Pattern Decisions.
- **Metrics**: none — one-time, in-memory, sub-millisecond arithmetic per push; not on any latency-sensitive path per requirements.md's Non-functional Requirements ("not applicable").
- **Alerts**: no new alerts required.

## Risk Control
- **Feature flag**: not gated — this only changes the *default* size/alignment applied when a mermaid image previously had no size at all (Docs' own tiny fallback); there is no prior explicit behavior to preserve behind a flag.
- **Rollback procedure**: standard revert via PR close + revert commit. No data migration, no irreversible side effect — the mermaid render/PNG cache (`mermaid_renderer.py`) is content-addressed and unaffected by this change's logic (per requirements.md's Risk Control section).
- **Staged rollout**: full rollout on merge.

## Unresolved Questions
None. The one open interpretive question research flagged — "does 'sized to page content width' mean cap-only (never upscale) or always-fill (scale to exactly 468pt)?" — is resolved by this plan: **always-fill**. `requirements.md`'s Success Metric states a pushed diagram "is inserted sized to the page's content width," not "sized up to at most the page's content width," and the Problem Statement's defect is specifically that no `objectSize` is set at all today (Docs' own tiny fallback governs), not that native diagram sizing is inconsistently too small. `_mermaid_image_size_pt()` therefore computes `scale = CONTENT_WIDTH_PT / logical_width_pt` unconditionally (see Story 1.1.2), matching the "no height cap, width-only fit" scope item and the UX research's fit-to-column-width consensus (`research/ux.md` §1).

**Pre-mortem P1 status (both addressed, this revision):**
- **Failure #3** (untested `before_newline` CENTER sub-case) — Story 1.2.1 now has a fourth Given-When-Then covering `before_newline` (range `{startIndex: insert_at_index+1, endIndex: insert_at_index+3}`), cross-referenced to validation.md's `test_mermaid_image_insert_adds_center_paragraph_style_request_when_before_newline`. Task 1.2.1b's description now explicitly names all three paragraph-boundary sub-cases (`is_bare_image`, `before_newline`, default) as required coverage, not two.
- **Failure #5** (no push-time signal for the known retroactive-resize limitation) — new Task 1.1.3c (Story 1.1.3) adds a stale-size push warning in `backend.py`'s `_build_push_plan()`, appended to the existing `image_warnings`/`plan.image_warnings` residue list, plus a corresponding Story 1.1.3 acceptance criterion and Task 1.1.3d test. The underlying idempotency limitation itself is unchanged and stays as designed in Story 1.3.3 — this only makes it visible at push time. Note the implementation lives in `backend.py`, not `image_source.py`: `resolve_document_images()` runs before the pulled doc (`current_nodes`) is fetched, so it has no pulled-size data to compare against.

## Known Limitation: Existing (Already-Pushed) Mermaid Diagrams Are Not Resized

This is a verified, load-bearing behavior of the existing diff/idempotency mechanism, not merely an out-of-scope simplification. Traced by adversarial review (`implementation/adversarial-review.md`, Blocker 2):

- A previously-pushed mermaid image's pulled `DocsImageNode` carries its real current `width_pt`/`height_pt` from the Docs API (`docs_structure_parser.py:554-555`).
- The post-feature target node computes this feature's new size (e.g. `468.0`/`234.0`) via `_mermaid_image_size_pt()` (`image_source.py`).
- `_node_key()` (`docs_request_builder.py:288`) includes `width_pt`/`height_pt`, so the pulled and target nodes' keys now differ.
- `_repair` folds this mismatch back to "equal" whenever `_content_key()` matches; for `DocsImageNode`, `_content_key()` is `(alt,)` only (`docs_request_builder.py:383-386`) — `alt` is an unchanged content-hash of the diagram source, so it still matches.
- Once folded to "equal," `_restyles()`/`_make_style_update_requests()` (`docs_request_builder.py:3043-3095`) explicitly no-op for `DocsImageNode`, so the new size/center is silently swallowed forever on every subsequent push of an already-existing diagram.

**Consequence**: this feature sizes/centers a mermaid diagram only at first insertion. Re-pushing a diagram whose previously-pushed size differs from what this feature would now compute (because the diagram's content changed, or because this feature was just deployed onto a doc with pre-existing small diagrams) does not retroactively resize it. The push is a safe no-op — not a crash, not corruption, not a spurious update — but also not a fix; readers must re-insert (delete-and-repush) an old diagram to pick up the new sizing. This is intentional given the diff engine's existing content-key design (`_node_key`'s own docstring documents the destructive alternative: flipping to delete-and-reinsert risks destroying anchored comments), so it is a **known limitation of that mechanism**, not an arbitrary scope cut. Story 1.3.3 below adds regression coverage locking in the current (safe no-op) behavior, so a future unrelated change to `_repair`/`_content_key`/`_restyles` can't silently flip it into something destructive without a test failing.

**Made visible, not fixed, at push time**: Task 1.1.3c (Story 1.1.3) adds a push-time warning surfaced in `plan.image_warnings` whenever a pulled diagram's existing `width_pt`/`height_pt` differs from what this feature would now compute for the same diagram — so a user re-pushing an old doc sees "diagram size may be stale ... re-create the image node or manually resize" instead of silence. This does not change the no-op behavior traced above (Story 1.3.3's regression test still locks in zero requests for that node); it only ensures the limitation is discoverable at the moment it would otherwise surprise a user, per pre-mortem.md Failure #5 (P1).

## Dependency Visualization

```
Epic 1.1 (image_source.py: read + compute)
  Story 1.1.1 (read IHDR)  ──┐
  Story 1.1.2 (compute pt)  ─┼─► Story 1.1.3 (wire into resolve_document_images)
                              │                     │
                              │                     ├─► Task 1.1.3c (backend.py: stale-size
                              │                     │    push warning, needs current_nodes,
                              │                     │    which only exists in backend.py's
                              │                     │    _build_push_plan, not image_source.py)
                              │                     │
Epic 1.2 (docs_request_builder.py: center)          │
  Story 1.2.1 (CENTER alignment) ◄───────────────────┘  (independent code path, but
                                                          exercised by the same node
                                                          once 1.1.3 lands — sequenced
                                                          after so end-to-end tests in
                                                          Epic 1.3 have both halves)
Epic 1.3 (tests)
  Story 1.3.1 (fix fixture) ──► Story 1.3.2 (new assertions, depends on 1.1.3 + 1.2.1)
                              ├─► Story 1.3.3 (no-retroactive-resize regression test)
                              └─► Story 1.3.4 (manual real-doc verification)
```

---

## Phase 1: Auto-size and center mermaid diagrams in Google Docs

### Epic 1.1: Compute width_pt/height_pt from the rendered PNG
**Goal**: A mermaid image's `DocsImageNode` carries real, deterministic, aspect-ratio-preserving `width_pt`/`height_pt` before it reaches `docs_request_builder.py`.

#### Story 1.1.1: Read PNG pixel dimensions from rendered mermaid bytes without crashing on malformed input
**As a** docspan push pipeline, **I want** to read a mermaid-rendered PNG's pixel width/height from its bytes, **so that** sizing can be computed without a new image-library dependency.
**Acceptance Criteria**:
- Given a well-formed minimal PNG byte string (valid 8-byte signature, `IHDR` chunk immediately following with width `2400` and height `1200` encoded as big-endian `uint32`s at bytes 16-19/20-23), when `_png_pixel_dimensions(data)` is called, then it returns `(2400, 1200)`.
  - *Given* `data = png_signature + struct.pack(">I", 25) + b"IHDR" + struct.pack(">II", 2400, 1200) + b"..."` (real IHDR layout), *When* `_png_pixel_dimensions(data)` runs, *Then* it returns `(2400, 1200)`.
- Given bytes that are not a valid PNG (e.g. the pre-fix test fixture `_PNG_MAGIC + diagram.encode("utf-8")`, which has the right 8-byte signature but no real `IHDR` chunk after it), when `_png_pixel_dimensions(data)` is called, then it returns `None` rather than raising.
  - *Given* `data = b"\x89PNG\r\n\x1a\n" + b"graph TD\n  A --> B"` (magic bytes only, no `IHDR`), *When* `_png_pixel_dimensions(data)` runs, *Then* it returns `None` (no exception).
- Given a truncated byte string shorter than 24 bytes, when `_png_pixel_dimensions(data)` is called, then it returns `None`.
  - *Given* `data = b"\x89PNG\r\n\x1a\n"` (8 bytes), *When* `_png_pixel_dimensions(data)` runs, *Then* it returns `None`.
**Files**: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.1a: Add `import struct` and the `_png_pixel_dimensions()` helper (~3 min)
- In `src/docspan/backends/google_docs/image_source.py`, add `import struct` to the import block (after `import hashlib` at line 12).
- After the `_MAGIC_BYTES` dict (line 33), add:
  ```python
  def _png_pixel_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
      """Read (width_px, height_px) from a PNG's IHDR chunk, or None if malformed.

      PNG guarantees IHDR is the first chunk: 8-byte signature, 4-byte chunk
      length, 4-byte "IHDR" tag, then width/height as big-endian uint32s at
      bytes 16-19/20-23 -- avoids a new Pillow dependency (see
      project_plans/mermaid-diagram-sizing/decisions/ADR-001-png-dimension-reading.md),
      consistent with this module's existing magic-byte MIME sniffing instead
      of a full image-parsing library.
      """
      if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
          return None
      width, height = struct.unpack(">II", data[16:24])
      if width == 0 or height == 0:
          return None
      return width, height
  ```
- Files: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.1b: Add unit tests for `_png_pixel_dimensions()` (~4 min)
- In `tests/test_gdocs_mermaid.py`, add a new `# PNG dimension reading` section with three tests matching Story 1.1.1's three Given-When-Then cases (valid IHDR → tuple; magic-bytes-only fake PNG → `None`; truncated bytes → `None`). Build the valid-PNG fixture with `struct.pack`, not a hand-typed byte literal, so the width/height values are visibly `2400`/`1200` in the test rather than opaque bytes.
- Import `_png_pixel_dimensions` from `docspan.backends.google_docs.image_source`.
- Files: `tests/test_gdocs_mermaid.py`

---

#### Story 1.1.2: Compute width_pt/height_pt scaled to content width with deterministic rounding
**As a** docspan push pipeline, **I want** a single function that turns raw PNG pixel dimensions into `(width_pt, height_pt)` scaled to fill `CONTENT_WIDTH_PT`, **so that** repeated pushes of an unchanged diagram always produce byte-identical values (protecting the diff-identity key).
**Acceptance Criteria**:
- Given a PNG rendered at `RENDER_SCALE=3` with native pixel dimensions `2400x1200` (i.e. logical CSS size `800x400px`), when `_mermaid_image_size_pt(png_bytes)` is called, then it returns `(468.0, 234.0)`.
  - *Given* `native_width_px, native_height_px = 2400, 1200` and `RENDER_SCALE = 3`, *When* `_mermaid_image_size_pt(png_bytes)` runs, *Then* `logical_width_pt = 2400/3*0.75 = 600.0`, `scale = 468.0/600.0 = 0.78`, and the function returns `(round(600.0*0.78, 2), round(300.0*0.78, 2)) == (468.0, 234.0)`.
- Given two PNGs whose native pixel dimensions differ only by `RENDER_SCALE` (e.g. `2400x1200` at `RENDER_SCALE=3` vs. `4800x2400` at a hypothetical `RENDER_SCALE=6`, both representing the same logical `800x400px` diagram), when `_mermaid_image_size_pt()` is called on each (with `image_source.RENDER_SCALE` monkeypatched to match), then both calls return the identical `(468.0, 234.0)` — proving the result is independent of `RENDER_SCALE`'s value, per `research/pitfalls.md` §1's must-catch regression.
  - *Given* `png_a` at `2400x1200px` with `RENDER_SCALE=3` and `png_b` at `4800x2400px` with `RENDER_SCALE=6`, *When* both are passed to `_mermaid_image_size_pt()` under their respective monkeypatched `RENDER_SCALE`, *Then* `_mermaid_image_size_pt(png_a) == _mermaid_image_size_pt(png_b) == (468.0, 234.0)`.
- Given malformed PNG bytes (`_png_pixel_dimensions()` returns `None`), when `_mermaid_image_size_pt(data)` is called, then it returns `None`.
  - *Given* `data = b"\x89PNG\r\n\x1a\n" + b"not a real png"`, *When* `_mermaid_image_size_pt(data)` runs, *Then* it returns `None`.
- Given a diagram whose logical width is already narrower than `CONTENT_WIDTH_PT` (e.g. a small 2-node diagram at native pixel dimensions `800x400px` with `RENDER_SCALE=3`, i.e. logical `200x100pt`), when `_mermaid_image_size_pt()` is called, then it is scaled *up* to `width_pt=468.0` (unconditional fill, per this plan's Unresolved-Questions resolution, not cap-only) with height scaled by the same factor.
  - *Given* `native_width_px, native_height_px = 800, 400` and `RENDER_SCALE = 3`, so `logical_width_pt = 800/3*0.75 = 200.0` and `logical_height_pt = 400/3*0.75 = 100.0`, *When* `_mermaid_image_size_pt()` computes `scale = 468.0/200.0 = 2.34`, *Then* it returns `(round(200.0*2.34, 2), round(100.0*2.34, 2)) == (468.0, 234.0)`.
**Files**: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.2a: Add `CONTENT_WIDTH_PT`/`_PT_PER_CSS_PX` constants and import `RENDER_SCALE` (~2 min)
- In `src/docspan/backends/google_docs/image_source.py`, change the existing import (line 19) from:
  ```python
  from docspan.backends.google_docs.mermaid_renderer import MermaidRenderError, render_mermaid_png
  ```
  to:
  ```python
  from docspan.backends.google_docs.mermaid_renderer import (
      RENDER_SCALE,
      MermaidRenderError,
      render_mermaid_png,
  )
  ```
- After `MAX_IMAGE_BYTES` (line 23), add:
  ```python
  # Letter page, 1in margins each side: 6.5in x 72pt/in content width. A
  # deliberate simplification -- doesn't read a doc's actual documentStyle
  # margins (see requirements.md's "Page content width isn't universal"
  # rabbit hole).
  CONTENT_WIDTH_PT = 468.0

  # 96 CSS px/in (the standard browser reference pixel mmdc's Chromium/
  # Puppeteer renderer uses) / 72pt/in.
  _PT_PER_CSS_PX = 0.75
  ```
- Files: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.2b: Add `_mermaid_image_size_pt()` helper (~5 min)
- Directly below `_png_pixel_dimensions()` (added in Task 1.1.1a), add:
  ```python
  def _mermaid_image_size_pt(png_bytes: bytes) -> Optional[Tuple[float, float]]:
      """Compute (width_pt, height_pt) for a mermaid PNG, filling CONTENT_WIDTH_PT.

      Divides out RENDER_SCALE (mmdc's pure supersampling factor) before any
      px-to-pt conversion, then derives height from width via one shared scale
      factor rather than independently rounding each axis -- so an unchanged
      diagram's rendered PNG always yields the identical (width_pt, height_pt)
      pair docs_request_builder.py's diff-identity key relies on (see
      research/pitfalls.md #2-3).
      """
      dims = _png_pixel_dimensions(png_bytes)
      if dims is None:
          return None
      native_width_px, native_height_px = dims
      logical_width_pt = (native_width_px / RENDER_SCALE) * _PT_PER_CSS_PX
      logical_height_pt = (native_height_px / RENDER_SCALE) * _PT_PER_CSS_PX
      scale = CONTENT_WIDTH_PT / logical_width_pt
      return round(logical_width_pt * scale, 2), round(logical_height_pt * scale, 2)
  ```
- Files: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.2c: Add unit tests for `_mermaid_image_size_pt()` (~5 min)
- In `tests/test_gdocs_mermaid.py`, add tests matching Story 1.1.2's four Given-When-Then cases: the `2400x1200` → `(468.0, 234.0)` case; the `RENDER_SCALE`-independence case (monkeypatch `image_source.RENDER_SCALE` via `monkeypatch.setattr`); the malformed-input → `None` case; and the upscale-small-diagram case.
- Build each fixture PNG with `struct.pack(">II", width, height)` inserted into a minimal valid IHDR-bearing byte string (a small helper `_minimal_png(width, height)` local to the test file is reasonable here, reused by Task 1.1.1b's tests too — refactor Task 1.1.1b's inline fixture into this helper if written first).
- Files: `tests/test_gdocs_mermaid.py`

---

#### Story 1.1.3: Wire computed size into `resolve_document_images()` for mermaid images only
**As a** docspan push pipeline, **I want** `resolve_document_images()` to set `width_pt`/`height_pt` on a mermaid `DocsImageNode` using its rendered PNG bytes, **so that** the size reaches `docs_request_builder.py`'s already-wired `objectSize` logic with zero changes needed there.
**Acceptance Criteria**:
- Given a `DocsImageNode` with `mermaid_source` set and a renderer that returns a valid `2400x1200`px PNG, when `resolve_document_images([node], ...)` is called, then the returned node has `width_pt == 468.0` and `height_pt == 234.0`.
  - *Given* `node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B")` and a renderer returning a real `2400x1200` PNG, *When* `resolve_document_images([node], str(tmp_path/"doc.md"), _fake_uploader, renderer=<that renderer>)` runs, *Then* `out[0].width_pt == 468.0 and out[0].height_pt == 234.0`.
- Given a non-mermaid `DocsImageNode` (plain `![alt](src)` image, `mermaid_source is None`), when `resolve_document_images([node], ...)` is called, then `width_pt`/`height_pt` on the returned node remain unset (`None`) — this feature never sizes a non-mermaid image.
  - *Given* `node = DocsImageNode(alt="a photo", src="local.png")` with no `mermaid_source`, *When* `resolve_document_images([node], ...)` runs, *Then* `out[0].width_pt is None and out[0].height_pt is None`.
- Given a `DocsImageNode` that already carries an explicit `width_pt`/`height_pt` (hypothetically, even though no push-side code path sets this today per `research/architecture.md` §1), when `resolve_document_images([node], ...)` is called, then the existing values are preserved, not overwritten — satisfying requirements.md's "must not change behavior for user-supplied images with explicit width/height" constraint.
  - *Given* `node = DocsImageNode(alt="mermaid diagram abc123", mermaid_source="graph TD\n  A --> B", width_pt=100.0, height_pt=50.0)`, *When* `resolve_document_images([node], ...)` runs, *Then* `out[0].width_pt == 100.0 and out[0].height_pt == 50.0` (unchanged).
- Given the render fails (`MermaidRenderError`, existing failure contract), when `resolve_document_images([node], ...)` is called, then the returned slot is `None` (unchanged existing behavior) and no sizing code runs.
  - *Given* a renderer that raises `MermaidRenderError("boom")`, *When* `resolve_document_images([node], ...)` runs, *Then* `out == [None]` and a warning containing `"mermaid render failed"` is returned (existing test `test_mermaid_render_failure_is_a_warning_not_a_crash` already covers this; this criterion only confirms no regression).
- Given a doc already containing a pushed mermaid diagram at some existing `width_pt`/`height_pt` (pulled via `DocsStructureParser`), and this feature computes a *different* `width_pt`/`height_pt` for the same diagram (matched by `alt`) on this push, when `_build_push_plan()` runs, then a push warning is surfaced (e.g. `"diagram size may be stale; re-create the image node or manually resize"`) — even though (per the Known Limitation below) the diff engine still silently no-ops the actual resize. This closes pre-mortem.md Failure #5 (P1): the retroactive-resize limitation had no push-time signal. See Task 1.1.3c.
  - *Given* `current_nodes` (pulled) contains `DocsImageNode(alt="mermaid diagram abc123", width_pt=100.0, height_pt=50.0)` and the freshly resolved target node for the same `alt` computes `width_pt=468.0, height_pt=234.0`, *When* `_build_push_plan()` runs, *Then* `plan.image_warnings` contains a string mentioning both "stale" and the diagram's `alt`, and the emitted requests for that node are still the Story 1.3.3 no-op (the warning is additive, not a behavior change to the diff).
**Files**: `src/docspan/backends/google_docs/image_source.py`, `src/docspan/backends/google_docs/backend.py`

##### Task 1.1.3a: Compute and set `width_pt`/`height_pt` in the `out` loop (~4 min)
- In `resolve_document_images()` (`src/docspan/backends/google_docs/image_source.py`), replace the loop at lines 304-307:
  ```python
  out: List[Optional[DocsImageNode]] = []
  for i, node in enumerate(nodes):
      result = resolved.get(str(i))
      out.append(replace(node, src=result.uri) if result else None)
  return out, warnings, temp_drive_file_ids, mermaid_entries
  ```
  with:
  ```python
  out: List[Optional[DocsImageNode]] = []
  for i, node in enumerate(nodes):
      result = resolved.get(str(i))
      if result is None:
          out.append(None)
          continue
      updates: Dict[str, object] = {"src": result.uri}
      if node.mermaid_source and node.width_pt is None and result.rendered_bytes is not None:
          size = _mermaid_image_size_pt(result.rendered_bytes)
          if size is not None:
              updates["width_pt"], updates["height_pt"] = size
      out.append(replace(node, **updates))
  return out, warnings, temp_drive_file_ids, mermaid_entries
  ```
  This mirrors the existing guard shape at line 298 (`if node.mermaid_source and result is not None and result.rendered_bytes is not None`) so the sizing computation only ever runs on the success path, and the `node.width_pt is None` check preserves any (hypothetical, per Story 1.1.3's third criterion) pre-existing explicit size.
- Files: `src/docspan/backends/google_docs/image_source.py`

##### Task 1.1.3b: Add integration tests for the wiring (~5 min)
- In `tests/test_gdocs_mermaid.py`, add tests matching Story 1.1.3's first three Given-When-Then cases. Reuse the `_minimal_png()` helper from Task 1.1.2c as the injected renderer's return value in place of `_fake_renderer` where a real size needs to be asserted (see Story 1.3.1 for the broader fixture fix — this task's new tests can use the fixed fixture directly rather than duplicating the fix).
- Files: `tests/test_gdocs_mermaid.py`

##### Task 1.1.3c: Warn at push time when a mermaid diagram's stored size looks stale (~8 min)
**Addresses pre-mortem.md Failure #5 (P1).** `resolve_document_images()` cannot detect staleness itself: `_build_push_plan()` (`backend.py:216-266`) calls it at line 259, *before* the doc is fetched and `current_nodes = DocsStructureParser().parse(doc)` runs at line 266 — so the pulled document's existing `width_pt`/`height_pt` for an already-pushed diagram isn't available yet at the point sizing is computed. The comparison therefore belongs in `_build_push_plan()`, right after `current_nodes` exists, alongside the adjacent `existing_image_alts` lookup (`backend.py:291-293`) that already matches images by `alt` for a related reason (preserving an unresolved image's original node).
- In `src/docspan/backends/google_docs/backend.py`, immediately after the `existing_image_alts` computation (line 291-293), add a parallel lookup:
  ```python
  existing_image_sizes = {
      n.alt: (n.width_pt, n.height_pt)
      for n in current_nodes
      if isinstance(n, DocsImageNode) and n.width_pt is not None
  }
  ```
- For each resolved mermaid node (`resolved is not None and resolved.mermaid_source is not None`), look up `existing_image_sizes.get(resolved.alt)`. If found and it differs (beyond a small rounding tolerance, e.g. `abs(old_w - resolved.width_pt) > 0.5 or abs(old_h - resolved.height_pt) > 0.5`) from `(resolved.width_pt, resolved.height_pt)`, append a warning string to `image_warnings` (the same `List[str]` already threaded into `plan.image_warnings` at line 371 and surfaced to users at lines 719/786-787), e.g.:
  ```python
  f"diagram size may be stale ({resolved.alt}): "
  f"doc has {old_w}x{old_h}pt, would now be {resolved.width_pt}x{resolved.height_pt}pt "
  "-- re-create the image node or manually resize (this push will not resize it; see known limitation)"
  ```
- This is additive only — it does not change `_repair`/`_content_key`/`_restyles` or the request list itself (Story 1.3.3's no-op behavior is unchanged and still the source of truth for what actually happens on push). It only makes that existing, silent behavior visible.
- This is a different residue-warning *list* than `ImageResolutionError` (which models a *failed* resolution, keyed by source dict key, and is emitted inside `resolve_images()` in `image_source.py`) — this warning is about a *successful* resolution with a stale-size caveat, discovered one layer up in `backend.py` where the pulled doc is available. Both ultimately land in the same user-facing `image_warnings`/`plan.image_warnings` surfacing mechanism, which is the "residue-warning path" pre-mortem.md Failure #5 refers to.
- Files: `src/docspan/backends/google_docs/backend.py`

##### Task 1.1.3d: Add a test for the stale-size push warning (~5 min)
- In a `backend.py`-level test file (e.g. `tests/test_gdocs_backend.py` or wherever `_build_push_plan`/`push_plan` behavior is already tested — check for an existing file first), add a test implementing Story 1.1.3's fourth Given-When-Then: a pulled doc with an existing mermaid `DocsImageNode` at a stale size, a target markdown producing a freshly-computed different size for the same `alt`, and assert `plan.image_warnings` contains a matching stale-size warning while the emitted request list for that node is still empty (Story 1.3.3's no-op, unaffected).
- Files: `tests/test_gdocs_backend.py` (or the existing colocated `_build_push_plan` test file, if found)

---

### Epic 1.2: Center mermaid image paragraphs on insert
**Goal**: A mermaid image's enclosing paragraph gets `CENTER` alignment on push; a plain (non-mermaid) image's paragraph is untouched.

#### Story 1.2.1: Add a CENTER `updateParagraphStyle` request for mermaid image paragraphs only
**As a** reader of a doc docspan pushed to, **I want** a mermaid diagram's paragraph horizontally centered, **so that** the diagram doesn't look left-stuck relative to surrounding centered/full-width content.
**Acceptance Criteria**:
- Given a `DocsImageNode` with `mermaid_source` set, `width_pt=468.0`, `height_pt=234.0`, inserted via the non-bare, non-`before_newline` path (the common case), when the image-insert requests are built, then the resulting request list includes an `updateParagraphStyle` request with `paragraphStyle: {"alignment": "CENTER"}`, `fields: "alignment"`, and `range == {"startIndex": insert_at_index, "endIndex": insert_at_index + 2}`, appended after the `insertInlineImage` and `insertText` requests for that node.
  - *Given* `node = DocsImageNode(src="https://drive.example.com/x", mermaid_source="graph TD\n A-->B", width_pt=468.0, height_pt=234.0)` and `insert_at_index=10`, *When* the builder's image-insert branch runs (`before_newline=False`, `bare_last=False`), *Then* the request list's tail three entries are `insertInlineImage` at index 10, `insertText` (newline) at index 11, and `updateParagraphStyle` with `range={"startIndex": 10, "endIndex": 12}`, `paragraphStyle={"alignment": "CENTER"}`, `fields="alignment"`.
- Given a `DocsImageNode` with `mermaid_source is None` (a plain user image) at the same insert point, when the image-insert requests are built, then no `updateParagraphStyle` request is added for that node — its paragraph alignment is left exactly as it is today.
  - *Given* `node = DocsImageNode(src="https://example.com/photo.png", alt="a photo")` (no `mermaid_source`), *When* the builder's image-insert branch runs, *Then* the request list for that node contains exactly `insertInlineImage` and `insertText`, no `updateParagraphStyle`.
- Given the same mermaid node inserted via the `is_bare_image` path (`bare_last=True`, node is the last of `nodes`), when the image-insert requests are built, then the `updateParagraphStyle` range is `{"startIndex": insert_at_index, "endIndex": insert_at_index + 1}` (covering only the 1-unit image element, since no boundary newline is inserted in this case).
  - *Given* the same node as above with `bare_last=True` and it being `nodes[-1]`, *When* the builder runs, *Then* the request list's tail two entries are `insertInlineImage` at `insert_at_index` and `updateParagraphStyle` with `range={"startIndex": insert_at_index, "endIndex": insert_at_index + 1}`.
- Given the same mermaid node inserted via the `before_newline` path (a leading newline is emitted ahead of the image, so the paragraph itself starts one UTF-16 unit later than `insert_at_index`), when the image-insert requests are built, then the `updateParagraphStyle` range is `{"startIndex": insert_at_index + 1, "endIndex": insert_at_index + 3}` — mirroring the default case's range shifted by the leading newline, not the unshifted `insert_at_index`. This closes pre-mortem.md Failure #3 (P1): the `before_newline` sub-case was previously untested, risking a misplaced `CENTER` request corrupting an adjacent paragraph's formatting. Covered by validation.md's `test_mermaid_image_insert_adds_center_paragraph_style_request_when_before_newline` (Requirement → Test Mapping table, "Story 1.2.1 CONCERN" row).
  - *Given* the same node as above with `before_newline=True` and `insert_at_index=10`, *When* the builder runs, *Then* the emitted `updateParagraphStyle` has `range={"startIndex": 11, "endIndex": 13}`, `paragraphStyle={"alignment": "CENTER"}`, `fields="alignment"`.
**Files**: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 1.2.1a: Add the CENTER `updateParagraphStyle` request inside the image branch (~5 min)
- In `src/docspan/backends/google_docs/docs_request_builder.py`, inside the `if isinstance(node, DocsImageNode):` branch (starting line 2807), after the existing `if/elif/else` that appends `insertInlineImage`/`insertText` (ending just before the `continue` at line 2849), add:
  ```python
  if node.mermaid_source is not None:
      paragraph_start = insert_at_index + 1 if before_newline else insert_at_index
      paragraph_len = 1 if is_bare_image else 2
      requests.append({
          "updateParagraphStyle": {
              "range": {
                  "startIndex": paragraph_start,
                  "endIndex": paragraph_start + paragraph_len,
              },
              "paragraphStyle": {"alignment": "CENTER"},
              "fields": "alignment",
          }
      })
  ```
  placed before the `continue` statement, so it executes for every branch (`is_bare_image`, `before_newline`, and the default `else`) using the already-computed `is_bare_image`/`before_newline` locals. This mirrors the adjacent plain-paragraph branch's `paragraph_start = insert_at_index + 1 if before_newline else insert_at_index` (line 2864) exactly, substituting the image's fixed 1-or-2-UTF16-unit length for the text branch's `_utf16_len(...)` call.
- Files: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 1.2.1b: Add unit tests for the CENTER request (~6 min)
- In `tests/test_gdocs_mermaid.py` (or wherever `docs_request_builder.py`'s image-insert branch is already unit-tested — check for an existing `test_gdocs_request_builder*.py`/similar file first and add there if one exists, to keep request-builder tests colocated), add tests matching Story 1.2.1's four Given-When-Then cases. This task must implement and cover **all three** paragraph-boundary sub-cases Task 1.2.1a's code handles — `is_bare_image` (paragraph_len=1), `before_newline` (paragraph_start shifted by +1), and the default/neither case (paragraph_len=2, unshifted start) — not just two of the three; the `before_newline` sub-case was the gap flagged by pre-mortem.md Failure #3 (P1).
- Files: `tests/test_gdocs_mermaid.py` (or the colocated request-builder test file, if found)

---

### Epic 1.3: Test fixture repair and regression coverage
**Goal**: Existing tests keep passing against real PNG bytes; new coverage locks in the `RENDER_SCALE`-independence and non-regression guarantees the pitfalls research flagged as must-catch; the known already-pushed-diagram limitation is tested rather than assumed; and requirements.md's Success Metric is verified against a real doc push, not only in-process tests.

#### Story 1.3.1: Replace `_fake_renderer`'s output with a structurally valid minimal PNG
**As a** test author, **I want** `tests/test_gdocs_mermaid.py`'s fake renderer to return real, IHDR-bearing PNG bytes, **so that** the new sizing code has something real to parse instead of crashing on the pre-existing `_PNG_MAGIC + diagram_bytes` stub.
**Acceptance Criteria**:
- Given the updated `_fake_renderer(diagram)`, when it is called with any diagram string, then its return value passes `_png_pixel_dimensions()` and yields a fixed, known `(width_px, height_px)` (e.g. `(2400, 1200)`, matching Story 1.1.2's worked example so existing and new tests share one expected `width_pt`/`height_pt` pair).
  - *Given* `_fake_renderer("graph TD\n  A --> B")`, *When* `_png_pixel_dimensions(_fake_renderer("graph TD\n  A --> B"))` runs, *Then* it returns `(2400, 1200)`, not `None`.
- Given all pre-existing tests in `tests/test_gdocs_mermaid.py` that call `_fake_renderer` and assert on `uri`/`temp_drive_file_id`/`alt`/sidecar behavior (not on size), when the fixture changes, then those tests still pass unchanged — the fixture's byte content changes, but nothing about `resolve_document_images()`'s non-sizing behavior (URI, cache sidecar, warnings) does.
  - *Given* `test_resolve_document_images_uses_mermaid_source_over_src` (existing, line 87-96), *When* run against the new `_fake_renderer`, *Then* it still passes: `out[0].src`, `temp_ids`, and `mermaid_entries`' hash (recomputed against the new fixture bytes, since the assertion already calls `hashlib.sha256(_fake_renderer(...))` rather than a hardcoded hash) are unaffected by the fixture's content change.
**Files**: `tests/test_gdocs_mermaid.py`

##### Task 1.3.1a: Replace `_fake_renderer` with a real minimal PNG built via `struct` (~5 min)
- In `tests/test_gdocs_mermaid.py`, replace:
  ```python
  def _fake_renderer(diagram: str) -> bytes:
      return _PNG_MAGIC + diagram.encode("utf-8")
  ```
  with a helper that builds a structurally valid minimal PNG at a fixed, known size (reusing/extracting the `_minimal_png(width, height)` helper introduced in Task 1.1.2c if that task landed first — this task and 1.1.2c should converge on one shared helper, not two):
  ```python
  def _minimal_png(width: int = 2400, height: int = 1200) -> bytes:
      """A structurally valid minimal PNG: real IHDR, no real pixel data.

      Only the signature + IHDR chunk are real; sizing code only ever reads
      those 24 bytes (see _png_pixel_dimensions), so no IDAT/IEND chunks are
      needed for these tests.
      """
      ihdr_body = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
      return (
          _PNG_MAGIC
          + struct.pack(">I", len(ihdr_body))
          + b"IHDR"
          + ihdr_body
      )


  def _fake_renderer(diagram: str) -> bytes:
      return _minimal_png()
  ```
  Add `import struct` to the file's imports if not already present from Task 1.1.1b/1.1.2c.
- Note: `diagram` is now unused in `_fake_renderer`'s body — keep the parameter (the `Renderer` type alias requires `Callable[[str], bytes]`) but this is expected, not a lint issue to chase.
- Files: `tests/test_gdocs_mermaid.py`

---

#### Story 1.3.2: Assert end-to-end sizing + alignment through the full push pipeline
**As a** maintainer, **I want** at least one test exercising `resolve_document_images()` → `docs_request_builder.py`'s image-insert branch together, **so that** a future refactor can't silently break the size-and-center feature without a test failing.
**Acceptance Criteria**:
- Given a markdown mermaid fence parsed to a `DocsImageNode`, resolved via `resolve_document_images()` with the fixed `_minimal_png()` renderer, and then passed into `docs_request_builder.py`'s image-insert branch, when the full sequence runs, then the resulting `insertInlineImage` request's `objectSize` is `{"height": {"magnitude": 234.0, "unit": "PT"}, "width": {"magnitude": 468.0, "unit": "PT"}}` and an accompanying `updateParagraphStyle` request sets `alignment: "CENTER"`.
  - *Given* `MERMAID_MD` (existing module constant) parsed, then resolved with `renderer=_fake_renderer`, *When* the resolved node's `width_pt`/`height_pt`/`mermaid_source` are fed into the request builder's image branch, *Then* the request list contains an `insertInlineImage` with `objectSize.width.magnitude == 468.0` and `objectSize.height.magnitude == 234.0`, plus one `updateParagraphStyle` with `paragraphStyle.alignment == "CENTER"`.
- Given the same end-to-end flow repeated twice with an unchanged diagram source, when both pushes' resulting `_node_key()` values are compared, then they are identical (`("__image__", alt, 468.0, 234.0)` both times) — confirming the diff engine treats the second push as unchanged, per requirements.md's idempotency rabbit hole.
  - *Given* two independent calls to `resolve_document_images()` with the same `mermaid_source` and the same `_fake_renderer`, *When* `DocsRequestBuilder()._node_key(node)` is computed for each result, *Then* both keys equal `("__image__", "mermaid diagram <hash>", 468.0, 234.0)` with the identical hash and floats.
**Files**: `tests/test_gdocs_mermaid.py`

##### Task 1.3.2a: Add the end-to-end sizing + alignment test (~5 min)
- In `tests/test_gdocs_mermaid.py`, add `test_mermaid_image_gets_sized_and_centered_on_push` implementing Story 1.3.2's first Given-When-Then: parse `MERMAID_MD`, call `resolve_document_images()` with `_fake_renderer`, then call `DocsRequestBuilder()`'s image-insert method (identify its exact public/internal name by reading `docs_request_builder.py` around the `_insert_requests`-equivalent method signature before writing this test — Task 1.2.1a's edit is in that same method) with the resolved node, and assert on the emitted `objectSize` and `updateParagraphStyle` request contents.
- Files: `tests/test_gdocs_mermaid.py`

##### Task 1.3.2b: Add the repeat-push idempotency test (~4 min)
- In `tests/test_gdocs_mermaid.py`, add `test_repeated_mermaid_push_has_stable_node_key` implementing Story 1.3.2's second Given-When-Then: run `resolve_document_images()` twice against the same diagram source and `_fake_renderer`, then assert `DocsRequestBuilder()._node_key(node)` is identical both times.
- Files: `tests/test_gdocs_mermaid.py`

##### Task 1.3.2c: Run the full existing mermaid/image test suites and confirm no regression (~3 min)
- Run `uv run pytest tests/test_gdocs_mermaid.py tests/test_gdocs_images.py tests/test_mermaid_appendix.py -v` and confirm all pass, including the pre-existing tests untouched by this feature (per requirements.md's Success Metrics: "existing non-mermaid images and existing tests ... are unaffected/updated, not broken").
- Files: none (verification task)

---

#### Story 1.3.3: Regression test — re-pushing an already-sized mermaid image is a safe no-op, not a resize or crash
**As a** maintainer, **I want** a regression test asserting that re-pushing a mermaid diagram whose previously-committed `width_pt`/`height_pt` differs from what this feature would now compute results in zero requests (no resize, no crash, no corruption), **so that** the diff engine's silent-swallow behavior documented above (see "Known Limitation: Existing (Already-Pushed) Mermaid Diagrams Are Not Resized") is a tested, intentional invariant rather than an untested assumption a future refactor could flip into something destructive.
**Acceptance Criteria**:
- Given a pulled `DocsImageNode` representing an already-pushed mermaid diagram with `width_pt=100.0`, `height_pt=50.0` (simulating an old, pre-feature or since-changed size) and unchanged `alt`, and a target node for the same diagram computed by this feature's new sizing logic (`width_pt=468.0`, `height_pt=234.0`), when the diff/repair pipeline (`_node_key`/`_content_key`/`_repair`) processes the pair, then `_repair` classifies them as unchanged (per the traced `_content_key() == (alt,)` mechanism) and the resulting request list contains **zero** requests for that node — no crash, no corruption, no spurious `updateParagraphStyle`/resize request.
  - *Given* `pulled = DocsImageNode(alt="mermaid diagram abc123", width_pt=100.0, height_pt=50.0, mermaid_source=None)` (as returned by the pull-side parser) and `target = DocsImageNode(alt="mermaid diagram abc123", width_pt=468.0, height_pt=234.0, mermaid_source="graph TD\n A-->B")` (as resolved by this feature), *When* the two are run through `DocsRequestBuilder`'s diff (`_node_key`/`_content_key`/`_repair`), *Then* `_content_key(pulled) == _content_key(target) == ("mermaid diagram abc123",)`, the pair is classified as unchanged, and the emitted request list for that node is empty.
- Given the same scenario, when the full diff pipeline runs, then it does not raise any exception — confirming this is a safe no-op, not a latent crash.
**Files**: `tests/test_gdocs_mermaid.py` (or wherever `_repair`/diff-level tests for images already live — check for an existing diff-engine test file first, per this plan's existing colocation convention from Story 1.2.1b).

##### Task 1.3.3a: Add the no-spurious-resize regression test (~5 min)
- In `tests/test_gdocs_mermaid.py` (or the colocated diff-engine test file, if one exists), add `test_repush_of_already_sized_mermaid_image_is_a_safe_noop` implementing Story 1.3.3's Given-When-Then: construct a "pulled" `DocsImageNode` with an old `width_pt`/`height_pt` and a "target" node with this feature's newly-computed size, run them through `DocsRequestBuilder`'s diff/repair path, and assert zero requests are emitted and no exception is raised.
- Files: `tests/test_gdocs_mermaid.py`

##### Task 1.3.3b: Run the new regression test alongside the existing suite (~2 min)
- Run `uv run pytest tests/test_gdocs_mermaid.py -v -k noop` (or the full file) and confirm it passes, documenting the current safe-no-op behavior as a locked-in regression rather than an untested assumption.
- Files: none (verification task)

---

#### Story 1.3.4: Manual verification against a real Google Doc push (not automated)
**As a** feature implementer, **I want** to manually push a real markdown file containing a mermaid fence to a real (scratch/test) Google Doc via the CLI and visually confirm the result, **so that** requirements.md's Success Metric — verified "against a real doc push, not just a unit test of the request payload" — is actually satisfied, not just asserted by in-process tests.

This is a manual, not automated, verification step. It complements (does not replace) Story 1.3.2's in-process end-to-end test. It's expected that Phase 6 (verify) of this SDD workflow will also perform real-world checks; naming this step explicitly here ensures the plan doesn't silently rely on that later phase to cover requirements.md's Success Metric.

**Acceptance Criteria**:
- Given a markdown file containing a ```` ```mermaid ```` fence (e.g. a simple `graph TD` diagram), mapped to a real scratch/test Google Doc via docspan's normal push CLI, when the file is pushed, then opening the doc in a browser shows the diagram inserted at approximately full content width (roughly 6.5in/468pt, not a small default-size thumbnail) and horizontally centered on the page, not left-aligned.
  - *Given* a scratch markdown file (e.g. `.scratch/mermaid-sizing-verify.md`, per this repo's gitignored `.scratch/` convention for local debug/snapshot artifacts) with a mermaid fence, mapped in a throwaway `docspan.yaml` entry to a real Google Doc created for this verification, *When* the project's push CLI is run against it, *Then* a visual check of the resulting Google Doc confirms the diagram is centered and reads at approximately full page width, matching requirements.md's Success Metric.
- Given the same push is repeated a second time with no changes to the source file, when the doc is re-opened, then the diagram's size/position is unchanged and no duplicate image was inserted (idempotency holds against a real doc, not just in-process).
**Files**: none (manual verification step; no source or test files are created by this story itself). Per this repo's `.scratch/` convention, any local test doc ID, source markdown fixture, or screenshot used for this verification may be noted under `.scratch/` — no new tracked infrastructure is introduced.

##### Task 1.3.4a: Manually push a real mermaid fence to a scratch Google Doc and visually confirm sizing/centering (~10 min)
- Create a scratch markdown file (e.g. `.scratch/mermaid-sizing-verify.md`) with a small mermaid fence, push it to a real (throwaway/test) Google Doc via the CLI, and open the resulting doc in a browser to confirm: (a) the diagram is roughly full content width, not tiny; (b) it's horizontally centered; (c) a second, unchanged push doesn't duplicate or resize it.
- This step is manual and not gated by CI — it directly satisfies requirements.md's Success Metric wording that no automated task in this plan otherwise covers. Note any scratch doc ID/markdown path under `.scratch/` if useful for later reference.
- Files: none tracked; optionally note the scratch doc ID/markdown path under `.scratch/`.

---

## Verification checklist (for Phase 6, not a task to implement now)
- [ ] `width_pt`/`height_pt` for a diagram rendered at the *current* `RENDER_SCALE` value roughly match `CONTENT_WIDTH_PT` (~468pt), not ~3x that (~1404pt) — the single highest-risk mistake per requirements.md's Rabbit Holes and `research/pitfalls.md` §1.
- [ ] No `Pillow`/`PIL` entry was added to `pyproject.toml` (confirms ADR-001 was followed).
- [ ] A non-mermaid image's paragraph has no `updateParagraphStyle` alignment request added.
- [x] Story 1.3.4's manual real-doc push was performed and confirmed (centered, ~full content width) — done post-merge (2026-09-19) via a real push to a scratch Google Doc, verified against the live Docs API response rather than a visual check: `objectSize` was `width=467.998pt` (≈ `CONTENT_WIDTH_PT`), `height=537.16pt`, and the image's own paragraph (and only that paragraph's range) carried `alignment: CENTER`. Confirmed via `git grep -n '"CENTER"'` that this string appears exactly once in the google_docs backend (docs_request_builder.py) that the update request's range was scoped to just the image's 2-unit paragraph, not bleeding into neighboring paragraphs.
- [ ] Story 1.3.3's regression test passes, confirming re-pushing an already-sized mermaid diagram is a safe no-op (no crash, no corruption, no spurious resize).
- [ ] Task 1.1.3c's stale-size warning fires when re-pushing a doc with a pre-existing mermaid diagram whose stored size differs from the freshly-computed size, and does not fire when they match or when there's no pre-existing diagram.
- [ ] Task 1.2.1b's tests cover all three paragraph-boundary sub-cases (`is_bare_image`, `before_newline`, default) — not just two — closing pre-mortem.md Failure #3.
