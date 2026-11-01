# Validation: Child Tab Creation (`child-tab-creation`)

## AC-to-Task Coverage

| AC | Criterion (abbreviated) | Covered by | Status |
|----|------------------------|-----------|--------|
| 1 | `--parent-tab` creates child tab, verified via `nestingLevel ≥ 1` | T2 (add→update→verify), T4 (CLI) | ✅ |
| 2 | YAML mapping does NOT need `parent_tab_id` field | Research §6, no Mapping model changes | ✅ |
| 3 | Unknown `--parent-tab` ID exits non-zero before any mutation | T3 (pre-check in backend) | ✅ |
| 4 | Silent-ignore guarded: raises if update didn't nest the tab | T2 step 3 (post-update get_document verify) | ✅ |
| 5 | Existing `--new-tab-in` (no `--parent-tab`) unchanged | T2 `parent_tab_id=None` default, T6 backward-compat assert | ✅ |
| 6 | `update_document_tab_properties()` unit-tested (shape, field mask, error) | T5 | ✅ |
| 7 | `create_tab()` with `parent_tab_id` unit-tested end-to-end incl. verification | T6 | ✅ |
| 8 | `--parent-tab` without `--new-tab-in` exits 1 with clear message | T4 (validation block) + T7 | ✅ |

All 8 ACs are addressed.

---

## Gaps and Edge Cases

**Gap 1 — Scope tension: `parent_tab_id` in YAML**
Requirements scope says "In scope: `parent_tab_id` field in `markgate.yaml` mapping key".
AC #2 contradicts this by saying the YAML does NOT require the field. Plan correctly
resolves this in favour of AC #2 (confirmed by research §6) and flags it in the adversarial
section. **Action required:** confirm with owner before implementation that the YAML field
is deferred; document the decision in the plan or as an ADR entry.

**Gap 2 — T3 pre-check placement**
T3 says "backend.py or cli/main.py" without committing. Leaving this open risks the
pre-check landing in the wrong layer. **Recommendation:** place it in `backend.py`
(i.e., the start of `create_tab()` when `parent_tab_id is not None`), matching the
adversarial note. Finalise this in the implementation brief.

**Gap 3 — No test for the pre-check + verify sharing one `get_document` call**
The adversarial review notes these two calls could be collapsed into one. The plan
implements them as two separate calls. No test currently verifies call count.
This is low-risk for a first implementation; note it as a follow-up optimisation.

**Gap 4 — `FakeBackend.create_tab()` kwarg pass-through is untested**
T7 says "verify `parent_tab_id` kwarg is passed through" but `FakeBackend` silently
swallows kwargs. The only way to verify pass-through in a CLI test is to capture the
`create_tab` call arguments — ensure T7 uses `unittest.mock.patch` or replaces
`FakeBackend.create_tab` with a spy, not just verifies the exit code.

**Gap 5 — No error test for API failure during `updateDocumentTabProperties`**
T5 marks the error path "optional but recommended". Given that a failure here leaves
an orphan root-level tab in the doc (the `addDocumentTab` succeeded), this should be
**required**: the error message must tell the user the tab was created but not nested,
with its `tab_id`, so they can clean up or re-parent manually.

---

## Risk Assessment

**Silent-ignore guard reliability (AC #4)**
Using `"parentTabId" in tab["tabProperties"]` is more reliable than `nestingLevel >= 1`
as the primary check (plan acknowledges this in the adversarial section). Risk is low if
the recommended primary check is used. **Confirm implementation uses `parentTabId`
presence as primary, `nestingLevel` as secondary.**

**Extra API calls per tab creation**
Two extra `get_document` calls (pre-check + verify) are acceptable for a one-time
`docspan map` operation. No performance risk.

**Backward compatibility**
`parent_tab_id=None` default on `create_tab()` preserves existing callers including
`FakeBackend`. Risk is low if existing `TestCreateTab` tests add the
"update not called when parent_tab_id=None" assertion (T6 includes this).

**Orphan tab on partial failure**
If `addDocumentTab` succeeds but `updateDocumentTabProperties` fails (network error,
permissions), a root-level tab is left in the doc with no mapping entry in YAML.
The existing `ConfigConflictError` path in `main.py` already prints the `tab_id`
on YAML-save failure — but there is no equivalent guard for the API-level partial
failure. **Recommendation (Gap 5):** the error from `update_document_tab_properties`
must include the newly-created `tab_id` so the user can recover.

---

## Go / No-Go

**GO** — the plan is sound and covers all 8 ACs. Three amendments required before
implementation starts:

1. **Confirm with owner** that `parent_tab_id` in YAML is deferred (Gap 1). If it is
   in scope for this iteration, add a task for `Mapping` model + serialization.
2. **Commit T3 to `backend.py`** (Gap 2) rather than leaving placement open.
3. **Make T5's error-path test required** (Gap 5) and ensure the error message includes
   the newly-created `tab_id` for user recovery from partial failure.

Gap 4 (FakeBackend spy) and Gap 3 (call-count test) are minor and can be addressed
during implementation without blocking the start of work.
