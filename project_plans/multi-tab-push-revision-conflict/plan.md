# Plan: Multi-Tab Push Revision Conflict

## Scope

Two targeted fixes to `_execute_push` in `src/docspan/backends/google_docs/backend.py`,
plus new tests. No changes to the CLI loop, orchestrator, or client.

---

## Fix 1 — Correct the pass-2 re-fetch predicate (RC1)

**File**: `src/docspan/backends/google_docs/backend.py`  
**Location**: The `if needs_pass2:` block in `_execute_push` (≈ line 598–609)

**Change**:
```python
# Before
if plan.requests:
# After
if first_pass_requests:
```

**Rationale**: `first_pass_requests = plan.requests + style_requests`. When only style changes
are needed (`plan.requests=[]`, `style_requests=[...]`), pass 1 still writes and advances the
revisionId. The re-fetch guard must trigger whenever pass 1 actually wrote something, not only
when content changes were part of that write.

**Risk**: Low. `first_pass_requests` is already computed in scope. The only behavioral
difference is an extra `get_document` call when `style_requests` is non-empty and
`plan.requests` is empty — i.e., documents that only need style reconciliation now do an
extra GET before pass 2. This extra call is correct and was always needed.

---

## Fix 2 — Auto-retry on 400 requiredRevisionId conflict (RC2)

**File**: `src/docspan/backends/google_docs/backend.py`  
**Location**: The `except HttpError` handler at the bottom of `_execute_push` (≈ line 834)

**Approach**: Wrap the entire `_build_push_plan` + batch_update sequence in a retry loop
(max 1 retry). On the first 400 "requiredRevisionId" error, re-run `_build_push_plan` (which
fetches the document fresh and recomputes all requests against the new revisionId) and repeat
the pass-1 / pass-2 batch_update calls. If the second attempt also fails with 400, return
`status="conflict"` (genuine external conflict).

**Implementation sketch in `_execute_push`**:

```python
def _execute_push(self, ...):
    for attempt in range(2):  # 0 = first try, 1 = retry
        try:
            return self._execute_push_once(...)
        except HttpError as exc:
            if exc.resp.status == 400 and "requiredRevisionId" in str(exc) and attempt == 0:
                # Stale revision — re-fetch and retry once (handles same-doc multi-tab
                # pushes where an earlier tab write advanced the doc's revisionId)
                logger.debug("requiredRevisionId conflict on attempt 0; retrying with fresh fetch")
                continue
            # Re-raise for the outer except handlers to handle
            raise
```

Or alternatively, extract the inner body into `_execute_push_once` and call it from a
2-iteration loop in `_execute_push`, with the `except HttpError` block at the outer level.

A simpler option (lower diff): keep the existing structure and just add a `_retry_push` call
inside the `except HttpError` branch for the 400/requiredRevisionId case. Either way, only one
retry is permitted.

**Risk**: Medium. The retry re-runs `_build_push_plan` which includes `get_document` (a GET),
potential image uploads (already done; temp Drive files are still referenced), and request
recomputation. The temp Drive file IDs from the first attempt are still valid; they must be
carried into the retry (not uploaded again). See implementation note on `temp_drive_file_ids`
below.

**Implementation note — temp Drive file IDs on retry**: If pass 1 already uploaded temp Drive
files and then failed, those files still exist. On retry, `_build_push_plan` would try to
re-upload them. To avoid double-upload:
- Option A: Track which image uploads already completed and skip re-upload (complex).
- Option B: Clean up temp uploads before retrying and let `_build_push_plan` re-upload.
- Option C (simplest): Only retry when `plan` is None or `plan.temp_drive_file_ids` is empty,
  i.e., only retry when pass 1 failed before any writes (the stale revision is detected before
  the first `batch_update` call, which is the common cross-mapping case). Since the failure
  is at `batch_update` time and temp uploads happen inside `_build_push_plan` (before
  `batch_update`), retrying requires handling the already-uploaded temp files. Option B
  (delete and re-upload) is the safest.

**Practical scope decision**: Given that RC1 (pass 2 stale revision) is the confirmed trigger
for the reported symptom, Fix 1 alone may resolve the user-facing issue. Fix 2 can be
implemented as a follow-on or in the same PR, depending on capacity. If Fix 2 is deferred,
the error message in the `status="conflict"` handler should at minimum be improved to not say
"run `docspan pull` again" when docspan itself is the conflicting writer.

---

## Fix 3 — Improve conflict error message (low effort)

**File**: `src/docspan/backends/google_docs/backend.py`  
**Location**: The `status="conflict"` PushResult (≈ line 836–841)

Change the message from "The doc changed since your last pull — run `docspan pull` again" to
something that acknowledges the multi-tab scenario:

```
"The doc changed between this push and the previous one in this run (or since your last pull).
 Re-run `docspan push` to retry."
```

This is a cosmetic fix but prevents user confusion when the conflict is internal.

---

## Tests to Add

**File**: `tests/test_google_docs_backend.py`

### Test A — RC1 regression

```
test_push_pass2_refetches_doc_when_only_style_requests_written_in_pass1
```

Setup: `plan.requests = []`, `style_requests = [some_request]`, document has spans (needs_pass2).
Verify: `get_document` is called TWICE (once in `_build_push_plan`, once before pass 2),
        and the pass-2 `batch_update` uses the revisionId from the second fetch.

### Test B — Retry on 400 conflict

```
test_push_retries_once_on_required_revision_id_conflict_and_succeeds
test_push_returns_conflict_after_two_consecutive_required_revision_id_failures
```

Test B1: First `batch_update` raises 400 "requiredRevisionId", second attempt succeeds.
         Verify the push returns `status="ok"` and `get_document` was called twice.

Test B2: Both attempts raise 400 "requiredRevisionId".
         Verify the push returns `status="conflict"`.

### Test C — Multi-mapping integration regression

```
test_sequential_pushes_to_different_tabs_of_same_doc_all_succeed
```

Simulate 3 mappings to the same doc_id with different tab_ids. Each `batch_update` call
increments the mocked revisionId. Verify all 3 return `status="ok"`.

---

## Implementation Order

1. Fix 1 (predicate) — highest impact, lowest risk, one line
2. Test A (RC1 regression) — confirms Fix 1
3. Fix 3 (error message) — cosmetic, low effort
4. Fix 2 (retry) — moderate effort; include if time permits, else defer
5. Tests B + C — go with Fix 2

---

## Files Changed

| File | Change |
|------|--------|
| `src/docspan/backends/google_docs/backend.py` | Fix 1 (1 line), Fix 2 (≈15 lines), Fix 3 (1 line) |
| `tests/test_google_docs_backend.py` | New tests A, B, C (≈80–120 lines) |
