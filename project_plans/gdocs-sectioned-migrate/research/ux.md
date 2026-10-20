# UX Research: `migrate-sectioned` CLI output

Agent 5 (UX), Phase 2 Research, project `gdocs-sectioned-migrate`.

## Existing conventions (from `src/docspan/cli/main.py`)

- `Console()` / `Console(stderr=True, style="bold red")` (`console` / `err_console`), Rich markup mode, not bare `typer.echo`.
- Status icon/style pairs centralized in `STATUS_DISPLAY` (`main.py:66-71`): `ok`/`skipped` → `✓` green, `warning` → `⚠` yellow, default (`error`/`blocked`/`conflict`) → `✗` red. `_status_display()` looks these up. Any new outcome type should reuse this table rather than inventing new colors.
- Line shape for a completed action: `[{style}]{icon}[/{style}]  {source} → {dest}` (push: `main.py:399`; pull first-sync/fast-forward: `main.py:505`), with an optional indented `[dim]` detail line below (`   [dim]{escape(message)}[/dim]`, `main.py:404,492,507`). `escape()` is required on any interpolated content that might contain literal `[`/`]` (checklist markers, user text) so Rich markup doesn't swallow it.
- `--dry-run` today is a single yellow-tagged preview line, no table: `[yellow]dry-run[/yellow]  {mapping.local} → [{mapping.backend}] {mapping.remote_id}` (push, `main.py:311-313`) and `[yellow]dry-run[/yellow]  [{mapping.backend}] {mapping.remote_id} → {mapping.local}` (pull, `main.py:465-467`). Where a backend has a richer preview (`preview_push`), it's rendered as pre-formatted text via `console.print(escape(preview.render()))`, not a Rich `Table`.
- `Table` (`rich.table.Table`) *is* used elsewhere for multi-row structured listings: `docspan status` (`main.py:646-655`, columns `Local file`/`Backend`/`Remote ID`/`Direction`), `config list` (mappings table), and `conflicts` (`main.py:896`, "Files with merge conflicts"). Convention: `Table(title="...")`, `table.add_column(name, style=...)` with `cyan`/`magenta` for identifying columns, plain for data columns.
- Special outcome states get bespoke lines, not the generic icon table: `up-to-date` → `[dim]up to date[/dim]  {mapping.local}` (`main.py:474`); `local-only` (dirty-working-tree-style guard for pull) → `[yellow]warning[/yellow]  {mapping.local} has local changes not yet pushed. Pull skipped. Push first or use 'docspan conflicts resolve'.` (`main.py:475-479`) — this is the direct precedent for the migration safety gate's dirty-tree message; `merged` → `[yellow]merging[/yellow]  {mapping.local}` plus a conflict-count sub-line or `[green]Merged cleanly.[/green]`.
- Errors that abort the whole mapping go to `err_console` with a bare `✗` prefix (not through `_status_display`), e.g. `err_console.print(f"✗  {mapping.remote_id} → {mapping.local}: {message}")` (`main.py:496-499`).
- A machine-parseable trailer line is printed unconditionally, no color/glyphs, meant for CI: `STYLE_UPGRADE_COUNT={n}` (`main.py:415`). This is the precedent for a rollup line on `migrate-sectioned`.
- Exit code: `had_error` flag accumulated across mappings, `typer.Exit(1)` once at the end (never mid-loop) — same shape should hold for migration's per-mapping loop (a `pull --to-sectioned` run over multiple mappings shouldn't abort mapping 2 because mapping 1's migration failed; it should report and continue, matching push/pull's existing behavior).

## Resolution of Open Question #3

**Recommendation: hybrid — match the existing line-based idiom for the one-line-per-outcome parts, but use a Rich `Table` for the dry-run section-boundary preview.** A single dry-run line (the current `pull`/`push` convention) cannot show N proposed section boundaries; forcing it into a stack of yellow lines would be a worse fit than a table, and `status`/`config list`/`conflicts` already establish `Table` as the codebase's tool for "N rows of structured facts." The command's *non-preview* lines (header, per-mapping icon line, warnings, final rollup) still follow the existing icon/style/escape conventions exactly, so `migrate-sectioned` doesn't feel like a foreign command bolted onto the CLI. This is not a wholesale new shape — it reuses `STATUS_DISPLAY`, `[dim]` sub-lines, `escape()`, and the unconditional rollup-line pattern; only the *dry-run preview body* gets a shape existing dry-run doesn't have, because existing dry-run never needed to show multiple rows before.

