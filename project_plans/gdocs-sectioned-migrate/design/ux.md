# UX Design: `gdocs-sectioned-migrate`

Source: `requirements.md`, `research/ux.md` (Agent 5, Phase 2). This document finalizes
the UX for implementation — surfaces, output samples, flows, and testable acceptance
criteria. No interactive prompts are in scope: every gate in this feature is a
hard refusal with a remediation path, not a y/n confirmation (confirmed against
requirements.md's Risk Control and Out of Scope sections — there is no "are you sure?"
prompt anywhere in this design).

## Surfaces inventory

All surfaces are CLI invocations / terminal output — no screens, no interactivity beyond
argument parsing:

1. `docspan migrate-sectioned <mapping>` — success output
2. `docspan migrate-sectioned <mapping> --dry-run` — preview table (+ zero-heading warning variant)
3. Dirty-working-tree refusal (error, non-dry-run)
4. Remote-drift-abort error (heading/section-count mismatch between local split and live Docs API fetch)
4.5. Migration-lock-held refusal (error) — per-mapping sentinel-lock file already exists
5. Rollback-on-partial-failure success (state fully restored)
6. Rollback-itself-fails (state possibly inconsistent — must look categorically worse than #5)
7. `docspan pull --to-sectioned` — folded success output, plus its own dirty-tree/drift error variants inside a multi-mapping loop
8. `MIGRATED_SECTIONS={n}` / other CI trailer lines (condensed, non-interactive)

Surfaces 3, 4, 4.5, 6 are refusals/failures and get the full error-state treatment. Surfaces
1, 2, 5, 7, 8 get output sample + flow + condensed acceptance criteria.

---

## 1. `migrate-sectioned` success output

```
$ docspan migrate-sectioned handbook.md
[yellow]migrating[/yellow]  handbook.md → sections/handbook/ (this will create a git commit)
[green]✓[/green]  handbook.md → sections/handbook/ (4 sections)

   01-introduction.md       "Introduction"              38 lines   612 words
   02-getting-started.md    "Getting Started"           95 lines  1,480 words
   03-configuration.md      "Configuration"             61 lines    902 words
   04-appendix.md            "Appendix"                  22 lines    340 words

   _manifest.yaml written · markgate.yaml updated (sectioned: true, split_level: h2)
   SyncState updated for 4 sections
   Section identity will stabilize after your next pull.
   git history preserved — verify with:
     git log --follow -C20% sections/handbook/01-introduction.md
     git blame -C20% -C20% -C20% sections/handbook/01-introduction.md
MIGRATED_SECTIONS=4
```

**Flow:** user runs the command against a clean, non-sectioned mapping → tool prints the
`[yellow]migrating[/yellow]` commit-creation notice (before any write) → does one
live Docs API fetch (to harvest real `heading_id`s) → splits local file → writes section
files + `_manifest.yaml` → updates `markgate.yaml` and `SyncState`, landing a git commit
→ prints the summary above → exits 0.

**Acceptance criteria:**
- User completes the migration in 1 command, 0 manual `markgate.yaml` edits.
- Output names all four artifacts touched (section files, `_manifest.yaml`, `markgate.yaml`, `SyncState`) — no silent side effect.
- The git-history-verification commands are printed literally (copy-pasteable), not just described, per the Success Metrics revision in requirements.md — the exact `-C20%` (`log`) / triple `-C20%` (`blame`) invocations verified in `research/stack.md`'s spike, not the earlier-considered `--find-copies-harder`/`-C -C` flags, which were superseded (default `git log --follow`/`blame` won't show continuity for 3+-way splits; the non-default invocation must be handed to the user).
- The success output explicitly tells the user section identity will stabilize after their next pull, mitigating the placeholder-`heading_id` limitation (plan.md Epic 2 Risks/Mitigations).
- `MIGRATED_SECTIONS={n}` trailer is always printed on success, matching the `STYLE_UPGRADE_COUNT={n}` CI-signal precedent (`main.py:415`), for scripting/CI consumption.
- Reuses `STATUS_DISPLAY["ok"]` (`✓`, green) — no new color introduced.

---

## 2. `--dry-run` output

