# Implementation Plan: Child Tab Creation (`child-tab-creation`)

## Overview

Add `--parent-tab TAB_ID` to `docspan map --new-tab-in` so docs with many sections can
be organized as a tab tree. The Google Docs API silently ignores `parentTabId` on
`addDocumentTab`, so the fix is a two-step sequence: `addDocumentTab` (creates root-level
tab, returns new `tabId`) → `updateDocumentTabProperties` (sets `parentTabId`) →
`get_document` (verify `nestingLevel ≥ 1`). The read side already works via
`flatten_tabs()`; only the creation path changes.

---

## Task List

### T1 — Add `update_document_tab_properties()` to `GoogleDocsClient`
- **File:** `src/docspan/backends/google_docs/client.py`
- **Category:** backend | **Size:** small
- After `add_document_tab()` (line 176), add:
  ```python
  def update_document_tab_properties(
      self, doc_id: str, tab_id: str, parent_tab_id: str
  ) -> None:
      """Set parentTabId on an existing tab via updateDocumentTabProperties."""
      self.batch_update(
          doc_id,
          [{
              "updateDocumentTabProperties": {
                  "tabProperties": {"tabId": tab_id, "parentTabId": parent_tab_id},
                  "fields": "parentTabId",
              }
          }],
      )
  ```
- No changes to `batch_update()` itself; backoff is inherited.

### T2 — Extend `create_tab()` in `GoogleDocsBackend`
- **File:** `src/docspan/backends/google_docs/backend.py`
- **Category:** backend | **Size:** medium
- Change signature to `create_tab(self, doc_id: str, title: str, parent_tab_id: Optional[str] = None, **kwargs)`.
- When `parent_tab_id` is not None:
  1. Call `add_document_tab(doc_id, title)` → get `tab_id`.
  2. Call `update_document_tab_properties(doc_id, tab_id, parent_tab_id)`.
  3. Call `get_document(doc_id, include_tabs_content=True)` and use
     `flatten_tabs()` to find the new tab; assert
     `tab["tabProperties"].get("nestingLevel", 0) >= 1` (or `parentTabId` present).
     Raise `RuntimeError` with a descriptive message if the check fails.
  4. Return `CreateResult` as before (URL unchanged).
- When `parent_tab_id` is None: existing code path is untouched.

### T3 — Validate parent tab exists before mutation
- **File:** `src/docspan/backends/google_docs/backend.py` (or `cli/main.py`)
- **Category:** backend | **Size:** small
- Before calling `add_document_tab`, fetch the doc and confirm `parent_tab_id` appears
  in `flatten_tabs()`. If not, raise `ValueError` with a list of valid tab IDs.
- Placing this in `backend.py` keeps CLI thin; the CLI can catch `ValueError` and print it.
- Note: this costs one extra `get_document` call. Acceptable because tab creation is rare.

### T4 — Add `--parent-tab` option to `docspan map` CLI
- **File:** `src/docspan/cli/main.py`
- **Category:** backend | **Size:** small
- Add after `--new-tab-in`:
  ```python
  parent_tab: Optional[str] = typer.Option(
      None,
      "--parent-tab",
      help="Tab ID within the --new-tab-in doc to nest the new tab under. "
           "Requires --new-tab-in. Example: t.abc123",
  )
  ```
- Validation block (after existing `--new-tab-in` guards):
  - If `parent_tab` is not None and `new_tab_in` is None: print error, exit 1.
- Pass to `create_tab`: change call from `create_tab(parent_mapping.remote_id, doc_title)`
  to `create_tab(parent_mapping.remote_id, doc_title, parent_tab_id=parent_tab)`.
- The `ValueError` from T3 bubbles up through the existing `except ValueError` block
  (line 1024–1026), so no new exception handling is needed.

### T5 — Unit-test `update_document_tab_properties()` (new method)
- **File:** `tests/test_google_docs_backend.py`
- **Category:** test | **Size:** small
- New class `TestUpdateDocumentTabProperties` with tests:
  - Happy path: verifies exact request dict sent to `batch_update()`, `fields: "parentTabId"`.
  - Error path (optional but recommended): 4xx from batch_update propagates unmodified.

### T6 — Update `TestCreateTab` for the new parent path
- **File:** `tests/test_google_docs_backend.py`
- **Category:** test | **Size:** medium
- Existing 3 tests mock `client.add_document_tab` only; they must also mock
  `client.update_document_tab_properties` and `client.get_document` (for verification).
