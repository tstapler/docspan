# Validation: Fix Mermaid Fence Collapse on Structural Pull

## Acceptance Criteria Verification

### AC-1: Merge-path sidecar lookup uses `mapping.local`, not the temp path

**Verified by:** `tests/test_orchestrator.py` — mock `backend.pull` in a merge scenario and
assert that the `sidecar_path_override` kwarg equals `str(mapping.local)`, not any path
starting with `/tmp`.

**Edge case:** `preview_pull()` also uses a temp path and has the same bug, but since it never
writes the file, the blast radius is only a missing mermaid fence in preview output. Out of
scope per requirements, but noted for a follow-up.

### AC-2: Sidecar consulted even when contentUri fetch fails

**Verified by:** `tests/test_pulled_image_recovery.py` — pass a `fetch` that always returns
`None`; assert that `recover_structural_mermaid_images` still returns the sidecar-stored
fence when the sidecar has a matching `oid:` entry.

**Precondition:** the push path must write the `oid:` key to the sidecar (Fix 3). A sidecar
created before this fix was deployed won't have `oid:` keys — the warning (AC-3) covers that
gap.

### AC-3: WARNING emitted when mermaid fence unrecoverable

**Verified by:** `tests/test_pulled_image_recovery.py` — pass a `fetch` that returns `None`
and a sidecar with no matching entry; assert `logging.WARNING` is emitted mentioning
`object_id` and the path.

**Implementation note:** use `pytest`'s `caplog` fixture at `WARNING` level, not
`capsys` — the warning is emitted via `logger.warning`, not `print`.

### AC-4: All existing tests pass

Run `pytest tests/` — no pre-existing failures expected. The regex change (`*` vs `+`) is
the most likely source of unintended breakage; verify against existing
`test_pulled_image_recovery.py` cases which use real contentUri strings (non-empty), so the
regex change should be transparent to them.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Regex `*` change matches non-image `![]()` spans | Low | Recovery no-op (count guard catches mismatch) | Count guard unchanged; only structural-pull output is processed |
| `oid:` key collision across tabs | Very low | Wrong diagram restored | `object_id` is document-scoped; Docs API docs confirm uniqueness per document |
| `sidecar_path_override` breaks other `pull()` callers | None | — | Keyword arg with default `None`; existing callers unchanged |
| `documentTab.inlineObjects` absent from API response | Unknown | Continues to produce `![]()` (only partially fixed by Fix 2/3) | The `oid:` key in sidecar provides recovery path when `node.src` is empty but `object_id` is known |

## Open Questions

1. **Does the Google Docs API always populate `documentTab.inlineObjects`?** The #140 fix
   assumes it does. If not, `node.src` will be empty and only the `object_id`-keyed sidecar
   (Fix 3) recovers the diagram. This needs a live test with a freshly-created tab.

2. **Does `DocsImageNode.object_id` survive as non-empty when `inlineObjects` is missing?**
   In `_parse_image_paragraph`, `object_id = image_element.get("inlineObjectId")` is read
   from the paragraph element, not from `inlineObjects` — so `object_id` is present even when
   the object can't be resolved. Fix 3 relies on this. VERIFIED by reading the code.

3. **Does the merge logic in `_merge_pull()` correctly handle `![alt](contentUri)` vs
   mermaid fence diffs?** If the base is a fence and the remote is a contentUri image,
   git-style three-way merge with local=fence should keep local (no local change). This needs
   an integration test to confirm no regression.