```
$ docspan migrate-sectioned handbook.md --dry-run
[yellow]dry-run[/yellow]  handbook.md → sections/handbook/ (would split into 4 sections at split_level=h2)

┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳═══════════┓
┃ Target file             ┃ Heading          ┃  Lines  ┃  Words    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇═══════════┩
│ 01-introduction.md      │ Introduction     │     38  │      612  │
│ 02-getting-started.md   │ Getting Started  │     95  │    1,480  │
│ 03-configuration.md     │ Configuration    │     61  │      902  │
│ 04-appendix.md          │ Appendix         │     22  │      340  │
└─────────────────────────┴──────────────────┴─────────┴───────────┘

Nothing written. Run without --dry-run to apply, or add --split-level to change the boundary.
```

**Zero-heading warning variant** (single-section split — likely a wrong `--split-level`):

```
[yellow]⚠[/yellow]  No h2 headings found in handbook.md — the entire file would become
   a single "01-preamble.md" section. This is probably not what you want;
   pass --split-level h1 (or another level) to choose a different boundary.
```
followed by the same table shape, one row, heading column reading `(no heading — preamble)`.

**Flow:** user runs with `--dry-run` before committing to the real migration → tool runs
the same split logic but writes nothing → prints the boundary table → exits 0 (dry-run
never fails the run, even in the zero-heading case).

**Acceptance criteria:**
- Table uses the `status`/`config list` `Table(title=...)` convention (`main.py:646-655`) — `Target file` styled `cyan`, `Heading` plain and `escape()`-wrapped (headings are untrusted text that may contain `[`/`]`), `Lines`/`Words` right-aligned.
- Dry-run never writes a file, never touches `markgate.yaml`/`SyncState`, and always exits 0 — verified by running twice in a row and diffing `git status` before/after.
- The zero-heading case is visually distinguished (`⚠` warning line above the table) from the normal N>1 case, so a user scanning output does not mistake "degenerate split" for "expected small split."
- `docspan migrate-sectioned <mapping> --dry-run` and `docspan pull <mapping> --to-sectioned --dry-run` render **byte-identical** table output for the same mapping (cross-surface consistency requirement from research/ux.md §5).

---

## 3. Dirty-working-tree refusal (error)

```
$ docspan migrate-sectioned handbook.md
✗  handbook.md has uncommitted changes — migrate-sectioned refuses to split a
   dirty working tree, since the split must operate on a known-good, versioned
   state (see git status). Commit or stash your changes, then re-run:

     git status handbook.md
     git add handbook.md && git commit -m "..."
     docspan migrate-sectioned handbook.md
```

Sent to `err_console`, exit 1. Mirrors the `local-only` pull guard (`main.py:475-479`) but
as a hard failure (not a skip) with an added three-line remediation snippet, since this
is the gate most likely to surprise a first-time user.

## 4. Remote-drift-abort error

Triggered when the live Docs API fetch's section count/titles don't match the local
markdown's own split (resolved Open Question in requirements.md — this is a distinct
check from the dirty-tree gate, since a clean git tree can still be stale relative to
the remote doc).

```
$ docspan migrate-sectioned handbook.md
✗  handbook.md: remote has diverged from your local copy — the live Google Doc
   splits into 5 sections (by heading), but your local file only has 4. Migrating
   now would produce section files that don't line up with the doc's real
   heading IDs.

   Pull the latest content first, then retry:

     docspan pull handbook.md
     docspan migrate-sectioned handbook.md
```

Sent to `err_console`, exit 1. No files are written before this check fails, so there is
nothing to roll back — the check runs before any section file is created.

## 4.5. Migration-lock-held refusal (error)

Triggered when the per-mapping sentinel-lock file (`.docspan-migration-{mapping_key}.lock`,
plan.md Task 4.5) already exists — another migration for this mapping is mid-flight (or a
prior run crashed without releasing it). Checked before any file, git, or config state is
touched, so this refusal is the very first thing that can happen.

```
$ docspan migrate-sectioned handbook.md
✗  handbook.md: migration already in progress for this mapping — a
   .docspan-migration-handbook.lock file already exists.

   Wait for the other migration to finish, then retry. If you're sure no
   migration is actually running, it may be a stale lock left behind by a
   crashed run — check for and remove it, then retry:

     ls -la .docspan-migration-handbook.lock
     rm .docspan-migration-handbook.lock
     docspan migrate-sectioned handbook.md
```

Sent to `err_console`, exit 1. No files are written and no state is mutated before this
check fails — same "nothing to roll back" property as the remote-drift-abort error.

