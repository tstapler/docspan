# Feature-Gap Report

**Purpose**: Every known docspan limitation this wedding doc exposes, in one lookup-able place. If you hit one of these mid-sync, it's a known, already-decided-against gap — not a new bug to debug under time pressure. See `implementation/plan.md` Story 3.2.1 for the acceptance criteria this report satisfies, and ADR-001 / ADR-002 for the design rationale behind items 5–9.

---

## 1. Tables, section breaks, and TOC are silently skipped

`src/docspan/backends/google_docs/docs_structure_parser.py:75` — the parse loop only handles `paragraph` elements; `table`, `sectionBreak`, and `tableOfContents` structural elements are dropped with no warning, on both pull and push.

Effect: if the doc has a table or a TOC, it's invisible to Claude's summary and to docspan entirely. Because both pull's HTML path and push's JSON path independently ignore these, an existing table is *stable* (nothing deletes it), but any manual edit near one still needs a live-doc check — docspan can't tell you it's there.

*(Research citation: `research/features.md:175-181`. Line number confirmed against current code — was `docs_structure_parser.py:64` in the original plan citation, now `:75` after Epic 1.2 added the `is_native_checkbox` field and its docstring above the loop.)*

## 2. No Google Sheets backend

`src/docspan/backends/__init__.py:7-9` registers only `google_docs` and `confluence` in `BACKENDS`. There is a `GoogleSheetsClient` (`src/docspan/backends/google_docs/client.py:29-40`), but it's a narrow read-only helper for reading a doc-id/vault-path mapping sheet — not a `Backend` subclass, and not wired to sync any Sheet's actual content.

Effect: the grocery list and packing list, if they live in Google Sheets rather than a Doc, never sync through docspan. Edit those by hand.

## 3. No image support on push

No code in `src/docspan/backends/google_docs/` references `inlineObject`, `insertInlineImage`, or any image-upload request type (confirmed by grep — zero matches). `DocsRequestBuilder` only emits `insertText`, `updateParagraphStyle`, `createParagraphBullets`, and `updateTextStyle` requests.

Effect: images can't be added to the doc via docspan. Add them directly in the Google Docs UI.

## 4. Links and bold/italic/monospace formatting are dropped on edited paragraphs

`src/docspan/backends/google_docs/docs_request_builder.py:287-323` defines `_make_text_style_requests` — a fully-implemented method that would emit `updateTextStyle` requests for bold/italic/link/monospace. It is **dead code**: `_make_insert_requests` (`docs_request_builder.py:228-268`), the only method that writes new paragraph content on a `replace`/`insert` diff opcode, never calls it. Confirmed by grep across the file — `_make_text_style_requests` has exactly one reference (its own `def`).

Effect: any paragraph that gets edited (opcode `replace` or `insert`) loses all inline formatting on push — a link to a per-day sub-doc, bold, italic, or monospace text is silently flattened to plain text. Unedited paragraphs are untouched and keep their formatting.

