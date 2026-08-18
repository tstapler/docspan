# Requirements: gdocs-sectioned-migrate

**Date**: 2026-08-18
**Type**: feature addition
**Complexity**: 2 — focused feature

## Problem Statement
`gdocs-sectioned-sync` (shipped 2026-08-14, PR #106) lets a Google Docs mapping opt into sectioned mode (`sectioned: true` + `split_level`), pulling a large doc into a directory of per-section markdown files instead of one flat file. But that project explicitly scoped out migration: "users opt in explicitly per mapping" meant a user who wants to convert an *existing* single-file mapping has no supported path — they must hand-edit `markgate.yaml` and then figure out on their own how to reconcile the pre-existing local markdown file with the new directory-based layout `pull_sectioned` expects.

## Baseline
Today, converting an existing single-file mapping to sectioned mode requires the user to: (1) manually flip `sectioned: true` and set `split_level` in markgate.yaml, (2) manually delete or move the existing local markdown file out of the way, (3) run `docspan pull` and hope the structural-parser path (`DocsStructureParser` + `project()` + `split_nodes`) produces a directory that supersedes it correctly, with no guidance on state-file (`SyncState`) reconciliation, no dry-run preview, and no preservation of file history for the resulting per-section files.

## Users / Consumers
Docspan users (individuals/teams syncing Google Docs to markdown) who have an existing non-sectioned mapping for a doc that has grown large enough to want sectioned mode — the same audience `gdocs-sectioned-sync` targeted, now needing an upgrade path rather than only greenfield opt-in.

## Success Metrics
A user with an existing single-file mapping runs one command and ends up with a working sectioned mapping — correct `_manifest.yaml`, correct per-section files, updated `markgate.yaml`, and updated `SyncState` — without manually deleting files or reasoning about the structural parser. Zero manual `markgate.yaml` hand-editing required for the split itself (config field updates are made by the tool, not the user). `git log --follow -C --find-copies-harder` / `git blame -C -C` on any resulting section file resolves to the original commit history of that content (git-history preservation) — **revised during research**: git's default rename/copy similarity detection is diff-time, line-overlap-based, and thresholded at 50%, so a 1-file-to-N≥3 split cannot show continuity under *default* `git log --follow`/`git blame` flags regardless of how the split is performed (spike-verified in `research/stack.md`); the achievable bar is byte-identical section content plus non-default detection flags, and the command's output must tell the user which flags to use. **Further revision (adversarial review, Phase 4)**: the flags named above (`--find-copies-harder` / `-C -C`) are themselves superseded by `research/stack.md`'s spike — the actual verified and shipped flags are `git log --follow -C20% <file>` and `git blame -C20% -C20% -C20% <file>` (three copies for blame, no `--find-copies-harder`); treat `research/stack.md`'s spike as the source of truth for the exact flags, not the wording above.

## Appetite
Medium (1–2 weeks)
*(Adds safety rails: dry-run preview, git-history preservation via `git mv`-based extraction, rollback on partial failure — beyond the Small/bare-minimum version.)*

## Constraints
- Must reuse the existing structural parser/converter and `split_nodes`/manifest logic from `gdocs-sectioned-sync` rather than building a second splitting pipeline (same constraint the original project had).
- Must not change behavior for mappings that don't invoke migration — this is an explicit, opt-in action per mapping, same as `sectioned: true` itself was opt-in.
- Backends without sectioned support (currently Confluence — see `_SECTIONED_UNSUPPORTED_BACKENDS`) must be rejected by the migration command, matching the existing `Mapping` validator's rejection of `sectioned: true` on those backends.

## Non-functional Requirements
- **Performance SLO**: not specified — migration is a one-off, human-triggered operation, not a hot path.
- **Scalability**: must handle documents large enough to have motivated sectioned mode in the first place (the same large-doc case `gdocs-sectioned-sync` targeted).
- **Security classification**: internal (local CLI tool, local filesystem + user's own Google Docs credentials).
- **Data residency**: not applicable.

## Scope

### In Scope
- A `docspan migrate-sectioned <mapping>` CLI command that: validates the mapping is currently non-sectioned and on a sectioned-capable backend, requires a clean git working tree for the mapping's local file (see Risk Control), splits the existing local markdown content using the existing `split_nodes`/heading-level logic, writes the resulting section files + `_manifest.yaml` using `git mv`-style extraction so each section file's history traces back through the original file's commits, updates `markgate.yaml` (`sectioned: true`, `split_level`) and `SyncState` accordingly, and removes the superseded single file.
- A `--to-sectioned` flag on `docspan pull` that invokes the same underlying migration logic inline as part of a pull run, for users who'd rather fold it into their normal workflow than run a separate command.
- A `--dry-run` mode (on both surfaces) that reports the section boundaries and target filenames it would produce, without writing anything.
- Rollback on partial failure: if the migration fails partway (e.g. manifest write fails after some section files were created), the local file layout is restored to its pre-migration state.

### Out of Scope
- Automatic/implicit migration triggered merely by editing `sectioned: true` in markgate.yaml with no explicit command/flag invocation (rejected in the earlier UX question — both an explicit command and a pull flag are in scope, but not a silent auto-detect path).
- Migrating *away* from sectioned mode back to a single file (reverse migration) — not requested, not addressed here.
- Confluence or any other non-sectioned-capable backend — migration is rejected for those, consistent with existing `sectioned: true` validation.
- Splitting local edits that diverge from the last-synced remote content — the safety gate refuses migration on a dirty working tree rather than attempting to reconcile.

## Rabbit Holes
- **Git-history preservation mechanics**: extracting N section files out of one file while preserving `git blame`/`git log --follow` continuity is not a single `git mv` — it likely needs per-section content written to the new path in a way `git`'s rename/copy detection can follow (e.g. writing each section's exact original text unchanged into its new file so git's similarity heuristic attributes history), which is different from writing freshly re-rendered markdown that could shift whitespace/formatting enough to break the heuristic. Needs explicit resolution in planning.
- **SyncState reconciliation**: `SyncState` currently tracks one file's hash/mtime per mapping; sectioned mode's `pull_sectioned` presumably already defines a per-section state shape (from `gdocs-sectioned-sync`) — migration must produce state that looks identical to what a fresh `pull_sectioned` would have produced, not a parallel/divergent shape. Needs verification against the existing sectioned-sync state code, not assumed.
- **Rollback atomicity**: "restore pre-migration state on partial failure" implies either a staging-directory-then-atomic-rename strategy or a transaction log of created files to unwind — needs a concrete mechanism decided in planning, not hand-waved.

## Alternatives Considered
- Bare-minimum (Small appetite) version with no dry-run, no git-history preservation, and a required clean tree only — rejected in favor of Medium appetite once the user chose to include history preservation and rollback safety.
- Automatic migration on next `pull` after a bare `sectioned: true` config edit, with no dedicated command/flag — rejected; user asked for the explicit command *and* pull flag instead.

## Feasibility Risks
- Google's Drive HTML export (the default pull path) can't be scoped to a heading range — `gdocs-sectioned-sync`'s research already established sectioned pull must go through the structural parser path; migration's split must operate on the *local* file's existing markdown rather than re-fetching from Google, so this risk may not apply directly, but needs confirming that local-markdown splitting doesn't require re-deriving `heading_id`s that only the Docs API can assign (see `PREAMBLE_HEADING_ID`/`heading_id` design in `manifest.py`) — if migration can't obtain real `heading_id`s without a live pull, the manifest identity model may need a fallback for migrated sections.
- Git-history-preserving extraction (see Rabbit Holes) may prove significantly harder than a straightforward split if git's rename detection doesn't cooperate with the way sections get sliced out.

## Observability Requirements
*(complexity ≥ 3 only — not required at complexity 2, but the command should still print a clear per-section summary of what was created, consistent with existing `docspan pull`/`push` output conventions.)*

## Risk Control
Feature is invoked explicitly per mapping (no automatic trigger), matching the opt-in model of `gdocs-sectioned-sync` itself. Safety gate: migration refuses to run if the mapping's local file has uncommitted edits relative to git HEAD (mirrors the existing `pull` "local-only" guard pattern), forcing the user to commit or push first so the split is always performed against a known-good, versioned state. Rollback on partial failure (see Scope) is the primary in-command risk control; beyond that, `git revert` of the migration commit is the standing rollback path since the change is fully committed to version control.

## Open Questions — resolved in research (Phase 2)
- **Real `heading_id`s**: resolved — `heading_id` is only ever assigned by `DocsStructureParser` from a live Docs API fetch (`docs_structure_parser.py:712`); the local-markdown parser never sets it. Migration must perform one live API fetch of the current remote doc, split it structurally to harvest real `heading_id`s, and zip those by position against a separate split of the local markdown's own byte-faithful content — aborting with a "remote diverged, pull first" error if section counts/titles don't line up. This is a new drift-detection check distinct from the git dirty-tree gate. See `research/features.md` and `research/architecture.md`.
- **Git-history-preservation mechanism**: resolved via spike (see `research/stack.md`) — plain `git mv` has no effect; history continuity is possible only by writing byte-identical original content into each new section path and committing once, and even then git's default 50%-similarity threshold won't surface it for 3+-way splits under default flags. Success Metrics above revised accordingly to require non-default git flags, with the command printing the exact invocation to the user.
- **`--dry-run` output format**: resolved — hybrid convention (see `research/ux.md`): a Rich `Table` for the multi-section boundary preview (matching `status`'s table style, since no existing dry-run today needs to represent N proposed items), all other lines (headers, results, errors, rollback messaging) reuse `pull`/`push`'s existing line conventions.