## 6. Rollback-itself-fails (worst case)

```
[yellow]migrating[/yellow]  handbook.md → sections/handbook/
✗  Migration failed while writing _manifest.yaml: [error detail]
✗  Rollback FAILED: could not remove 02-getting-started.md: [error detail]

   Your working tree may be in an inconsistent state. Staged section files
   that may still be present: 02-getting-started.md, 03-configuration.md
   Compare against your last commit before continuing:

     git status
     git diff -- sections/handbook/
     git checkout -- handbook.md sections/handbook/   # discard partial migration
```

Two `✗` lines (never softened to one `✗` + warning) because this is strictly worse than
a clean rollback — the closing block gives an explicit escape command (`git checkout --`)
rather than only diagnostic commands, so the user has a concrete way out even in the
worst case.

### Error-state table

| Surface | Trigger | Message shown | User's next action | Exit code |
|---|---|---|---|---|
| Dirty tree | `git status` shows uncommitted changes to the mapping's local file | `✗ ... has uncommitted changes ...` + 3-line snippet | `git add && git commit`, then re-run same command | 1 |
| Remote drift | Live-fetch section count/titles ≠ local split | `✗ ... remote has diverged ...` + section counts | `docspan pull`, then re-run same command | 1 |
| Migration lock held | `.docspan-migration-{mapping_key}.lock` already exists for this mapping | `✗ ... migration already in progress for this mapping ...` + wait/stale-lock remediation | Wait for the other migration, or remove the lock file if stale and confirmed no migration is running, then re-run same command | 1 |
| Zero-heading split (dry-run) | No headings found at `--split-level` | `⚠ No {level} headings found ...` | Re-run with a different `--split-level`, or accept the single-section result | 0 (dry-run never fails) |
| Non-sectioned-capable backend | Mapping backend in `_SECTIONED_UNSUPPORTED_BACKENDS` | `✗ ... does not support sectioned mode` (existing validator message pattern) | Nothing to do — migration is not applicable to this mapping | 1 |
| Already-sectioned mapping | Mapping already has `sectioned: true` | `✗ ... is already sectioned — nothing to migrate` | Nothing to do | 1 |
| Partial-write failure, rollback succeeds | e.g. manifest write fails after N section files staged | `✗ Migration failed ... / Rolling back... done.` + "nothing was lost" closing line | Fix the underlying cause (disk space, permissions), re-run | 1 |
| Partial-write failure, rollback itself fails | Filesystem error during rollback cleanup | Two `✗` lines + explicit `git checkout --` escape command | Run the printed `git status`/`git diff`/`git checkout --` sequence manually | 1 |

Every row has a concrete next command or an explicit "nothing to do" — no dead ends.

---

## 5. Rollback success (partial failure, fully recovered)

```
[yellow]migrating[/yellow]  handbook.md → sections/handbook/
✗  Migration failed while writing _manifest.yaml: [error detail]

   Rolling back... done.
   handbook.md is unchanged and markgate.yaml was not modified.
   (3 of 4 section files had been staged; all were removed during rollback.)

Nothing was written. Original file and config are exactly as they were before this run.
```

**Flow:** failure occurs mid-migration → tool unwinds staged section files → prints what
was undone (count) → prints the invariant twice (once as a `[dim]`-equivalent detail
line, once as a plain closing sentence) since a failure message is the output a user is
least likely to read carefully.

**Acceptance criteria:**
- The count of undone files is stated explicitly, not just "rolled back" — answers "what state am I in now."
- The "nothing was lost" invariant appears twice (inline + closing sentence).
- Exit code 1 even though rollback succeeded — the *migration* failed; success of the safety net doesn't change that.

---

## 7. `pull --to-sectioned` folded output

```
$ docspan pull handbook.md --to-sectioned
[yellow]migrating[/yellow]  handbook.md → sections/handbook/ (this will create a git commit)
[green]✓[/green]  1AbC...xyz → sections/handbook/ (migrated to sectioned mode, 4 sections)
   Pulled 4 sections from Google Docs.
```

**Flow:** user runs normal `pull` with the flag → tool prints the same
`[yellow]migrating[/yellow]` commit-creation notice as the standalone command (Task 5.3
print-parity) before invoking `migrate_sectioned` → performs the migration inline
before/as part of the pull → the pull's own per-mapping icon line gets a parenthetical
noting the migration, with the pull-proper result kept on its own `[dim]` sub-line so
a diagnosing user knows which of the two operations (migrate vs. pull) failed if
something goes wrong.

