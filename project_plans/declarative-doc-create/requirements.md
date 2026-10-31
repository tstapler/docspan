# Requirements: Declarative Google Doc and Tab Creation

## Problem

Adding a new multi-tab Google Doc to a project is a manual, order-dependent shell sequence.
`remote_id` is currently required for a mapping to function, so the config cannot describe a
doc that doesn't exist yet. The only supported path is the imperative `docspan map` command:

```sh
docspan map README.md --backend google_docs
docspan map DETAIL.md --backend google_docs --new-tab-in README.md
docspan map OPTIONS.md --backend google_docs --new-tab-in README.md
```

Each call pushes immediately, and a 4-tab doc takes several minutes total. An interrupted run
leaves a partly-mapped doc, and the README's first-tab mapping gets no `tab_id` which requires
a hand edit. The config is not the source of truth — the terminal history is.

## Goal

Allow a mapping in `docspan.yaml` / `markgate.yaml` to declare intent without a `remote_id`.
A `push` (or `sync`) run creates what's missing, writes the IDs back into the config, then
pushes content — making `docspan push` the whole workflow after editing YAML.

## Acceptance Criteria

1. **Declarative create flag**: A mapping may specify `create: true` with no `remote_id`. On push, if `remote_id` is absent and `create: true`, the backend creates the doc and writes `remote_id` (and `tab_id: t.0` for the first tab) back into config before pushing content.

2. **Tab creation via `tab_in`**: A mapping may specify `tab_in: <local-path>` referencing another mapping's `local` field. On push, a new tab is added to the referenced doc's `remote_id`, and the mapping's `tab_id` is written back to config.

3. **Dependency-ordered creation**: Mappings with `tab_in` references are processed in topological order (parent doc first) regardless of their order in the YAML file.

4. **Idempotent and resumable**: If a run is interrupted after creating some docs/tabs (IDs already written back), re-running push skips already-created entries and picks up from where it stopped.

5. **Config written back atomically**: After creation, `remote_id` and `tab_id` are written back to `docspan.yaml` atomically (using the existing `save_config` mechanism with mtime conflict detection) before content is pushed.

6. **Non-interactive mode support**: If `create: true` and `remote_id` is None in a non-interactive/CI context, creation proceeds automatically (no prompt required) because the intent is declared in config.

7. **Dry-run support**: `docspan push --dry-run` prints what docs/tabs would be created without making any API calls or writing config changes.

8. **`docspan map --from-dir DIR` convenience command**: Maps every `*.md` file in DIR as tabs of a single new Google Doc — README.md (if present) becomes the first tab, remaining files are sorted alphabetically. Writes all mappings to config and pushes content.

9. **Validation at config load time**: If `tab_in` references a `local` path that is not present in the same config's `mappings`, the config load fails with a clear error message.

10. **`create: true` requires google_docs backend**: If `tab_in` is set on a non-google_docs mapping, config load raises a clear error (same pattern as `sectioned` + `_SECTIONED_UNSUPPORTED_BACKENDS`).

11. **Circular dependency detection**: If `tab_in` references form a cycle, config load raises a clear error.

12. **First-tab `tab_id` written correctly**: When a doc is created with `create: true`, the mapping for the first tab gets `tab_id: t.0` written back, matching the format used by existing multi-tab docs.

## Out of Scope

- Confluence child-page declarative creation (noted as future parity in the proposal; not in this iteration)
- Title derivation from file's first H1 (already supported by `docspan map`; same logic can be reused)
- Deleting remote docs that have been removed from config (separate destructive operation)