- For existing tests: confirm `update_document_tab_properties` is **not called** when
  `parent_tab_id=None` (backward-compatibility assertion).
- New tests:
  - `test_create_tab_with_parent_calls_update_and_verifies_nesting` — mocks all three
    calls; verifies update is called with correct args and doc is re-fetched.
  - `test_create_tab_with_parent_raises_if_nesting_not_confirmed` — mock returns
    `nestingLevel=0` from get_document; asserts `RuntimeError` raised.
  - `test_create_tab_with_parent_raises_if_parent_tab_not_found` — mock get_document
    (pre-check) returns tabs without the parent; asserts `ValueError` raised.

### T7 — CLI tests for `--parent-tab`
- **File:** `tests/test_cli.py`
- **Category:** test | **Size:** small
- Add to (or alongside) `TestMapNewTabIn`:
  - `test_parent_tab_happy_path` — `FakeBackend.create_tab()` already accepts `**kwargs`;
    verify `parent_tab_id` kwarg is passed through.
  - `test_parent_tab_requires_new_tab_in` — `--parent-tab` without `--new-tab-in` exits 1
    with message containing "requires --new-tab-in".

### T8 — CHANGELOG / docs note
- **File:** `CHANGELOG.md` (unreleased section)
- **Category:** docs | **Size:** small
- One line: "feat: `docspan map --new-tab-in` now supports `--parent-tab TAB_ID` to
  create nested (child) tabs."

---

## Key Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| API changes `updateDocumentTabProperties` semantics silently | Low | Verification step (T2 step 3) catches silent-ignore at runtime, not just at test time |
| Pre-check `get_document` (T3) sees stale state (eventual consistency) | Very low | Docs API is strongly consistent for tab metadata within a session; single retry acceptable |
| `--parent-tab` without tab ID validation causes confusing 400 from API | Mitigated by T3 | Pre-check raises `ValueError` with valid IDs before any mutation |
| Existing `TestCreateTab` tests break after signature change | Certain | T6 explicitly updates all three; keep `parent_tab_id=None` default to avoid breaking callers |
| `FakeBackend.create_tab()` in test fixtures silently swallows `parent_tab_id` | Known | The acceptance criteria only require the kwarg be passed; FakeBackend already uses `**kwargs` |

---

## Adversarial Review

**"The two extra API calls (pre-check + verify) make tab creation slow."**
Both calls are `get_document` (read-only). Tab creation is a one-time `docspan map`
operation, not a push/pull hot path. The cost is acceptable. If profiling ever
flags it, the pre-check and verify can share one `get_document` call by fetching once
before the add and trusting the post-update fetch for verification.

**"nestingLevel may not be present on all Docs API versions."**
The safer check is `"parentTabId" in tab["tabProperties"]` (see research §4). Use
that as the primary guard and keep `nestingLevel >= 1` as a secondary signal — either
one present and correct is success.

**"What if the user passes a parent tab from a *different* doc?"**
The pre-check (T3) fetches the target doc (from `--new-tab-in`) and validates
`parent_tab_id` against its tabs. A mismatched ID from another doc would fail validation
with a `ValueError` listing valid IDs — same as a typo.

**"The `Mapping` model has no `parent_tab_id` field — can't reproduce the tab tree from
the YAML alone."**
Correct and intentional. The tab tree lives in Google Docs; docspan is not a mirror of it.
Existing tabs are discovered by `flatten_tabs()` on every fetch. Adding `parent_tab_id`
to YAML would be misleading (the user could change it without effect on the live doc).
If re-parenting is ever needed, that's a separate feature.

**"`--parent-tab` is not validated for format (e.g. `t.` prefix)."**
The API accepts any string; Google Docs assigns IDs like `t.moqlkhpwn82e`. Format
validation is fragile (IDs may change format) and redundant — the pre-check validates
existence, which subsumes format.

**"Scope creep: requirements mention `parent_tab_id` in the markgate.yaml mapping key."**
Research concludes this is not needed (§6). The requirements doc includes it in scope but
also says the YAML mapping does NOT require a new field (AC #2). Clarify with the owner
that `parent_tab_id` in YAML is out of scope for this iteration; only the CLI flag is
implemented.

---

## Implementation Order

1. T1 (client method) — smallest, unblocks everything
2. T2 + T3 (backend logic) — add→update→verify sequence
3. T4 (CLI flag) — thin wiring
4. T5 + T6 + T7 (tests) — can parallel with T4
5. T8 (changelog) — last
