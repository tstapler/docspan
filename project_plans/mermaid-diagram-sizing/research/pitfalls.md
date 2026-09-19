# Research: Pitfalls — auto-sizing mermaid diagrams to document width

## 1. RENDER_SCALE=3 supersampling — MUST-catch bug

`RENDER_SCALE = 3` (`src/docspan/backends/google_docs/mermaid_renderer.py:33`) is passed to
`mmdc` as `-s 3` (line 156), so the PNG mermaid produces is rendered at **3x** the diagram's
"natural" CSS pixel size. Pillow's `Image.open(...).size` reports the *actual* PNG pixel
dimensions — i.e. the already-3x-scaled values.

If the new sizing code reads `img.width`/`img.height` and converts straight to points
(`px * 72/96` or similar) without first dividing by `RENDER_SCALE`, every computed
`width_pt`/`height_pt` comes out **3x too large**.

Quantified impact: target content width is ~468pt (6.5in). A diagram meant to fill that width
would instead compute to **~1404pt (~19.5in)** wide. Google Docs' `insertInlineImage`
`objectSize` does not clamp to the page — per Google's own API reference, if both width and
height are given the image is scaled to **fit within** those dimensions while preserving aspect
ratio, so it is inserted at (close to) the requested size regardless of the page's actual
content width. A 1404pt-wide inline image on a ~468pt-wide page will overflow the right margin
by roughly 3 page-widths, likely forcing Docs to either render it clipped/overflowing or push
surrounding content, and at minimum makes the diagram look absurdly oversized rather than
"too small" (the bug this feature exists to fix). **This must be explicitly checked in review**:
confirm the pixel→point conversion divides by `RENDER_SCALE` before (or as part of) the
px→pt conversion, and add a unit test asserting `width_pt` for a diagram rendered with the
current `RENDER_SCALE` is independent of `RENDER_SCALE`'s value (i.e. bumping `RENDER_SCALE` in
a test must not change the resulting `width_pt`).

## 2. Determinism of Pillow's `.size` and of the float math around it

- **Pillow's PNG header parsing is deterministic.** `Image.open().size` for PNG comes from
  parsing the fixed-format `IHDR` chunk (width/height as big-endian 4-byte integers at fixed
  offsets, per the PNG spec) — there is no hashing, iteration-order, or thread-scheduling
  dependency in that path. For identical PNG bytes, `.size` is byte-for-byte identical on every
  call, every run, every platform. This is not the risk.
- **The risk is downstream float arithmetic**, specifically computing something like
  `width_pt = native_width_px / RENDER_SCALE * 72 / 96` (or any scale-factor multiply/divide
  chain) and feeding the raw float into `docs_request_builder.py`'s identity key
  (`("__image__", node.alt, node.width_pt, node.height_pt)`, line 289). IEEE-754 double
  arithmetic is deterministic for a *fixed* sequence of operations in a *fixed* order on typical
  hardware, so two runs of the identical Python code on the same CPU architecture will produce
  the same float bit-pattern. The realistic nondeterminism sources are:
  - **Refactoring changes operation order** (e.g. `w * scale / dpi` vs. `w / dpi * scale`) between
    versions of the sizing code — same logical formula, different float result, silently busting
    identity for every previously-pushed diagram.
  - **Different code paths compute the "same" value differently** — e.g. width computed one way
    at push time and re-derived a different way when diffing against a pulled/round-tripped doc.
  - Cross-architecture reproducibility (x86 vs. ARM) is a much smaller practical risk here since
    both operands are exact integers/small rationals, but is not guaranteed by the language spec.
- **Recommendation:** round `width_pt`/`height_pt` to a fixed, small number of decimal places
  (e.g. `round(value, 2)` for 0.01pt, well under any visually-perceptible difference) immediately
  after computing them, in one place, before they ever reach `DocsImageNode` or the identity-key
  comparison. Rounding collapses any last-bit float noise into a stable canonical value and keeps
  the identity key stable across repeat pushes — this directly protects the idempotency rabbit
  hole called out in requirements.md.

## 3. Aspect ratio: Docs API behavior and independent rounding

Per Google's own `InsertInlineImageRequest`/`objectSize` reference: if both width and height are
specified, the image is **scaled to fit within** those dimensions while **preserving its native
aspect ratio** — the API does not stretch/distort to force an exact width×height. So a mismatched
`width_pt`/`height_pt` pair does not visibly distort the diagram; instead the API will silently
render the diagram *smaller* than the requested box on whichever axis is "too generous," fitting
it in on the constraining axis. Two consequences worth designing against:

1. **Silent size mismatch, not a crash or visible distortion.** If `width_pt` and `height_pt` are
   rounded independently (e.g. each to the nearest 0.01pt) from a native aspect ratio that isn't
   a clean rational, the two values can encode a slightly different ratio than the source PNG.
   The doc won't look "stretched," but the actually-rendered image may not fill the intended
   ~468pt width — it can come in a hair narrower than expected on repeat renders, and because the
   result isn't visually broken, this class of bug is easy to miss in manual review.
