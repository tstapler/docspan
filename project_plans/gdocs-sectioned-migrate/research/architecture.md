# Architecture Research: gdocs-sectioned-migrate

## Prior research read (build on this, don't re-derive)

- `project_plans/gdocs-sectioned-sync/research/architecture.md` (§1–5): established the flat-node-list
  model, that `split_nodes()` must run *after* `projection.project()`, that sectioned pull must always use
  the structural path (never Drive HTML export), and the manifest-order-is-authority invariant.
- ADR-001 (manifest keyed by `heading_id`, sidecar YAML), ADR-002 (reorder = in-place move, `heading_id`
  churn accepted as documented limitation when a reorder also carries a content edit), ADR-003 (sectioned
  pull always structural path).
- `implementation/plan.md`'s Domain Glossary, Pattern Decisions, Migration Plan (explicitly scoped *out*
  auto-migration — this project is the fast-follow that was deferred), Epic 5/6, Summary of new modules.
- No `decisions/architecture-review.md` exists in `gdocs-sectioned-sync` (checked — only the three ADRs
  above are present); the task brief's reference to it was a naming guess, not a missing-file gap on my
  part.

All four questions below were checked directly against the current source, not just the plan (the plan
predates the merge; source is ground truth): `src/docspan/config.py`, `src/docspan/cli/main.py`,
`src/docspan/backends/google_docs/section_splitter.py`, `manifest.py`, `backend.py`, `core/orchestrator.py`.

## 1. Where migration logic lives, and how it reuses `split_nodes()`

**New module: `src/docspan/backends/google_docs/migration.py`**, not a method bolted onto an existing
class. Reasoning:

- `GoogleDocsBackend` (`backend.py`) already carries `pull()`/`pull_sectioned()`/`push()`/`push_sectioned()`
  at ~850-1050 lines; migration is a *one-shot* orchestration that calls into the backend (for the live
  fetch) and into `config.py`/state (for the config+state rewrite) — it doesn't belong inside the backend
  class itself, which has no business writing `markgate.yaml` or `SyncState` (those are `cli/main.py`/
  `core/orchestrator.py` concerns today, kept out of `backend.py` by convention — grep confirms
  `backend.py` never imports `docspan.config` or `core.state`).
- `section_splitter.py` is deliberately backend-adjacent but stateless (pure function over `Node` lists,
  no I/O) — `migration.py` sits at the same layer, doing I/O orchestration the way `core/orchestrator.py`
  does for pull/push, but scoped to the google_docs backend package since (per Constraints) only
  `google_docs` is sectioned-capable, matching where `section_splitter.py`/`manifest.py` themselves live.

**Critical finding that changes the shape of "reuse `split_nodes()` without a second pipeline" — a real
gap in requirements.md's Feasibility Risks that needs to be resolved, not assumed:**

`split_nodes()` (`section_splitter.py:62-191`) takes `Sequence[Node]` and only reads `node.heading_id`,
`node.style`, `node.text` off whatever it's given (duck-typed, confirmed at lines 130-135, 111). It does
not care whether those nodes came from `DocsStructureParser` (live fetch) or `MarkdownToParagraphParser`
(local markdown → nodes, used today only for the *push* direction, `markdown_to_paragraph_parser.py:578-`).
So "reuse `split_nodes()`" is satisfiable either way — the real design question, which requirements.md's
own Open Questions flags but doesn't resolve, is **which node source to split**, because the two sources
disagree on exactly the field the manifest needs:

