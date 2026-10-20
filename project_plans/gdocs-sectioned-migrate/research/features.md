# Research: Features (Agent 2)

Sources: `project_plans/gdocs-sectioned-sync/implementation/plan.md`, and the
shipped code it produced (PR #106) — `src/docspan/backends/google_docs/{manifest,section_splitter,markdown_to_paragraph_parser,backend}.py`,
`src/docspan/core/{orchestrator,state}.py`, `src/docspan/config.py`.

## 1. Exact target shapes migration must reproduce

**`_manifest.yaml` entry** (`manifest.py:38-65`, `SectionManifestEntry`):
`heading_id: str`, `slug: str`, `filename: str` (`NN-slug.md`, zero-padded
width = `max(2, len(str(len(sections)-1)))`), `title: Optional[str]`. The
preamble (content before the first split-level heading) always gets an
entry using the sentinel `PREAMBLE_HEADING_ID = "__preamble__"`
(`manifest.py:31`) and `PREAMBLE_SLUG = "preamble"` (`section_splitter.py:28`)
— it is never omitted even if empty.

**`SyncState` per-section entries** (`core/state.py:11-18`,
`core/orchestrator.py:384-396`): `SyncState.mappings` is a flat
`dict[str, MappingState]` keyed by file path — sectioned mode does **not**
add a new schema, it just adds one entry per section file, keyed by that
section file's own path (e.g. `<dir>/00-preamble.md`, `<dir>/01-intro.md`,
...), each an independent `MappingState(doc_id, backend, last_synced_at,
base_hash, remote_version, local_hash)` with the *same* `doc_id`/
`remote_version` (one Google Doc, one remote version) but its own
`base_hash`/`local_hash` computed via `record_state(... content ...)`
(`orchestrator.py:316-330`). Migration must call the same `_record_state`/
`record_state` helper per section file it creates, not hand-build
`MappingState`.

**`markgate.yaml` fields** (`config.py:104-139`, `Mapping`): `sectioned:
bool = False`, `split_level: Optional[str]` (must be a `HEADING_N` string
in `_VALID_SPLIT_LEVELS`). Pydantic validator `_validate_sectioned_split_level`
enforces: `split_level` set requires `sectioned: true` and vice versa, and
`sectioned: true` on a backend in `_SECTIONED_UNSUPPORTED_BACKENDS = {"confluence"}`
(`config.py:90`) raises at config-load time. `Mapping.local` must become a
directory path, not the old file path, when `sectioned: true`.

## 2. How `pull_sectioned` actually produces this (backend.py:1377-1476)

Always: `self._client.get_document(doc_id)` (live Docs API call) →
`DocsStructureParser().parse(doc)` → `project(nodes)` → `split_nodes(nodes,
split_level, existing_entries=...)` → per section, `render_nodes_to_markdown`
→ write to a temp dir → `ManifestStore.save` → atomic `os.replace`-based
swap into place (`_atomic_replace_dir`). `heading_id` on each
`SectionManifestEntry` comes from `section.heading_id`, which
`section_splitter.split_nodes` sets to the *heading node's own*
`heading_id` attribute — and that attribute is populated only by
`docs_structure_parser.py:712`: `heading_id=paragraph_style.get("headingId")`,
i.e. read directly off the Google Docs API's paragraph style JSON. There is
no other producer of a non-`None`, non-sentinel `heading_id` anywhere in the
codebase.

## 3. Open Question #1 — resolved

**Migration cannot obtain real Google Docs `heading_id`s without a live
API pull, and there is no code path that fakes or derives one.**

