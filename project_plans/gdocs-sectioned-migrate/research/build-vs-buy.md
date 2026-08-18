# Build vs. Buy: gdocs-sectioned-migrate

Agent 6, Phase 2 (Research). Verdicts per component the requirements call out.

## 1. Git-history-preserving file split — BUILD (in-house), not an existing tool

Candidates considered:

- **`git-filter-repo`** (and legacy `git filter-branch`) rewrite history across
  an *entire repo* (or a path within it) to produce a differently-shaped
  history — e.g. extracting a subdirectory's commits into a new repo, or
  removing a path from every commit. They operate on the commit graph itself
  and rewrite every affected commit's tree. That's the wrong shape here: this
  feature needs one *new* commit, in the *existing* repo, that git's own
  rename/copy-similarity heuristic can follow backward through the file's
  pre-existing history — not a rewritten history.
- **`git subtree split`** extracts a subdirectory's history into a form
  usable as its own branch/repo (for splitting a monorepo). It operates on
  directories that already exist as distinct paths across history, not on
  slicing one file's *content* into N new sibling files at a single point in
  time. Not applicable.
- **`git mv` / rename detection generally**: this is the right mechanism, but
  it's not a tool to shell out to beyond what git already does automatically.
  Git has no "split file A into B, C, D and preserve blame" primitive — rename
  detection (`-M`) and copy detection (`-C`, off by default, needs
  `--find-copies-harder` for `blame`/`log --follow` to catch a copy from
  unstaged deletion) work by comparing blob *content similarity* between a
  deleted path and each added path in a commit, not by matching against
  developer intent. The existing Rabbit Hole note in requirements.md is
  correct: the way to get `git log --follow`/`git blame` continuity is to
  write each section's *exact original byte content* unchanged into its new
  path (git's delete-old+add-new-with-shared-content is what its similarity
  index picks up as a rename/copy) — there's no external tool for this,
  it has to be the migration command's own commit-construction logic
  (delete old file, write new files with unmodified extracted content, single
  commit, verify with `git log --follow` in tests).

