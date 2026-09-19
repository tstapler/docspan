# Architecture Research: mermaid-diagram-sizing

## 1. Google Docs data flow for a mermaid image

There is only **one** mermaid-image code path today, not two.

- `markdown_to_paragraph_parser.py`'s `CodeTokenConverter.convert()` (line 521) detects a
  ` ```mermaid ` fence and calls `_mermaid_image_node()` (line 326), which builds a
  `DocsImageNode(alt=<hash>, mermaid_source=<raw diagram text>)` — `src`, `width_pt`, `height_pt`
  all stay at their dataclass defaults (`""`, `None`, `None`).
- The only other `DocsImageNode(...)` construction site for markdown push is
  `ParagraphTokenConverter.convert()` (line 485), for a plain `![alt](src)` image. It never sets
  `mermaid_source`, and there is no markdown syntax anywhere in this parser for an
  author-specified width/height on push — `width_pt`/`height_pt` are only ever populated on the
  **pull** side, in `docs_structure_parser.py:554-562`, from a live document's `objectSize`.
- `render_mermaid_png()` (`mermaid_renderer.py`) has exactly one call site:
  `image_source.py`'s `_render_mermaid()` → `_resolve_one()`, invoked from
  `resolve_document_images()` (called once, from `backend.py:259`, in the push pre-pass **before**
  the pass-1/pass-2 diff and before `docs_request_builder.py` ever sees the node). `MermaidSource`
  is constructed in exactly one place — `resolve_document_images()` line 275, gated on
  `node.mermaid_source` — so the "separate path for `![]() `pointing at a `MermaidSource`"
  hypothesized in the research prompt does not exist in this codebase: `build_source()` (used for
  every non-fence image) only ever returns `LocalPathSource`/`UrlSource`, never `MermaidSource`.

**This means the PNG's raw bytes are available exactly where they're needed.** The rendered PNG
bytes are already carried on `ResolvedImage.rendered_bytes` at line 187-192, specifically so the
mermaid-cache sidecar can record them. `resolve_document_images()` is the single integration point
that can also read the PNG's pixel dimensions (Pillow) and set `width_pt`/`height_pt` on the node
it returns (line 304-307 currently only does `replace(node, src=result.uri)`), before that node is
spliced back into `target_nodes` in `backend.py` and reaches `docs_request_builder.py`. No new
plumbing/threading is needed — this is a same-function edit gated on
`node.mermaid_source is not None and node.width_pt is None` (never touching a node that already
carries an explicit size, satisfying the "don't change user-supplied sizing" constraint — see §4).

`docs_request_builder.py`'s image-insert builder (~2807-2820) already reads `node.width_pt`/
`node.height_pt` and, if both are truthy, sets `insertInlineImage`'s `objectSize`. That branch
needs zero changes — it was already built to support this, just never fed live data for a mermaid
node.

## 2. Paragraph alignment (CENTER)

Confirmed: **no `updateParagraphStyle` request accompanies an image insert today.** The image
branch in `_insert_requests`-equivalent (docs_request_builder.py ~2807-2849) only emits
`insertInlineImage` + a boundary `insertText`. Contrast with the plain-paragraph branch
immediately below it (2864-2882), which computes a `paragraph_range` from `insert_at_index`/
`before_newline`/text length and always appends an `updateParagraphStyle` (`namedStyleType`, plus
blockquote fields) for that range.

**Integration point:** inside the `isinstance(node, DocsImageNode)` branch, after computing
`insert_at_index`/the newline placement (the same `is_bare_image`/`before_newline` cases already
handled), compute the image paragraph's range the same way the text branch does — 1 UTF-16 unit
for the image element + 1 for the newline — and append one more `updateParagraphStyle` request
with `paragraphStyle: {"alignment": "CENTER"}`, `fields: "alignment"`, gated on
`node.mermaid_source is not None` (bookkept via a node flag/field, since by push time the raw
mermaid diagram text may or may not still be worth keeping — see Task Breakdown note below) so a
plain user image is never centered.

**Idempotency/diff impact:** `_node_key()` (line 289) already keys image identity on
`("__image__", node.alt, node.width_pt, node.height_pt)` — deliberately excluding `src` because
Drive URIs churn. Once `width_pt`/`height_pt` are populated for mermaid nodes, they become part of
that identity; since mermaid rendering is deterministic for unchanged diagram source, an unchanged
diagram keeps producing the same pixel dimensions → same `width_pt`/`height_pt` → same key → still
detected as unchanged, so no spurious delete/reinsert loop. `_alignment_key()` (line 2239) is
coarser (`("__image__", node.src)`) and is unaffected either way — it's used only for pass-2
pairing, not identity, and alignment isn't part of it in any node type today (headings/tables
don't carry it either). Adding an `updateParagraphStyle` purely inside the *insert* path (not
`_alignment_key`) means no regression risk there: pass 1 either inserts a mermaid image with its
paragraph pre-centered, or (unchanged diagram) skips the whole node because `_node_key` says
nothing changed — the centering was already applied on the prior push and Google Docs persists
paragraph style across untouched paragraphs.

## 3. Confluence: MermaidNodeConverter and pixel dimensions

**Critical finding, changes the feature's real-world impact on this side:** Confluence's mermaid
rendering pipeline is dead code today. `render_mermaid_diagrams` (`config/models.py:78-103`) is
documented in its own docstring as "currently a no-op — no code path reads this flag." The actual
push pipeline (`markdown/parser.py:672-684` `_parse_mermaid()`) builds a bare `MermaidNode(code=...)`
with no `rendered_url`/`attachment_id`/`layout`/`width` ever set on `.attrs`. `MermaidParser.parse()`
(`markdown/extensions/mermaid.py`) only sets `attrs["id"]`; its sibling `render_diagram()` is an
explicit `# TODO: Implement actual rendering logic` stub, confirmed unreachable from the real
parser by `docs/backends/confluence.md`'s own limitations section ("Mermaid diagrams are not
rendered... dead code, unreachable from the real parse pipeline").

