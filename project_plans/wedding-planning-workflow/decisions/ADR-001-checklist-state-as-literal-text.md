# ADR-001: Represent checklist state as literal `[ ]`/`[x]` text, not native `BULLET_CHECKBOX` glyphs

**Date**: 2026-07-18
**Status**: Accepted

## Context

`requirements.md`'s Open Question asked: "Does the Google Docs API expose checklist items with a distinct type/glyph vs. plain bulleted lists with literal `[ ]`/`[x]` text?" This needed a real answer before any checklist round-trip code could be designed, since it determines where in `src/docspan/backends/google_docs/*.py` a fix belongs — or whether one is needed at all.

**Assumption explicitly revised after adversarial review**: this ADR was originally written expecting a single, document-wide answer (all-literal or all-native), verified by sampling one checklist line. adversarial-review.md correctly flagged this as unsound: the live doc has been edited by three different people (Nora, Bekah, Tyler) over months, and Google Docs' editor auto-converts typed `"[ ] "` into a native checkbox glyph inconsistently (depending on autocomplete/smart-bullets context at the moment each line was typed or pasted). A **mixed** document — some checklist paragraphs literal text, others native glyphs — is therefore the realistic expectation, not an edge case. Verification (Task 0.1.2a) accordingly surveys **every** `bullet`-bearing paragraph in the `ScratchDoc`, not one sampled line, and records a per-paragraph finding.

