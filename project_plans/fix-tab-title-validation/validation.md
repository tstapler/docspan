# Validation: fix-tab-title-validation

## Risk Assessment

**Risk: low.** The change is a defensive guard added to an existing pre-flight check
block. No existing behavior is altered — the only new codepath is an early exit that
previously would have produced an unhandled traceback.

## Edge Cases Verified in Plan

| Case | Handled? |
|---|---|
| H1 > 50 chars → exit 1, message names limit + suggests `--title` | ✓ |
| H1 exactly 50 chars → exit 0 | ✓ |
| `--title` > 50 chars → exit 1, message names limit + says "Shorten" | ✓ |
| Basename fallback > 50 chars → same guard fires | ✓ (same code path) |
| `--new-tab-in` not used → guard not reached, no regression | ✓ (guarded by `if new_tab_in is not None`) |

## Test Feasibility

All three new test cases use the existing `FakeBackend` and `_invoke_new_tab()` helper.
`FakeBackend.create_tab()` should never be reached when the guard fires (exit 1 before
the `try:` block). The exact-50-char success case does call `FakeBackend.create_tab()`,
confirming exit 0 is not just "no guard" but actual success.

## File Change Budget

- `src/docspan/cli/main.py`: +1 constant line, +7 guard lines (~8 lines total)
- `tests/test_cli.py`: +3 test methods (~35 lines total)

No new dependencies. No config changes.

## Definition of Done

- `pytest tests/test_cli.py -k "new_tab"` passes with 10 tests (7 existing + 3 new).
- `pytest` full suite passes.
- `mypy src/docspan/cli/main.py` reports no new errors.