*(Research citation: `research/architecture.md:55,125`. Line number confirmed against current code — was `docs_request_builder.py:174-210` in the original plan citation; the method moved to `:287-323` after Epic 1.2's changes to this file. Still unequivocally dead code.)*

**Cross-reference**: treat any paragraph containing a markdown link as `HighRiskParagraph`-equivalent by your own judgment before editing it, even though `find_high_risk_paragraphs()` (`src/docspan/backends/google_docs/push_preview.py:33`) only checks for open comments and native-checkbox glyphs, not links. This is not code-enforced — it's a manual habit to adopt.

## 5. Native `BULLET_CHECKBOX` state is unreadable, and mixed-doc handling is pending

Per ADR-001, `documents.get()` cannot reliably read back a checkbox's checked/unchecked state — Google's own API returns the same JSON shape regardless of check state. docspan's checklist scheme (literal `[ ]`/`[x]` text) sidesteps this, but any paragraph that's *already* a native checkbox glyph in the live doc is a named exception.

**Phase 0's full-document survey (Task 0.1.2a) has not yet been run.** Once it is, any paragraph found to be a native checkbox glyph will be listed here by name (paragraph text prefix), per ADR-001's per-paragraph table. Until then, treat this as a pending finding, not a confirmed empty result — a native-glyph paragraph could exist anywhere the doc has a checklist.

What *is* already built regardless of the survey outcome: pull emits a `MixedChecklistWarning` whenever `DocsStructureParser` resolves a bullet paragraph as a native `BULLET_CHECKBOX` (`docs_structure_parser.py:34`, `is_native_checkbox` field), and push blocks editing any such paragraph via `GlyphShapeCheck` — folded into `find_high_risk_paragraphs()` (`src/docspan/backends/google_docs/push_preview.py:72-73`) — unless `--force` is passed. So the gap is guarded on write, not just disclosed on pull, even before Phase 0's survey names specific paragraphs.

## 6. Comment-risk detection is a substring heuristic, not a semantic anchor decode

`find_high_risk_paragraphs()` (`src/docspan/backends/google_docs/push_preview.py:33-85`) flags a paragraph as at-risk when an open comment's `quotedFileContent.value` appears as a **substring** of the paragraph's current text (`push_preview.py:62-70`). This is deliberate per ADR-002 — a full anchor-range decode was judged out of appetite (the Drive `anchor` field format is undocumented).

Known limitations, accepted not hidden:
- False positives possible (quoted text coincidentally substring-matches unrelated text).
- False negatives possible if Google normalizes whitespace/quotes differently, or a comment spans multiple paragraphs.
- **Even when the `⚠ COMMENT AT RISK` warning is heeded or `--force` is used, the underlying delete+reinsert mechanism can still drop the comment.** Whether this actually happens in practice is a known theoretical risk pending live confirmation — Story 2.2.1b's live scratch-doc test has not yet been run, so this is not yet a confirmed finding either way. Once that test runs, its result should be recorded here as either "confirmed: comment loss occurs even with `--force` reviewed" or "confirmed: comment survives" — treat it as unverified until then.

## 7. `merge3` is line-based and conflates checklist text-edits with check-state toggles

`src/docspan/core/merge.py:20-21` — `three_way_merge()` calls `Merge3(base_lines, ours_lines, theirs_lines).merge_lines(...)`, pure line-based diffing on the markdown representation.

Effect: a checklist toggle (`- [ ] Book DJ` → `- [x] Book DJ`) and a wording edit to the same line are, semantically, independent changes (status vs. text) — but merge3 sees them as two edits to the same line. Depending on overlap, it either silently picks one side or wraps the whole line in conflict markers (`<<<<<<< ours` / `=======` / `>>>>>>> theirs`). Expect occasional conflict markers when a task is reworded locally at the same time it's checked off remotely; resolve with `docspan conflicts resolve`. This is accepted per ADR-001, not a bug to fix this cycle.

*(Research citation: `research/pitfalls.md:66`.)*

## 8. Residual millisecond TOCTOU window on comments

`push()` (`src/docspan/backends/google_docs/backend.py:119-176`) builds its `PushPlan` — including the comment-risk check — from one single fetch (`_build_push_plan`, called at `backend.py:133`), then writes via `batch_update()` at `backend.py:145-147`. This closes the larger CLI/backend-split TOCTOU that architecture-review.md flagged as a BLOCKER.

What remains: a comment added by a collaborator in the window between that fetch and `batch_update()` landing (now milliseconds, within one CLI invocation — not an entire CLI call's worth of time as before the fix) is still invisible to the pre-write check, because Drive comments are metadata outside the Doc's `revisionId`, and `writeControl` only guards the document body. `CommentCountBackstop` (item 9) catches this after the fact but can't prevent it.

## 9. `CommentCountBackstop` is a detector, not a preventer

`src/docspan/backends/google_docs/backend.py:150-165` — after a successful `batch_update()`, `push()` re-fetches the open-comment count via `list_comments()` and compares it to the pre-write count captured in the plan (`before_count = len(plan.comments)` at `backend.py:154`). If the count dropped, `push()` returns `PushResult(status="warning", ...)` (`backend.py:157-165`) with message `⚠ open comment count dropped (...) — a comment may have been lost even though it wasn't flagged`.

Confirmed against current code: this genuinely escalates `PushResult.status` from `"ok"` to `"warning"` — it is never left as a false `"ok"` with only the message text changed (verified by reading `backend.py:119-176` directly; the `"warning"` branch returns before the `"ok"` return at `backend.py:166`). If it fires, the comment is already gone — treat a `"warning"` status or the `⚠ open comment count dropped` line as "go check the doc now," not as a push that auto-corrected itself.

## 10. Checklist-only push isolation is operator discipline only

`PushPreview.render()` (`src/docspan/backends/google_docs/push_preview.py:175-186`) emits an informational note — `ⓘ This push mixes N checklist toggle(s) with M other edit(s) — consider pushing checklist-only changes separately` — when a `--dry-run` preview contains both checklist and non-checklist edits.

This is a render-time nudge only. docspan does not reject, split, or otherwise enforce isolation of a push that mixes checklist and non-checklist edits — "isolate checklist-only pushes near comments" is documented operator discipline, not a tool-enforced rule.

---

## Cross-reference note (link/formatting loss)

Item 4 (links/formatting dropped on edited paragraphs) is not covered by the automated risk gate. `find_high_risk_paragraphs()` (`push_preview.py:33`) only checks for open comments (`CommentCrossReference`) and native checkbox glyphs (`GlyphShapeCheck`) — it has no concept of "this paragraph contains a link." Treat any paragraph you know contains a markdown link as `HighRiskParagraph`-equivalent by your own judgment before pushing an edit to it. This is stated explicitly here because it is not code-enforced this cycle, per Story 3.2.1's acceptance criteria.