**Verdict: build.** No existing tool solves "slice one file into N new
files while keeping each slice's line history" — the tools above solve a
different problem (repo/subtree extraction, not intra-repo content slicing).
The mechanism is: write unmodified original-byte spans to new paths in the
same commit that removes the old file, and let git's default rename/copy
similarity detection (already active, no tool install needed) do the
attribution. Rollback (Rabbit Hole #3) is also in-house: since the whole
migration lands as a single commit, `git stash`/working-tree restore before
commit (for the dry-run-adjacent staging phase) or `git revert` after
commit are the two rollback levers already implied by requirements.md's Risk
Control section — no transaction-log library needed.

## 2. Heading IDs without a live pull (Open Question #1) — REUSE existing client, but it is not local-only

Checked `src/docspan/backends/google_docs/client.py`:

- `GoogleDocsClient.get_document(doc_id, include_tabs_content=True)`
  (`client.py:119`) returns the **full document JSON body structure**, and
  `DocsStructureParser` reads `headingId` off of
  `paragraph.paragraphStyle.headingId` (`docs_structure_parser.py:712`) while
  walking that body. This is the *only* place a real, Docs-API-assigned
  `heading_id` comes from anywhere in the codebase — confirmed via
  `grep -n heading_id docs_structure_parser.py`.
- The two metadata-only calls that exist, `get_modified_time` (`client.py:480`,
  Drive `files().get(fields='modifiedTime')`) and `get_doc_info`
  (`client.py:511`, Drive `fields='id,name,modifiedTime,mimeType'`), are Drive
  file-metadata lookups. Neither touches document body content, so neither
  can ever surface `headingId`s — that's a Docs API structural field, not a
  Drive metadata field. There is no cheaper Docs API call that returns just
  heading IDs; the Docs API's `documents.get` returns the whole body or
  nothing.

**Verdict: reuse, not build — but the requirements' Feasibility Risk is
confirmed, not dismissed.** `migrate-sectioned` should call the existing
`client.get_document()` (no new API client code needed) to obtain real
`heading_id`s, exactly as `pull_sectioned` already does. But that means
migration is **not** purely a local-markdown-splitting operation — it
requires one live API round trip (a full document fetch), same cost as a
pull. Planning should either: (a) treat migration as implicitly doing a
one-time authenticated fetch (simplest, reuses 100% of existing
`DocsStructureParser` + `split_nodes` + `heading_id_to_slug` pipeline the
constraints already mandate reusing), or (b) fall back to a synthetic
per-section id (mirroring `PREAMBLE_HEADING_ID`'s pattern) for a
credential-less/offline mode if that's ever required — but nothing in
requirements.md asks for offline migration, so (a) is the straightforward
answer and resolves Open Question #1: yes, real heading_ids are obtainable,
via the existing full-document fetch, not new logic.

## 3. Manifest/YAML handling — REUSE as-is, confirmed sufficient

`src/docspan/backends/google_docs/manifest.py`'s `ManifestStore` already
provides everything migration needs:

- `ManifestStore.save()` (`manifest.py:153`) does atomic temp-file-in-same-dir
  + `os.replace()` writes — exactly the "manifest write fails, don't leave a
  partial file" property the Rollback rabbit hole worries about, already
  built and tested for `gdocs-sectioned-sync`.
- `ManifestStore.load()` and `SectionManifestEntry` give the identical shape
  `pull_sectioned` produces (the requirements' explicit non-negotiable: "must
  produce state that looks identical to what a fresh `pull_sectioned` would
  have produced").
- `_validate_filename()` (`manifest.py:68`) already guards path traversal on
  manifest-sourced filenames, relevant since migration is another writer of
  this same file format.

**Verdict: build = reuse.** No changes needed to `manifest.py` itself;
migration should call `ManifestStore.save()` with entries built from the same
`split_nodes`/`heading_id_to_slug` pipeline pull_sectioned uses, per the
existing project constraint. No new manifest/YAML library or format needed.

## 4. Dry-run/diff-preview formatting — REUSE `rich` (already a dependency), no new library

- `rich>=13.0.0` is already a direct dependency (`pyproject.toml`) and already
  used by the CLI (`src/docspan/cli/main.py` imports rich and uses inline
  markup like `console.print(f"[yellow]dry-run[/yellow] ...")` at
  `main.py:312` and `main.py:466` for `pull --dry-run`/`push --dry-run`).
- No existing code uses `rich.table.Table` for dry-run output — the
  established convention is one styled line per item, not a table.

**Verdict: reuse `rich`, no new dependency.** For consistency (Open Question
#3), the simplest and most consistent option is one `[yellow]dry-run[/yellow]`
line per proposed section file (mirroring the existing single-file dry-run
line), listing `NN-slug.md` and its heading title — matching, not diverging
from, `pull`/`push --dry-run`'s established shape. A `rich.table.Table` is
available in the same dependency if planning decides a tabular view reads
better for N rows, but it would be a new *pattern* (not a new *library*) —
call this out in planning as a UX choice, not a build-vs-buy question.

## Summary Table

| Component | Verdict | Notes |
|---|---|---|
| Split-one-file-into-many w/ git history | Build | `git-filter-repo`/`subtree split` solve repo/subtree extraction, not intra-repo content slicing; mechanism is unmodified-content + git's built-in rename/copy similarity detection |
| Real Google Docs `heading_id`s | Reuse existing `client.get_document()` | No metadata-only Docs API call exists; this is a live full-document fetch, same cost as pull — resolves Open Question #1 |
| Manifest/YAML I/O | Reuse `ManifestStore` as-is | Already atomic-write, already produces `pull_sectioned`-identical shape |
| Dry-run preview formatting | Reuse `rich` (already a dep) | Match existing one-line-per-item convention; `Table` is a UX option, not a new dependency |
