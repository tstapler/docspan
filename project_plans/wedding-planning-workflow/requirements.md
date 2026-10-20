# Requirements: wedding-planning-workflow

**Date**: 2026-07-18
**Type**: feature addition (docspan patch) + personal workflow deliverable
**Complexity**: 3 — system design (two epics, external integration already in place, real risk of data loss on a live shared document)

## Problem Statement
Tyler's wedding weekend (7/29–8/1) is coordinated through one live Google Doc ("Planning document") that Nora, Bekah, and other wedding-party members already edit and comment on directly. Tyler wants to pull that doc into markdown, use Claude to track/organize tasks, schedule, housing, and food logistics, and push updates back — while minimizing risk to collaborators' concurrent edits or comments (see "Revised Guarantees" below — this is a loud-warning-plus-manual-override mitigation, not an absolute guarantee), and without losing structure (checklists, nested lists, links) docspan doesn't currently round-trip.

## Baseline
Today the doc is maintained by hand, directly in Google Docs, by multiple people. Task status (`- [x]` / `- [ ]`) is toggled manually inside Docs. There is no local copy, no LLM-assisted summarization of open items/owners/due dates, and no repeatable way to safely sync edits back without re-reading the whole multi-page doc first. `docspan push --dry-run` was assumed functional when this document was first written; research (Phase 2) found it is currently a no-op stub (`src/docspan/cli/main.py:107-111`) that prints one line and never builds a diff — fixing it for real is now necessary supporting work under Scope item 1, not optional polish (see plan.md Phase 1 Epic 1.2).

## Users / Consumers
- Tyler (primary) — pulls the doc, uses Claude to review/update it, pushes back.
- Wedding-party members (Nora, Bekah, Ann, etc.) — continue editing the Google Doc directly and are never expected to touch docspan or markdown; their concurrent edits/comments must survive Tyler's sync cycles untouched, to the extent achievable (see "Revised Guarantees").

## Success Metrics
- Across at least 3 real pull→edit→push cycles before the wedding, zero collaborator edits or comments are lost or overwritten, **as achieved via risk-flagging + manual intervention, not an automatic guarantee** (see "Revised Guarantees" below).
- "What's still open and who owns it" goes from a multi-minute manual re-read of the doc (baseline) to a single Claude-generated summary, produced conversationally after `docspan pull` (not necessarily a new dedicated CLI subcommand).
- Checklist state (`- [x]`/`- [ ]`) round-trips correctly through pull and push (currently unverified/unsupported — see Feasibility Risks), where "checklist" means literal `[ ]`/`[x]` text, not native Google Docs checkbox glyphs — see "Revised Guarantees" and ADR-001.

## Revised Guarantees
*(Added after Phase 2 research and Phase 3 planning revealed real API limitations not known when this document was first written. See ADR-001 and ADR-002 in `project_plans/wedding-planning-workflow/decisions/` for full rationale.)*

