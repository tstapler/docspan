# Validation: Declarative Google Doc and Tab Creation

## Adversarial Review Pass

### Concern 1: `t.0` hard-coded tab_id assumption may break

**Risk**: The plan assumes the first tab of a newly-created Google Doc has `tab_id = "t.0"`. This
is derived from observing existing docs in the codebase, not from the API response. If the API
ever returns a different ID for the first tab (e.g. after a server-side change), IDs written to
config will be wrong.

**Mitigation**: Extend `GoogleDocsBackend.create()` to return the actual first-tab ID from the
API. `client.create_document()` already returns the document JSON. Call
`client.get_document(doc_id)` (or inspect the create response) to read `tabs[0].tabProperties.tabId`
and include it in `CreateResult`. This removes the assumption entirely.

**Plan update**: Task 1 in plan.md should note this; Task 3 should not hard-code `"t.0"`.

---

### Concern 2: mtime re-read between per-creation saves

**Risk**: The plan says "re-read loaded_mtime after each successful save" but the current
`save_config` signature writes the mtime check internally. If the caller passes a stale mtime
from the initial load across N saves, the second save will always fail with `ConfigConflictError`
because the file was modified by the first save.

**Verification**: `save_config(expected_mtime=...)` reads `config_path.stat().st_mtime` at call
time (line 249 of config.py) and compares to `expected_mtime`. So the caller must pass the *post-
save* mtime. `config_mtime()` can be called after each `save_config()` to capture the updated mtime.

**Mitigation**: In the creation pre-pass, after each `save_config()` call, immediately call
`config_mtime(config_path)` and update `loaded_mtime`. This is explicit and correct.

**Plan impact**: Task 3 must include this mtime refresh loop. Low risk if implemented carefully.

---

### Concern 3: Tab creation order vs. YAML order

**Risk**: The topological sort guarantees parent-before-child, but among sibling tabs of the same
doc, the sort order depends on `graphlib.TopologicalSorter`'s internal tie-breaking (which is
insertion order in Python 3.9+). If the user specifies tabs in a specific order in YAML expecting
that to control tab position in the Google Doc, this must be documented.

**Verification**: Google Docs API `addTab` adds tabs in the order they are called. `graphlib`
preserves insertion order for nodes with no interdependency (Python docs guarantee this). So
YAML file order IS preserved for sibling tabs as long as they are added to the sorter in YAML
order. The `topo_sort_mappings` implementation must iterate `mappings` in list order, not by
iterating a dict.

**Mitigation**: Confirm `ts.add()` calls follow `mappings` list order (not set iteration).
Add a test: `test_tab_in_preserves_yaml_order_among_siblings`.

---

### Concern 4: `MarkgateConfig` validator for `tab_in` targets runs at load time, not at `push` time

**Risk**: If a user has a config with a `tab_in` referencing a mapping that doesn't exist yet
(e.g. they forgot to add it), the error appears at every `docspan` command — even `docspan list`
— not just `push`. This could be disruptive.

**Counter-argument**: This is correct behavior. An invalid config should fail fast at load time,
same as `sectioned: true` + no `split_level`. The YAML should always be in a valid state.

**Decision**: Keep validation at load time. The error message must clearly state which `tab_in`
reference is missing and what to add.

---

### Concern 5: `map-dir` title handling for non-README first files

**Risk**: If DIR has no README.md, the alphabetically-first file becomes the doc's title source.
The doc title (from `--title` or first H1 of first file) becomes the Google Doc name for the
whole multi-tab doc, which may be confusing if the first file is `00-intro.md`.

**Mitigation**: `--title` option on `map-dir` explicitly sets the doc title. Document that without
`--title`, the doc title comes from the first file's H1 / basename. This is acceptable UX.

---

### Concern 6: Interaction between `create: true` pre-pass and the existing interactive prompt

**Risk**: After the pre-pass runs, the existing push loop at line 322 of `main.py` checks
`if mapping.remote_id is None` and prompts interactively (or errors in CI). For a `create: true`
mapping that the pre-pass handled, `remote_id` will be set by then — so the existing branch
won't trigger. But if a `create: true` mapping's pre-pass creation *failed* (network error),
`remote_id` is still None and the loop will prompt interactively or error.

**Decision**: This is correct behavior — the pre-pass failed, so the push loop's existing
guardrail catches it. The error from the pre-pass is already printed. No change needed.

---

### Concern 7: Python version for `graphlib`

**Risk**: `graphlib.TopologicalSorter` requires Python 3.9+. If `pyproject.toml` targets 3.8,
this would be a breaking dependency.

**Verification needed**: Check `pyproject.toml` for `python_requires`.

**Fallback**: Implement Kahn's algorithm inline (~15 lines) if Python 3.8 is required.

---

## Validation Summary

| AC | Status | Notes |
|----|--------|-------|
| 1 (create: true flag) | VALID | Straightforward Mapping field + pre-pass |
| 2 (tab_in) | VALID | Existing create_tab() API sufficient |
| 3 (dependency order) | VALID | graphlib.TopologicalSorter; verify Python version |
| 4 (idempotent) | VALID | Check remote_id != None before create call |
| 5 (atomic write-back) | VALID | save_config() already atomic; mtime refresh needed between saves |
| 6 (CI non-interactive) | VALID | Pre-pass bypasses interactive prompt entirely |
| 7 (dry-run) | VALID | Skip API calls, print plan |
| 8 (map-dir) | VALID | Reuses existing backend methods |
| 9 (tab_in validation) | VALID | MarkgateConfig validator; load-time check |
| 10 (google_docs only) | VALID | Same pattern as sectioned unsupported backends |
| 11 (cycle detection) | VALID | CycleError from graphlib |
| 12 (t.0 tab_id) | RISK | Must read from API response, not hard-code |

## Pre-implementation Blockers

1. **Confirm Python version**: `grep python_requires pyproject.toml` — if < 3.9, implement Kahn's algorithm instead of `graphlib`.
2. **Confirm `t.0` from API**: Read `create_document` response JSON schema or add a `get_document` call post-create to extract real first-tab ID — do not hard-code `"t.0"`.