Research (`research/stack.md` §2, `research/build-vs-buy.md` §1c, cross-checked against multiple independent sources — Google's own REST reference, a tanaikech Apps Script gist, a Latenode community thread) confirms:

- The Docs API's `createParagraphBullets` request does support a distinct `bulletPreset: "BULLET_CHECKBOX"` value — checkboxes *can* be created programmatically.
- **Checked/unchecked state cannot be read back.** `documents.get()` returns `glyphType: GLYPH_TYPE_UNSPECIFIED` for a checkbox bullet regardless of whether it is checked or unchecked. The JSON is reported identical across a check/uncheck action — the only observed difference is the document's `revisionId`, which carries no state information. This is confirmed symmetric between the REST API and the Apps Script `DocumentApp` service, meaning it is a limitation of the underlying document model, not an SDK gap.
- The only indirect, *unofficial* signal is `textStyle.strikethrough` (checking a box in the UI applies strikethrough as a side effect) — but this is a UI convention, not a documented API contract, and Google Docs now offers a "checklist without strikethrough" UI option that breaks even this heuristic.

Options considered:

1. **Native `BULLET_CHECKBOX` glyph as source of truth.** Push writes real Docs checkboxes; pull attempts to read checked state back via `glyphType` or the strikethrough proxy.
2. **Literal text as source of truth** (`[ ]`/`[x]` inside the paragraph's plain text, rendered as an ordinary disc/circle/square bulleted paragraph — exactly what today's code already produces when it sees a markdown line like `- [x] Foo`, since no checkbox handling exists anywhere in the repo today).
3. **Hybrid**: native glyph for visual polish on push, but literal text tracked separately as the actual source of truth, kept in sync manually.

## Decision

Use **Option 2 — literal text** as the default, document-wide representation. Checklist state lives entirely as the substring `[ ]`/`[x]` at the start of an ordinary list-item paragraph's text. `DocsRequestBuilder` continues to emit `createParagraphBullets` with `bulletPreset: "BULLET_DISC_CIRCLE_SQUARE"` (unchanged) for every list item, checklist or not. Mistune's `task_lists` plugin is deliberately **not** enabled in `markdown_to_paragraph_parser.py`, so `- [x] Foo` parses as ordinary inline text (`"[x] Foo"`) rather than being split into a `checked` attribute — this is required, not incidental, because splitting it would strip the marker out of `.text` and require re-serializing it, for no benefit under this design.

**If the full-document survey (Task 0.1.2a) finds a mix of literal-text and native-glyph checklist paragraphs (finding (c) — the realistic case for a multi-author doc), this decision does not apply uniformly.** The literal-text scheme remains the default assumed representation, but any specific paragraph the survey identified as already being a native `BULLET_CHECKBOX` glyph is treated as **high-risk / requires manual handling**, not silently assumed to behave like the rest of the doc:

- Those paragraphs are listed by text prefix in `feature-gap-report.md` as known native-checkbox lines docspan does not track.
- Pull emits a `MixedChecklistWarning` (a `WARN`-level log line, plus a trailing marker comment in the pulled markdown, e.g. `<!-- docspan: native checkbox glyph, state not readable -->`) whenever it encounters a bullet paragraph whose resolved glyph is checkbox-shaped, so the blind spot is visible on every pull, not just documented once here and then forgotten.
- Tyler continues toggling those specific lines by hand in the Docs UI; docspan's pull renders them as plain, unmarked bulleted text (no marker corruption, just invisible state) until a future cycle designs real conversion support.
- **As of the pre-mortem repair pass, `push()` itself also refuses (fail-closed, `--force`-gated) to write through any of these specific native-glyph paragraphs.** A live `GlyphShapeCheck`, folded into `find_high_risk_paragraphs()` alongside the existing comment-risk check, re-resolves each changed paragraph's glyph shape from `push()`'s own single fetch — not from this ADR's static survey table — and flags it the same way an open-comment paragraph is flagged (same `HighRiskParagraph`/`--force` mechanism, see plan.md Story 1.2.2/1.2.3). This closes pre-mortem.md #1: without it, checking off a paragraph the survey found to be a native glyph would layer a literal `[x]`/`[ ]` marker onto that glyph with no warning and no `--force` requirement.

## Rationale

- Option 1 is a trap: it would silently break Success Metric 3 ("checklist state round-trips correctly") the first time a collaborator toggles a real UI checkbox, because docspan would have no way to detect the toggle on the next pull. This is exactly the kind of silent-data-loss failure mode requirements.md is trying to avoid.
- Option 2 requires **zero new Docs API surface** and flows through `docs_request_builder.py`'s existing, already-tested diff/index-shift machinery unchanged (confirmed by `research/build-vs-buy.md` §1c and §3). A checklist toggle is diffed exactly like any other single-paragraph text edit — no new opcode, no new index-shift edge case.
- Option 3 (hybrid) would add complexity (two representations to keep in sync) to work around a limitation that Option 2 sidesteps entirely, for a purely cosmetic gain (checkbox glyph vs. bracket text) that isn't a stated requirement.
- Owner/due-date/checked-state parsing for Claude's summary (`OwnerDigest`) happens by reading the literal markdown text in the Claude conversation itself — it never needs a structured `checked: bool` field in docspan's Python types, so there's no downstream consumer that would benefit from Option 1 or 3's added structure.

## Verification Evidence

*(Populated by Task 0.1.2a/0.1.2b against the `ScratchDoc` before Phase 2 implementation begins. Task 0.1.2a surveys **every** `bullet`-bearing paragraph in the document — the table below must have one row per checklist paragraph found, not a single sampled line.)*

| Paragraph text prefix | Resolved glyph (checkbox-shaped?) | Literal `[ ]`/`[x]` present in `textRun.content`? | Shape finding |
|---|---|---|---|
| `<e.g. "Whatsapp group">` | `<paste documents.get() glyphType>` | `<yes/no>` | `<(a) native \| (b) literal>` |
| `<next checklist paragraph>` | ... | ... | ... |
| *(one row per checklist paragraph in the doc — add rows as needed)* | | | |

- Structural element JSON for at least one representative example of each distinct shape found: `<paste documents.get() excerpt(s) here>`
- HTML export for the same lines: `<paste get_doc_content() excerpt(s) here>`
- Overall finding: `<(a) all native glyph | (b) all literal text | (c) mixed — see per-paragraph table above>`
- If finding (a) (all native glyph): pull-side native-checkbox-to-literal-text conversion is **out of scope this cycle** — see `feature-gap-report.md`. Any native checkboxes found continue to be toggled by hand in the Docs UI; docspan's pull will render them as plain, unmarked bulleted text until a future cycle designs and verifies the HTML-preprocessing pass described in `research/architecture.md` §2.
- If finding (c) (mixed — the realistic outcome for a multi-author doc): the specific native-glyph paragraphs identified in the table above are carried into `feature-gap-report.md` by name, and the `MixedChecklistWarning` pull-time warning (see Decision section above) is implemented so the gap surfaces on every pull rather than only here.

## Consequences

- No new fields are added to `DocsParagraphNode` (no `checked: Optional[bool]`), keeping the diff key (`style, text, is_list_item`) and all downstream request-building logic untouched.
- A checklist toggle always produces a `deleteContentRange` + `insertText` (delete+reinsert) for that paragraph, exactly like any other text edit to that paragraph — this is the same mechanism that risks dropping an anchored comment on that paragraph (see ADR-002 for the mitigation).
- If a future cycle wants native checkbox glyphs purely for visual polish (not as source of truth), that can be added later as a strictly additive, cosmetic push-time flag without touching this decision — but must never become the mechanism used to detect checked/unchecked state on pull.
- **If the survey finds a mixed document (finding (c)):** the literal-text scheme is not a safe blanket assumption — specific paragraphs are exceptions, tracked by name, and flagged loudly (not silently) on every pull via `MixedChecklistWarning`. Tyler must treat any doc-wide claim like "checklist state round-trips correctly" (Success Metric 3) as true only for the literal-text paragraphs, not the whole document, until a future cycle closes this gap. Since the pre-mortem repair pass, `push()` also refuses to write through one of these specific paragraphs without `--force` (`GlyphShapeCheck`, plan.md Story 1.2.2/1.2.3) — the mixed-doc case is now guarded at write time, not only disclosed at read time via `MixedChecklistWarning`.
