# Epic 0 / Story 0.1 — Findings

## Status: LIVE-VERIFIED (2026-08-17)

The live spike described below was run for real on 2026-08-17, with explicit human
authorization to use the cached OAuth credential at `~/.config/docspan/google_token.json`. A
throwaway Doc (`docspan-epic0-spike-DELETE-ME-2026-08-17`) was created, styled with the
candidate `BLOCKQUOTE_BORDER_MARKER`/indent values via `batchUpdate`, read back via
`documents.get`, diffed, and deleted.

**Result: the candidate values themselves are unchanged, but the live spike found a real bug**
in the pull-side detection code, since fixed:

- `borderLeft.width`, `borderLeft.dashStyle`, and `borderLeft.padding` echoed back
  byte-identical to what was sent.
- `indentStart.magnitude` echoed back as int `18` vs. the sent float `18.0` — numerically
  equal (`18 == 18.0` in Python), not a real divergence.
- `borderLeft.color.color.rgbColor` echoed back **quantized to 8-bit RGB**
  (`round(sent*255)/255`): sent `{"red": 0.494, "green": 0.549, "blue": 0.612}`, echoed
  `{"red": 0.49411765, "green": 0.54901963, "blue": 0.6117647}` — confirmed by computing
  `round(sent*255)/255` for each channel and matching it exactly to the echoed value.
- `_detect_blockquote_depth` in `docs_structure_parser.py` compared the `color` sub-field with
  exact Python `!=`, which would evaluate every real round trip as "not a blockquote" — the
  feature passed all 1096 existing (mock-based) unit tests but was silently broken against any
  real Google Doc. Fixed by adding `_rgb_close` (tolerance `0.003` per RGB channel — comfortably
  above the max 8-bit quantization error of `1/510 ≈ 0.00196`, far below a genuinely different
  color) and using it only for the `color` sub-field; `width`/`dashStyle` still compare exactly
  since they echoed identically. `tests/fixtures/blockquote_border_marker_spike.json` now holds
  the real captured
  request/response pair (replacing the hand-built one), and
  `TestEpic0LiveDocSpike::test_live_doc_spike_should_ReproduceRecordedBorderBehavior_When_RerunAgainstFixture`
  is unskipped and asserts through `_detect_blockquote_depth` against the real echo.

This settles plan.md's Unresolved Questions 1/2 (no `padding` surprise beyond what was already
handled; the only real divergence is the color quantization) and ADR-001's two empirical
unknowns for the single-paragraph case. Border-coalescing across adjacent paragraphs, visual
contrast/legibility, and cross-account/Workspace rendering remain **not** verified by this spike
— see "Explicitly left unverified" below, which still applies.

### Why this initially ran as a documented decision, not a live spike

