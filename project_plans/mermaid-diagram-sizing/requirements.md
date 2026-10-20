# Requirements: mermaid-diagram-sizing

**Date**: 2026-09-18
**Type**: feature addition
**Complexity**: 2 — focused feature

## Problem Statement
Mermaid diagrams pushed by docspan to Google Docs render too small to read comfortably and are left-aligned instead of centered. Google Docs' `insertInlineImage` request never sets `objectSize`, so Docs falls back to a small default insertion size; there's no built-in image zoom in Google Docs, so a reader stuck with a small diagram has no easy way to inspect it in place.

**Scope correction (post-research, 2026-09-18)**: Confluence was originally in scope too, but Phase 2 research (`research/architecture.md`) found Confluence's mermaid→image path is dead code in production — `MermaidParser.render_diagram()` (`markdown/extensions/mermaid.py`) is an unimplemented stub, so a real push of a ```mermaid fence to Confluence today falls through to a plain `code_block` (visible source text, no rendered image at all), never reaching `MermaidNodeConverter`'s `width=800`/`layout="center"` branch. There is nothing to size or center yet on that backend. Per user decision, Confluence is now **out of scope** for this project; Google Docs is the sole target.

## Baseline
Today, a reader who pastes a ```mermaid fence into a doc synced to Google Docs gets an inline image sized to mmdc's rendered pixel dimensions with no `objectSize` override and no paragraph alignment — small, left-aligned, and only enlargeable by manually dragging the image's resize handles (no zoom/lightbox exists in Docs).

## Users / Consumers
Anyone reading a doc that docspan pushed a `.md` file containing ```mermaid fences into, via the Google Docs backend. No API/consumer-facing contract changes; this is purely about the visual result of an existing push path.

## Success Metrics
- A newly pushed mermaid diagram in Google Docs is inserted sized to the page's content width (proportionally scaled by the diagram's real aspect ratio, from `mmdc`'s rendered PNG dimensions) and horizontally centered — verified against a real doc push, not just a unit test of the request payload.
- Existing non-mermaid images and existing tests for image push (`test_gdocs_images.py`, `test_gdocs_mermaid.py`, `test_mermaid_appendix.py`) are unaffected/updated, not broken.

## Appetite
Small (1–2 days)

