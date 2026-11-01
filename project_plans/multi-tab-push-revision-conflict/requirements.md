# Requirements: Multi-Tab Push Revision Conflict

## Problem Statement

When multiple markdown files are mapped to different tabs of the same Google Doc, running
`docspan push a.md b.md c.md ...` in a single invocation intermittently fails for some files
with:

```
HttpError 400 ... batchUpdate: The required revision ID '...' does not match the latest revision.
```

In a reported run of 11 files, 2 failed. Re-running the same command succeeded for all files.
The root cause is a stale `requiredRevisionId` after an earlier push in the same run advanced
the document's revision.

## Root Causes (Identified in Code)

### Root Cause 1 — Pass-2 re-fetch condition uses wrong predicate

`_execute_push` in `src/docspan/backends/google_docs/backend.py` (≈ line 598–609) re-fetches
the document before pass 2 only when `plan.requests` is non-empty:

```python
if plan.requests:          # ← BUG: should be `if first_pass_requests:`
    whole_doc = self._client.get_document(doc_id)
    ...
else:
    pass2_doc = plan.doc   # ← stale if pass 1 wrote style_requests
```

When `plan.requests = []` (no content diffs) but `style_requests` is non-empty, pass 1 writes
and advances the revisionId from R1 → R2. Pass 2 then uses the pre-pass-1 `plan.doc["revisionId"]`
(= R1), which is now stale, and the pass-2 `batch_update` fails with 400.

### Root Cause 2 — No retry / refresh across mappings for the same doc

Even with root cause 1 fixed, sequential pushes across different tabs of the same doc can
encounter stale revisions if the Google Docs API exhibits any read-after-write lag (eventual
consistency). Each mapping's `_build_push_plan` does a fresh `get_document`, but there is no
retry on 400 "requiredRevisionId" errors — the current handler returns `status="conflict"` with
the message "The doc changed since your last pull", which is misleading when docspan itself
caused the revision advance.

## Acceptance Criteria

1. A push that requires only style changes (no content diff, `plan.requests = []`) but needs
   pass 2 (spans/tables present) does not fail with a 400 requiredRevisionId error: pass 2
   re-fetches the document when pass 1 wrote anything (`first_pass_requests` is non-empty).

2. When a single `docspan push` invocation targets multiple tabs of the same Google Doc, all
   tabs succeed on the first invocation without requiring a re-run.

3. On a 400 "requiredRevisionId" conflict, `_execute_push` retries the entire push once
   (re-runs `_build_push_plan` with a fresh document fetch) before returning `status="conflict"`.

4. The `status="conflict"` message distinguishes between "a human edited the doc" (external
   conflict) and "docspan itself advanced the revision in this same run" (internal conflict),
   or at minimum the retry in criterion 3 absorbs the internal conflict transparently.

5. Existing tests continue to pass; new unit tests cover:
   - Pass 2 re-fetch when `plan.requests=[]` but `style_requests` is non-empty (regression for RC1).
   - Auto-retry on 400 requiredRevisionId (regression for RC2/retry path).
   - Multi-mapping push to same doc_id produces all successes (integration-level regression).

6. No existing `status="conflict"` behavior is regressed for genuine external conflicts (a
   human edited the doc since last pull, so the revisionId truly mismatch and a second attempt
   would also fail — those should still surface as "conflict" after the retry exhausts).
