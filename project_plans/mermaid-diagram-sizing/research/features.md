# Research: similar patterns, determinism, and edge cases (mermaid-diagram-sizing)

## 1. Google Docs image identity/diffing (determinism requirement)

`src/docspan/backends/google_docs/docs_request_builder.py`:
- `_node_key` (~line 286-289) keys an image's identity for diffing on
  `("__image__", node.alt, node.width_pt, node.height_pt)` — **not** `src`
  (explicitly excluded, per the docstring at line 276-284, because `src` is a
  volatile Drive/`contentUri` link that can change even when the image
  hasn't). This means once mermaid nodes carry computed `width_pt`/
  `height_pt`, those two numbers become part of the identity key alongside
  `alt`.
- `alt` for a mermaid image is **already** a stable hash of the diagram
  *source text* (`markdown_to_paragraph_parser.py:326-344`,
  `_mermaid_image_node`): `f"mermaid diagram {sha256(diagram)[:12]}"`. It is
  not derived from the rendered bytes.
- Insertion (`docs_request_builder.py:2807-2848`) builds `objectSize` from
  `node.width_pt`/`node.height_pt` only `if node.width_pt and node.height_pt`
  — today these are always `None` for a freshly-parsed mermaid node (no
  sizing logic exists yet), so no `objectSize` is sent and Docs uses its tiny
  default. This confirms the problem statement and shows the exact
  insertion point to extend.
- **Determinism requirement, precisely stated**: for the diff to treat an
  unchanged diagram as unchanged across repeated pushes, `(alt, width_pt,
  height_pt)` must be byte-for-byte identical every time the *same* markdown
  source is pushed. Since `alt` already only depends on diagram text (not
  render output), the new risk is entirely in `width_pt`/`height_pt`: they
  must be a deterministic function of the diagram text as long as the
  renderer/cache produce the same PNG. See §2 for whether that holds.
- `resolve_document_images` (`image_source.py:307`) is the exact point where
  `src=result.uri` is currently spliced onto the node via
  `dataclasses.replace(node, src=result.uri)`. This is the natural place to
  also set `width_pt=`/`height_pt=` once PNG pixel dimensions are known,
  since `ResolvedImage` (line 73-83) already threads `rendered_bytes` through
  for mermaid renders (used today only for the sidecar cache, at
  `image_source.py:298-301`).

## 2. Mermaid render+cache pipeline determinism

`src/docspan/backends/google_docs/mermaid_renderer.py` (full file read):
- `_cache_key` (line 123-129) hashes `diagram text + RENDER_SCALE (3) +
  _mmdc_version()`. `RENDER_SCALE` is a supersampling multiplier applied via
  `mmdc -s 3` (line 33, 156) — same logical CSS layout, 3x more pixels.
- `render_mermaid_png` (line 160-198) returns cached bytes on a cache-key hit
  (line 176-181) and only re-renders on a miss, writing atomically
  (`os.replace`, line 193) so a race never serves partial bytes.
- **Conclusion: yes, deterministic** — same diagram text + same mmdc version
  + same `RENDER_SCALE` ⇒ same cache key ⇒ same cached PNG bytes ⇒ same
  pixel dimensions ⇒ same computed `width_pt`/`height_pt`, satisfying the
  identity-key stability requirement in §1. Two caveats worth naming
  explicitly for the plan:
  - An **mmdc version upgrade** (already busts the cache key today) can
    legitimately produce different pixel dimensions for the same source —
    this would change `width_pt`/`height_pt` and thus the identity key,
    causing one spurious re-insert per changed diagram after an upgrade.
    That's consistent with today's behavior for any other rendering change
    and is not a new bug the sizing feature introduces; it's the same
    "re-render → different bytes → different image" case that already
    exists, just now also visible in size rather than only in pixel content.
  - Sizing computation must **not** be part of the cache key itself — it
    should be derived at resolve/build time by reading whatever PNG bytes
    the cache returns, otherwise every render-scale or mmdc bump becomes a
    two-part invalidation.

## 3. No Pillow dependency actually present — requirements doc is wrong here

The requirements state "reading actual PNG dimensions (Pillow already a
dependency)". **This is false** — verified two ways:
- `pyproject.toml`'s `dependencies` list (lines 31-52) has no Pillow/PIL
  entry, in either core or `dev`/`docs` optional groups.
- `python3 -c "import PIL"` in the repo's environment raises
  `ModuleNotFoundError: No module named 'PIL'`.
- Existing code in `image_source.py` explicitly rejected Pillow before:
  ```
  # Magic-byte sniffing instead of `imghdr` (removed in Python 3.13) or a new
  # Pillow dependency -- covers the formats insertInlineImage actually supports.
  ```
  (`image_source.py:25-26`), and does its own magic-byte MIME sniffing
  (`_MAGIC_BYTES` dict, lines 27-33) rather than shelling out to an image
  library.

**Implication for planning**: either (a) add Pillow as a new real dependency
(cost: a native-code dependency for a single `Image.open(...).size` call), or
(b) parse the PNG's `IHDR` chunk directly — width/height live as two
big-endian uint32s at a fixed byte offset (bytes 16-24 of any valid PNG,
right after the 8-byte signature + 4-byte length + 4-byte "IHDR" tag), which
is ~5 lines of `struct.unpack` with no new dependency, consistent with the
existing magic-byte-sniffing style in this same file. Given the file's
existing anti-Pillow precedent and mermaid-cli always producing PNG (never
another format `mmdc` can emit here), (b) is the pattern-consistent choice
and should be surfaced to the planning phase as a correction to the
requirements' "Pillow already a dependency" assumption.

