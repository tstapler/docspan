# Research: Child Tab Creation (`child-tab-creation`)

## 1. `updateDocumentTabProperties` API shape

The Google Docs v1 batchUpdate API accepts an `updateDocumentTabProperties` request:

```json
{
  "updateDocumentTabProperties": {
    "tabProperties": {
      "tabId": "t.newtab",
      "parentTabId": "t.parent"
    },
    "fields": "parentTabId"
  }
}
```

Key points:
- `tabId` inside `tabProperties` identifies **which tab to update** (the newly-created one).
- `parentTabId` is the tab to nest under.
- `fields` is a FieldMask string. Setting `"parentTabId"` tells the API to update only
  that property; omitting the parent from the mask would clear it on some API versions.
- The response `replies` entry for `updateDocumentTabProperties` is an empty `{}` object
  (no data returned). Success is confirmed only by re-fetching the document.

## 2. Reuse of `batch_update()`

`GoogleDocsClient.batch_update()` (`client.py` line 178) takes a plain `list` of request
dicts and POSTs them verbatim — no special handling per request type. The new
`update_document_tab_properties()` method can call it directly:

```python
def update_document_tab_properties(
    self, doc_id: str, tab_id: str, parent_tab_id: str
) -> None:
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

No changes to `batch_update()` itself are needed. Backoff is inherited.

## 3. `addDocumentTab` response shape (extracting `tabId` for the update)

`batch_update()` returns the full response dict. For `addDocumentTab`, the reply at
`response["replies"][0]["addDocumentTab"]["tabProperties"]` contains at least `tabId`
and `title`. The current `add_document_tab()` already extracts and returns this dict.

So in `create_tab()` (backend.py line 1918), `tab_properties["tabId"]` is available
immediately after `add_document_tab()` returns — no additional API call needed to get
the new tab's ID for the follow-up update.

## 4. Verifying `nestingLevel` after `updateDocumentTabProperties`

After the update call, fetch the document with `get_document(doc_id,
include_tabs_content=True)`. The response `tabs` array nests child tabs under
`childTabs`:

```json
{
  "tabs": [
    {
      "tabProperties": {"tabId": "t.parent", "title": "Parent", "nestingLevel": 0},
      "childTabs": [
        {
          "tabProperties": {
            "tabId": "t.newtab",
            "title": "Child",
            "nestingLevel": 1,
            "parentTabId": "t.parent"
          },
          "childTabs": []
        }
      ]
    }
  ]
}
```

Verification strategy: use `flatten_tabs()` from `tabs.py` (already imported in
backend.py) to find the newly created tab by `tabId`, then assert
`tab["tabProperties"].get("nestingLevel", 0) >= 1`. If it remains 0, the update was
silently ignored and the method should raise a `RuntimeError` with a clear message.

Alternative simpler check: confirm `"parentTabId"` appears in the child's
`tabProperties` and equals the requested parent ID — this is the canonical signal
that nesting succeeded, independent of `nestingLevel` integer accuracy.

## 5. Existing test coverage for the affected paths

### `tests/test_google_docs_backend.py`

**`TestAddDocumentTab`** (line 67, 1 test):
- `test_sends_add_document_tab_request_and_returns_tab_properties` — verifies request
  shape and return value of `client.add_document_tab()`.

**`TestCreateTab`** (line 2777, 3 tests):
- `test_create_tab_calls_client_and_returns_doc_id_tab_id_url`
- `test_create_tab_echoes_title_from_api_response_over_requested_title`
- `test_create_tab_falls_back_to_requested_title_when_response_omits_it`

**`test_create_tab_default_raises_not_implemented`** (line 2838, 1 test):
- Exercises the base `Backend.create_tab()` default raising `NotImplementedError`.

All three `TestCreateTab` tests mock `client.add_document_tab` directly. They do **not**
mock `client.update_document_tab_properties` (which doesn't exist yet) or
`client.get_document` for the verification step. All three will need updates once
`create_tab()` gains the add→update→verify sequence with a `parent_tab_id` argument.

### `tests/test_cli.py`

**`TestMapNewTabIn`** class (line 810, 7 tests):
- `test_new_tab_in_creates_tab_in_existing_doc_and_maps_it` — happy path via `FakeBackend`.
- `test_new_tab_in_requires_google_docs_backend` — guard: non-gdocs backend rejected.
- `test_new_tab_in_rejects_unmapped_parent` — guard: parent not in config.
- `test_new_tab_in_rejects_non_google_docs_parent` — guard: parent mapped to confluence.
- `test_new_tab_in_and_tab_id_are_mutually_exclusive` — guard: both flags present.
- `test_new_tab_in_rejects_space` — guard: --space with --new-tab-in.
- `test_new_tab_in_rejects_parent_with_no_remote_id_yet` — guard: parent unpushed.

`FakeBackend.create_tab()` (line 69) accepts `**kwargs` and ignores them — it will
transparently handle the new `parent_tab_id` kwarg without changes. New tests are needed
only to cover the `--parent-tab` option specifically.

## 6. `Mapping` model: does it need `parent_tab_id`?

**No.** The parent relationship is structural state stored server-side by Google Docs.
Once set at creation time, docspan never needs to re-assert it. Push and pull operations
target the tab by `tab_id` (already recorded in the mapping), and `tabs.py` discovers
its content by traversing `childTabs` recursively — which already works. Adding
`parent_tab_id` to the `Mapping` model would provide no runtime benefit and would
complicate config serialization for a field that's already baked into the remote doc.

The `Mapping` YAML therefore stays unchanged. The `--parent-tab` argument is consumed
once at `docspan map` time and then discarded.

## Summary of implementation touch-points

| File | Change |
|------|--------|
| `src/docspan/backends/google_docs/client.py` | Add `update_document_tab_properties(doc_id, tab_id, parent_tab_id)` method |
| `src/docspan/backends/google_docs/backend.py` | Extend `create_tab(doc_id, title, parent_tab_id=None)` with add→update→get-verify sequence |
| `src/docspan/cli/main.py` | Add `--parent-tab` option; pass to `backend_instance.create_tab(..., parent_tab_id=...)` |
| `tests/test_google_docs_backend.py` | Add `TestUpdateDocumentTabProperties`; extend `TestCreateTab` for parent path + verification |
| `tests/test_cli.py` | Add `--parent-tab` happy-path test and `--parent-tab without --new-tab-in` error test |