## 1. `docspan migrate-sectioned <mapping>` — success output

```
$ docspan migrate-sectioned handbook.md
[green]✓[/green]  handbook.md → sections/handbook/ (4 sections)

   01-introduction.md       "Introduction"              38 lines   612 words
   02-getting-started.md    "Getting Started"           95 lines  1,480 words
   03-configuration.md      "Configuration"             61 lines    902 words
   04-appendix.md            "Appendix"                  22 lines    340 words

   _manifest.yaml written · markgate.yaml updated (sectioned: true, split_level: h2)
   SyncState updated for 4 sections
MIGRATED_SECTIONS=4
```

Rendered form (colors elided): header line uses the existing `[green]✓[/green]  {source} → {dest}` shape (`main.py:399/505`), with a `(N sections)` suffix instead of a bare path. The per-section rows are plain indented text (not a Table — see below) so they read as an extension of the `[dim]` detail-line convention (`main.py:404`), not a second competing table style for a single successful run. Two `[dim]` summary lines follow (manifest/config write confirmation, SyncState confirmation) so the user can see the three moving parts (files, config, state) were all updated without opening any of them. A final unconditional rollup line `MIGRATED_SECTIONS={n}` mirrors `STYLE_UPGRADE_COUNT={n}` (`main.py:415`) for CI/scripting.

Rationale for *not* using a `Table` here: the actual-write summary is a confirmation of something already decided (the dry-run table, below, is where the decision happens) — a lighter plain-text list keeps the "the work is done" moment terse, consistent with how `push`/`pull` report real (non-preview) outcomes as one line + optional dim detail, not a table.

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

Table columns mirror the `status` command's `Table(title=...)` + `add_column(style=...)` convention (`main.py:646-650`); `Target file` styled `cyan` (identifier column, matching `Local file` in `status`), `Heading` plain, `Lines`/`Words` right-aligned plain. Heading text goes through `escape()` since arbitrary doc headings can contain `[`/`]`.

**Warning case — no headings found at `split_level`:**

```
$ docspan migrate-sectioned handbook.md --dry-run
[yellow]dry-run[/yellow]  handbook.md → sections/handbook/ (would split into 1 section at split_level=h2)

[yellow]⚠[/yellow]  No h2 headings found in handbook.md — the entire file would become
   a single "01-preamble.md" section. This is probably not what you want;
   pass --split-level h1 (or another level) to choose a different boundary,
   or check the document for a level mismatch.

┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳═══════════┓
┃ Target file           ┃ Heading                 ┃  Lines  ┃  Words    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇═══════════┩
│ 01-preamble.md        │ (no heading — preamble) │    216  │    3,102  │
└───────────────────────┴─────────────────────────┴─────────┴───────────┘
```