- **Local-markdown-parsed nodes** (`MarkdownToParagraphParser().parse(existing_file_content)`): produces
  a `Node` list whose `heading_id` is always `None`/unset — `HeadingTokenConverter`
  (`markdown_to_paragraph_parser.py:466-478`) has no way to invent a Docs-assigned id, and confirmed
  nothing in that parser ever sets `heading_id` (grepped — the only writer of `DocsParagraphNode.heading_id`
  in the whole tree is `docs_structure_parser.py:712`, `paragraph_style.get("headingId")`, i.e. only the
  Docs-API-parsing path). Splitting this gives byte-exact original content but **every section gets
  `heading_id=""`**, which `manifest.py`'s `SectionManifestEntry` and ADR-001's whole identity model treat
  as "no real id" — this would make every migrated section indistinguishable from `PREAMBLE_HEADING_ID`-
  adjacent junk identity, and the *next* `push_sectioned`'s `SequenceMatcher`-over-`heading_id` reorder
  classifier (plan.md's Pattern Decisions) would have nothing real to key on.
- **Live-doc-parsed nodes** (`backend.pull_sectioned()`'s own path: `get_document()` →
  `DocsStructureParser().parse()` → `project()`, `backend.py:1408-1412`): produces real, Docs-assigned
  `heading_id`s, but re-renders through `render_nodes_to_markdown()` — a genuinely different pipeline
  (ADR-003's own Consequences section already documents this: "sectioned pull's output may have subtly
  different formatting-fidelity... since it's a genuinely different conversion pipeline"). Re-rendered
  section text is **not guaranteed byte-identical** to what's already committed in the single local file,
  which directly threatens the git-history-preservation requirement (§4 below).

**Resolution — split both, zip them, reuse `split_nodes()` twice, no second pipeline:**

1. Live fetch (read-only, same call `pull_sectioned` already makes): `doc → DocsStructureParser().parse()
   → project()` → `live_nodes`. Call `split_nodes(live_nodes, split_level)` → `live_sections` (real
   `heading_id`s, in doc order, content discarded — only `.heading_id` and `.title` are kept).
2. Local content: `MarkdownToParagraphParser().parse(open(mapping.local).read())` → `local_nodes`. Call
   `split_nodes(local_nodes, split_level)` → `local_sections` (byte-faithful content, `heading_id=""`).
3. Guard: `len(live_sections) == len(local_sections)` and titles line up pairwise (position-based
   matching is safe here — see the git-clean-tree gate below, which already guarantees the *local* side
   hasn't drifted from the last synced state; a live/local *count* mismatch means the remote moved since
   last pull, and migration must abort with "remote has diverged — run `docspan pull` first," the same
   shape as the existing local-only/dirty-tree refusal pattern rather than a new error vocabulary).
4. Zip `live_sections[i].heading_id` onto `local_sections[i]` (both dataclasses are `@dataclasses.dataclass`,
   trivially replaced via `dataclasses.replace`) and write `local_sections[i]`'s **local**, byte-original
   content to each new section file.

This calls `split_nodes()` twice against two different node sources it already accepts by duck typing —
zero new splitting logic, satisfying the "no second pipeline" constraint literally, while resolving
requirements.md's own open question ("can migration obtain real heading_ids without a live pull?" — **no**,
confirmed by tracing `heading_id`'s only writer to the Docs-API parse path, so a live fetch is required,
but it only needs to be read for identity, never for content).

## 2. CLI command structure (`docspan migrate-sectioned`)

Same file (`src/docspan/cli/main.py`), same patterns as `pull`/`push`/`migrate-xdg`:

- `@app.command("migrate-sectioned")` alongside the existing commands (`push` at line 220, `pull` at 427,
  `migrate-xdg` at 799 — `migrate-xdg` is the closest existing precedent for "a command that rewrites
  config/state as a one-shot migration," not `pull`/`push`'s per-run sync loop).
- Mapping resolution: reuse `resolve_mapping_for_path(mappings, file)` (`cli/main.py:515-533`) exactly as
  `pull`/`push` already do (lines 443, 256) — a target for `migrate-sectioned <path>` is looked up the
  same way, then the command must additionally validate `not mapping.sectioned` (already-sectioned is a
  no-op/error, not silently re-run) and `mapping.backend not in _SECTIONED_UNSUPPORTED_BACKENDS`
  (`config.py:90`) — this second check should be a **shared helper**, not reimplemented in the CLI, since
  `Mapping._validate_sectioned_split_level` (`config.py:114-142`) already encodes the same rejection; see
  §3 for why the CLI still needs an explicit pre-check rather than only relying on that validator.
- Options: `--dry-run` (mirrors `pull`/`push`'s existing `--dry-run` flag, same flag name/semantics per
  requirements.md's own question about matching convention — reuse, don't invent a table format, since
  `push --dry-run`'s `preview.render()` pattern at `cli/main.py:298-313` already establishes "print a
  human-readable preview object" as this codebase's convention; a `MigrationPreview` dataclass with its
  own `.render()` is the natural fit, not a bespoke table).
- Error handling: same `err_console.print(...); raise typer.Exit(1)` idiom used throughout (e.g. lines
  318-323, 448-449) — no new error-reporting mechanism.
- `--to-sectioned` on `pull` (line 428): a boolean flag added to the existing `pull()` signature that,
  when set and `not mapping.sectioned`, calls the same underlying `migration.migrate_sectioned(...)`
  entry point *before* the normal pull dispatch for that mapping, then continues the loop treating the
  now-`sectioned` mapping normally for the rest of that run (matches requirements.md Scope: "invokes the
  same underlying migration logic inline as part of a pull run"). This means `migration.py`'s public
  entry point must be a plain function callable from both `cli/main.py`'s `migrate_sectioned` command and
  from inside `pull()`'s per-mapping loop — not something that assumes it owns the whole CLI invocation
  (e.g. it must not itself call `typer.Exit`; it should raise/return a result object that each caller
  formats differently).

## 3. Config-update sequence — does the `sectioned`/`split_level` validator fire naturally?

**Yes, but only if migration goes through `save_config()`, and it must write `split_level` and `sectioned`
in the same mutation, not two separate writes.**

`Mapping._validate_sectioned_split_level` (`config.py:114-142`) is a Pydantic `model_validator(mode="after")`
— it runs whenever a `Mapping` instance is *constructed*, which happens in exactly two places that matter
here:

1. **In-memory, immediately**: if migration code does `mapping.sectioned = True;
   mapping.split_level = "HEADING_1"` on an already-constructed `Mapping` object, **the validator does
   NOT re-run** — Pydantic v2 `BaseModel` only validates on construction (or with `model_validate`/
   `validate_assignment=True`, and `Mapping` doesn't opt into `validate_assignment` — confirmed no
   `model_config` override in `config.py:93-142`). So naive in-place attribute mutation would silently
   produce an inconsistent in-memory `Mapping` (e.g. `sectioned=True, split_level=None`) that only gets
   caught the *next* time `markgate.yaml` is loaded fresh (`load_config()` → `MarkgateConfig(**raw)` →
   each `Mapping(**entry)` constructed from scratch, validator fires there).
2. **On disk, at the next `load_config()`**: `save_config()` (`config.py:180-221`) does
   `config.model_dump(exclude_none=True)` then merges into the YAML doc and writes it — it does not
   re-validate the `MarkgateConfig` it's given (no re-`model_validate` call in `save_config`). So a
   `save_config()` call with an invalid in-memory `Mapping` would happily write invalid YAML to disk,
   and the error would only surface on the *next* `docspan pull`/`push` invocation's `load_config()` —
   too late for a migration command, which needs to fail loudly at the point of the bad write, not on
   the user's next unrelated command.

**Required sequence for `migration.py`**: build the new `Mapping` via **construction**, not mutation —
`new_mapping = mapping.model_copy(update={"sectioned": True, "split_level": split_level, "local":
new_dir_path})` — `model_copy(update=...)` in Pydantic v2 also does **not** re-run validators by default
(same caveat as plain attribute assignment). So the safe pattern is explicit: construct a fresh
`Mapping(**{**mapping.model_dump(), "sectioned": True, "split_level": split_level, "local": new_dir_path})`,
which *does* go through `__init__` and therefore the validator, and only proceed to
`config.mappings[i] = new_mapping; save_config(config, ...)` once that construction has already succeeded
— i.e., **validate before mutating the config object in memory, not by trusting `save_config` to catch
it**. This also naturally rejects a Confluence mapping (`_SECTIONED_UNSUPPORTED_BACKENDS`) at the same
construction step, so the CLI's pre-check in §2 is a fast, friendlier-message early exit, not the only
enforcement point — defense in depth matching how `push()` already treats CLI checks as UX sugar over a
backend/model-level enforcement (`cli/main.py:291-296`'s own comment: "the CLI is NOT the enforcement
point").

`local` must change from the single file path to the new directory path in the *same* validated
construction — `Mapping` doesn't validate that `local` is a directory vs. file (it's just `str`), so
nothing blocks this, but migration must decide and apply the new `local` value atomically with
`sectioned`/`split_level`, not leave a stale file-shaped `local` pointing at a path that no longer exists
after the section files are written.

## 4. Rollback/staging vs. git-observable renames — the real conflict, reconciled

Requirements.md is correct that this is a genuine tension, and it has a concrete resolution once you
separate **two different atomicity mechanisms already in this codebase that solve different problems**:

- `manifest.py`/`config.py` use **temp-file + `os.replace`** (single-file atomic swap) — irrelevant here,
  wrong granularity.
- `pull_sectioned` (`backend.py:1425-1451`) uses **temp-dir + `_atomic_replace_dir`** (whole-directory
  atomic swap, delegating to `core/atomic_dir.atomic_replace_dir` per the docstring at line 1483-1490) —
  this is filesystem-atomic (`os.rename`/two `os.replace` calls) but **git has no awareness of it at
  all**: it operates entirely outside git's index, before anything is staged.

Git's rename/copy detection (`-M`/`-C`, what `git log --follow`/`git blame` rely on) is a **diff-time
heuristic computed from committed blob content**, never something baked into how a file was written to
disk. This means the staging-dir mechanism and the git-history mechanism don't actually compete for the
same step — they compose in sequence:

1. **Compute + validate phase** (fully reusable, zero git involvement): run §1's split (live fetch for
   `heading_id`s + local split for content), build the manifest, render `--dry-run` output — all of this
   happens against a scratch temp directory exactly like `pull_sectioned` already does. This is where a
   partial failure (manifest write fails, live fetch fails, count-mismatch guard trips) aborts with the
   working tree completely untouched — no different from today's pull crash-safety story. `--dry-run`
   stops here unconditionally.
2. **Commit-to-working-tree phase** (new, git-aware, short): only after step 1 fully succeeds,
   `_atomic_replace_dir`-swap the validated temp directory into place as `mapping.local`'s **new**
   directory path (this part *is* reusable verbatim — `core/atomic_dir.atomic_replace_dir` doesn't care
   whether the caller is `pull_sectioned` or `migration.py`), then `os.remove()` the old single file.
   Record every path touched (old file removed, new directory + N files + manifest created) in an
   in-memory transaction log *before* touching the filesystem for this phase, so a failure partway
   through (e.g. the manifest write inside the swap succeeded but the old-file removal then raised
   `PermissionError`) can be unwound by replaying the log backwards (`os.replace` the swapped-out `.old.*`
   sibling directory back, or simply delete the newly-created directory and leave the never-yet-removed
   original file in place) — this is the "transaction log to unwind" option requirements.md's Rabbit
   Holes names, and it's the right one *specifically because* phase 2's operations are irreversible
   filesystem moves, not something a second atomic-rename can wrap (there is no single parent directory
   whose swap covers "old file gone, new directory present" as one unit — they're two different paths).
3. **Git-visibility phase** (new, no atomicity concerns — it's advisory, not correctness-critical): once
   phase 2's working tree matches the target state, run `git add <new-section-files> <manifest>` and
   `git rm --cached <old-file>` (or rely on the user's own `git add -A` / a printed instruction, per the
   Risk Control's existing standing rollback path of "`git revert` the migration commit") so the *next*
   commit is a clean one-shot in the working tree — this step doesn't need to be transactionally coupled
   to phase 2's rollback at all, because if phase 2 already succeeded, the working tree is correct; git
   staging is just catching it up, and failing to `git add` cleanly is recoverable with an ordinary
   `git status`/`git add` retry, unlike phase 2's file moves.

**The part that genuinely cannot be fully solved, and must be documented rather than promised**: git's
`-M`/`-C` similarity index compares each new blob's content against each deleted blob's content by
line-overlap ratio. A 1-file-to-N split means each new section file is, by construction, only a **fraction**
of the original file's lines — for anything past 2-3 roughly-equal-sized sections, every individual
section's similarity to the whole original file falls under git's default 50% rename threshold, so `git
log --follow` (which only follows a single detected *rename*, not multiple *copies*) will **not**
automatically trace most section files back through the original file's history. What migration *can*
guarantee (and must, per §1's resolution) is writing byte-identical original text into each section file
— this maximizes the chance that `git log --follow -C --find-copies-harder` (explicit, lower-threshold
copy detection, which the user must opt into — it is not git's default for `log` or `blame`) recovers
attribution, but "zero manual reasoning, git blame just resolves" as stated in requirements.md's Success
Metrics is **not achievable with git's default flags** for a genuine one-to-many split; this needs to be
either (a) walked back to "resolves with `git log --follow -C`" in requirements, or (b) accepted as a
best-effort guarantee with the caveat documented in the command's own `--help`/output. This is a
correction to requirements.md's Success Metrics, not a new rabbit hole — flag it back to planning rather
than silently narrowing scope.

## Summary of new/changed surface area

- `src/docspan/backends/google_docs/migration.py` (new): `migrate_sectioned(mapping, backend, config,
  config_path, state, state_dir, state_path, split_level, dry_run=False) -> MigrationResult`. Calls
  `split_nodes()` twice (§1), builds the manifest via `ManifestStore.save` (reused as-is), stages via a
  temp dir, swaps via `core.atomic_dir.atomic_replace_dir` (reused as-is), removes the old file, and
  returns a result object `cli/main.py` and `pull()`'s `--to-sectioned` branch both render.
- `src/docspan/cli/main.py`: new `@app.command("migrate-sectioned")`; `pull()` gains `--to-sectioned`
  (calls into the same function before normal pull dispatch for that mapping).
- `src/docspan/config.py`: no schema change (`sectioned`/`split_level` already exist from
  gdocs-sectioned-sync) — migration must construct a *new* `Mapping(**{...})` (not mutate/`model_copy` an
  existing instance) so `_validate_sectioned_split_level` actually re-runs before `save_config()` persists
  it (§3).
- `core/orchestrator.py` / `core/state.py`: migration must produce `SyncState` entries matching exactly
  what `orchestrate_push`'s existing per-section-file state recording already does (`orchestrator.py:378-395`,
  one `MappingState` per section file path) and remove the old single-entry state for `mapping.local`'s
  prior (file) path — reuse `_record_state`, don't hand-roll a parallel state shape (this directly answers
  requirements.md's SyncState Rabbit Hole: the shape to match already exists and is exercised by every
  ordinary sectioned push, not something migration needs to newly design).
