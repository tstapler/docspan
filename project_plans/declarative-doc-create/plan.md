# Implementation Plan: Declarative Google Doc and Tab Creation

## Overview

Four focused changes: (1) extend the `Mapping` config model with `create` and `tab_in` fields,
(2) add a topologically-sorted creation pre-pass to the push flow, (3) add `--from-dir` to
`docspan map`, (4) test coverage for all new paths.

---

## Task 1 — Extend `Mapping` model in `config.py` (backend)

**File**: `src/docspan/config.py`

Add to `Mapping`:
```python
create: bool = False
tab_in: Optional[str] = None   # local path of parent mapping (tab creation)
tab_title: Optional[str] = None  # explicit tab title; falls back to first H1 / basename
```

Extend `_validate_sectioned_split_level` (or add a second `@model_validator`) to enforce:
- `tab_in` is only allowed when `backend == "google_docs"` (same pattern as `_SECTIONED_UNSUPPORTED_BACKENDS`)
- `create: false` + `tab_in` set → validation error ("tab_in requires create: true")
- `create: true` + `remote_id` already set → validation error at load time? Or silently skip? 
  **Decision**: silently skip (idempotent: `remote_id` set means it's already been created).

Cross-mapping validation (tab_in target exists) must happen at a higher level — the `Mapping`
model has no access to the full `mappings` list. Add a validator on `MarkgateConfig` instead:
```python
@model_validator(mode="after")
def _validate_tab_in_targets(self) -> "MarkgateConfig":
    local_paths = {m.local for m in self.mappings}
    for m in self.mappings:
        if m.tab_in and m.tab_in not in local_paths:
            raise ValueError(f"mapping {m.local!r}: tab_in {m.tab_in!r} not found in mappings")
    # Cycle detection via topological sort
    ...
    return self
```

**Estimate**: 2h

---

## Task 2 — Topological sort utility (`src/docspan/core/` or inline)

**File**: `src/docspan/core/topo.py` (new, ~30 lines)

```python
from graphlib import TopologicalSorter

def topo_sort_mappings(mappings: list[Mapping]) -> list[Mapping]:
    """Return mappings in creation order: parents before tabs.
    Raises ValueError on cycles (error message names all cycle members)."""
    index = {m.local: m for m in mappings}
    ts = TopologicalSorter()
    for m in mappings:
        if m.tab_in:
            ts.add(m.local, m.tab_in)
        else:
            ts.add(m.local)
    try:
        return [index[local] for local in ts.static_order() if local in index]
    except CycleError as exc:
        raise ValueError(f"tab_in cycle detected: {exc.args[1]}") from exc
```

`graphlib` is stdlib since Python 3.9. Check `pyproject.toml` for minimum Python version.

**Estimate**: 1h

---

## Task 3 — Creation pre-pass in push flow (`src/docspan/cli/main.py`)

**File**: `src/docspan/cli/main.py`

Add `_resolve_create_mappings(mappings, config, config_path, dry_run, backend_factory)` function:

1. Filter to mappings where `create` is True and `remote_id` is None (skip if ID already set — idempotent)
2. Topologically sort them (Task 2)
3. For each in order:
   a. If `tab_in` set → look up parent mapping's `remote_id` (must be set by now due to sort order)
      call `backend.create_tab(parent.remote_id, title)`; set `mapping.tab_id = result.tab_id`
   b. Else → call `backend.create(title)`; set first-tab `tab_id = "t.0"` on this mapping
   c. Set `mapping.remote_id = result.doc_id`
   d. Call `save_config(config, config_path, expected_mtime=loaded_mtime)`
   e. Re-read `loaded_mtime` after each successful save
   f. If `dry_run`: print what would be created, skip API calls

4. In dry-run mode, print:
   ```
   [dry-run]  would create google_docs doc → my-doc/README.md  "[Proposal] My doc"
   [dry-run]  would create tab in README.md's doc → my-doc/DETAIL.md  "Detail"
   ```

Modify the existing push loop to remove the existing CI-guard branch for `remote_id is None`
when `mapping.create is True` — those mappings will have `remote_id` set by the pre-pass.
The existing interactive-prompt branch for `create: false` + `remote_id is None` remains.

**Estimate**: 3h

---

## Task 4 — `docspan map --from-dir DIR` subcommand

**File**: `src/docspan/cli/main.py`

Add a new `@app.command("map-dir")` (or extend `map_` with `--from-dir`). Prefer a separate
subcommand to keep `map` argument-compatible with existing scripts:

```sh
docspan map-dir ./my-doc/ --backend google_docs --title "[Proposal] My Doc"
```

Logic:
1. Glob `DIR/*.md`; sort: README.md first (case-insensitive), then alphabetically
2. Check none are already mapped (error if any are)
3. Create the doc from README.md (or first file) → `backend.create(title)`
4. For each remaining file: `backend.create_tab(doc_id, tab_title)` where `tab_title` is
   `--title` only for the first file; subsequent files use their own first H1 / basename
5. Build `Mapping` objects with `create=False` and all IDs populated
6. Append to config and `save_config()`
7. Push all mapped files

**Estimate**: 3h

---

## Task 5 — Unit tests for new config fields

**File**: `tests/test_config.py`

- `create: true` with no `remote_id` round-trips through `load_config` / `save_config`
- `tab_in` referencing unknown local path raises `ValidationError` at load time
- `tab_in` cycle raises `ValidationError` at load time  
- `tab_in` on a non-google_docs backend raises `ValidationError`
- `create: true` + `remote_id` already set: loads successfully (idempotent)

**Estimate**: 2h

---

## Task 6 — Unit tests for push creation pre-pass

**File**: `tests/test_cli.py`

Using existing `FakeGoogleDocsBackend` pattern:

- `test_declarative_create_doc_creates_and_pushes`: `create: true`, no `tab_in` → doc created,
  `remote_id` + `tab_id: t.0` written to config, content pushed
- `test_declarative_create_tab_creates_in_parent_doc`: parent + child with `tab_in` → parent
  created first, tab created in parent's doc, both IDs written to config
- `test_declarative_create_idempotent_when_remote_id_already_set`: `create: true` + `remote_id`
  already set → no backend.create() call, content pushed normally
- `test_declarative_create_dry_run_prints_plan`: `--dry-run` with `create: true` → prints plan,
  no API calls, config unchanged
- `test_declarative_create_noninteractive_ci_no_prompt`: `create: true` in CI → creates without
  prompting (env TERM=dumb or similar to simulate CI)
- `test_tab_in_dependency_order`: YAML has tab before parent → still creates parent first

**Estimate**: 3h

---

## Task 7 — Unit tests for `map-dir` subcommand

**File**: `tests/test_cli.py`

- `test_map_dir_creates_doc_and_tabs_for_all_md_files`
- `test_map_dir_puts_readme_first`
- `test_map_dir_refuses_already_mapped_file`
- `test_map_dir_errors_on_empty_dir`

**Estimate**: 2h

---

## Task 8 — Documentation update

**File**: `README.md` and/or `doc.md`

Add a section "Declarative doc creation" with the YAML example from the proposal, and a note
that `docspan map-dir` is equivalent to the manual sequence. Update the `docspan map` help text.

**Estimate**: 1h

---

## Sequencing

```
Task 1 (config model)
  └─ Task 2 (topo sort)
       └─ Task 3 (push pre-pass)
            └─ Task 6 (push tests)
  └─ Task 5 (config tests)
Task 4 (map-dir command)
  └─ Task 7 (map-dir tests)
Task 8 (docs) — last, after implementation stabilizes
```

Tasks 1+2+5 can land in one PR; Tasks 3+4+6+7+8 in a second PR after config is merged.

---

## Open Questions (deferred to implementation)

1. Should `create: true` be required, or should `tab_in` alone imply `create: true`?
   Current plan: require explicit `create: true` for clarity.
2. `tab_title` field: include now or defer? Current plan: include (mirrors `title` in `docspan map`).
3. Confluence parity for `create`: out of scope for this iteration.
4. Should `push` require `--allow-create` flag? Current plan: no — `create: true` in config is sufficient authorization.