`(no heading — preamble)` surfaces the `PREAMBLE_HEADING_ID` sentinel (`manifest.py`) in human terms rather than leaking the internal sentinel string. This warning uses `[yellow]⚠[/yellow]` matching `STATUS_DISPLAY["warning"]`, and does not set a non-zero exit code by itself in dry-run mode (dry-run never fails the run; it's advisory) — but the real (non-dry-run) migration should refuse or require `--force` in this same one-section-only case, since a single-section "split" defeats the purpose and the user likely picked the wrong `--split-level`. That refusal condition itself is a planning-phase decision, not a UX-only one — flagging it here as input to Phase 3.

## 3. Dirty-working-tree safety gate (error output)

Mirrors the existing `local-only` guard pattern in `pull` (`main.py:475-479`) but as a hard failure, not a skip, since migration cannot proceed at all without a clean base:

```
$ docspan migrate-sectioned handbook.md
✗  handbook.md has uncommitted changes — migrate-sectioned refuses to split a
   dirty working tree, since the split must operate on a known-good, versioned
   state (see git status). Commit or stash your changes, then re-run:

     git status handbook.md
     git add handbook.md && git commit -m "..."
     docspan migrate-sectioned handbook.md
```

Sent to `err_console` (matching the `err_console.print(f"✗  ...")` pattern at `main.py:496-499`), exits 1. The three-line remediation snippet is included because this is a *refusal* the user must act on before retrying — unlike `local-only` on pull (which is routine and self-explanatory), this is the one gate most likely to surprise a first-time migration user, so it earns the extra guidance lines the existing `local-only` message doesn't need.

## 4. Rollback-on-failure output

The key UX requirement (from Success Metrics / Risk Control): the user must walk away confident *nothing was lost*, not just that something failed.

```
$ docspan migrate-sectioned handbook.md
[yellow]migrating[/yellow]  handbook.md → sections/handbook/
✗  Migration failed while writing _manifest.yaml: [error detail]

   Rolling back... done.
   handbook.md is unchanged and markgate.yaml was not modified.
   (3 of 4 section files had been staged; all were removed during rollback.)

Nothing was written. Original file and config are exactly as they were before this run.
```

Design choices:
- `[yellow]migrating[/yellow]  {source} → {dest}` in-progress line before any table, matching the `[yellow]merging[/yellow]  {mapping.local}` shape used for pull's in-progress `merged` action (`main.py:481`) — gives the user a checkpoint if the process is later interrupted (e.g. Ctrl-C) between this line and the result line.
- The failure line goes to `err_console` with the same bare `✗` + colon-detail shape as `pull`'s hard-error path (`main.py:496-499`), so a failed migration reads like a failed pull, not a novel error class.
- The two `[dim]`-equivalent follow-up lines *name what was undone* (count of staged section files removed) rather than just asserting "rolled back" — this directly answers "what state am I in now," which is the actual anxiety a partial-failure message needs to resolve per the Risk Control section of requirements.md.
- Final unconditional confirmation sentence, un-styled, restates the invariant in plain language for anyone scanning output quickly. This is deliberately redundant with the `[dim]` lines above it — a failure message is exactly the place where the user is least likely to read carefully, so the important fact (nothing was lost) is stated twice, once inline and once as a clear closing sentence.
- Exit code 1.

If rollback *itself* fails (the harder case — e.g. filesystem error while removing a staged file), that must be visually distinct from a clean rollback, since it's the one case where local state might genuinely be inconsistent:

```
✗  Migration failed while writing _manifest.yaml: [error detail]
✗  Rollback FAILED: could not remove 02-getting-started.md: [error detail]

   Your working tree may be in an inconsistent state. Staged section files
   that may still be present: 02-getting-started.md, 03-configuration.md
   Compare against your last commit (git status / git diff) before continuing.
```

Two `✗` lines (not one softened into a warning) because this is strictly worse than a normal migration failure — silently treating it as a mere warning would misrepresent the risk.

## 5. `pull --to-sectioned` output

Folded into a normal pull run, so it must read as *one more outcome line in the loop*, not a separate sub-report. Non-dry-run success extends the existing pull per-mapping line rather than replacing it:

```
$ docspan pull handbook.md --to-sectioned
[green]✓[/green]  1AbC...xyz → sections/handbook/ (migrated to sectioned mode, 4 sections)
   Pulled 4 sections from Google Docs.
```

- Reuses the exact `[{style}]{icon}[/{style}]  {remote_id} → {dest}` shape from pull's first-sync/fast-forward branch (`main.py:505`), just substituting the destination directory for the destination file and appending a parenthetical instead of introducing a new line. This keeps a `pull --to-sectioned` run visually indistinguishable from a normal successful pull at a skim, which is the point of "folding it into the normal workflow" per the requirements' framing of the flag.
- The `[dim]` sub-line (`main.py:507`-style) reports the pull-proper outcome (content pulled into the new sections) separately from the migration-proper outcome (mentioned in the parenthetical above), since these are two logically distinct operations happening in one invocation and a user diagnosing a problem needs to know which one failed.
- `--dry-run` combined with `--to-sectioned` on `pull` should print the exact same section-boundary table as standalone `migrate-sectioned --dry-run` (section 2 above), inserted in place of pull's one-line dry-run preview for that mapping only — other mappings in the same `pull --dry-run` run keep the existing single-line preview. This is the one place the two commands' dry-run output is required to be pixel-identical, since a user who becomes comfortable reading `migrate-sectioned --dry-run` output should not have to re-learn a different table shape when the same preview appears inside `pull --dry-run`.
- Dirty-tree gate failure under `--to-sectioned`: reported as a per-mapping error (section 3's message, `err_console`) and that mapping is skipped, but the pull loop continues to the next mapping (`had_error = True`, no early `typer.Exit`) — consistent with how a `pull --all`-style run today never lets one mapping's failure stop the rest (`main.py:493-499` sets `had_error` and continues the `for` loop).
