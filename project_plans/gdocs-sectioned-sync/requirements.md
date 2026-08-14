# Requirements: gdocs-sectioned-sync

**Date**: 2026-08-13
**Type**: feature addition
**Complexity**: 3 — system design

## Problem Statement
Today, pulling a Google Doc through docspan (or the ad-hoc google-docs-plugin skill) always produces a single flat markdown file for the whole document. For large design docs (multi-thousand-word, many sections), this is expensive and awkward for both humans and AI agents: an agent that only needs one section has to load the entire doc into context (or worse, dump raw Google Docs API JSON and hand-parse it, as happened while working the "Deployment Safety on Kubernetes" doc), and a human reviewer loses the doc's section structure as a navigation aid.

## Baseline
Without this feature: `docspan pull` writes one `.md` file per mapped Google Doc, regardless of size. Editing a specific section means opening the whole file. Programmatic/agent access to a specific section means fetching the whole doc's HTML/JSON export and parsing it locally (the inefficient workaround this project was born from). Push works the same way in reverse: the whole file is diffed and pushed back as one unit, using docspan's existing three-way-merge machinery (`src/docspan/backends/google_docs/backend.py` push/pull, `docs_structure_parser.py`, `nodes_to_markdown.py`).

## Users / Consumers
- docspan CLI users maintaining large Google Docs (design docs, RFCs, runbooks) via markgate.yaml mappings.
- AI agents (Claude Code sessions) that read/edit a specific section of a large doc without loading the whole thing.
- Indirectly, the separate `google-docs-plugin` skill (~/ws/ngp-skills/plugins/google-docs-plugin) could later shell out to this instead of its own ad-hoc `read`/`export-markdown` commands — explicitly out of scope for this project (see Out of Scope).

## Success Metrics
- A `docspan pull` on a mapping configured for sectioned mode produces a directory of per-section markdown files instead of one flat file, and an agent/human can read or edit exactly one section's file without loading the rest of the document.
- `docspan push` on that same mapping correctly reassembles the directory into the Google Doc, preserving section order, and detects/report section-level add, delete, and reorder — not just line-level diffs within one file.
- Round-trip fixpoint holds: pull → push with no edits produces no diff on the Google Doc (matches the existing single-file pull/push guarantee, extended across the split).
- Existing (non-sectioned) mappings are completely unaffected — this is opt-in per mapping.

