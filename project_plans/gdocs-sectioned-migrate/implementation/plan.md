# Implementation Plan: gdocs-sectioned-migrate

Extends `gdocs-sectioned-sync` (PR #106) with an explicit upgrade path from a
single-file mapping to a sectioned mapping. See `requirements.md` for the
resolved-in-research corrections this plan takes as given (git-history
Success Metric revised to require non-default `-C`/`--find-copies-harder`
flags; both `docspan migrate-sectioned` and `pull --to-sectioned` in scope).

## Domain Glossary

(New terms only — see `gdocs-sectioned-sync/implementation/plan.md`'s
glossary for `Section`, `Manifest`, `split_level`, `heading_id`,
`Sectioned mapping`, `Preamble`, etc., all reused unchanged.)

| Term | Definition | Source |
|---|---|---|
| Migration | The one-shot operation converting an existing non-sectioned `Mapping` (`sectioned: false`, `local` = a file) into a sectioned `Mapping` (`sectioned: true`, `local` = a directory), producing byte-identical section content, a fresh `_manifest.yaml`, updated `markgate.yaml`, and updated `SyncState`. | requirements.md |
| Placeholder heading_id | A synthetic, non-Docs-API `heading_id` of the form `"__migrated-{index}__"` assigned to a migrated section when the live/local zip (below) can't be trusted (see Epic 2) — never collides with a real opaque Docs id or with `PREAMBLE_HEADING_ID`. | research/features.md §3 |
| Live/local zip | Migration's core algorithm: `split_nodes()` is run once on live-doc-parsed nodes (real `heading_id`s, content discarded) and once on local-markdown-parsed nodes (byte-faithful content, no `heading_id`), then the two section lists are zipped position-by-position, taking identity from the live side and content from the local side. | research/architecture.md §1 |
| Remote-diverged abort | The drift-detection guard: migration aborts with a "remote diverged, pull first" error when live-section count or titles don't line up pairwise against local sections — distinct from the git dirty-tree gate, which only checks the local file against git HEAD. | requirements.md Open Questions |
| Dirty-tree gate | New (no prior art in docspan): refuses migration unless `git status --porcelain -- <mapping.local>` is empty, scoped to the mapping's own path, with explicit refusal (not silent pass) for untracked files, no-HEAD repos, and unresolvable git roots (no `git`/not a repo). | research/pitfalls.md §3 |
| Staging directory | A sibling scratch directory (e.g. `.<dirname>.migrate-<pid>.tmp/`) where every new section file + `_manifest.yaml` is written and validated before anything touches the live tree; disposable on any failure prior to the atomic swap. | research/stack.md §3 |
| Created-paths list | The minimal in-memory unwind mechanism for the few steps that happen *after* the atomic directory swap and aren't atomic by construction (`SyncState` entries, `markgate.yaml`); replaces the more general "transaction log" the requirements' Rabbit Hole raised, scoped down since the bulk of the operation is already covered by the staging directory's disposability. | research/stack.md §3, research/pitfalls.md §2 |
| Migration commit | The single atomic git commit migration itself creates (delete original file + add all section files + manifest), the unit `git revert` targets as the standing rollback path per requirements' Risk Control. | research/stack.md §1 |
| History-continuity tip | The one-line message migration prints naming the exact non-default git flags (`git log --follow -C20% <file>` / `git blame -C20% -C20% -C20% <file>`) a user needs for split-history continuity to appear — the only lever docspan has over the user's own future git invocations. | research/stack.md §1 |

## Architecture Summary

New module `src/docspan/backends/google_docs/migration.py`, sitting at the
same I/O-orchestration layer as `core/orchestrator.py` but scoped to
`google_docs` (the only sectioned-capable backend), matching where
`section_splitter.py`/`manifest.py` already live. Public entry point:

```python
def migrate_sectioned(
    mapping: Mapping,
    backend: GoogleDocsBackend,
    config: MarkgateConfig,
    config_path: str,
    state: SyncState,
    state_dir: str,
    state_path: str,
    split_level: str,
    dry_run: bool = False,
) -> MigrationResult:
```

It must not call `typer.Exit` or print directly — it returns a
`MigrationResult` (outcome enum + per-section preview data + messages) that
`cli/main.py`'s `migrate-sectioned` command and `pull()`'s `--to-sectioned`
branch each render in their own idiom (per research/architecture.md §2, so
the same function serves both entry points without assuming it owns the CLI
process).

**Return-value contract** (defined once here so Task 5.1/5.2's standalone
`migrate-sectioned` command and Task 5.3/5.4's `pull --to-sectioned` consume
an identical shape instead of each inferring its own fields):

```python
class MigrationOutcome(Enum):
    SUCCESS = "success"
    DRY_RUN = "dry_run"
    REFUSED = "refused"                 # pre-flight failure, nothing attempted
    FAILED = "failed"                   # attempted, rolled back
    ROLLBACK_FAILED = "rollback_failed" # attempted, rollback itself failed

@dataclass
class MigrationPreview:
    filename: str
    title: str
    line_count: int
    word_count: int

    def render(self) -> Table: ...      # Task 3.4

@dataclass
class MigrationResult:
    outcome: MigrationOutcome
    sections: List[MigrationPreview]        # populated for SUCCESS, DRY_RUN
    messages: List[str]                     # user-facing lines: errors, the
                                             # history-continuity tip, rollback
                                             # status, etc.
    new_mapping: Optional[Mapping] = None   # SUCCESS only — the fresh sectioned
                                             # Mapping Task 4.2's
                                             # `_apply_config_update` returns,
                                             # handed back explicitly so callers
                                             # never rely on mutation or on
                                             # re-reading `config.mappings`
    rolled_back: bool = False               # True for FAILED / ROLLBACK_FAILED
```

Task 5.3's CLI wiring rebinds its loop variable via `mapping =
result.new_mapping` (see below) — that rebind, and Task 5.2's
success/dry-run/error rendering, are the two things this contract exists to
make explicit instead of duplicated per call site.

Pipeline (non-dry-run):
1. **Pre-flight checks** (fail fast, no writes): mapping not already
   sectioned; backend not in `_SECTIONED_UNSUPPORTED_BACKENDS`; dirty-tree
   gate; single-section-only guard (Epic 2).
2. **Live/local zip**: one live Docs API fetch (`client.get_document`) →
   `DocsStructureParser().parse()` → `project()` → `split_nodes(live_nodes,
   split_level)` = `live_sections` (identity only). Local file →
   `MarkdownToParagraphParser().parse()` → `split_nodes(local_nodes,
   split_level)` = `local_sections` (byte-faithful content). Guard:
   `len(live_sections) == len(local_sections)` and titles line up pairwise;
   abort with remote-diverged error otherwise.
3. **Stage**: write each `local_sections[i]`'s original byte content,
   using `live_sections[i].heading_id` for identity, into
   `NN-slug.md` inside a scratch staging directory; build
   `_manifest.yaml` via `ManifestStore.save` (reused as-is).
4. **Dry-run stops here** — returns the preview, writes nothing further.
5. **Atomic swap**: capture `pre_migration_head` (`git rev-parse HEAD` in
   `repo_root`) as the last action before any repo-mutating call, then
   `core.atomic_dir.atomic_replace_dir(staging_dir, new_section_dir)`, then
   remove the original single file, then `git add`/`git rm --cached`/commit
   (Epic 3) as one migration commit — delete original + add all sections,
   landing before step 6. `pre_migration_head` is threaded through as an
   explicit value (not re-derived later) so it's available to `_rollback`
   regardless of which later step fails.
6. **State + config** (created-paths-list-guarded, config last per
   pitfalls.md §2): one `_record_state` call per new section file, then
   construct-and-validate a fresh `Mapping` (never mutate/`model_copy`) and
   `save_config`. The migration commit has already landed by this point
   (step 5) — a failure here does not mean "nothing happened to git yet."
   `_apply_config_update` (Task 4.2) returns the fresh `Mapping` it
   constructed; the pipeline sets it as `MigrationResult.new_mapping` on the
   success path so callers get it back explicitly rather than by mutation.
7. The entire pipeline (steps 1-6) runs under a `try/except BaseException`
   (not `except Exception`), so `KeyboardInterrupt`/`SystemExit` raised at
   any point — including mid-`_commit_swap`, after `git add`/`git rm
   --cached` have staged changes but before `commit` returns — still
   triggers `_rollback` rather than propagating past the pipeline with the
   repo left half-staged. Print success summary + history-continuity tip on
   the success path, or roll back (per Task 4.3's two cases: pre-commit vs.
   post-commit failure) and report failure, nothing partially applied.

## Pattern Decisions

| Concern | Pattern | Rationale |
|---|---|---|
| Splitting local content for migration | Reuse `split_nodes()` twice (live nodes for identity, local nodes for content), zipped by position — **not** a second splitting pipeline | Satisfies the Constraints' explicit "no second pipeline"; resolves the identity-vs-content tension research/architecture.md §1 found between the two possible node sources (live has real `heading_id`s but re-rendered content; local has byte-exact content but no `heading_id`). |
| Real `heading_id` acquisition | One live `client.get_document()` fetch, same cost/shape as `pull_sectioned`'s own live fetch — reused client, no new API surface | research/build-vs-buy.md §2: no cheaper Docs API call returns heading IDs alone; `heading_id` is exclusively assigned by `DocsStructureParser` from a live fetch (`docs_structure_parser.py:712`). |
| Git-history preservation | Byte-identical section content + single atomic commit (delete + add-all), no `git mv`-special-casing — `git mv` has zero effect on rename/copy detection | research/stack.md §1 spike: `git mv` is `rm`+`add` sugar; detection is a diff-time content-similarity heuristic over the single commit's parent-diff, so multi-commit splits break pairing entirely. |
| Config mutation for `sectioned`/`split_level`/`local` | Construct a **fresh** `Mapping(**{**mapping.model_dump(), "sectioned": True, "split_level": ..., "local": new_dir})`, never mutate attributes or use `model_copy(update=...)` | Both in-place mutation and `model_copy(update=...)` skip Pydantic v2 validator re-run; only `__init__` construction re-fires `_validate_sectioned_split_level`, so this is the only way to fail loudly *before* `save_config` persists an invalid combination (research/architecture.md §3). |
| Rollback / partial-failure recovery | Staging directory (disposable, covers steps 1-4) + `git reset --hard <pre_migration_head>` for anything after the migration commit lands (covers the commit itself, plus the two post-commit non-atomic steps `SyncState`/`markgate.yaml` for whatever they touch inside the mapping's own git tree) + a small in-memory created-paths/prior-snapshot record for state outside that tree | research/stack.md §3: three different failure windows — pre-commit (disposable staging dir), the commit landing itself and anything git-tracked after it (`git reset --hard`, since a landed commit can't be undone by file-copy restore), and non-git-tracked state (explicit snapshots). A single mechanism for all three is either wasteful (staging dir for a git-native undo) or unsafe (file-copy restore for an already-committed delete). |
| Git plumbing | Plain `subprocess.run([...], capture_output=True, check=False)`, list-argv, mirroring `mermaid_renderer.py`'s existing pattern; no GitPython/pygit2 | research/stack.md §2, research/build-vs-buy.md §1: no git wrapper exists in docspan today; adding one is a new dependency for a handful of plumbing calls that gets no functional advantage (same underlying `-C`/`-M` algorithm either way). |
| `--to-sectioned` on `pull`: ordering vs. the dirty-tree gate | **Decision: dirty-tree gate runs against pre-pull HEAD, before pull's own fetch/write phase; the split is performed against the pre-pull local content; pull's normal write/merge proceeds only after migration's split step has captured what it needs.** Concretely: `pull(..., to_sectioned=True)` calls `migrate_sectioned(...)` in "capture" mode *before* the mapping's own pull dispatch runs for that mapping, using the file exactly as it sits on disk before this invocation touches it; only after `migrate_sectioned` has fully committed (git commit + config + state) does the loop's normal per-mapping pull logic run again, now against the mapping's new sectioned config, so the immediately-following pull is a plain "sectioned pull" using the standard `pull_sectioned` path — not something migration itself needs to special-case. | Resolves the ordering conflict research/pitfalls.md §5 flagged explicitly as unreconciled. Rejected alternative (b) (split post-pull content, skip history preservation for this entry point) was rejected because it would make `--to-sectioned`'s history guarantee silently weaker than the standalone command's with no user-visible signal why; rejected alternative (c) (treat pre-pull-clean as sufficient, accept discontinuous history at the pull boundary) is subsumed by this decision since running migration entirely before pull's write means there is no discontinuity to accept — the committed migration state is exactly the pre-pull content, and the subsequent pull is a completely ordinary sectioned pull layered on top. |
| Dry-run preview | Rich `Table` (mirrors `status`'s `Table(title=...)` convention) for the N-row section-boundary preview; every other output line (header, warnings, errors, rollback messaging, final rollup) reuses `pull`/`push`'s existing icon/`[dim]`/`escape()` line conventions verbatim | research/ux.md's resolution of Open Question #3 — a table is this codebase's established tool for "N rows of structured facts" (`status`, `config list`, `conflicts`), but nothing else about `migrate-sectioned` should look like a foreign command. |
| Single-preamble-only split | Refuse by default (distinct error, not silent success) unless the doc genuinely has no headings the user could plausibly target — surfaced pre-flight, before any writes | research/pitfalls.md §1: `split_nodes()` itself treats "no headings" as a valid single-preamble result for steady-state pull, but for *migration specifically* this defeats the whole purpose while still burning git history and flipping `sectioned: true` for no benefit. |

## Testing Strategy

**Unit tests** (`tests/backends/google_docs/test_migration.py`, new file,
matching the existing `tests/backends/google_docs/` layout):
- `split_nodes` zip logic: live/local section counts match → correct
  `heading_id` assigned per index; count mismatch → remote-diverged error;
  title mismatch at matching positions → remote-diverged error; single
  preamble-only result → refusal, not silent success (unless an explicit
  override is exercised, see Epic 2).
- Placeholder `heading_id` scheme: migrated sections never collide with
  `PREAMBLE_HEADING_ID` or with each other; format matches
  `"__migrated-{index}__"`.
- Rollback/unwind: simulate a failure at each of the three post-stage
  phases (swap, state write, config write) and assert rollback correctly
  restores the original file and `markgate.yaml` to their byte-identical
  pre-migration state — via `git reset --hard <pre_migration_head>` for the
  post-commit cases (state write, config write, since the migration commit
  has already landed by then) and via staging-dir disposal (plus `git reset
  --hard` only if `_commit_swap` had already begun staging) for the
  pre-commit case (swap). Also: a `KeyboardInterrupt` raised mid-
  `_commit_swap` (after `git add`/`git rm --cached` have staged changes but
  before `commit` returns) is caught by the pipeline's
  `except BaseException` (not `except Exception`) and triggers `_rollback`,
  which restores the pre-migration git state via `git reset --hard`.
- Config construction: assert migration builds a fresh `Mapping(**{...})`
  (not `model_copy`) — a targeted test that an invalid combination (e.g.
  `split_level` not in `_VALID_SPLIT_LEVELS`) raises at construction time,
  before any `save_config` call, given a mocked/spied `save_config`.
- Dirty-tree gate edge cases (mockable via a fixture git repo, see
  integration tests below, or via a stubbed `_run_git` for pure unit
  coverage): clean tracked file → pass; modified tracked file → refuse;
  untracked file → refuse (distinct message); no HEAD (fresh `git init`) →
  refuse; unrelated dirty file elsewhere in the repo → pass (scope is
  pathspec-limited).

**Integration test** (`tests/cli/test_migrate_sectioned_cli.py` or extending
the existing CLI test module, against a real fixture git repo — `git init`
in a `tmp_path`, real commits, real `subprocess` calls to git, not mocked,
since the dirty-tree gate and history-preservation are exactly the things
that need to be proven against real git behavior):
1. Seed a repo with a committed single markdown file + a `markgate.yaml`
   mapping (`sectioned: false`) and a `SyncState` with one entry for that
   file; stub the Google Docs client to return a live document whose
   structural parse matches the local file's heading structure.
2. Run `docspan migrate-sectioned <file>` end to end: assert the resulting
   directory has the expected `NN-slug.md` files with byte-identical
   content to the original file's corresponding spans, `_manifest.yaml`
   entries in order, `markgate.yaml` updated (`sectioned: true,
   split_level`), `SyncState` has one entry per section file and no
   leftover entry for the old file path, and exactly one new git commit
   exists.
3. Assert `git log --follow -C20% <section-file>` (spike-verified flags
   from research/stack.md) surfaces the pre-split commit — this is the
   actual regression test for the git-history-preservation claim, run for
   real rather than asserted from documentation.
4. `--dry-run` variant: assert no files/commits/config changes happen and
   the printed table lists the expected section rows.
5. Failure-injection variant: force the manifest write (or the config
   write) to raise partway through and assert the working tree, git log,
   and `markgate.yaml` are unchanged afterward (proves rollback, not just
   unit-level unwind logic).
6. `KeyboardInterrupt` variant: patch `_run_git`'s `commit` call to raise
   `KeyboardInterrupt` after `add`/`rm --cached` have already run for real
   against the fixture repo, and assert `_rollback` still runs (via the
   pipeline's `except BaseException`), leaving `git status --porcelain`
   clean and `git log` showing no new commit — the actual regression test
   for Blocker 2 (Ctrl-C mid-`_commit_swap`), run against real git rather
   than asserted from documentation.
7. `pull --to-sectioned` variant: assert ordering — migration commits
   against pre-pull content first, then the normal sectioned pull runs
   and produces a second, ordinary pull outcome layered on top. Assert this
   directly, not just by outcome shape: that `orchestrate_pull`/
   `pull_sectioned` (not the non-sectioned path) is actually invoked for
   that mapping in the same `pull` process — e.g. via a spy/mock on
   `pull_sectioned` — proving the loop's `mapping = result.new_mapping`
   rebind (Task 5.3) took effect and dispatch didn't fall through on the
   stale pre-migration `Mapping`.

## Risks / Mitigations (carried forward from research/pitfalls.md)

| Risk | Mitigation | Plan reference |
|---|---|---|
| Single-preamble-only split silently "succeeds," burning history for nothing | Pre-flight refusal (distinct from steady-state `split_nodes` behavior), single-section-only guard in Epic 2 | pitfalls.md §1 |
| Partial state spans two systems (filesystem+manifest, `SyncState`, `markgate.yaml`) not just the filesystem | Explicit ordering: filesystem/state → config-last; created-paths list unwinds pre-config-flip steps; config write itself is the last, and only, step whose failure needs no filesystem rollback (nothing durable changed yet) | pitfalls.md §2, Epic 4 |
| No prior git-integration code exists; dirty-tree gate has real edge cases (unrelated dirty files, untracked file, no HEAD, nested/submodule repo, no git installed) | New, explicitly-designed dirty-tree check scoped to the mapping's own pathspec, with each edge case as its own refusal branch (not lumped into a generic error) | pitfalls.md §3, Epic 1 |
| No lock/mutex mechanism exists; concurrent `pull`/`push` could race migration's multi-step write sequence, and `_rollback`'s `git reset --hard <pre_migration_head>` (Task 4.3) would discard *any* commit landed after `pre_migration_head` by another process, not just migration's own half-done work | **Revised (pre-mortem P1 #2): a full pull/push-aware lock is still out of scope, but migration now takes its own per-mapping sentinel-lock file** (see Task 4.5) held for the duration of the write phase (Tasks 3.2–4.2), refusing to proceed if already held — this doesn't make `pull`/`push` respect the lock, but it does close the specific `git reset --hard` blast-radius gap by ensuring no *other migration* can be mid-flight on the same mapping when a rollback fires. `save_config`'s `expected_mtime` check remains the guard against a concurrent config edit. | pitfalls.md §4, Epic 4 |
| `--to-sectioned` ordering conflict between the dirty-tree gate and pull's own write | Resolved via Pattern Decision above: migration runs and commits fully before pull's own fetch/write phase for that mapping | pitfalls.md §5, Epic 5 |
| git's default rename/copy detection won't show continuity for 3+-way splits regardless of how carefully the split is performed | Success Metric already revised in requirements.md; command prints the exact non-default flags needed (history-continuity tip); integration test proves those specific flags work, not that default flags do | stack.md §1, Epic 3 |
| Migrated sections get placeholder (non-Docs) `heading_id`s, not real identity | Documented as a known, self-healing limitation: the next ordinary `pull` on the mapping re-derives real `heading_id`s via the existing rename-reconciliation ladder `pull_sectioned` already has for `heading_id` churn — success output explicitly says "section identity will stabilize after your next pull" | features.md §3, Epic 2 |

## Epics / Tasks

### Epic 1 — Dirty-tree gate + git plumbing helper

New infrastructure; nothing to reuse. Blocks all later epics (migration
cannot run without this gate per the Risk Control).

- **Task 1.1**: `src/docspan/backends/google_docs/migration.py` (new file):
  `_run_git(*args, cwd, timeout=None) -> subprocess.CompletedProcess` helper —
  list-argv, `capture_output=True`, `check=False`, decode with
  `errors="replace"`, mirroring `mermaid_renderer.py`'s subprocess pattern.
  **Timeout is sized per call site, not a single global constant** (pre-mortem
  P1 #1): plumbing calls (`rev-parse`, `status --porcelain`) default to `10`
  seconds; `add`/`rm --cached`/`commit` — the calls in `_commit_swap` (Task
  3.2) that scale with the number of migrated sections — default to `60`
  seconds, since these run over N newly-created section files from exactly
  the large docs this feature targets, and a "small" timeout tuned for a
  quick `rev-parse` would abort mid-write on realistic inputs. Both defaults
  are named constants (`_GIT_PLUMBING_TIMEOUT_S = 10`,
  `_GIT_WRITE_TIMEOUT_S = 60`) at module scope, not inline magic numbers, and
  each `_run_git` call site passes the constant matching its own cost profile
  explicitly rather than relying on a default. Raises a new `MigrationError`
  (module-local exception, matching `ManifestError`'s error-domain
  convention) on git-not-found (`OSError`), timeout
  (`subprocess.TimeoutExpired`), or unexpected nonzero exit, rather than
  letting `CalledProcessError`/`OSError`/`TimeoutExpired` propagate raw.
- **Task 1.2**: `_check_clean_tree(local_path: str) -> None` (raises
  `MigrationError` with a specific, distinguishable message per case):
  1. Resolve repo root via `git rev-parse --show-toplevel` run with `cwd`
     set to the file's own directory (handles nested/submodule repos
     correctly per pitfalls.md §3) — no `git`/not-a-repo → refuse
     ("not inside a git repository").
  2. `git rev-parse HEAD` in that root — failure (unborn branch) → refuse
     ("repository has no commits yet; make an initial commit first").
  3. `git status --porcelain -- <local_path>` scoped to the mapping's own
     pathspec (never whole-repo) — non-empty output starting with `??` →
     refuse as "untracked" (distinct message: "not yet tracked by git");
     non-empty output otherwise → refuse as "uncommitted changes" (the
     ux.md §3 message shape, with the three-line remediation snippet);
     empty → pass.
- **Task 1.3**: Unit tests for `_check_clean_tree` and `_run_git` against a
  real `tmp_path` git repo (all five edge cases from pitfalls.md §3): clean
  tracked file, dirty tracked file, untracked file, no-HEAD repo, unrelated
  dirty file elsewhere in the same repo (must pass).

### Epic 2 — Live/local zip + split (`migration.py` core)

Depends on Epic 1 (pre-flight checks run before any of this). Reuses
`split_nodes()` per the Constraints — this epic is exclusively orchestration
around two existing calls, no new splitting logic.

- **Task 2.1**: `_split_live(client, doc_id, split_level) -> List[Section]`
  — `client.get_document(doc_id)` → `DocsStructureParser().parse()` →
  `project()` → `split_nodes(nodes, split_level)`. Discards content, keeps
  only `heading_id`/`title` per section (by index).
- **Task 2.2**: `_split_local(local_path, split_level) -> List[Section]` —
  `MarkdownToParagraphParser().parse(Path(local_path).read_text())` →
  `split_nodes(nodes, split_level)`. Every section's `heading_id` is falsy
  here by construction (no writer of `heading_id` in that parser); this is
  expected, not a bug to fix.
- **Task 2.3**: `_zip_sections(live, local) -> List[Section]` — abort with
  `MigrationError("remote has diverged — run 'docspan pull' first")` unless
  `len(live) == len(local)` and each pair's `title` matches; on success,
  `dataclasses.replace(local[i], heading_id=live[i].heading_id or
  f"__migrated-{i}__")` — the `or` branch covers the case where the live
  side's own section is itself a preamble/placeholder (a real Docs
  `heading_id` is never falsy for a genuine heading, but the preamble
  section's `heading_id` is `PREAMBLE_HEADING_ID`, which is intentionally
  passed through unchanged so migrated preambles line up with what a fresh
  `pull_sectioned` would produce).
- **Task 2.4**: Single-preamble-only guard: if `len(zipped) == 1` and that
  section's `heading_id == PREAMBLE_HEADING_ID`, raise `MigrationError`
  naming the deepest heading style actually present in the local doc (reuse
  `SectionSplitError`'s message-construction logic/wording rather than
  inventing new phrasing) — surfaced pre-stage, before any files are
  written. No `--allow-single-section` override in this pass (YAGNI per
  Medium appetite; can be added later if a real use case appears).
- **Task 2.5**: Unit tests per the Testing Strategy above (zip matching,
  count/title mismatch abort, placeholder id scheme, single-preamble
  refusal).

### Epic 3 — Stage, atomic swap, git commit

Depends on Epic 2 (needs the zipped section list to write). Reuses
`ManifestStore.save` and `core.atomic_dir.atomic_replace_dir` verbatim —
no new manifest or directory-swap code.

- **Task 3.1**: `_stage_sections(zipped, target_dir) -> pathlib.Path`
  (staging dir path) — writes each section's original byte content
  (`local[i]`'s content, never re-rendered) to `NN-slug.md` inside a sibling
  scratch dir (`.{dirname}.migrate-{pid}.tmp/`), builds
  `List[SectionManifestEntry]` and calls `ManifestStore.save(...)` into the
  same staging dir. `--dry-run` calls this function's pure
  compute-boundaries half only (see Task 3.4) and never creates the
  directory.
- **Task 3.2**: `_commit_swap(staging_dir, target_dir, old_file_path,
  repo_root) -> str` — first captures and returns `pre_migration_head =
  _run_git("rev-parse", "HEAD", cwd=repo_root).stdout.strip()` (before any
  other call in this function runs, so it reflects the exact state the
  dirty-tree gate already validated), then `atomic_replace_dir(staging_dir,
  target_dir)`, then `os.remove(old_file_path)`, then
  `_run_git("add", target_dir)`, `_run_git("rm", "--cached", old_file_path)`,
  `_run_git("commit", "-m", <generated message>)` — one commit (delete
  original + add all sections), matching research/stack.md §1's "docspan
  does create the commit" recommendation so `git revert`/`git reset --hard`
  is available as the standing rollback path. Removal of the original file
  is **not** deferred past the commit — the commit's parent-diff must
  contain the delete for `git log --follow`/`git blame` continuity to have
  any chance of working. The caller threads the returned
  `pre_migration_head` value into `_rollback` (Task 4.3); this function
  itself does not attempt partial rollback of its own steps beyond what's
  already-atomic (the swap) — including a `KeyboardInterrupt` raised after
  `add`/`rm --cached` have staged changes but before `commit` returns (Task
  4.3 covers restoring that half-staged state via `git reset --hard`).
- **Task 3.3**: Commit message template includes the history-continuity tip
  content so it's discoverable via `git log` later too, plus the plain
  console-only version for immediate display: `f"migrate-sectioned:
  split {old_file_path} into {n} sections\n\nTip: git's default rename
  detection won't show pre-split history for these files. Use:\n  git log
  --follow -C20% <file>\n  git blame -C20% -C20% -C20% <file>"`.
- **Task 3.4**: `MigrationPreview` dataclass (filename, title, line count,
  word count per section) + `.render()` — pure function over the zipped
  section list, callable without staging anything, feeding both the CLI's
  `--dry-run` table and (per Pattern Decisions) the pixel-identical
  `pull --dry-run --to-sectioned` table.
- **Task 3.5**: Integration test (fixture git repo) proving
  `git log --follow -C20% <section-file>` resolves to the pre-split commit,
  per the Testing Strategy.

### Epic 4 — State + config update, rollback

Depends on Epic 3 (needs a landed swap to record state against). This is
where the created-paths list lives.

- **Task 4.1**: `_record_migrated_state(state, state_path, state_dir,
  mapping, zipped_sections, remote_version) -> None` — captures
  `prior_state_snapshot` (a copy of the relevant `state.mappings` entries)
  before making any change, for `_rollback` (Task 4.3) to restore from if a
  later step fails. Then: one `_record_state` call per new section file
  path, **each passing `save=False`** (reusing `orchestrator._record_state`
  exactly, per the Constraint that migration must produce state identical to
  a fresh `pull_sectioned` — `save=False` avoids the N intermediate
  disk-writes-per-section that `orchestrate_push`'s existing sectioned path
  incurs at `orchestrator.py:393` by leaving the default `save=True`,
  which migration deliberately does not want); then remove the old
  single-file entry from `state.mappings`; then call
  `state.save(state_path)` **exactly once**, after every in-memory mutation
  above is complete — this single call is the literal meaning of "saving
  once," not an approximation of it. Track every path added/removed in the
  created-paths list as it happens (not after the fact).
- **Task 4.2**: `_apply_config_update(config, config_path, mapping,
  split_level, new_local_dir) -> Mapping` — captures
  `prior_config_snapshot` (the pre-update `MarkgateConfig`, or at minimum
  the mapping's prior field values) before mutating, for `_rollback` to
  restore from. Then: construct
  `new_mapping = Mapping(**{**mapping.model_dump(), "sectioned": True,
  "split_level": split_level, "local": new_local_dir})` (fresh construction,
  never `model_copy`/mutation, per Pattern Decisions — this is also where
  `_SECTIONED_UNSUPPORTED_BACKENDS`/`_validate_sectioned_split_level`
  provide defense-in-depth beyond the CLI's own pre-check), swap it into
  `config.mappings`, then `save_config(config, config_path,
  expected_mtime=...)` — re-check `expected_mtime` immediately before this
  call (pitfalls.md §4's concurrency mitigation) and treat
  `ConfigConflictError` as a trigger for full rollback, not a retry. Returns
  `new_mapping` (not the `MarkgateConfig`) so `migrate_sectioned`'s pipeline
  can set it directly on the returned `MigrationResult.new_mapping` per the
  return-value contract in the Architecture Summary.
- **Task 4.3**: `_rollback(repo_root: Path, pre_migration_head: str | None,
  commit_landed: bool, created_paths: list[Path], prior_state_snapshot,
  prior_config_snapshot) -> None` — two distinct cases, since the migration
  commit's landing point (end of Task 3.2/step 5) is the dividing line:
  - **Pre-commit failure** (anything in Epics 1-2, or `_commit_swap` itself
    raising/being interrupted before `commit` returns, including a
    `KeyboardInterrupt` after `add`/`rm --cached` have staged changes):
    `commit_landed` is `False`. If `pre_migration_head` was already
    captured (i.e. failure happened inside or after `_commit_swap` began),
    run `git reset --hard <pre_migration_head>` in `repo_root` to discard
    any half-staged index/working-tree state and restore the original file;
    otherwise (failure before `_commit_swap` ran at all) there is nothing
    git-side to unwind — just discard the staging directory. Either way,
    `created_paths` should be empty in this case (nothing past the swap has
    run yet).
  - **Post-commit failure** (failure during/after Task 4.1's `SyncState`
    write or Task 4.2's config write — the migration commit has already
    landed): `commit_landed` is `True`. Run `git reset --hard
    <pre_migration_head>` in `repo_root` — this undoes the landed migration
    commit *and* discards any uncommitted working-tree changes made after
    it (e.g. `markgate.yaml` edits from Task 4.2, since `_commit_swap` never
    `git add`s the config file), restoring the original single file and the
    pre-migration repo state in one step. This replaces "restore from a
    kept-aside copy" — the original file was in fact already deleted and
    committed by this point, so a copy-based restore doesn't apply; `git
    reset --hard` is the only mechanism that can undo a landed commit.
    Separately, restore `SyncState` from `prior_state_snapshot` and
    `MarkgateConfig` from `prior_config_snapshot` (the in-memory values
    captured immediately before Task 4.1/4.2 ran) for any part of that
    state that isn't itself inside `repo_root`'s git tree (e.g. a
    `SyncState` file stored outside the mapping's repo) — `git reset --hard`
    only covers what's tracked/dirty inside `repo_root`. Restoring
    `SyncState` here means writing it back to disk, not just mutating the
    in-memory object: since Task 4.1's per-section `_record_state` calls use
    `save=False` and only the single terminal `state.save()` call ever
    touches disk, a failure at or after that terminal save may have already
    persisted the new (now-wrong) state — so this branch must call
    `state.save(state_path)` itself after restoring `prior_state_snapshot`
    in memory, rather than assuming the in-memory restore is sufficient.
  - `created_paths` (populated by 4.1/4.2 as state/config entries are
    added) is still consulted for anything `git reset --hard` can't reach
    by construction, but for paths inside `repo_root` it is now redundant
    with the reset and exists mainly as a cross-check / for the
    outside-`repo_root` case above.
  - If rollback itself fails partway (e.g. `git reset --hard` errors), report
    the harder "rollback FAILED" case per ux.md §4's distinct two-`✗`-line
    shape rather than downgrading it to a warning.
- **Task 4.4**: Unit tests per Testing Strategy (failure injection at each
  phase; config-construction validation-before-save test; pre-commit vs.
  post-commit `_rollback` branch coverage, asserting `git reset --hard
  <pre_migration_head>` is invoked with the correct sha in the post-commit
  case and is skipped in the before-`_commit_swap`-ran case; a
  `KeyboardInterrupt` raised mid-`_commit_swap` — after `add`/`rm --cached`
  but before `commit` returns — is caught by the pipeline's
  `except BaseException` and triggers `_rollback`, and the working tree
  ends up clean and matching `pre_migration_head` afterward).
- **Task 4.5** (pre-mortem P1 #2): `_acquire_migration_lock(repo_root: Path,
  mapping_key: str) -> Path` / `_release_migration_lock(lock_path: Path) ->
  None` — a per-mapping sentinel-lock file (e.g.
  `.docspan-migration-{mapping_key}.lock` under `repo_root`, `O_CREAT |
  O_EXCL` so creation itself is the atomic race-check) acquired immediately
  before the write phase begins (start of Task 3.2) and released in a
  `finally` after `_rollback`/success, whichever occurs. If the lock file
  already exists, `migrate_sectioned` raises `MigrationError("migration
  already in progress for this mapping")` before touching any file, git
  state, or config — refusing to proceed rather than racing. This closes the
  specific gap pre-mortem P1 #2 identified: without it, a second
  concurrent migration's `_rollback` (`git reset --hard
  <pre_migration_head>`) could discard a commit made by an unrelated
  `pull`/`push` after this migration's `pre_migration_head`. Unit tests:
  acquiring an already-held lock raises `MigrationError` without mutating
  any state; the lock file is removed after both a successful run and a
  rolled-back run (including the `KeyboardInterrupt` case from Task 4.4).

### Epic 5 — CLI integration

Depends on Epics 1-4 (the underlying `migrate_sectioned()` function must be
complete and tested before wiring the CLI, since both entry points share it
verbatim).

- **Task 5.1**: `@app.command("migrate-sectioned")` in `src/docspan/cli/main.py`,
  alongside `pull`/`push`/`migrate-xdg`. Signature: mapping path argument,
  `--dry-run`, `--config`/`-c`, `--prefix`/`-p` (matching existing option
  conventions). Resolves mapping via `resolve_mapping_for_path` (exact reuse,
  no new lookup). Pre-checks (already-sectioned, backend capability) as a
  friendly-message fast exit per research/architecture.md §2 — real
  enforcement is Task 4.2's construction-time validator, this is UX sugar
  only, matching `push()`'s own "CLI is NOT the enforcement point" comment.
- **Task 5.2**: Render `MigrationResult`/`MigrationPreview` per ux.md's
  exact templates: success (icon line + per-section plain-text summary +
  `[dim]` manifest/config/state confirmation lines + `MIGRATED_SECTIONS={n}`
  rollup), dry-run (yellow one-liner + `Table` + "Nothing written" footer,
  including the single-preamble warning variant), dirty-tree error
  (`err_console`, three-line remediation snippet), in-progress
  `[yellow]migrating[/yellow]` line before the table/result (Ctrl-C
  checkpoint), rollback success/failure (two distinct shapes per ux.md §4).
  The pre-migration `[yellow]migrating[/yellow]` line doubles as the
  commit-creation notice — it must explicitly say this action will create a
  git commit, since migration's write is not idempotent-looking output like
  a normal `pull`/`push`; `pull --to-sectioned` (Task 5.3) reuses this exact
  line for parity. `had_error`/`typer.Exit(1)` idiom matches existing
  commands.
- **Task 5.3**: `pull()` gains `--to-sectioned: bool = typer.Option(False,
  "--to-sectioned")`. Per the Pattern Decision above: when set and
  `not mapping.sectioned`, call `migrate_sectioned(...)` to completion
  *before* that mapping's normal pull dispatch in the loop — using the file
  exactly as it is pre-pull (the dirty-tree gate therefore checks
  legitimately-pre-pull state, no special-casing needed inside
  `migration.py` itself). On success, **explicitly rebind the loop's local
  `mapping` variable**: `mapping = result.new_mapping` (the
  `MigrationResult.new_mapping` field from the return-value contract,
  populated by Task 4.2's `_apply_config_update`). This rebind is required,
  not optional fall-through — `_apply_config_update` swaps a freshly
  constructed `Mapping` into `config.mappings`, but the pull loop's
  already-bound `mapping` reference for this iteration does not
  automatically follow that swap (Python does not re-read the list
  mid-iteration), so without the explicit rebind `mapping.sectioned` would
  still read `False` and `orchestrate_pull` (`orchestrator.py:420`) would
  dispatch the non-sectioned path against a `local` file that migration just
  deleted (Task 3.2). Only after this rebind does falling through into that
  iteration's normal pull logic reach the ordinary `pull_sectioned` branch
  as intended. On migration failure, report per ux.md §3's error shape and `continue` to
  the next mapping (`had_error = True`, no early exit — matches existing
  per-mapping failure handling in the pull loop). Before invoking
  `migrate_sectioned(...)`, print the same commit-creation notice the
  standalone `migrate-sectioned` command prints (Task 5.2) — `pull
  --to-sectioned` creates a git commit as a side effect just as the
  standalone command does, and the user needs the same up-front signal
  regardless of which entry point triggered it. No interactive confirm gate
  is required for either surface (out of scope for this pass) — this is
  print-parity only.
- **Task 5.4**: `--dry-run --to-sectioned` renders the same `Table` as
  standalone `migrate-sectioned --dry-run` for that one mapping, in place
  of pull's normal one-line preview for it; other mappings in the same run
  keep the existing single-line preview (per ux.md §5, "pixel-identical"
  requirement for this specific overlap).
- **Task 5.5**: Integration test for both CLI surfaces per the Testing
  Strategy (items 2, 4, 7).

## Summary of New/Changed Surface Area

- `src/docspan/backends/google_docs/migration.py` (new) — `migrate_sectioned`,
  `MigrationResult`, `MigrationPreview`, `MigrationError`, `_run_git`,
  `_check_clean_tree`, `_split_live`, `_split_local`, `_zip_sections`,
  `_stage_sections`, `_commit_swap`, `_record_migrated_state`,
  `_apply_config_update`, `_rollback`.
- `src/docspan/cli/main.py` — new `@app.command("migrate-sectioned")`;
  `pull()` gains `--to-sectioned`.
- `src/docspan/config.py`, `src/docspan/core/orchestrator.py`,
  `src/docspan/core/state.py`, `src/docspan/backends/google_docs/manifest.py`,
  `src/docspan/core/atomic_dir.py` — **no changes**, all reused as-is
  (`Mapping` construction path, `_record_state`/`record_state`,
  `SyncState`, `ManifestStore.save`, `atomic_replace_dir` respectively).
- `tests/backends/google_docs/test_migration.py` (new),
  `tests/cli/test_migrate_sectioned_cli.py` (new, or extends the existing
  CLI test module) — per Testing Strategy.