A working, non-expired, write-scoped OAuth credential was found on this machine at
`~/.config/docspan/google_token.json` (`auth.py`'s `default_token_path()`), with a `refresh_token`
and `documents`/`drive`/`spreadsheets.readonly` scopes (`PUSH_SCOPES`), tied to what appears to be
a real personal/work Google account rather than a designated disposable test account. Creating a
document — even a throwaway one — under an unattended agent run has real side effects under that
real identity (it appears in the account's real Drive, consumes API quota, and is an action taken
as that user), so this agent initially treated running the spike as a decision for a human to make
explicitly rather than one implied by "spike this and report back," and proceeded only with the
documented engineering decision below.

The human then explicitly authorized running the live spike against that account, which is what
"Status: LIVE-VERIFIED" above reflects. No raw token/secret value was read, logged, or committed at
any point, before or during the live run.

### Decided values

```python
# Epic 1 will add these to docs_structure_parser.py; shown here as plain data, not yet
# present in that module.

BLOCKQUOTE_BORDER_MARKER: dict = {
    "color": {"color": {"rgbColor": {"red": 0.494, "green": 0.549, "blue": 0.612}}},
    "width": {"magnitude": 1, "unit": "PT"},
    "dashStyle": "SOLID",
    "padding": {"magnitude": 1, "unit": "PT"},
}

BLOCKQUOTE_INDENT_PT_PER_LEVEL: float = 18.0
```

Per ADR-001, marker detection on pull compares only `color`/`width`/`dashStyle` — `padding` is
included here only because a full `ParagraphBorder` must be sent in its entirety on write (Docs
API v1 reference: "when changing a paragraph border, the new border must be specified in its
entirety"), not because it participates in detection.

### Rationale

- **Schema**: `ParagraphStyle.borderLeft` is a `ParagraphBorder` (`color: OptionalColor`,
  `width: Dimension`, `dashStyle: string enum`, `padding: Dimension`);
  `ParagraphStyle.indentStart` is a `Dimension` (`magnitude: number`, `unit: "PT"`). Source:
  [Google Docs API v1 reference, `ParagraphBorder`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#ParagraphBorder)
  and [`ParagraphStyle`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#ParagraphStyle).
- **Color choice — `#7E8C9C` (RGB 0.494, 0.549, 0.612), a low-saturation slate blue-gray**:
  - Deliberately *not* one of the Docs UI's built-in border-color swatch presets (a fixed palette
    of pure grays/primaries), to reduce — not eliminate — the false-positive risk ADR-001 already
    accepts ("Marker migration risk"/"false-positive marker detection remains probabilistic").
  - Contrast against white, computed by hand using the WCAG 2.x relative-luminance formula
    (`f(c) = ((c+0.055)/1.055)^2.4` for `c>0.03928` else `c/12.92`;
    `L = 0.2126·R+0.7152·G+0.0722·B`; ratio `=(L_lighter+0.05)/(L_darker+0.05)`):
    **≈3.44:1**, clearing the ≥3:1 acceptance criterion in `design/ux.md` §1.4 / validation.md's
    `manual_border_contrast_check`. **This is an arithmetic check, not a run through an actual
    contrast-checker tool against a rendered Doc — the UX manual check itself remains
    unverified** (see below).
  - A plain mid-gray (`#737373`, ≈4.73:1) was also considered and rejected only because it is
    closer to a plausible human-chosen "light gray divider" color, which would raise — not lower
    — the false-positive-collision risk that is the more Docs-specific concern here.
- **Width — `1pt`, `dashStyle: SOLID`**: matches the plan's own illustrative example and is a
  visually unremarkable, unsurprising choice; distinctiveness is carried primarily by the
  non-swatch color, not an unusual width/dash combination.
- **Indent — `18pt` (0.25in) per level**: a common, round paragraph-indent unit already used by
  word processors generally (matches Word/LibreOffice's default indent step), chosen so a nested
  quote's indent reads as intentional rather than as an arbitrary magic number.

### Fixture and re-runnable test scaffold

- `tests/fixtures/blockquote_border_marker_spike.json` — the real captured
  `batchUpdate` request / `documents.get` response pair from the 2026-08-17 live run (replacing the
  earlier hand-constructed placeholder).
- `tests/test_google_docs_backend.py::TestEpic0LiveDocSpike::test_live_doc_spike_should_ReproduceRecordedBorderBehavior_When_RerunAgainstFixture`
  — unskipped, replays the fixture through the mocked client boundary and asserts through the
  production `_detect_blockquote_depth` function that the real echoed style is still recognized as
  a blockquote marker despite the color quantization.

### How this spike was run (2026-08-17, for reference)

1. Confirmed `google_token.json` was the credential to use, with explicit human authorization to
   create a throwaway document under that real account.
2. Created one throwaway Doc via the Drive API (`docspan-epic0-spike-DELETE-ME-2026-08-17`).
3. Sent one `batchUpdate` with an `insertText` + `updateParagraphStyle` using the candidate
   `BLOCKQUOTE_BORDER_MARKER`/indent above, then `documents.get` the same range back.
4. Diffed the echoed `paragraphStyle.borderLeft`/`indentStart` against what was sent — see
   "Result" above for the one real divergence found (8-bit RGB color quantization).
5. `BLOCKQUOTE_BORDER_MARKER`/`BLOCKQUOTE_INDENT_PT_PER_LEVEL` needed no value changes; the real
   request/response pair replaced the hand-built fixture and the `pytest.mark.skip` was removed.
6. Deleted the throwaway Doc.
7. The manual UX checks below still require visual inspection and remain unautomated.

### Explicitly left unverified (do not treat as passing)

- Story 0.1 task 3 — border-coalescing behavior across adjacent blockquote paragraphs (ADR-001
  already resolves this from documented Docs behavior for `borderLeft` specifically, but it is
  still not a live-Doc observation made in this session).
- Story 0.1 task 7 / `design/ux.md` — actual visual contrast and grayscale/indent-alone legibility
  spot-check in the Docs UI (only the arithmetic WCAG estimate above was done).
- Story 0.1 task 8 (pre-mortem) — cross-account/cross-Workspace-domain rendering normalization.
- validation.md's full "UX Acceptance Tests" table (`manual_indent_visible_in_grayscale`,
  `manual_border_contrast_check`, `manual_nested_depth_indent_increase`,
  `manual_list_in_quote_bullets_legible`, `manual_code_in_quote_visual_treatment`) — all manual,
  none performed.
- plan.md's Unresolved Questions 1 and 2 (omission-vs-default write semantics; table-cell
  inheritance) remain open; the `_cell_fill_request_should_ClearBlockquoteFields_When_...` test
  validation.md describes for Epic 2/S2.4 should stay a skip-tagged placeholder until this spike
  actually runs.