2. **It can mask other bugs.** Because Docs silently *shrinks to fit* rather than erroring on an
   aspect mismatch, a computation bug that produces a wrong ratio won't throw a distortion red
   flag — it'll just look "slightly off," making it harder to catch than an outright crash.

**Recommendation:** derive `height_pt` from `width_pt` using a *single* scale factor —
`scale = target_width_pt / (native_width_px / RENDER_SCALE)`; `height_pt = round(native_height_px
/ RENDER_SCALE * scale, 2)` — rather than independently computing and rounding both dimensions
from separate px→pt conversions. This guarantees `width_pt/height_pt` exactly reproduces
`native_width_px/native_height_px` (up to the single rounding step), eliminating the
independent-rounding drift entirely.

## 4. Confluence: default width vs. user override

`MermaidNodeConverter.convert_typed` (`src/docspan/backends/confluence/adf/converters.py:997-1005`)
reads `width = node.attrs.get("width", 800)` — i.e. `800` is purely a **fallback default** used
only when `node.attrs` has no `"width"` key. Traced where `attrs["width"]` could be populated for
a mermaid node before this point:

- The markdown parser constructs mermaid nodes bare — `MermaidNode(code=code)` in both
  `src/docspan/backends/confluence/markdown/parser.py:684` and
  `src/docspan/backends/confluence/markdown/extensions/mermaid.py:36` — never setting `width` in
  `attrs`. There is currently no markdown fence syntax (no `mermaid {width=...}` or similar) that
  lets a user set a per-diagram width.
- The only other place `mermaid_node.attrs["rendered_url"]` (and sibling keys) get read is
  `src/docspan/backends/confluence/adf/visitors.py:402-491` — the dead-code visitor path the
  requirements explicitly say not to touch — and it does not set `attrs["width"]` either; it only
  *reads* `rendered_url`/`embed_html`/`live_link`.

**Conclusion:** there is currently no code path that lets a mermaid node carry a real user-set
width override; every mermaid diagram today falls through to the `800` default. Raising the
default (e.g. to a fresh constant) is therefore safe and changes only the fallback value — it
cannot regress a user override because none exists yet. If a width-override mechanism is added
later, `node.attrs.get("width", <new_default>)` already does the right thing (explicit attrs value
wins), so no additional guard is needed for this task's scope.

## 5. Testing pitfall: don't invoke real `mmdc`/Puppeteer, and fake PNGs aren't decodable

- `tests/test_gdocs_mermaid.py` explicitly documents (module docstring, lines 1-6) that diagrams
  are "rendered to a PNG at resolve time via an injected renderer (never a real mermaid-cli
  subprocess in these tests)." The renderer is injected as a plain `Callable[[str], bytes]`
  (`Renderer` alias in `image_source.py`), and existing tests use `_fake_renderer` (line ~40),
  which returns `_PNG_MAGIC + diagram.encode("utf-8")` — bytes that satisfy the PNG **magic-byte**
  sniff (`image_source.py`'s `_MAGIC_BYTES` dict) but are **not a structurally valid PNG** (no
  real `IHDR` chunk, no valid width/height). `mermaid_renderer.py` tests
  (`test_mmdc_command_*`, `test_render_mermaid_png_*`) similarly monkeypatch `shutil.which` and
  the internal `_mmdc_version`/rendering call rather than shelling out to a real `mmdc` binary —
  confirming CI/reviewer environments are not assumed to have `mmdc`/Puppeteer installed.
- **Pitfall for this task specifically:** as soon as sizing code calls `Image.open(png_bytes).size`
  (or similar) on the resolved image bytes, `_fake_renderer`'s output will break that call — Pillow
  will raise (`UnidentifiedImageError` / truncated-file error) trying to parse magic-bytes-only
  "PNG" data that has no real header. Any new or existing test that exercises the sizing path with
  the current `_fake_renderer` will fail not because the sizing logic is wrong, but because the
  fixture PNG isn't real.
  - **Do not** fix this by writing a new test that shells out to real `mmdc`/Puppeteer — that
    reintroduces exactly the slow, environment-dependent dependency the existing suite was
    designed to avoid, and may not run at all in CI or on a reviewer's machine without Puppeteer's
    Chromium download.
  - **Do** generate a minimal *real* PNG in the test fixture instead (e.g. via
    `PIL.Image.new("RGB", (w, h)).save(buf, format="PNG")`, or a tiny hardcoded valid PNG byte
    string with a real `IHDR`), so `_fake_renderer`/its replacement returns bytes that are both
    magic-byte-sniffable *and* Pillow-decodable with a known, asserted pixel size. This keeps the
    fast/hermetic pattern the suite already relies on while giving the new size-computation code
    something real to compute against.