## Constraints
- No new *runtime service* dependency and no config surface — sizing is derived from data already available (mmdc's rendered PNG's own pixel dimensions; Google Docs' standard content-width constant). Adding **Pillow** as a new library dependency is accepted (see Alternatives Considered — the original assumption that Pillow was already a dependency was wrong; `pyproject.toml` has no Pillow, and `image_source.py:25-26` explicitly avoided it in favor of magic-byte sniffing for MIME detection). Reading pixel dimensions is narrow enough to instead hand-parse the PNG IHDR chunk (fixed-offset big-endian u32s at bytes 16-19/20-23) with `struct`, avoiding a new dependency entirely — Phase 3 planning decides between the two.
- Must not change behavior for user-supplied images with explicit width/height (existing `width_pt`/`height_pt` on `DocsImageNode`) — this only changes the *default* applied when no explicit size is given, and specifically only for mermaid-sourced images (mermaid nodes never carry a user-specified size today).

## Non-functional Requirements
- **Performance SLO**: not applicable (one-time computation at push time, no added network calls — reading PNG dimensions is a local, in-memory operation).
- **Scalability**: not applicable.
- **Security classification**: internal (no change to auth/data handling).
- **Data residency**: not applicable.

## Scope
### In Scope
- Google Docs: compute `width_pt`/`height_pt` for mermaid-rendered images from the PNG's actual pixel dimensions, scaled proportionally (single shared scale factor, not independently rounded per-axis) to fit a page-content-width constant of **468pt (6.5in Letter, 1in margins)**, dividing raw pixel dimensions by `RENDER_SCALE` (3) first and using 0.75pt/px (96 CSS px/inch) for the base conversion, rounded to a fixed precision (e.g. 2 decimal places) for determinism.
- Setting the enclosing paragraph's alignment to `CENTER` for mermaid image paragraphs specifically, via a new `updateParagraphStyle` request in `docs_request_builder.py`'s image-insert branch (~line 2807-2849, which today emits none) — mirroring the adjacent plain-paragraph styling branch, without affecting adjacent text paragraphs.
- The correct integration point for computing `width_pt`/`height_pt` is `image_source.py`'s `resolve_document_images()`/`_resolve_one()` (confirmed in research: this is the *only* mermaid-image code path — no separate fence-vs-explicit-markdown-image split exists) — it already has the rendered PNG bytes (`ResolvedImage.rendered_bytes`) before the node reaches `docs_request_builder.py`, which already honors `objectSize` from `width_pt`/`height_pt` with no builder change needed on that side.
- Reading actual PNG pixel dimensions from the rendered mermaid bytes to preserve aspect ratio: either add **Pillow** as a new dependency, or hand-parse the PNG IHDR chunk with `struct` (no new dependency) — Phase 3 decides.
- Updating/adding tests: fixing `tests/test_gdocs_mermaid.py`'s fake renderer (currently `_PNG_MAGIC + diagram.encode()`, not a structurally valid PNG — any real dimension-reading code will crash on it) to emit a genuinely valid minimal PNG, and adding new assertions for `width_pt`/`height_pt`/`objectSize`/paragraph alignment (none exist today).

### Out of Scope
- **Confluence entirely** — its mermaid→image rendering path is dead code (`MermaidParser.render_diagram()` stub); there is no live image to size or center. See Problem Statement's scope correction. A future project can tackle implementing that rendering path first.
- No new `docspan.yaml`/`markgate.yaml` configuration knob for the target width — the "fill page content width" default is hardcoded, per the answered question. A future request can add configurability if needed.
- No change to non-mermaid image handling (explicit-size images, pulled/recovered images) beyond what's needed to avoid regressing them.
- No change to `RENDER_SCALE` (the mmdc supersampling factor) — that governs sharpness/pixel density, not the logical insertion size this task controls; it must be *divided out* of the computation, not modified.
- No height cap / max-height constraint — width-only fit with unconstrained height, matching how comparable tools (GitHub, Confluence's own rendering) handle diagram embeds and Confluence's own existing (dead) `MermaidNodeConverter` precedent (width-only, no height param).
- No change to `alt` — mermaid's `alt` is a content-hash string used as part of the `(alt, width_pt, height_pt)` diff-identity key; this task must set `width_pt`/`height_pt` without touching `alt`.

## Rabbit Holes
- **Page content width isn't universal.** Google Docs' default margins (1in each side on Letter, giving ~6.5in/468pt content width) can be overridden by the doc's actual `documentStyle` margins, which docspan doesn't currently read. Using a fixed constant is a deliberate simplification (matches the "Recommended" answer); if a target doc has non-default margins, the diagram may not exactly fill the visible content width. Flag this as an accepted approximation, not a bug to chase.
- **Diffing/idempotency**: `docs_request_builder.py`'s node identity for images is keyed on `(alt, width_pt, height_pt)` (see `_node_key`/`_alignment_key`, ~line 289). Once mermaid nodes start carrying computed `width_pt`/`height_pt`, re-pushing an unchanged diagram must still resolve to "unchanged." Research confirmed this is achievable — the render cache (`mermaid_renderer.py`) is keyed on diagram text + `RENDER_SCALE` + mmdc version, so identical source always yields byte-identical cached PNG bytes, and Pillow/IHDR dimension reads are deterministic — but the *arithmetic* (float scale computation) must round to a fixed precision immediately after computing, and derive `height_pt` from `width_pt` via one shared scale factor rather than independently rounding both dimensions, or float drift across code versions could change the key and cause spurious re-inserts.
- **Existing pushed docs**: this only affects newly-inserted images going forward; it does not retroactively resize diagrams already in a doc (no requirement to touch `pull()`/reconciliation for existing content).
- **RENDER_SCALE-forgetting is the single highest-risk mistake**: omitting the ÷3 step before px→pt conversion makes `width_pt` ~3x too large (e.g. ~1404pt vs. the intended ~468pt) — `objectSize` has no page clamp, so this silently overflows margins by roughly 3 page-widths rather than erroring. Phase 6 verification must explicitly check this isn't happening (e.g. an inserted diagram roughly matching, not vastly exceeding, ~468pt).

## Alternatives Considered
- Adding a `docspan.yaml` config option for max diagram width — rejected for this pass (out of scope, per answered question); "fill page content width" was chosen as simpler and needing no new config surface.
- Fixing Confluence too — rejected after research revealed there's no live Confluence mermaid-image path to fix; doing so would require first implementing `MermaidParser.render_diagram()` (wiring mmdc + Confluence attachment upload), a materially larger scope than this project's 1–2 day appetite. User confirmed Google-Docs-only scope once this was surfaced.
- Using Pillow vs. hand-parsing the PNG IHDR chunk for reading pixel dimensions — both are viable (Pillow is standard/battle-tested but is a new dependency; IHDR parsing needs zero new dependencies and mermaid always renders PNG, so PNG-only parsing is sufficient). Deferred to Phase 3 planning to pick one.
- Using `mmdc`'s own `-w`/`-H` flags to render directly at a target size instead of computing a fit-after-the-fact — rejected: those flags set the Puppeteer viewport, not the final cropped-to-content-bounding-box PNG size, so they can't reliably replace a post-render dimension read.

## Feasibility Risks
- Reading PNG pixel dimensions needs either a new Pillow dependency or a hand-written IHDR parser; both are low-risk, well-understood approaches (no bespoke general-purpose image library needed).
- The exact conversion from CSS/rendered pixels to Google Docs points is now resolved by research: raw PNG px ÷ `RENDER_SCALE` (3) → × 0.75pt/px (96 CSS px/inch) → scale-to-fit 468pt width preserving aspect ratio via one shared factor.

## Observability Requirements
Not applicable at this complexity — standard push warnings (already emitted via the existing `ImageResolutionError` residue path) are sufficient for any sizing/rendering failures.

## Risk Control
Not applicable — low risk, easily reverted (a rendering/sizing change with no data migration or irreversible side effect; existing render/PNG cache is content-addressed and unaffected by this change's logic).

## Open Questions
All three original open questions were resolved by Phase 2 research:
- Page-content-width constant: **468pt (Letter, 1in margins)**, the more conservative choice vs. A4's ~451-455pt.
- Pixel-to-point conversion: raw PNG px ÷ `RENDER_SCALE` (3) → × 0.75pt/px (96 CSS px/inch) → scale-to-fit 468pt.
- Confluence ADF `width` capping question is moot — Confluence is now out of scope (see Problem Statement).

Remaining decision for Phase 3: Pillow vs. hand-parsed IHDR chunk for reading PNG pixel dimensions (see Alternatives Considered).