- **Comment preservation (Success Metric 1)** is delivered via ADR-002's comment-risk flag: any push touching a paragraph whose text overlaps an open comment's quoted content is blocked with a `⚠ COMMENT AT RISK` warning unless Tyler passes `--force`, and a `CommentCountBackstop` detects (after the fact) if the open-comment count dropped anyway. This is a **loud, blockable heuristic**, not a guarantee — the substring match can have false negatives, and even a `--force`-confirmed push through the delete+reinsert code path can still lose a comment (this is a known, if unlikely, Google Docs API behavior — see `feature-gap-report.md` item 6). "Zero loss across ≥3 cycles" depends on Tyler heeding every warning, not on the tool making loss structurally impossible.
- **Checklist state** round-trips as literal text (`[ ]`/`[x]`), not native `BULLET_CHECKBOX` glyphs, because the Google Docs API cannot reliably report checked/unchecked state back via `documents.get()` (confirmed by research — glyph type is identical either way). If the live doc turns out to already use native checkbox glyphs for some items (checked by Phase 0's full-document survey), those specific paragraphs are named individually and left untouched this cycle, not silently converted.

## Appetite
Small (1–2 focused days). Hard deadline: must be usable well before 7/29/2026 (~11 days from today).
*(Scope must fit the appetite. If it doesn't fit, cut scope — do not move the deadline.)*

## Constraints
- Must be working and safe to use several days before 7/29/2026 — no room for a multi-week rebuild.
- The Google Doc is the live source of truth for the whole wedding party; any sync bug that clobbers someone else's edit or comment is unacceptable.
- Single-developer effort (Tyler + Claude); no team to split work across.
- Existing docspan CLI/config (`markgate.yaml`, `docspan pull/push`, three-way merge, `.orig` backups) is the substrate — reuse it, don't replace it.

## Non-functional Requirements
- **Performance SLO**: not applicable (single-user, on-demand CLI usage).
- **Scalability**: not applicable — one document, occasional syncs.
- **Security classification**: internal/confidential (personal event planning content, family details, addresses).
- **Data residency**: no special requirements.

## Scope
### In Scope
1. **docspan fixes** (only what's needed for this doc to round-trip safely):
   - Verify/fix checklist (`- [ ]` / `- [x]`) round-trip on pull and push — currently unimplemented (no checkbox/glyph handling found in the parser, converter, or push builder).
   - Verify comment risk-flagging (downgraded from full comment-anchoring preservation — see "Revised Guarantees") survives a pull→push cycle for comments anchored to specific text spans (the doc already has two open comments anchored mid-paragraph).
   - Necessary supporting infrastructure: fix `docspan push --dry-run` (currently a no-op stub) and add a `writeControl.requiredRevisionId` guard — both are prerequisites for the risk-flagging above to mean anything, not separate scope.
2. **Personal workflow/skill** — "skill" here means an informal capability/runbook, not a packaged Claude Skill file — on top of docspan for this one document, covering (in priority order):
   - Tasks/TO-DOs — open items, owners, due dates.
   - Schedule (Wed–Sat).
   - Housing/room assignments.
   - Food/catering (including surfacing that grocery/packing lists are Google Sheets, which docspan doesn't sync — see Feasibility Risks).
   - Workflow shape: `docspan pull` → Claude summarizes open tasks/owners/due dates and flags gaps → Tyler edits locally → `docspan pull` again immediately before push to catch concurrent collaborator edits → review diff (`docspan push --dry-run`) → `docspan push`.
3. **Feature-gap report**: a written list of docspan gaps this real document exposes (checklists, tables/sectionBreak/TOC currently silently skipped in the parser, no Sheets backend, image support), so gaps that are out of appetite for this cycle are explicit, not silently dropped.

### Out of Scope
- A general-purpose Google Sheets backend (grocery/packing lists) — document as a gap, do not implement.
- Table support in push (README's known limitation) — document as a gap, do not implement.
- Wedding website, RSVP system, Splitwise/budget integration, Golden Gardens permit process — these stay exactly as they are today (manual, outside docspan).
- Syncing the four linked per-day sub-docs as separate mappings — only the main planning doc is in scope this cycle.

## Rabbit Holes
- Google Docs represents checklists via a distinct bullet "glyph"/list-preset, not just markdown text — round-tripping checked/unchecked state correctly (not just the `[x]`/`[ ]` text) could be deeper than it looks.
- Comment-anchor preservation across a structural diff push is already a known sharp edge (README: "comments on edited paragraphs are lost on push") — the two open comments in this doc sit on paragraphs likely to be edited, so this needs explicit verification, not assumption.
- Nested list reconstruction (`_reconstruct_nested_lists` in converter.py) is already a fragile HTML-parsing heuristic; touching checklist handling risks regressing existing nested-bullet behavior.

## Alternatives Considered
- **Keep editing directly in Google Docs (status quo)** — rejected: no LLM assistance, no structured task tracking.
- **Separate tracker (spreadsheet, Todoist, etc.)** — rejected: collaborators already live in this one Doc; a second system fragments the source of truth days before the wedding.
- **Manual copy-paste into Claude for one-off analysis** — rejected: goes stale immediately, not repeatable across multiple sync cycles.

## Feasibility Risks
- No checkbox/task-list handling exists anywhere in the Google Docs backend today (parser, converter, or request builder) — confirmed by code search. This is the single biggest risk to the "tasks" use case and may need a real (if small) code change, not just a workflow wrapper.
- `docs_structure_parser.py` silently skips `table`, `sectionBreak`, and `tableOfContents` elements — the doc has a TOC; confirm push doesn't regenerate/duplicate it or drop content near it.
- Time pressure: 11 days total, and the fix needs real-world validation against the live doc (not just unit tests) before trusting it with collaborator content.

## Observability Requirements
Standard CLI output is sufficient (personal-use tool, not a running service). Every push in this workflow must be preceded by `docspan push --dry-run` so the diff is visually reviewed before it touches the live doc. No metrics/alerting needed.

## Risk Control
- Always `docspan pull` immediately before `docspan push` to catch concurrent collaborator edits, and always review `docspan push --dry-run` output before the real push.
- Rely on docspan's existing `.orig` backup and `.markgate-base` merge-base store for rollback; additionally keep the local markdown file under git so every synced version is independently recoverable.
- If a push looks destructive (drops a collaborator's comment or content) in the dry-run diff, stop and resolve manually in Google Docs rather than forcing the push.

## Open Questions
- Does the Google Docs API expose checklist items with a distinct type/glyph vs. plain bulleted lists with literal `[ ]`/`[x]` text? (Needs research phase investigation before planning the fix.)
- Can comment-anchor preservation be verified/tested without live-doc access in CI, or does verification have to happen against the real document?