**Acceptance criteria:**
- Visually indistinguishable from a normal successful pull at a skim (same icon/line shape) — the point of "folding it in."
- In a multi-mapping `pull` run, one mapping's dirty-tree/drift failure under `--to-sectioned` is reported per-mapping (`err_console`) and that mapping is skipped, while the loop continues to the remaining mappings (`had_error = True`, no early `typer.Exit`) — matches existing `pull --all` behavior where one mapping's failure never aborts the rest.
- `pull --to-sectioned --dry-run` renders the identical table from surface 2, not a separate shape.

---

## 8. CI trailer lines (condensed)

```
MIGRATED_SECTIONS=4
```

- Printed unconditionally on success, once per successful migration, no color/glyphs — same shape as `STYLE_UPGRADE_COUNT={n}` (`main.py:415`).
- Not printed on dry-run or on failure (nothing was migrated in either case).
- Acceptance: `grep MIGRATED_SECTIONS= output.log` reliably detects a successful migration in CI without parsing colored text.

---

## Cross-cutting UX acceptance criteria

1. **Command count**: a user with an existing single-file mapping reaches a working sectioned mapping in exactly 1 command (`migrate-sectioned <mapping>`) or 0 extra commands if folded into their normal workflow (`pull <mapping> --to-sectioned`) — matches the Success Metrics "one command" bar.
2. **No dead ends**: every error/refusal state in the error-state table above names either a next command to run or an explicit "not applicable, nothing to do" — audited row-by-row above.
3. **Color/icon reuse**: no new status colors or icons are introduced; every surface maps to an existing `STATUS_DISPLAY` entry (`ok`→`✓` green, `warning`→`⚠` yellow, default/error→`✗` red) or the unstyled dry-run/rollup conventions. A human reviewer can confirm this by diffing the design doc's icons against `STATUS_DISPLAY` in `src/docspan/cli/main.py:66-71`.
4. **Escape hygiene**: any interpolated text from the document (headings, error details) is wrapped in `escape()` before being placed in a Rich-markup string, consistent with existing `push`/`pull` lines — testable by feeding a heading containing literal `[bracket]` text through `--dry-run` and confirming it renders literally rather than being swallowed as markup.
5. **Dry-run is inert**: `--dry-run` on either surface never writes a file, never modifies `markgate.yaml`/`SyncState`, and never sets a non-zero exit code by itself (including the zero-heading warning case) — testable via `git status --porcelain` before/after.
6. **Cross-surface consistency**: `migrate-sectioned --dry-run` and `pull --to-sectioned --dry-run` produce byte-identical table output for the same mapping — testable with a direct diff of captured output.
7. **Rollback failure is visually worse than rollback success**: two `✗` lines and an explicit `git checkout --` escape hatch, vs. one `✗` + `Rolling back... done.` — testable by triggering both paths in a test harness and asserting the line count/exit path differs.
8. **Multi-mapping resilience**: a `pull --to-sectioned` run over N mappings where one mapping's migration fails still processes the remaining N-1 mappings and reports a per-mapping error rather than aborting — testable with a fixture of 2+ mappings, one deliberately dirty.

---

## Accessibility

N/A for this feature. All eight surfaces are CLI stdout/stderr text — there is no new
screen, widget, or interaction modality, so standard screen-reader/terminal-emulator
accessibility already applies unchanged. Two specific things were checked, not just
assumed:

- **Color is never the sole signal.** Every colored token in the samples above is paired
  with literal text carrying the same information: `[yellow]migrating[/yellow]` (word
  "migrating"), `[yellow]dry-run[/yellow]` (word "dry-run"), `[green]✓[/green]`/`✗`/`⚠`
  (glyphs, not just color, and always followed by a plain-text message). A user with no
  color perception loses nothing by reading the text alone.
- **No new interaction modality.** This feature reuses docspan's existing Rich console
  conventions (`STATUS_DISPLAY`, `err_console`, `Table`) verbatim — criterion 3 above —
  rather than introducing a new one, so it inherits whatever terminal/screen-reader
  behavior those conventions already have across the rest of the CLI.