## 4. Extreme aspect ratios — no existing cap logic to reuse in this repo

Confluence's `MermaidNodeConverter` (`src/docspan/backends/confluence/adf/
converters.py:996-1006`) sets `layout = node.attrs.get("layout", "center")`
and `width = node.attrs.get("width", 800)` (pixel width only) and wraps via
`builder.media_single(image_node, layout=layout, width=width,
width_type="pixel")` — **width-only**, no height parameter, no aspect-ratio
or max-height cap. Confluence's media-single renderer preserves the image's
own aspect ratio and derives height from width client-side, so there's
nothing in this codebase to model a height cap after; the pattern to copy is
"pick a width, let height float."
- Industry precedent for width-fit-only, unconstrained height: GitHub's
  Markdown image rendering constrains rendered width to the content column
  and lets height scale proportionally with no max-height clamp; Confluence
  itself (media-single, "center") behaves the same way — both treat vertical
  space as free (readers scroll) and only page/column width as the fixed
  budget. This supports the plan defaulting to **width-only fit, no height
  cap** for both backends, consistent with `MermaidNodeConverter`'s existing
  width-only knob and out-of-scope note in the requirements ("no new
  docspan.yaml config knob").
- A very wide diagram (many sequence-diagram participants) scaled to fit
  ~468pt/6.5in width becomes proportionally very short — visually fine, no
  special-case needed (thin banner images are common and acceptable).
- A very tall diagram (long vertical flowchart) scaled to fit width alone
  can become extremely tall (many pages) — since no comparable tool in this
  codebase or referenced externally caps height, and the requirements'
  "Feasibility Risks" section explicitly poses this as an open question
  without mandating a fix, the recommendation is to **not** invent a new cap
  now (would need its own config knob to override, which is explicitly out
  of scope) and instead document the tall-diagram case as an accepted
  approximation, matching the "Rabbit Holes" item about non-universal
  page/content width already being accepted as an approximation.

## 5. Existing test fixtures/patterns to keep passing or extend

- `tests/test_gdocs_mermaid.py` (309 lines): `_fake_renderer` (line 38-39)
  returns `_PNG_MAGIC + diagram.encode("utf-8")` — **not a structurally
  valid PNG** (no real IHDR chunk, just the 8-byte PNG magic number followed
  by raw diagram text). Any sizing logic that parses the IHDR chunk (or
  calls Pillow) **will crash or misparse** on this fixture as-is. This is
  the most concrete "test updates" item in scope: `_fake_renderer` needs to
  produce a real minimal PNG (valid IHDR with real width/height) once the
  push path starts reading pixel dimensions from rendered bytes, or sizing
  must be injected/stubbed at a different seam so the fake bytes are never
  parsed as a real image in these unit tests.
- No existing test in `test_gdocs_mermaid.py`, `test_mermaid_appendix.py`,
  or `test_confluence_mermaid_push_pipeline.py` currently asserts on
  `width_pt`/`height_pt`, `objectSize`, `CENTER` alignment, or pixel/point
  sizing at all (`grep` for those terms returned nothing) — this is new
  test surface, not a modification of an existing assertion, aside from the
  `_fake_renderer` fixture fix above.
- `test_resolve_document_images_uses_mermaid_source_over_src` and
  `test_resolve_images_renders_mermaid_source_via_injected_renderer`
  (lines 79-97) are the two tests most directly exercising the
  `resolve_document_images`/`resolve_images` path where size computation
  would be added (§1) — both currently assert only on `uri`/`temp_drive_file_id`,
  so they're extension points, not blockers.
- `test_mermaid_render_failure_is_a_warning_not_a_crash` (line 120-132)
  confirms the existing failure contract: a `MermaidRenderError` from the
  renderer produces `out == [None]` and a warning string containing
  "mermaid render failed", never a crash and never a placeholder image
  node. New sizing logic must sit strictly after a successful render
  (i.e., only run when `result is not None` in `resolve_document_images`,
  mirroring the existing `if node.mermaid_source and result is not None and
  result.rendered_bytes is not None` guard at `image_source.py:298`) — on
  the failure path there is no `ResolvedImage`/`rendered_bytes` to size at
  all, so there's no separate crash risk to add tests for beyond keeping
  this existing guard's shape.

## Summary of unstated needs for the plan

1. Compute `width_pt`/`height_pt` at the exact point `image_source.py:307`
   already does `replace(node, src=result.uri)`, using
   `result.rendered_bytes`' PNG pixel dimensions — divide out `RENDER_SCALE`
   (3x supersampling) before converting to points, then scale to fit
   ~468pt/6.5in width, preserving aspect ratio, no height cap.
2. Read PNG dimensions via direct `IHDR`-chunk `struct.unpack`, not Pillow —
   the requirements' claim that Pillow is already a dependency is incorrect
   and contradicts this file's own existing anti-Pillow precedent.
3. Update `_fake_renderer` in `tests/test_gdocs_mermaid.py` to emit a
   structurally valid minimal PNG so new sizing logic has real dimensions to
   parse instead of crashing on today's `_PNG_MAGIC + diagram_bytes` stub.
4. No changes needed to the failure-path contract (`MermaidRenderError` →
   warning, no node) — sizing logic naturally can't run there since there's
   no `ResolvedImage` to read bytes from.
5. Confluence's `MermaidNodeConverter` width bump (800px → higher) is a
   one-line default change with no other logic to touch — it already only
   provides `layout="center"` + a pixel `width`, with no height parameter to
   reconsider.
