# Pitfalls research: gdocs-sectioned-migrate

Agent 4 (Pitfalls), SDD Phase 2. Scope: failure modes specific to the
*migration* operation (single-file → sectioned), not the already-analyzed
sectioned-sync steady-state pitfalls (see
`project_plans/gdocs-sectioned-sync/research/pitfalls.md`, which this doc
assumes as prior art and does not repeat). Note: no `pre-mortem.md` exists
under `gdocs-sectioned-sync/decisions/` — that directory instead holds three
ADRs (`ADR-001` manifest identity, `ADR-002` reorder-as-move, `ADR-003`
sectioned-pull-always-structural), which are cited below where relevant.

## 1. `split_level` doesn't cleanly match the local file's headings

Read `section_splitter.py`'s `split_nodes()`
([src/docspan/backends/google_docs/section_splitter.py:88-100](../../../src/docspan/backends/google_docs/section_splitter.py)):
this is already decided behavior, not open for migration to reinterpret.

- **No headings at `split_level` at all, but *some* headings at another
  level present** → `SectionSplitError`, naming the deepest level actually
  present (lines 113-118). Migration must surface this as a clear
  pre-flight validation error (e.g. "this document's deepest heading is
  HEADING_2, but split_level=3 was given") *before* touching any files —
  not let the splitter's exception propagate as a raw traceback.
- **No headings anywhere in the document** → explicitly *not* an error per
  the docstring (line 98: "An empty document... is not an error — it
  produces a single preamble section"). For steady-state `pull_sectioned`
  this is a reasonable no-op-ish outcome. **For migration specifically it
  is a near-useless result the user almost certainly didn't intend**: the
  whole point of migrating is to get multiple section files, and the
  command would "succeed" by producing exactly one file (renamed into a
  directory) that still isn't split, discarding git history in the
  process (a single-preamble migration still runs the rename/history
  machinery for no benefit) while updating `markgate.yaml` to
  `sectioned: true`. **Mitigation**: migration's CLI layer should treat
  "split produced only a preamble section" as a distinct warning/refusal
  case — either refuse by default with a message telling the user to pick
  a `split_level` that matches headings actually present, or require an
  explicit `--allow-single-section`/similar opt-in flag if there's a
  legitimate reason to proceed (e.g. testing). Do not silently do it,
  since it burns the "one command, done, don't reason about it" success
  metric from requirements.md.

## 2. Partial state spans TWO systems: `markgate.yaml` (config) and the filesystem (section files + manifest), not just the filesystem

Requirements.md's Rabbit Holes flags rollback atomicity but frames it purely
in terms of file layout. The actual blast radius is wider, confirmed by
reading the write paths:

- `save_config()` ([src/docspan/config.py:180-221](../../../src/docspan/config.py))
  is individually atomic (temp file + `os.replace`, with an
  `expected_mtime` conflict check) — but it is **one atomic write among
  several**, not a transaction spanning the whole migration. The same is
  true of `SyncState.save()`
  ([src/docspan/core/state.py:33-36](../../../src/docspan/core/state.py)):
  `tmp = path + ".tmp"` + `os.rename` — atomic per-call, not atomic
  *across* calls.
- A migration necessarily makes at least three separate atomic writes:
  (a) section files + `_manifest.yaml` under the new directory, (b)
  `markgate.yaml` update (`sectioned: true`, `split_level`), (c)
  `SyncState` update (new per-section state shape, replacing the single
  `MappingState` entry). Any ordering leaves a window where a crash
  between two of these three produces an inconsistent triple: e.g.
  section files exist and `markgate.yaml` says `sectioned: true`, but
  `SyncState` still holds the old single-file `MappingState` — the very
  next `pull`/`push` would then read a `sectioned: true` mapping through
  code paths that assume `SyncState` already has per-section keys,
  which is not a case the sectioned-sync state code was written to
  positionally guess at (it was written assuming state is fresh-derived
  by `pull_sectioned`, per ADR-003, not partially migrated).
- **Mitigation**: define an explicit ordering with the *config write last*,
  since `markgate.yaml`'s `sectioned: true` is the flag every other code
  path (pull, push, the lock/state lookups) branches on. Concretely:
  (1) stage all section files + manifest in a temp directory, (2) if that
  succeeds, atomically move the temp directory into place (rename) and
  delete the old single file, (3) write the new `SyncState` entries,
  (4) only then call `save_config()` to flip `sectioned: true`. If any
  step from (1)-(3) fails, nothing has touched `markgate.yaml` yet, so the
  mapping is still validly non-sectioned and no rollback of *config* is
  needed — only filesystem/state rollback (delete the temp dir / restore
  the `SyncState` entry, both cheap because neither was ever the "live"
  state until step 4). If step 4 itself fails (e.g. `ConfigConflictError`
  because `markgate.yaml` was hand-edited concurrently — see §3), the
  command must roll back (1)-(3) rather than leaving `markgate.yaml`
  un-flipped while the filesystem already looks sectioned, since that
  combination is the specific inconsistency both `pull` and `push` have no
  designed-for handling for today.
- A rollback log is cheaper here than a general transaction mechanism: a
  small in-memory list of "paths created" + "state/config values before
  this run" is sufficient to unwind steps (1)-(3), since docspan already
  has no multi-file-transaction primitive to reuse (`save_config`/`SyncState.save`
  are independently atomic, not composably transactional) and building a
  general one is out of scope for a Medium-appetite one-off command.

## 3. Git edge cases

Grepping the codebase (`git status`, `is_dirty`, `GitCommandError`, `import
git`, GitPython in `pyproject.toml`) found **zero existing git-integration
code** — the "clean git working tree" guard requirements.md describes as
mirroring "the existing pull local-only guard pattern" is not actually the
same mechanism: `orchestrator.py`'s `"local-only"` action (lines 243, 463,
584, 654) is a **hash comparison against `SyncState`**, not a check against
the real git working tree. There is no prior art for a git-dirty check in
this codebase; migration's dirty-tree guard is wholly new code, so its edge
cases need explicit design rather than "reuse the existing check":

- **The mapping's repo has unrelated uncommitted changes elsewhere.** The
  guard must scope the dirty check to *only* the mapping's own local
  path (`git status --porcelain -- <path>`, or the GitPython equivalent
  scoped to that pathspec) — a naive whole-repo `git status` would block
  migration on changes that have nothing to do with the mapping, which
  would be a false-positive refusal and a support complaint. Requirements
  §Constraints/Risk Control implies "the mapping's own file" scope; make
  that explicit in the implementation, not assumed.
- **The file isn't tracked by git at all** (`git ls-files` empty for that
  path, or `git status --porcelain` reports it as `??`). "Clean working
  tree" is meaningless for an untracked file — there's no HEAD-relative
  content to diff. This isn't the same as "dirty"; treat it as its own
  case. Since requirements.md's whole risk-control rationale is "the split
  is always performed against a known-good, versioned state," an untracked
  file arguably fails that rationale just as hard as a dirty one — the
  natural mitigation is to refuse migration for an untracked file with a
  message asking the user to `git add`/commit it first, same remediation
  as the dirty case, rather than silently treating untracked-but-matches-disk
  as clean.