Evidence:
- `heading_id` is exclusively API-assigned (`docs_structure_parser.py:712`,
  confirmed by `manifest.py:26-31`'s own comment: "Real Google Docs
  heading_ids are always API-assigned opaque strings").
- The only other place that turns markdown *back* into `Node`-shaped
  objects — the mechanism migration's local-only split would have to reuse
  per the requirements' Constraint ("reuse `split_nodes`") — is
  `MarkdownToParagraphParser` (`markdown_to_paragraph_parser.py:578`),
  used today only for local→remote diffing in `_build_push_plan`
  (`backend.py:230`). It has no `heading_id` assignment anywhere in the
  file (grep returns zero hits); nodes it builds get the dataclass default,
  `None`.
- `split_nodes` (`section_splitter.py:62-100`) is agnostic to *where* its
  `Sequence[Node]` came from — it only reads `node.style` to find split
  points and `section.heading_id` off the heading node. So it *can* run on
  `MarkdownToParagraphParser` output structurally, but every resulting
  section's `heading_id` will be `None`/falsy, never a real Docs id.

**Recommendation**: migration must use `PREAMBLE_HEADING_ID`-style
synthetic sentinels (e.g. one per section, not just the preamble) for
every section it creates from local-only markdown, and treat "acquire real
`heading_id`s" as a follow-up act performed by the *next* `pull_sectioned`
run against the live doc — not something `migrate-sectioned` itself can
produce. Concretely:
- `migrate-sectioned` writes manifest entries with placeholder ids (a
  scheme like `"__migrated-{index}__"`, following the `__preamble__`
  convention so real API ids — always opaque non-`__`-prefixed strings —
  can never collide).
- The very next `docs pull` on that mapping will run `pull_sectioned`
  against the live doc, which regenerates the manifest from scratch with
  real `heading_id`s and (per `backend.py:1017-1039`, the existing rename
  ladder) treats this the same way sectioned-sync already treats
  `heading_id` churn from a Google-side delete+reinsert: a one-time
  best-effort rename match, falling back to first-sync-style
  fresh-manifest generation if it can't line sections up. This is not a
  new mechanism — it is exactly what already happens today whenever a doc
  edit invalidates `heading_id`s, so migration inherits an existing,
  tested reconciliation path rather than requiring a new one.
- This should be called out explicitly in the command's success output
  (e.g. "section identity will stabilize after your next pull") so users
  don't treat migration's placeholder manifest as permanent identity.

This resolves Feasibility Risk #1 in requirements.md as **confirmed, not
speculative**: the constraint is real, and the mitigation is "immediate
placeholder + reconciled on next live pull," not a blocker to shipping.

## 4. CLI/UX feature surface

No `migrate-sectioned` command or `--to-sectioned` flag exists yet (greenfield,
as expected — `cli/main.py` has no `sectioned`/`migrate-sectioned` hits besides
the sectioned-sync plumbing and an unrelated `migrate-xdg` command that can serve
as a structural precedent for a top-level `migrate-*` verb).

Validation gates and their existing analogues, in the order they should fire:
1. **Mapping resolution** — same `resolve_mapping_for_path`/lookup-by-local-path
   pattern `pull`/`push` already use (`cli/main.py`).
2. **Already-sectioned check** — reject if `mapping.sectioned` is already `True`
   (new; requirements.md Constraint: "validates the mapping is currently
   non-sectioned").
3. **Backend-capability check** — reject if `mapping.backend in
   _SECTIONED_UNSUPPORTED_BACKENDS` (`config.py:90,136-139`), reusing the same
   set the `Mapping` pydantic validator already checks, not a new constant.
4. **Clean-git-tree check** — genuinely new; nothing in the existing codebase
   checks git status. The `pull` "local-only" guard (`orchestrator.py:463`,
   `cli/main.py:475`) is a **content-hash** comparison against `SyncState`
   (`current_local_hash != entry.local_hash`), not a git operation — it
   detects uncommitted-relative-to-last-sync, not uncommitted-relative-to-git-HEAD.
   `migrate-sectioned`'s "clean git working tree" gate as described in
   requirements.md Risk Control needs a real `git status --porcelain
   <mapping.local>` (or GitPython equivalent) shell-out; there is no existing
   helper to reuse for this specific check, and none of the existing tests
   invoke git. This should be flagged in planning as new infrastructure, not
   assumed to already exist under a similar name.

Dry-run and success output should follow `pull --dry-run`'s existing
one-line-per-mapping convention (`cli/main.py:464-466`:
`[yellow]dry-run[/yellow]  ...`) as a base, extended with a per-section
list of `NN-slug.md` proposed filenames — this matches Open Question #3's
framing and is consistent with `pull`'s per-outcome status lines
(`up-to-date`/`merged`/`error`, `cli/main.py:470-500`) as the house style
for multi-line command output.

## 5. Summary for planning

- Migration's split step cannot call `backend.pull_sectioned` as-is (that
  method always re-fetches from the Docs API); it needs a new function that
  runs `MarkdownToParagraphParser().parse(content)` → `split_nodes(nodes,
  split_level)` on the *local* file's current content, using placeholder
  `heading_id`s.
- `_record_state`/`record_state` (`orchestrator.py:316-330`) is the correct,
  reusable primitive for populating one `SyncState` entry per new section
  file — do not hand-roll `MappingState` construction.
- `ManifestStore.save` (`manifest.py`) is the correct primitive for writing
  `_manifest.yaml` — reuse directly, do not hand-serialize YAML.
- The git-clean-tree gate and the git-history-preserving extraction
  mechanism are the two genuinely new pieces of infrastructure this project
  introduces; nothing in `gdocs-sectioned-sync`'s shipped code does anything
  with git directly, so there is no existing helper to delegate to.
