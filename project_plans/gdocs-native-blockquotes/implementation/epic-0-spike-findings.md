# Epic 0 / Story 0.1 — Findings

## Status: ENGINEERING DECISION — PENDING LIVE VERIFICATION

**This is not a completed live-Doc spike.** No `batchUpdate`/`documents.get` call was made
against the real Google Docs API while producing this document. The values below are a
documented engineering decision, reasoned from the public Docs API v1 schema and WCAG contrast
math, not an empirical finding. Epic 1 may proceed by importing these values as
`BLOCKQUOTE_BORDER_MARKER`/`BLOCKQUOTE_INDENT_PT_PER_LEVEL` in
`src/docspan/backends/google_docs/docs_structure_parser.py`, but plan.md's Unresolved Questions
1-3 and ADR-001's two empirical unknowns (§"Consequences", last bullet) remain open until a human
explicitly authorizes and runs the live spike described under "How to actually run this spike"
below.

### Why this ran as a documented decision, not a live spike

A working, non-expired, write-scoped OAuth credential was found on this machine at
`~/.config/docspan/google_token.json` (`auth.py`'s `default_token_path()`), with a `refresh_token`
and `documents`/`drive`/`spreadsheets.readonly` scopes (`PUSH_SCOPES`). This contradicts the task
assumption that no live credentials would be present, and is called out here explicitly since it
may warrant separate human follow-up.

It was deliberately **not** used to run the spike autonomously:

- The credential is tied to what appears to be a real personal/work Google account (the harness's
  own secret-redaction fired when this agent inspected the token's `account` field), not a
  designated disposable test account.
- Creating a document — even a throwaway one — under an unattended agent run has real side
  effects under that real identity (it appears in the account's real Drive, consumes API quota,
  and is an action taken as that user) that this agent judged to be a decision for a human to
  make explicitly, not one implied by "spike this and report back."
- The task's own escape hatch required judging the live path "safe to use" before any network
  call; this agent could not make that judgment call on the user's behalf without confirmation.

No raw token/secret value was read, logged, or committed at any point.

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

- `tests/fixtures/blockquote_border_marker_spike.json` — a hand-constructed (not captured)
  `batchUpdate` request / `documents.get` response pair built from the values above, explicitly
  labeled as not live-API output.
- `tests/test_google_docs_backend.py::TestEpic0LiveDocSpike::test_live_doc_spike_should_ReproduceRecordedBorderBehavior_When_RerunAgainstFixture`
  — `@pytest.mark.skip("requires live Docs API credentials")`, replays the fixture through the
  mocked client boundary. This exercises the fixture's internal shape consistency; it does **not**
  and cannot confirm that a real Doc echoes these exact bytes back.

### How to actually run this spike (for a future maintainer, once authorized)

1. Confirm `google_token.json` (or a fresh consented OAuth run via `GoogleAuthenticator`) is a
   credential explicitly designated for throwaway test documents — not a shared/production
   account — before making any write call.
2. Create one throwaway Doc via the Drive API, name it unambiguously (e.g.
   `docspan-epic0-spike-DELETE-ME-<date>`).
3. Send one `batchUpdate` with an `insertText` + `updateParagraphStyle` using the candidate
   `BLOCKQUOTE_BORDER_MARKER`/indent above, then `documents.get` the same range back.
4. Diff the echoed `paragraphStyle.borderLeft`/`indentStart` against what was sent. Record any
   divergence (added defaults, unit normalization, etc.) — this settles plan.md's Unresolved
   Question 1/2.
5. Update `BLOCKQUOTE_BORDER_MARKER`/`BLOCKQUOTE_INDENT_PT_PER_LEVEL` in
   `docs_structure_parser.py` if the values need adjustment, capture the *real* request/response
   pair as the new fixture (replacing the hand-built one), and remove the `pytest.mark.skip`.
6. Delete the throwaway Doc.
7. Separately perform the manual UX checks below — they require visual inspection and cannot be
   automated.

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