Consequence: `MermaidNodeConverter.convert_typed()`'s `rendered_url` branch (line 959-1006) — the
one with `layout="center"` + `width=800` the requirements doc describes — is **never exercised by
a real push today**. Every real push falls through to the final fallback, `code_block(node.code,
"mermaid")` (line 1022), rendering the fence as a plain visible-source code block, not an image.
The 800px default is real code, but it is currently dead in production; only unit tests that call
`convert_typed()` directly with a hand-built `rendered_url`/`attrs` dict exercise it.

Given that, pixel dimensions are moot for now: there is no live attachment-upload step at
conversion time to have measured a PNG against, because there is no live PNG. `AdfBuilder.image`/
`media_single` (`adf/nodes.py` ~261-360/~950-1000) take `width`/`width_type="pixel"` as opaque
pass-through fields; they have no dependency on where the number comes from, so raising the
literal `800` → a page-content-width-equivalent constant (per the requirements' "Out of scope: no
docspan.yaml config knob") is a one-line, purely defensive change: swap
`node.attrs.get("width", 800)` for `node.attrs.get("width", CONFLUENCE_MERMAID_DEFAULT_WIDTH_PX)`
with that constant sized to Confluence's page content width in pixels (matching the same
"~468pt/6.5in-equivalent" sizing philosophy as the Google Docs side, converted to px). It cannot
be aspect-ratio-aware today because nothing here has ever seen a rendered PNG's dimensions — that
constrains this half of the feature to exactly the fixed-width bump the requirements already scope
it to.

## 4. Disposition

**Extend as-is — no SOLID/architecture violation blocks this feature.** The hypothesized "two
mermaid-path split" does not exist on the Google Docs side (§1); the real, only path already
carries the rendered PNG bytes through `resolve_document_images()`, which is the single natural
seam for both computing `width_pt`/`height_pt` (Pillow, before the diff sees the node) and gating
that computation to mermaid-only nodes with `None` defaults (satisfying the "don't touch explicit
sizing" constraint for free, since no push-side code path currently sets an explicit size at all).
`docs_request_builder.py` already supports `objectSize` from `width_pt`/`height_pt`; the only new
request-builder work is adding one `updateParagraphStyle(alignment=CENTER)` beside the existing
image-insert branch, mirroring the pattern the adjacent plain-paragraph branch already uses. The
Confluence half is a one-constant change in an already-isolated `convert_typed()` branch — no
refactor needed — but it is currently inert in production because the surrounding render pipeline
is dead code (§3); that's worth flagging to the requester as a scoping/expectations note, not a
blocker to implementing exactly what the requirements ask for.

## Notes for planning

- Google Docs needs a new runtime dependency: **Pillow** is not currently in `pyproject.toml`/
  `requirements.txt` and must be added.
- No existing "page content width" constant exists in `google_docs/*.py`; one needs to be
  introduced (~468pt / 6.5in, per requirements) alongside the Confluence-side px-equivalent
  constant — two separate constants in two backends, not a shared one, since nothing currently
  couples the two backends' sizing.
- `_mermaid_image_node()`'s docstring notes identity is keyed on `(alt, width_pt, height_pt)` —
  worth re-reading when implementing, since it's the exact mechanism that makes this feature safe
  to add without a diff/idempotency regression (§2).
