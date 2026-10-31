# Research: Multi-Tab Push Revision Conflict

## Codebase Investigation

### How `requiredRevisionId` flows through the stack

```
CLI push loop (main.py:292)
  └─ orchestrate_push (orchestrator.py:374)
       └─ backend.push() (backend.py:449)
            └─ _execute_push() (backend.py:504)
                 ├─ _build_push_plan() → fresh get_document() → plan.doc["revisionId"]
                 ├─ pass 1: batch_update(..., required_revision_id=plan.doc["revisionId"])
                 └─ pass 2: batch_update(..., required_revision_id=pass2_doc["revisionId"])
```

`batch_update` in `client.py:178` sets `body["writeControl"] = {"requiredRevisionId": ...}`
when a revision ID is provided.

### Root Cause 1: Wrong re-fetch predicate (backend.py ≈ line 598–609)

```python
if needs_pass2:
    if plan.requests:           # ← BUG: should check first_pass_requests
        whole_doc = self._client.get_document(doc_id)
        pass2_doc, pass2_tab_id, _ = resolve_document_tab(whole_doc, tab_id)
    else:
        whole_doc = plan.whole_doc
        pass2_doc, pass2_tab_id = plan.doc, plan.resolved_tab_id  # stale!
```

`plan.requests` = content-change requests only.  
`style_requests` = `_document_style_requests(plan.whole_doc, pageless)` = style toggle requests.  
`first_pass_requests = plan.requests + style_requests`

When `plan.requests=[]` and `style_requests=[...]`:
- Pass 1 writes (style only) → revisionId R1 → R2
- `if plan.requests:` → False → pass 2 uses `plan.doc["revisionId"]` = R1 (STALE)
- Pass 2 `batch_update` → 400 "requiredRevisionId mismatch"

**Fix**: `if plan.requests:` → `if first_pass_requests:` (line ≈ 604)

This is a single-line change. No architectural impact; `first_pass_requests` is already in
scope at that point.

### Root Cause 2: No retry across mappings sharing the same doc_id

Sequential push of N tabs of doc X:
- Tab 1 push: fetch (R1) → write → doc at R_n
- Tab 2 push: fetch (R_n ideally) → write with R_n → should succeed

This SHOULD work since each `_build_push_plan` does a fresh `get_document`. However:
- The Google Docs API may exhibit brief read-after-write lag (eventual consistency)
- No retry mechanism exists for 400 requiredRevisionId errors

Current error handler (backend.py:834–842):
```python
except HttpError as exc:
    if exc.resp.status == 400 and "requiredRevisionId" in str(exc):
        return PushResult(
            status="conflict",
            message="The doc changed since your last pull — run `docspan pull` again",
        )
```

This message is misleading when docspan itself advanced the revision in the same run.

**Fix**: Retry `_execute_push` once on 400 requiredRevisionId (re-runs `_build_push_plan`
with a fresh fetch and rebuilds all requests against the new revisionId). If the retry also
fails, return `status="conflict"` — that indicates a genuine external conflict.

### Error surfacing path

`_execute_push` catches all exceptions and returns a `PushResult`. The CLI (`main.py:404`)
prints the icon/status but continues processing remaining mappings — a "conflict" on mapping A
does not abort the run. This means RC2 only affects the individual failing mapping, not
subsequent mappings. The reporter's observation ("2 failed on the first pass, re-run succeeds")
is consistent with:

- RC1: Pass 2 stale revision on the first run. On re-run, style was already applied so
  `style_requests=[]` → `first_pass_requests=[]` → pass 1 doesn't write → pass 2 uses
  `plan.doc["revisionId"]` which is now the latest (no stale issue since nothing was written).

- RC2 (less certain): cross-mapping staleness from Google API eventual consistency.

### Existing tests

`tests/test_google_docs_backend.py` covers:
- `batch_update` includes `writeControl.requiredRevisionId` when provided
- `push()` threads the fetched revisionId into `batch_update`
- `push()` returns `status="conflict"` on 400 requiredRevisionId error

No existing tests for:
- Pass 2 re-fetch when `plan.requests=[]` but `style_requests` is non-empty
- Auto-retry on 400 requiredRevisionId
- Multi-mapping push to same doc (different tabs)

### API surface: `batch_update` response

The Docs API `batchUpdate` response does NOT include the new `revisionId` by default. The
`writeControl.targetRevisionId` response field returns the CURRENT revision after the write —
but only when `writeControl.targetRevisionId` was set on the REQUEST (not `requiredRevisionId`).
This means re-fetching the document is the only reliable way to get the post-write revisionId.

### Alternative: omit `requiredRevisionId` for pass 2

Since pass 2 only applies styling (links, bold, italic, monospace) and not structural changes,
one option is to omit `requiredRevisionId` from the pass-2 `batch_update`. This is slightly
less safe (a concurrent edit between pass 1 and pass 2 would go undetected) but simpler than
a retry loop. The chosen approach (fix the re-fetch predicate) is safer and simpler.

### Alternative: group same-doc pushes and serialize tab writes

Instead of retrying, the CLI could group mappings by `remote_id`, push tab 1, carry the
post-push revisionId forward as the starting point for tab 2, etc. This is architecturally
cleaner but significantly more invasive (requires threading state between push calls, changing
`orchestrate_push` / CLI loop structure). Not recommended for this scope.
