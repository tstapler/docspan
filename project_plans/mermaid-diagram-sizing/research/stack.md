# Research: Stack (Libraries, Units, Existing Patterns)

## 1. Pillow dependency — NOT actually present (contradicts requirements.md)

`pyproject.toml` (`/Users/tstapler/.stapler-squad/repos/github.com/tstapler/docspan/pyproject.toml:31-52`)
lists no `Pillow`/`PIL` dependency anywhere (core or `dev`/`docs` extras), and there is no
`uv.lock` entry for it. Grepping the whole `src/` tree for `PIL`/`Pillow` turns up exactly one
hit, and it's a comment explaining Pillow was **deliberately avoided**:

`src/docspan/backends/google_docs/image_source.py:25-26`:
```python
# Magic-byte sniffing instead of `imghdr` (removed in Python 3.13) or a new
# Pillow dependency -- covers the formats insertInlineImage actually supports.
_MAGIC_BYTES: Dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    ...
}
```

**This is a real requirements gap, not a research nuance**: the requirements doc's Feasibility
Risks section states "Reading PNG pixel dimensions requires Pillow (already a dependency)" — that
premise is false as of this branch. Options for the planning phase to weigh:
- Add `Pillow` as a new core dependency (heavyweight — pulls in libjpeg/zlib bindings — for a
  feature that only needs PNG width/height).
- Parse the PNG header directly without Pillow: PNG's format guarantees the IHDR chunk is always
  the first chunk, at a fixed offset — width and height are 4-byte big-endian integers at bytes
  16-19 and 20-23 of the file (after the 8-byte PNG signature + 4-byte chunk length + 4-byte
  "IHDR" tag). This is ~10 lines of `struct.unpack` and matches the existing magic-byte-sniffing
  style in `image_source.py` (no new dependency, PNG-only is fine since mermaid always renders
  PNG via `mmdc`). Recommend this over adding Pillow, given the file's own stated policy of
  avoiding it and the feature's scope being PNG-only (mermaid output, never SVG/JPEG here).

If Pillow's `Image.open(io.BytesIO(data)).size` API is still wanted for some reason (e.g. future
non-PNG use), it does not require writing to disk — `Image.open` accepts any file-like object,
and `.size` returns `(width, height)` in pixels without decoding full pixel data (it reads only
the header). But given the dependency gap above, a raw IHDR parse is the lighter-weight fix
consistent with this codebase's existing conventions.

## 2. Google Docs objectSize units and page content width

Confirmed via `docs_request_builder.py:2807-2820` (existing code): `DocsImageNode.width_pt` /
`.height_pt` are already wired into `insertInlineImage`'s `objectSize`, using
`{"magnitude": ..., "unit": "PT"}` — this matches Google's documented API shape
(https://developers.google.com/workspace/docs/api/how-tos/images uses the identical
`objectSize: {height: {magnitude, unit: 'PT'}, width: {magnitude, unit: 'PT'}}` structure). The
fields are populated today only on the **pull** path
(`docs_structure_parser.py:554-563`, reading size back from a live doc) — never on **push**. That
is the actual gap this feature closes: `markdown_to_paragraph_parser.py` (where `DocsImageNode`
gets built on push, see the `(alt, width_pt, height_pt)` cache-key comment at
`docs_request_builder.py:282-289`) needs to compute and set these two fields for mermaid images
before they reach `docs_request_builder.py`.