## Appetite
Large (3–6 weeks)
*(Scope must fit the appetite. If it doesn't fit, cut scope — do not move the deadline.)*

## Constraints
- Must not change behavior for existing markgate.yaml mappings that don't opt into sectioned mode — this is an additive, opt-in mode, not a replacement of the default pull/push path.
- Must reuse the existing structural parser/converter (`DocsStructureParser`, `render_nodes_to_markdown`, `project()`) rather than building a second Google Docs → markdown pipeline — the split logic operates on the same node tree push() already uses.
- No new external dependencies or services; this is a CLI feature, not a hosted service. No new observability stack — docspan has no metrics/alerting today.

## Non-functional Requirements
- **Performance SLO**: not specified — this is a CLI tool invoked interactively/in CI, not a latency-sensitive service.
- **Scalability**: should handle docs with dozens of top-level sections and mid-size documents (the motivating case was ~180 paragraphs / 22KB of text); no requirement beyond what a single Google Doc can realistically contain.
- **Security classification**: internal — same trust boundary as existing docspan pull/push (relies on the user's own Google OAuth credentials).
- **Data residency**: no special requirements.

## Scope
### In Scope
- A new opt-in sectioned mode for Google Docs mappings in markgate.yaml, configurable heading level to split on (e.g. `split_level: 1` vs `2`).
- Pull: split the doc into a directory of markdown files at the configured heading level, one file per section, preserving each section's subheadings/content as-is within its file.
- A manifest (mechanism TBD in planning) recording section identity and order, needed so push can detect inserted/deleted/reordered sections rather than only diffing file contents.
- Push: reassemble the directory (in manifest/declared order) back into the Google Doc body, reusing the existing three-way-merge/structural push path.
- Comment sidecar behavior extended to work per-section (today it's per-file; needs a decision on whether it becomes per-section-file or stays doc-level).
- Tests covering: split correctness, round-trip fixpoint (pull→push with no edits = no diff), section add/delete/reorder detection.

### Out of Scope
- The Confluence backend — this project targets Google Docs only.
- Changing or wrapping the separate `google-docs-plugin` skill — that repo is out of scope for this project; a future project can point it at docspan once this exists.
- Migrating existing single-file mappings automatically to sectioned mode — users opt in explicitly per mapping.
- Sub-section-level (below the configured split heading) file splitting — one file per section at the configured level is the unit; deeper nesting stays as markdown content within that file.
- A UI or interactive conflict-resolution tool — conflict *detection* is in scope, resolution stays manual (edit the file, re-push), matching today's single-file behavior.

## Rabbit Holes
- **Section identity across renames**: if a user renames a section heading between pull and push, is that an edit to the existing section or a delete+insert? Needs an explicit identity strategy (e.g. stable ID in the manifest) or it will misfire on the most common edit (retitling a section).
- **Images, tables, and cross-section links that span or reference across the split boundary**: e.g. a heading anchor link from one section to another must still resolve once each section is its own file.
- **Comment sidecars**: today one sidecar file maps 1:1 to one markdown file; splitting into N files means N sidecars or a redesigned mapping — could balloon scope if not bounded early.
- **Tab-scoped docs** (`tab_id` pull/push path) interacting with sectioned mode — two structural pull paths already exist (default HTML-export path vs. tab-scoped structural path); sectioned mode needs to pick one consistently rather than supporting both from day one.

## Alternatives Considered
- Keep pulling as one flat file and instead build a *read-only* section-extraction command (no push/reassembly) — rejected per this project's scope decision to support both pull and push, since the motivating pain point (editing one section of a large doc) needs push too.
- Build the splitting logic into the separate google-docs-plugin skill instead of docspan — rejected because docspan already owns the structural parser/converter and the three-way-merge push path; duplicating that in the skill would fork the conversion logic.

## Feasibility Risks
- No existing "N files ↔ 1 external document" sync model in docspan today — the manifest/identity design is new territory, not an extension of an existing pattern.
- Google's Drive HTML export (used by the default pull path) can't be scoped to a heading range, so sectioned pull likely must go through the structural path (`DocsStructureParser` + `project()`) always, not just for `tab_id`-scoped pulls — needs confirming in research/planning.
- Push reassembly must produce a batchUpdate that Google Docs accepts as a coherent single document body; getting section ordering and formatting-preservation right across a full rewrite (vs. today's targeted diff-based push) is the highest-risk part of this project.

## Observability Requirements
Standard docspan `PullResult`/`PushResult` status reporting (`ok`/`warning`/`error`) is sufficient — extend it to report section-level ambiguities (e.g. "section renamed and reordered, treated as X" ) the same way existing residue warnings surface today. No metrics or alerting needed; docspan is a CLI tool with no running service to instrument.

## Risk Control
Opt-in via markgate.yaml config field (e.g. `mode: sectioned` or `split_level: N` presence) — default behavior for all existing mappings is completely unchanged. No feature flag infrastructure needed since this is a CLI tool: the config field itself is the flag. Rollback is trivial — remove the config field and the mapping reverts to single-file pull/push.

## Open Questions
- What identifies a "section" stably across edits — a stored heading-anchor ID, a manifest with ordinal + heading text, or something else? (For Phase 2/3 research/planning.)
- Does the comment sidecar become per-section-file, or stay as one doc-level sidecar keyed by section? 
- Does sectioned pull always use the structural parser path (bypassing Drive's HTML export entirely), even for non-tab-scoped docs?
- File/directory naming convention (e.g. `NN-slug.md`) and where the manifest lives (a sidecar file vs. embedded front-matter per section file).
