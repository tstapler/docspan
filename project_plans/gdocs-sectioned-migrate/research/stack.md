# Research: Stack / Technical Foundation — gdocs-sectioned-migrate

Agent 1 (Stack), Phase 2 (Research). Scope: git plumbing for history-preserving
1-file-to-N split, subprocess-vs-library choice, atomic multi-file rollback
pattern, dependency check.

## 1. Git history preservation: exact command sequence

**Empirical finding (spike, not recalled trivia)**: built a scratch repo with a
120-line `doc.md` (3 roughly-equal 40-line blocks), split it into
`section-a.md`/`section-b.md`/`section-c.md` in one commit (`git rm doc.md` +
write 3 new files with byte-identical slices of the original content + single
`git add`/`git commit`), then compared history tools:

| Command | Result |
|---|---|
| `git log --follow section-b.md` (default, no `-C`) | **Fails** — shows only the split commit |
| `git log --follow section-a.md` (default; this is the chunk holding the original preamble) | **Fails too** — shows only the split commit |
| `git log --follow -M20%` / `-C20%` section-{a,b,c}.md | **Succeeds** — shows split commit + original `add original doc` commit |
| `git blame --line-porcelain section-b.md` (default) | **Fails** — every line attributed to the split commit |
| `git blame -C -C -C section-b.md` (triple `-C`, default 50% threshold) | **Succeeds** — every line attributed to the original pre-split commit |
| `git blame -C20% -C20% -C20% section-c.md` | **Succeeds** |

**Root cause of the default-tooling failure**: git has no first-class "this
file was split" operation — `git mv` is pure sugar for `rm` + `add` and has
**zero effect on later history detection**; detection is 100% a retroactive,
per-commit content-similarity comparison between deleted and added blobs
(`-M` for 1:1 rename, `-C` for copy/fan-out). The default similarity
threshold is **50% of lines shared**. A file split into 3 roughly-equal
sections leaves each section ~33% similar to the original — under the
default threshold — so `git log --follow` and `git blame` **silently show no
pre-split history at all** unless the caller passes a lowered threshold
(`-C20%`/`-M20%`) and, for `git log --follow` specifically, copy detection
(`-C`) rather than only rename detection (`-M`, which is what `--follow`
enables internally by default).

