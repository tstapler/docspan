# Research: Declarative Google Doc and Tab Creation

## Codebase Findings

### Config Model (`src/docspan/config.py`)

`Mapping` (line 133) already has:
- `remote_id: Optional[str] = None` — documented as "None means not yet created" in a comment
  but the current push flow only handles it interactively (prompt user) or errors in CI
- `tab_id: Optional[str] = None` — targets a specific tab in a multi-tab doc
- `@model_validator` pattern exists for cross-field validation (`sectioned` + `split_level`)
  — the same pattern is available for `create` + `tab_in` validation

New fields needed:
- `create: bool = False` — signals this mapping should be auto-created on push if remote_id is None
- `tab_in: Optional[str] = None` — local path of the parent mapping whose doc this is a tab of
- `tab_title: Optional[str] = None` — optional explicit tab title (fallback: first H1 / basename)

Existing validator infrastructure in `config.py`:
- `_SECTIONED_UNSUPPORTED_BACKENDS` set — same pattern can gate `tab_in` on google_docs only
- `save_config()` uses round-trip YAML (ruamel.yaml) preserving comments, atomic write with mtime conflict detection

### CLI Push Flow (`src/docspan/cli/main.py`, lines 292–405)

Current handling of `remote_id is None` (line 322–365):
1. In non-interactive (CI), prints error and exits
2. In interactive, prompts user (Y/N) then calls `backend.create(title)`
3. Writes `remote_id` back into `mapping` object
4. Calls `save_config()` — but catches `ConfigConflictError` and prints the orphaned ID

The proposed feature replaces step 1 (CI error) with auto-creation when `create: true`. The
write-back logic (steps 3-4) is already present; the key change is triggering it before content
push rather than abandoning in CI.

### Backend Layer

`Backend.create(title) -> CreateResult` — creates a new Google Doc, returns `doc_id` + URL.  
`Backend.create_tab(doc_id, title) -> CreateResult` — adds a tab to existing doc, returns `tab_id`.  
`CreateResult` fields: `doc_id`, `title`, `url`, `tab_id` (set only by `create_tab`).

Both methods exist on `GoogleDocsBackend`; `create_tab` raises `NotImplementedError` on backends
without tab support (Confluence). No changes needed to the backend layer itself.

### Orchestrator (`src/docspan/core/orchestrator.py`)

`orchestrate_push()` (line 374) asserts `mapping.remote_id is not None` at line 395 — 
creation must happen before this call. Creation logic either lives in CLI push (current approach)
or moves to a pre-push creation phase (cleaner for the declarative path).

### `docspan map` command (lines 933–1065)

Already handles `--new-tab-in` for single-file imperative mapping. The `_default_title(file)`
helper at line 1012 reads the file's first H1 — this exact function can be reused for bulk
`--from-dir` title derivation.

### Tests

- `tests/test_cli.py` has `TestPushCommand` and `TestMapCommand` classes with fake backends
- `FakeGoogleDocsBackend` (line ~55) implements `create()` and `create_tab()` already
- Pattern for testing config write-back: check `docspan.yaml` after runner invocation
- `test_interactive_confirm_yes_creates_persists_and_pushes` (line 602) — closest existing test
  for creation flow; the new tests mirror this but without interactive prompts and with `create: true`

## Design Decisions

### Where to put creation phase

**Option A**: In the push command loop (current location, just remove the CI guard for `create: true`)  
**Option B**: A dedicated pre-push pass that resolves all `create: true` mappings first, then push

Option B is better because:
1. Topological sort over all `tab_in` dependencies is naturally expressed as a separate phase
2. All IDs written back before any content push — if content push fails, IDs are still saved
3. Cleaner separation: creation is a one-time setup, push is recurring

### Topological sort

`tab_in` forms a DAG: parent docs (no `tab_in`) must be created before child tabs. Standard
Kahn's algorithm or `graphlib.TopologicalSorter` (Python 3.9+, already in stdlib). Cycle
detection is free: any node left in the graph after sort completes is a cycle member.

### Config write-back after each creation

Write config after each individual creation (not a batch at the end), so partial runs are
resumable. Use `save_config(expected_mtime=...)` to detect concurrent edits. If write fails,
print the orphaned ID (same as existing `ConfigConflictError` handling).

### `--from-dir` implementation

1. Glob `*.md` in DIR
2. Sort: README.md first, then alphabetically
3. Create a single new doc from README.md (or the first file)
4. Add each remaining file as a new tab
5. Write all mappings to config at the end

This is purely a CLI convenience that calls the same backend methods as the declarative path.

### `--allow-create` flag on push

The proposal raises this as an open question. Recommendation: **do not require it**. The
intent is already declared explicitly in the YAML with `create: true`. Requiring an additional
flag on every push run (including CI) defeats the declarative UX goal. Document that `create: true`
in config is the authorization.

## Risks

- **Orphaned remote docs on partial failure**: mitigated by writing ID back to config immediately
  after each creation. A re-run skips already-created entries.
- **Tab ordering**: Google Docs API adds tabs in the order they are created; YAML file order is
  used as the intended canonical order. Document this.
- **`t.0` tab_id assumption**: The first tab of a newly created Google Doc has ID `t.0`. This is
  verified behavior (existing multi-tab docs in the codebase show this format). If the API changes
  this, `create()` could be extended to return the first tab's ID explicitly.
- **mtime conflict on config write-back**: The `save_config(expected_mtime=...)` mechanism
  handles concurrent edits, but a rapid sequence of creations (one per tab) each re-reads mtime.
  Ensure mtime is re-read after each successful save, not cached once at the start.
