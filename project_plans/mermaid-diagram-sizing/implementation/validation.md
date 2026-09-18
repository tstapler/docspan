# Validation Plan: mermaid-diagram-sizing

**Date**: 2026-09-18

## Happy Path Scenario
Given a `.md` file with a ```` ```mermaid ```` fence mapped into a Google Doc that has never been pushed before (Baseline: mmdc renders it small and left-aligned with no `objectSize`), when docspan pushes the file, then the resulting `insertInlineImage` request carries `objectSize` scaled to `width_pt=468.0`/`height_pt=234.0` (for a `2400x1200`px native PNG at `RENDER_SCALE=3`) and the enclosing paragraph gets a `CENTER` `updateParagraphStyle` request, so the diagram lands full-content-width and centered on first insertion.

## Requirement → Test Mapping

| Requirement | Test File | Test Name | Type | Scenario |
|-------------|-----------|-----------|------|----------|
| Story 1.1.1: read PNG IHDR without crashing (valid input) | tests/test_gdocs_mermaid.py | `test_png_pixel_dimensions_reads_valid_ihdr` | Unit | Happy path — `struct.pack`-built `2400x1200` IHDR → `(2400, 1200)` |
| Story 1.1.1: read PNG IHDR without crashing (magic-bytes-only fake) | tests/test_gdocs_mermaid.py | `test_png_pixel_dimensions_returns_none_for_fake_png_without_ihdr` | Unit | Error path — `_PNG_MAGIC + diagram.encode()` (the old fixture shape) → `None`, no raise |
| Story 1.1.1: read PNG IHDR without crashing (truncated bytes) | tests/test_gdocs_mermaid.py | `test_png_pixel_dimensions_returns_none_for_truncated_bytes` | Unit | Error path — 8-byte signature only → `None`, no raise |
| Story 1.1.2: compute width_pt/height_pt scaled to content width (happy path) | tests/test_gdocs_mermaid.py | `test_mermaid_image_size_pt_scales_2400x1200_at_render_scale_3_to_468x234` | Unit | Happy path — `2400x1200`px, `RENDER_SCALE=3` → `(468.0, 234.0)` |
| Story 1.1.2: RENDER_SCALE division correctness (must-catch regression, `research/pitfalls.md` #1) | tests/test_gdocs_mermaid.py | `test_mermaid_image_size_pt_is_independent_of_render_scale_value` | Unit | Happy path — `2400x1200`@scale-3 vs. `4800x2400`@scale-6 (monkeypatched `RENDER_SCALE`) both → `(468.0, 234.0)` |
| Story 1.1.2: malformed PNG error path | tests/test_gdocs_mermaid.py | `test_mermaid_image_size_pt_returns_none_for_malformed_png` | Unit | Error path — `_png_pixel_dimensions()` returns `None` → `_mermaid_image_size_pt()` returns `None` |
| Story 1.1.2: aspect-ratio preservation / unconditional upscale of a small diagram | tests/test_gdocs_mermaid.py | `test_mermaid_image_size_pt_upscales_small_diagram_preserving_aspect_ratio` | Unit | Happy path — `800x400`px @ scale-3 (logical `200x100pt`) → `(468.0, 234.0)`, same 2.34x factor on both axes |
| Story 1.1.2: rounding/determinism for idempotent re-push (`research/pitfalls.md` #2-3) | tests/test_gdocs_mermaid.py | `test_mermaid_image_size_pt_is_deterministic_across_repeated_calls` | Unit | Happy path — calling `_mermaid_image_size_pt()` twice on identical bytes returns the identical `(float, float)` tuple (same object equality, not just approximately equal) |
| Story 1.1.3: wire size into resolve_document_images (happy path) | tests/test_gdocs_mermaid.py | `test_resolve_document_images_sets_width_and_height_for_mermaid_image` | Integration | Happy path — mermaid node + `_minimal_png(2400, 1200)` renderer → `out[0].width_pt == 468.0`, `out[0].height_pt == 234.0` |
| Story 1.1.3: non-mermaid image is never sized | tests/test_gdocs_mermaid.py | `test_resolve_document_images_leaves_width_and_height_none_for_non_mermaid_image` | Integration | Error/negative path — plain `![alt](src)` node → `width_pt`/`height_pt` stay `None` |
| Story 1.1.3: explicit pre-existing size is preserved, not overwritten | tests/test_gdocs_mermaid.py | `test_resolve_document_images_preserves_explicit_width_and_height_on_mermaid_node` | Integration | Edge case — mermaid node with `width_pt=100.0`/`height_pt=50.0` already set → unchanged after resolve |
| Story 1.1.3: render failure doesn't crash sizing | tests/test_gdocs_mermaid.py | `test_mermaid_render_failure_is_a_warning_not_a_crash` *(existing test; re-run as regression, no new assertions needed)* | Integration | Error path — `MermaidRenderError` → `out == [None]`, warning emitted, no sizing code runs |
| Story 1.2.1: CENTER `updateParagraphStyle` for mermaid image, default path | tests/test_gdocs_mermaid.py | `test_mermaid_image_insert_adds_center_paragraph_style_request` | Unit | Happy path — `before_newline=False`, `bare_last=False` → `updateParagraphStyle` range `{startIndex: insert_at_index, endIndex: insert_at_index+2}` |
| Story 1.2.1: no CENTER request for a plain (non-mermaid) image | tests/test_gdocs_mermaid.py | `test_plain_image_insert_adds_no_paragraph_style_request` | Unit | Error/negative path — `mermaid_source is None` → no `updateParagraphStyle` in the request list |
| Story 1.2.1: CENTER request for the bare-image path | tests/test_gdocs_mermaid.py | `test_mermaid_image_insert_adds_center_paragraph_style_request_when_bare_last` | Unit | Edge case — `bare_last=True`, node is `nodes[-1]` → range `{startIndex: insert_at_index, endIndex: insert_at_index+1}` |
| Story 1.2.1 CONCERN (architecture-review.md Remediation): CENTER request for the `before_newline` sub-case | tests/test_gdocs_mermaid.py | `test_mermaid_image_insert_adds_center_paragraph_style_request_when_before_newline` | Unit | Edge case flagged as a review CONCERN — `before_newline=True` → range `{startIndex: insert_at_index+1, endIndex: insert_at_index+3}` (mirrors the default case's off-by-one, shifted by the leading newline) |
| Story 1.3.1: fixture repair — `_fake_renderer` now returns a real IHDR-bearing PNG | tests/test_gdocs_mermaid.py | `test_fake_renderer_output_is_a_valid_png_with_known_dimensions` | Unit | Happy path — `_png_pixel_dimensions(_fake_renderer(...))` returns `(2400, 1200)`, not `None` |
| Story 1.3.1: pre-existing non-sizing tests keep passing against the new fixture | tests/test_gdocs_mermaid.py | `test_resolve_document_images_uses_mermaid_source_over_src` *(existing test; re-run as regression, no changes needed since the hash is recomputed from `_fake_renderer(...)` rather than hardcoded)* | Integration | Regression — URI/temp-id/mermaid-entry-hash behavior unaffected by the fixture's byte content changing |
| Story 1.3.2: end-to-end sizing + centering through the full push pipeline | tests/test_gdocs_mermaid.py | `test_mermaid_image_gets_sized_and_centered_on_push` | Integration | Happy path — parse `MERMAID_MD` → `resolve_document_images()` → request-builder image branch → `objectSize.width.magnitude == 468.0`, `objectSize.height.magnitude == 234.0`, one `updateParagraphStyle` with `alignment == "CENTER"` |
| Story 1.3.2: repeated push has a stable `_node_key()` (idempotency) | tests/test_gdocs_mermaid.py | `test_repeated_mermaid_push_has_stable_node_key` | Integration | Happy path — two independent `resolve_document_images()` calls on identical source → identical `_node_key()` tuples, same hash and floats |
| Story 1.3.3: re-pushing an already-sized mermaid image is a safe no-op | tests/test_gdocs_mermaid.py | `test_repush_of_already_sized_mermaid_image_is_a_safe_noop` | Integration | Happy path (regression) — pulled node (`width_pt=100.0`/`height_pt=50.0`) vs. target node (`width_pt=468.0`/`height_pt=234.0`), same `alt` → `_content_key()` matches, `_repair` classifies unchanged, zero requests emitted |
| Story 1.3.3: no-op path raises no exception | tests/test_gdocs_mermaid.py | `test_repush_of_already_sized_mermaid_image_is_a_safe_noop` *(same test; second assertion)* | Integration | Error path — diff/repair pipeline runs to completion without raising for the mismatched-size pair |
| Story 1.3.4: manual real-doc verification | — (manual) | — | Manual | See Manual Verification below |

## UX Acceptance Tests
N/A — no user-facing/interactive surface; this is a backend rendering fix. See plan.md Story 1.3.4 for the manual real-doc verification step instead. No `design/ux.md` exists for this project (backend-only feature; UX design was not run as a separate SDD phase).

## Manual Verification
Story 1.3.4 (not automated): push a scratch markdown file with a mermaid fence to a real Google Doc via docspan's CLI, and visually confirm (a) the diagram inserts at approximately full content width (~468pt/6.5in, not a small thumbnail), (b) it is horizontally centered, and (c) a second, unchanged push doesn't resize, duplicate, or otherwise alter it. Complements, does not replace, `test_mermaid_image_gets_sized_and_centered_on_push`.

## Test Stack
- **Unit**: pytest
- **Integration**: pytest (with fake/stub renderer, matching existing tests/test_gdocs_mermaid.py convention — no real mmdc/Puppeteer shell-out in tests)
- **E2E / UX**: manual (Story 1.3.4)

## Coverage Targets and How to Measure
No coverage threshold is configured in this repo (`pyproject.toml`'s `[tool.pytest.ini_options]` has no `--cov-fail-under`, and there's no `[tool.coverage]` section). `pytest-cov` is a dev dependency and `CONTRIBUTING.md` documents the ad hoc invocation:
```
pytest --cov=docspan --cov-report=term-missing
```
For this feature, run the touched test module plus its two documented sibling suites (per plan.md Task 1.3.2c and requirements.md's Success Metrics: existing non-mermaid image tests must stay green):
```
uv run pytest tests/test_gdocs_mermaid.py tests/test_gdocs_images.py tests/test_mermaid_appendix.py -v
```
No fixed percentage target — treat "every new function (`_png_pixel_dimensions`, `_mermaid_image_size_pt`) and every new branch (mermaid vs. non-mermaid, default/bare/before_newline) has at least one direct test" (per the Requirement → Test Mapping table above) as the bar, consistent with this repo's existing practice of not gating on a numeric coverage threshold.