Page content width: PT is 1/72 inch (standard typographic point, matches Docs' own units enum).
Google Docs' default Letter template uses 1in margins on 8.5x11in paper → 6.5in content width =
**468pt**. A4 (21cm x 29.7cm) with Docs' default ~2.54cm margins gives content width
21cm − 2×2.54cm = 15.92cm = 6.268in ≈ **451pt** (close to, not exactly, the requirements doc's
"~455pt" estimate — the discrepancy is presumably a different assumed default margin, e.g. 1.91cm
side margins some A4 templates use, which would give ~455pt; both are in the same ballpark).

**Recommendation**: standardize on **468pt (Letter)** as the single constant. Rationale:
- It's the more common case for this tool's English-locale users and matches the number already
  cited in the requirements doc's baseline discussion.
- Since A4 content width is *smaller* than 468pt, sizing to 468pt on an A4 doc means the image
  would render right at (or fractionally over) the A4 content edge — not catastrophic (Docs
  doesn't hard-clip inline images, it just lets them slightly overflow the margin guide visually)
  but not ideal. If exact-fit correctness for A4 authors matters more than simplicity, use the
  smaller ~451-455pt constant instead so the diagram always fits within either page size's
  margins with margin to spare. Given the requirements doc frames this as "approximately page
  content width" (Confluence side) and doesn't mention a `docspan.yaml` locale/page-size knob is
  in scope, a single hardcoded constant is expected either way — recommend 468pt for simplicity
  unless A4 users are a known priority for this project (not indicated in requirements.md).

## 3. PNG pixel → Docs PT conversion, accounting for RENDER_SCALE=3

`mermaid_renderer.py:27-33` is explicit that `-s` (`RENDER_SCALE = 3`) is **pure supersampling**:
"same logical layout, more pixels per unit" — it does not change mmdc's logical/CSS-pixel layout
size, only how many raster pixels represent that same layout (for sharper rendering / manual
zoom headroom). This means: **raw PNG pixel dimensions must be divided by `RENDER_SCALE` before
any px→pt conversion**, or the computed size will be exactly 3x too large. E.g. a diagram whose
natural (unscaled) mmdc output would be 800x400 CSS-px instead comes out of the renderer as a
2400x1200px PNG; dividing by `RENDER_SCALE=3` first recovers the intended 800x400 logical size.

For the logical-px → pt conversion itself: mmdc renders via a headless Chromium/Puppeteer page,
which uses the standard CSS reference pixel — **96 CSS px = 1 inch** (CSS Values and Units spec,
w3.org; this is the universal browser rendering convention, not something Google-specific). Since
1 inch = 72pt, that gives **1 CSS px = 72/96 pt = 0.75pt**, confirming the "100 CSS px ≈ 1 inch"
figure mentioned in the requirements doc's Open Questions is off by a small margin (the correct,
spec-backed ratio is 96px/in, not 100px/in) — use 0.75pt/px, not the ~0.72pt/px that 100px/in
would imply. Full formula:

```
logical_px = raw_png_px / RENDER_SCALE   # undo mmdc's -s supersampling
pt = logical_px * 0.75                   # 96 CSS px/in ÷ 72pt/in
```

Then scale proportionally to fit the content-width constant (468pt) while preserving aspect
ratio: `scale = min(1.0, CONTENT_WIDTH_PT / logical_width_pt)` (only shrink to fit; the
requirements doc's success metric is "sized to page content width," implying width-driven
scaling up to that cap — whether to also *upscale* small diagrams to fill the full 468pt is an
open product decision the requirements doc doesn't explicitly resolve, since it only says "sized
to page content width" without qualifying "at most").

## 4. Confluence ADF mediaSingle/image `width` semantics

The in-scope code is `MermaidNodeConverter.convert_typed`'s `rendered_url` branch —
`src/docspan/backends/confluence/adf/converters.py:995-1006`:
```python
layout = node.attrs.get("layout", "center")
width = node.attrs.get("width", 800)  # Default to 800px for diagrams
...
return self.builder.media_single(image_node, layout=layout, width=width, width_type="pixel")
```
This converter is live — registered at `converters.py:195`
(`registry.register("mermaid", MermaidNodeConverter(builder))`). The width defaults to a bare
`800` (int, pixels) with no computation from the actual image's dimensions, no capping logic in
`AdfBuilder.media_single` (`src/docspan/backends/confluence/adf/nodes.py:1733-1763` just passes
`width`/`width_type` straight into the ADF `attrs` dict — no clamping, no aspect-ratio math). This
confirms the requirements doc's framing exactly: raising the `800` default (line 998) is a
one-line, mechanical change; no other capping/aspect-ratio logic exists in this codebase to
account for.

Two things worth flagging for planning:
- **`node.attrs.get("width", 800)` is *never actually populated with a non-default value* anywhere
  in this codebase** — I found no call site that sets `node.attrs["width"]` on a `MermaidNode`
  before this converter runs (only `attrs["id"]`, set by `MermaidParser.parse` in
  `src/docspan/backends/confluence/markdown/extensions/mermaid.py:37`, and presumably
  `rendered_url`/`attachment_id` set by whatever upstream code performs the actual mermaid→PNG
  render-and-upload for Confluence — that code wasn't located in this pass; it's outside
  `adf/converters.py` and `markdown/extensions/mermaid.py` — the latter's own
  `render_diagram` at line 43-64 is an unimplemented placeholder/TODO stub, so the real render
  step lives elsewhere and should be located during planning/implementation to see if PNG pixel
  dimensions are available at the point `node.attrs["width"]` could be set (enabling
  aspect-ratio-aware sizing analogous to the Google Docs side) versus the requirements' scoped-in
  approach of just raising the static default.
- **Confluence ADF's `width`/`width_type: "pixel"` attribute is not authoritative in the sense of
  guaranteed exact pixel rendering** — Atlassian's editor auto-fits `mediaSingle` width against
  the actual rendered page/column width at *display* time (an oversized pixel width is visually
  capped by the container, matching typical prose-editor behavior), but nothing in this repo's
  own code encodes that cap — it's an Atlassian-editor-side behavior, not something docspan
  computes or tests. No test in `tests/test_confluence_mermaid_push_pipeline.py` exercises the
  `width` attribute at all (that test only asserts fenced mermaid degrades to a plain `codeBlock`
  — it doesn't reach the `MermaidNodeConverter`/`rendered_url` path at all, since raw
  ```mermaid fences currently produce a `codeBlock`, not a `MermaidNode` with `rendered_url` — see
  that test's own docstring, "expected the mermaid fence to degrade to a plain codeBlock ... if
  this changes, MermaidNodeConverter has started emitting"). This means the `width=800` code path
  may not be reachable from the current fence-based push pipeline at all today — worth confirming
  during planning whether raising the default actually has any user-visible effect, or whether
  the real Confluence mermaid-image pipeline (wherever it sets `rendered_url`) is a separate,
  as-yet-unlocated code path that also needs the same treatment.

## Key file/line references
- `src/docspan/backends/google_docs/image_source.py:21-33` — no-Pillow policy, magic-byte sniffing
- `src/docspan/backends/google_docs/docs_structure_parser.py:395-418` — `DocsImageNode.width_pt`/`height_pt` fields
- `src/docspan/backends/google_docs/docs_structure_parser.py:554-563` — pull-side population of width_pt/height_pt (pattern to mirror on push)
- `src/docspan/backends/google_docs/docs_request_builder.py:2807-2820` — push-side `objectSize` emission (already wired, just needs non-None inputs)
- `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py:331` — push-side `DocsImageNode` construction (where width_pt/height_pt need to be set)
- `src/docspan/backends/google_docs/mermaid_renderer.py:25-33` — RENDER_SCALE=3 is pure supersampling, no logical-size change
- `src/docspan/backends/confluence/adf/converters.py:911-1006` — `MermaidNodeConverter`, the live `width=800` default (line 998)
- `src/docspan/backends/confluence/adf/converters.py:195` — converter registration confirming it's live, not dead code
- `src/docspan/backends/confluence/adf/visitors.py:383` — `MermaidNodeVisitor`, the actually-dead-code path requirements.md excludes
- `src/docspan/backends/confluence/adf/nodes.py:1733-1763` — `AdfBuilder.media_single`, no width-capping logic
- `src/docspan/backends/confluence/markdown/extensions/mermaid.py:43-64` — unimplemented `render_diagram` placeholder (real Confluence render path is elsewhere, not yet located)
- `tests/test_confluence_mermaid_push_pipeline.py` — current fence push degrades to `codeBlock`, doesn't exercise `MermaidNodeConverter`'s image/width path