**Implication for the requirement** ("`git blame` on any resulting section
file continues to resolve to the original commit history"): this is
achievable, but **not with plain `git log --follow`/`git blame` and no
flags** — the user (or a wrapper) must pass a copy-detection flag with a
threshold low enough for an N-way split (`N` sections → roughly `100/N %`
similarity per section, so pick a threshold with headroom below that, e.g.
`-C20%` as a safe default for splits up to ~4-5 sections; lower further for
finer splits). Docspan cannot change how a user's future `git log`/`git
blame` invocations are flagged, so the command's job is to **maximize the
chance detection succeeds when the right flags are used**, and to
**tell the user what flags to use**.

**Recommended sequence** (confirms the Rabbit Hole's hypothesis):

1. Gate: refuse unless `git status --porcelain -- <mapping.local>` is empty
   (clean tree) — see §3 for why this must run before any writes.
2. Extract each section's markdown as an **exact substring of the original
   file's bytes** — do not re-render/re-serialize through mistune or any
   markdown formatter, and do not touch trailing whitespace/newlines. Any
   reflow reduces the line-level similarity score and can push a section
   below the detection threshold entirely.
3. Perform the whole split as **one atomic commit**: delete the original
   file and create all N section files (+ `_manifest.yaml`) in the same
   tree change. Detection only compares blobs within a single commit's
   parent-diff, so splitting across multiple commits (e.g. one commit per
   section) breaks the pairing for every section after the first.
   `git rm <original>` then write+`git add` the new files is equivalent to
   `git mv` for one of them plus `git add` for the rest — no functional
   difference, since (as above) `git mv` doesn't get special treatment at
   diff time. Use whichever is more convenient in code (plain `os.remove`
   removal + `git add -A` is simplest).
4. **docspan itself should not `git commit`** — see requirements' Risk
   Control: the safety gate already requires a clean tree, and the natural
   boundary is "docspan stages the split, the user commits it" or "docspan
   commits it as a single commit it creates," either is consistent with the
   scope; recommend docspan **does** create the commit (so the migration is
   an atomic, revertable unit as the Risk Control section explicitly
   describes — "`git revert` of the migration commit is the standing
   rollback path"), since leaving it uncommitted would violate that
   rollback story.
5. Print a one-line tip in the command's summary output, e.g.:
   `Tip: git's default rename detection won't show pre-split history for these files. Use: git log --follow -C20% <file>  or  git blame -C20% -C20% -C20% <file>`
   This is the only lever docspan has over the *user's* future commands.

## 2. subprocess vs. git-wrapper library

**No existing git wrapper in docspan.** Grepped `src/docspan/` for
`subprocess`, `GitPython`, `pygit2`, `import git`: the only `subprocess` use
in the whole codebase is
[`src/docspan/backends/google_docs/mermaid_renderer.py`](src/docspan/backends/google_docs/mermaid_renderer.py)
(shelling out to the `mmdc` CLI), and there is no git-history/porcelain
tracking anywhere else — the "local-only" concept referenced in
`src/docspan/core/orchestrator.py:463` and `src/docspan/cli/main.py:475` is
docspan's own `SyncState` hash/mtime bookkeeping, unrelated to git.

**Recommendation: plain `subprocess`, no new dependency.** Reasons:
- `pyproject.toml` has neither GitPython nor pygit2 as a dependency; adding
  either is a new third-party dependency for a feature whose git usage is
  a handful of plumbing calls (`status --porcelain`, `rm`, `add`, `commit`).
  GitPython itself shells out to the `git` binary internally, so it adds an
  abstraction layer without avoiding the same external-binary dependency;
  pygit2 (libgit2 bindings) is a much heavier native dependency for no
  benefit here, and its rename/copy-detection API surface doesn't have any
  advantage over invoking `git diff -C`/`git log --follow -C` directly since
  it's the same detection algorithm.
- Mirror `mermaid_renderer.py`'s established pattern: `subprocess.run([...],
  capture_output=True, timeout=N, check=False)`, list-form argv (never
  `shell=True`), decode stdout/stderr with `errors="replace"`, and raise a
  domain-specific exception (`ManifestError`-style) on nonzero exit rather
  than letting `CalledProcessError` leak — matches `ManifestError` in
  `manifest.py` as the codebase's error-domain convention. Suggest a small
  `_run_git(*args, cwd) -> subprocess.CompletedProcess` helper local to the
  new migration module (not a generic new git-utils module unless a second
  git-touching feature appears — YAGNI).

## 3. Atomic multi-file rollback pattern

`ManifestStore.save` (`src/docspan/backends/google_docs/manifest.py:154-193`)
establishes docspan's existing atomic-write idiom: **temp file in the same
directory + `os.replace`**, cleaning up the temp file on any exception. That
pattern is exactly right for a *single file* but doesn't directly cover
"N section files + manifest + config edit + state edit, all-or-nothing."

**Recommendation: staging-directory-then-atomic-swap, not a transaction log.**

- Write every new artifact (section files, `_manifest.yaml`) into a
  **sibling staging directory** next to the mapping's target directory,
  e.g. `.<dirname>.migrate-<pid>.tmp/` in the same parent directory (same
  filesystem, so the final move is a cheap rename, not a copy — same
  reasoning `ManifestStore.save` already relies on for `os.replace`).
- Only after every file in the staging directory is written successfully:
  1. Remove the original single markdown file (`os.remove`).
  2. `os.replace(staging_dir, final_dir)` — on POSIX, renaming a directory
     over a non-existent target is atomic; if `final_dir` doesn't exist yet
     (common case: converting `doc.md` → `doc/`) this is a single atomic
     syscall, so there's no window where a partial directory is visible.
  3. Update `markgate.yaml` and `SyncState` last, using their own existing
     atomic-save mechanisms (grep confirms `manifest.py`/`config.py` both
     already use temp-file+`os.replace`; reuse those, don't reinvent).
- On any failure before step 2: `shutil.rmtree(staging_dir, ignore_errors=True)`
  and leave the original single file untouched — this is the entire rollback,
  no unwind log needed, because nothing outside the staging directory was
  ever touched until the one atomic swap.
- On failure *during* step 2/3 (after the section files are live but before
  config/state are updated): this is the one gap a pure staging-dir approach
  doesn't close for free. Order operations so the **irreversible, detectable
  step is last** and keep a plain **list of paths created so far** (not a
  generic "transaction log," just an in-memory `List[Path]` accumulated as
  each atomic step completes) so a `finally`/`except` block can `os.remove`/
  `shutil.rmtree` anything from that list if a later step raises — this is
  the minimal version of the Rabbit Hole's "transaction log of created
  paths to unwind" option, scoped down to only the steps after the one big
  atomic directory swap (config + state writes), since everything before
  that swap is already covered by the staging-directory being disposable.
- This combines both options the Rabbit Hole raised (staging dir *and* a
  small created-paths list) rather than picking one exclusively, because
  they cover different halves of the operation: staging dir for the
  bulk file writes (cheap, matches existing atomic-write idiom), created-
  paths list for the few remaining non-atomic-by-construction steps
  (markgate.yaml, SyncState) that must happen after the swap.
- `--dry-run` never creates the staging directory at all — it only computes
  and prints section boundaries/filenames, so there is nothing to roll back.

## 4. Dependency recommendation

No new dependency needed. `pyproject.toml`'s dependency list
(`typer`, `rich`, `PyYAML`, `ruamel.yaml`, `pydantic`, Google/Confluence
API clients, `python-dateutil`, `merge3`, `mistune`) has no git library and
none should be added — plain `subprocess` against the user's already-required
system `git` binary (docspan is already used inside git-backed markdown
repos per the Risk Control's "local-only"/git-based workflow assumptions)
is sufficient, matches the one existing subprocess precedent in the
codebase, and avoids a dependency whose only value-add (rename/copy
detection) is identical to what shelling out to `git diff -C`/`git log
--follow -C` already gives for free.

## Summary of concrete recommendations

1. Split as **one atomic commit** (delete original + write byte-identical
   section slices + add all + commit), never multi-commit — detection only
   pairs blobs within a single commit's diff.
2. Use **plain `subprocess`** (list-argv, `capture_output=True`,
   `check=False`, explicit error handling) — no GitPython/pygit2, no new
   dependency; mirror `mermaid_renderer.py`'s existing pattern.
3. Rollback via **staging directory + atomic `os.replace`** for the bulk
   file swap (reusing `ManifestStore.save`'s temp-file/`os.replace` idiom at
   the directory level), plus a **small in-memory created-paths list** to
   unwind the few post-swap steps (`markgate.yaml`, `SyncState`) that aren't
   already atomic by construction.
4. **Print a git-flags tip** in the command's output (`-C20%`/triple `-C`)
   since default `git log --follow`/`git blame` (spike-verified) do **not**
   show pre-split history for an N-way split with N ≥ 3 — this is a
   documentation/UX obligation the plan phase needs to account for, not
   something the split mechanics alone can guarantee.
