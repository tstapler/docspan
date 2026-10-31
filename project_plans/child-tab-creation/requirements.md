# Requirements: Child Tab Creation (`child-tab-creation`)

## Problem Statement

`docspan map --new-tab-in FILE` always creates a root-level tab because `client.py`
sends `addDocumentTab` with only `tabProperties.title`. The Docs API silently ignores
`parentTabId` on `addDocumentTab`, so including it there doesn't help. The two-step
workaround — `addDocumentTab` followed by `updateDocumentTabProperties` with
`fields: "parentTabId"` — is verified to work against the live API (the tab lands at
`nestingLevel` 1 with the correct parent, read back with `documents.get`).

`tabs.py` already reads nested tabs via `childTabs` recursion in `flatten_tabs()`, so
the read side is complete. Only the creation path is missing.

## Scope

- **In scope:**
  - `--parent-tab TAB_ID` CLI option on `docspan map`
  - `parent_tab_id` field in `markgate.yaml` mapping key (for non-interactive use)
  - `update_document_tab_properties()` method on `GoogleDocsClient` that calls
    `updateDocumentTabProperties` to set `parentTabId`
  - `create_tab()` in `GoogleDocsBackend` extended to accept `parent_tab_id`; internally
    does add → update-parent → read-back-to-verify
  - Verification: after the update, fetch the doc and confirm `nestingLevel` ≥ 1 (the
    silent-ignore guard)
  - Unit tests (mocked API) and CLI integration tests

- **Out of scope:**
  - Moving or re-parenting existing tabs
  - Multi-level nesting (parent of parent); `nestingLevel` ≥ 1 confirms success, depth
    is not constrained beyond what the API allows
  - Confluence tabs (Confluence has no tab-tree concept)

## Acceptance Criteria

1. `docspan map child.md --backend google_docs --new-tab-in parent.md --parent-tab t.PARENT_ID`
   creates a child tab under the specified parent, confirmed by reading back
   `nestingLevel ≥ 1` from the Docs API.
2. The mapping written to `markgate.yaml` records the new `tab_id` (existing behaviour)
   but does **not** require a new `parent_tab_id` field in the YAML — the parent
   relationship is enforced once at creation time by the API itself.
3. If `--parent-tab` names a tab ID that does not exist in the target doc, `docspan map`
   exits non-zero with a clear error message before making any API call.
4. `addDocumentTab` silent-ignore is guarded: after the `updateDocumentTabProperties`
   call, the client re-fetches the tab and asserts `nestingLevel ≥ 1`; if the update was
   silently ignored, an error is raised rather than silently mapping a root-level tab as
   if it were a child.
5. Existing `docspan map --new-tab-in` (no `--parent-tab`) continues to create root-level
   tabs without any behaviour change (backward compatibility).
6. `GoogleDocsClient.update_document_tab_properties()` is unit-tested with a mocked API:
   request shape, field mask, and error path.
7. `GoogleDocsBackend.create_tab()` with `parent_tab_id` is unit-tested end-to-end (mock
   add → mock update → mock get-document) including the nesting-level verification.
8. `docspan map --parent-tab` without `--new-tab-in` exits non-zero with a clear message
   (`--parent-tab requires --new-tab-in`).