- **No commits yet in the repo** (`git rev-parse HEAD` fails, "unborn
  branch" / freshly `git init`'d repo). `git diff HEAD -- <path>` and
  similar have no HEAD to diff against — the dirty-check implementation
  must handle "no HEAD exists" as a distinct branch (likely: no HEAD means
  nothing is committed, so by the same untracked-file logic above, refuse
  and ask the user to make an initial commit) rather than letting a git
  command throw an uncaught error that surfaces as an opaque stack trace.
- **The mapping's local path lives inside a nested git submodule** (or a
  separate git repo nested under the outer one, whether or not registered
  as an actual `.gitmodules` submodule). A dirty check run from the outer
  repo's root would either miss the submodule's own dirty state entirely
  (git status on the outer repo shows a submodule as one opaque
  "modified" line, or nothing, depending on flags) or resolve to the wrong
  repository boundary. **Mitigation**: resolve the git repo root
  relative to the mapping's local path (e.g. `git rev-parse
  --show-toplevel` run with cwd set to the file's directory, or GitPython's
  repo-discovery from that path) rather than assuming the outer/global
  repo root docspan itself might be invoked from — the correct repo for
  the dirty check is whichever one actually contains the file's history.
- **No git installed / not a git repo at all.** Requirements.md's Risk
  Control frames the guard as mandatory ("forces the user to commit or
  push first"), which implies the guard being *unable to run* (no `git`
  binary on PATH, or the path isn't inside any repo) should also refuse
  migration rather than silently skip the safety gate — otherwise a
  no-git environment gets weaker guarantees than a git environment with no
  warning that anything changed.

## 4. Concurrency: no lock/mutex mechanism exists in docspan to check for or reuse

Exhaustively grepped `src/docspan/` for `lock`, `flock`, `filelock`,
`fcntl`, `pidfile`, and `.lock` (case-insensitive): the only "lock" hits are
false positives (`blockquote`, `codeBlock`, `BLOCKED` status string,
"local-only"). **There is no existing inter-process lock, PID file, or
mutex of any kind protecting concurrent `pull`/`push` invocations against
the same mapping** — docspan today relies entirely on each write being
individually atomic (`save_config`'s temp-file+`os.replace`,
`SyncState.save`'s temp-file+`os.rename`) plus `save_config`'s
`expected_mtime` optimistic-concurrency check to *detect* a concurrent
config edit after the fact, not to *prevent* two operations running at once.

- Concrete race: `docspan pull` or `push` against the same mapping starts
  reading `markgate.yaml`/`SyncState`/the local file at roughly the same
  moment `migrate-sectioned` begins its multi-step write sequence (§2).
  Because migration's writes span three separate resources with no shared
  lock, a concurrent pull could read the local file mid-migration (after
  the new directory exists but before `markgate.yaml` is flipped, or
  after the flip but before `SyncState` catches up), producing behavior
  that depends on exactly which step won the race — not a designed
  outcome.
- **Mitigation, given no existing primitive to reuse**: this needs new
  infrastructure, not "reuse the lock" as originally hoped. The
  lowest-appetite option consistent with `save_config`'s existing
  optimistic-concurrency style is to have migration re-check
  `config_mtime()` immediately before its final config write (already
  half-present via `expected_mtime` in `save_config`) and treat a mismatch
  as "something else touched this mapping mid-migration, abort and roll
  back" rather than introducing a new file-based lock/pidfile mechanism
  for what is a rare, human-triggered, short-lived operation. If stronger
  guarantees are wanted, a simple sentinel lock file
  (`<local>.migrate.lock`, created exclusively with `O_EXCL` at the start
  and removed at the end/on rollback) is the smallest addition that would
  also give `pull`/`push` something to check for and refuse to proceed
  against — but that requires `pull`/`push` to be taught to check it too,
  which is new coupling beyond migration's own code and should be called
  out explicitly in planning as in-scope or explicitly deferred, not
  assumed away.

## 5. `--to-sectioned` on `pull`: migration splits stale local content that the same pull call is about to supersede

This is the most concrete correctness risk of the two entry points, because
`--to-sectioned` is defined as running "the same underlying migration logic
inline as part of a pull run" (requirements.md scope) — meaning migration's
split step and the pull's own remote-fetch step both operate on the same
mapping in the same invocation, and their relative ordering matters:

- **If the split runs before the pull's remote fetch**: it splits the
  *local* file as it stood before this pull incorporated any remote
  changes. If the remote doc has diverged since the last sync (the normal
  "fast-forward" or "merged" case in `orchestrator.py`'s action taxonomy,
  line 243), the section boundaries and content the user is shown (in
  `--dry-run`) or the git history the migration preserves are for content
  that is about to be discarded/merged over by the pull that follows in
  the same command. The user asked to migrate *and* pull in one step, but
  got a migration of the wrong (soon-to-be-stale) content — the git-history
  preservation guarantee ("git blame on any resulting section file
  continues to resolve to the original commit history of that content")
  becomes attached to content that no longer matches what's actually
  synced, which directly undermines the feature's own success metric.
- **If the split runs after the pull's remote fetch**: this only works if
  the pull step, for a non-sectioned mapping, already writes plain-file
  content that migration can then split — meaning `--to-sectioned` would
  need to be: pull normally into the single file first, *then* run the
  migration split against the freshly-pulled content. This is the
  ordering consistent with the dirty-working-tree guard's own rationale
  (§3: "split is always performed against a known-good, versioned
  state") — except the freshly-pulled file is *not yet committed*
  (pull writes to the working tree; the user hasn't committed the new
  pull's changes), so the dirty-tree guard as specified would immediately
  refuse the migration half of this same command, since pull just made
  the tree dirty relative to HEAD.
- **This is a real design conflict, not just an edge case**: requirements
  §Constraints doesn't reconcile "the migration split requires a clean
  git tree" with "the pull flag runs pull and migration in the same
  invocation," and pull's own act of writing new content is exactly what
  makes the tree dirty. **Mitigation, to resolve in planning, not
  implementation**: either (a) `--to-sectioned` on pull must special-case
  its own dirty-tree check to mean "clean relative to the state *before
  this pull's own write*, i.e. compare against the pre-pull HEAD rather
  than requiring the post-pull working tree be clean" (so pull's own
  legitimate write doesn't trip the guard it triggers downstream), or
  (b) `--to-sectioned` requires the split to happen against the
  post-pull content but explicitly does *not* attempt git-history
  preservation for that specific entry point (since there's no prior
  commit of the newly-pulled content to extract history from), or (c) the
  flag is documented as pull-then-migrate as two logically sequential
  steps where the user is expected to have a clean tree *before* the pull
  begins, and the guard checks that pre-pull state, accepting that the
  final section files' "first" history entry is the pull's own new
  commit boundary (which the user would then need to make themselves, or
  the flag makes for them) rather than continuous history further back.
  Whichever option is chosen changes what "history preservation" actually
  means for this entry point vs. the standalone `migrate-sectioned`
  command — needs an explicit decision, not left to be discovered mid-
  implementation.

## Summary of concrete mitigations

1. Treat "split produces only a preamble" as a migration-specific
   warning/refusal, not the pass-through success `split_nodes()` itself
   defines for steady-state pull.
2. Order migration's writes filesystem/state → config-last, so a failure
   before the final `markgate.yaml` write never requires config rollback,
   only filesystem/state unwind (cheap, via a plan-scoped created-paths
   list).
3. Build the dirty-tree check as new code (no prior art exists): scope it
   to the mapping's own path, handle untracked-file and no-HEAD as their
   own refusal cases (not silently "clean"), and resolve the git repo root
   from the file's own path to handle nested/submodule repos correctly.
4. There is no lock mechanism to reuse — call this out explicitly in
   planning as either "accept the optimistic-concurrency-only guarantee
   `save_config`'s `expected_mtime` already gives us" or "add a new
   sentinel lock file and teach pull/push to respect it," and pick one
   rather than assuming reuse of something that doesn't exist.
5. Resolve the pull-then-migrate vs. migrate-then-pull ordering conflict
   for `--to-sectioned` explicitly in planning — the dirty-tree guard and
   "pull writes new content in the same invocation" are in direct tension
   as currently scoped.
