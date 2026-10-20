# ADR-001: Use merge3 package for three-way merge

**Date**: 2026-06-01
**Status**: Accepted

## Context

Three-way merge is required for the conflict detection algorithm (Feature 3: Remote change integration). When both the local file and the remote document have changed since the last sync, the tool must attempt to reconcile them and emit Git-compatible conflict markers for unresolvable conflicts.

Options considered:

1. **`merge3` (PyPI: `merge3`, `breezy-team/merge3`, v0.0.16 Oct 2025)** — Python port of Bazaar's merge3 algorithm. Accepts sequences of lines. Produces Git-compatible `<<<<<<<`/`=======`/`>>>>>>>` markers via `merge_lines()`. MIT license.
2. **Manual `difflib` implementation** — Build two-way diffs (BASE→OURS, BASE→THEIRS) using `difflib.SequenceMatcher` and walk both opcode lists simultaneously. ~100 lines of non-trivial code with edge cases around adjacent changes.
3. **`three-merge` (PyPI)** — Simpler API but uses non-standard conflict markers (`<<<<<<< ++`/`=======`/`>>>>>>>`). Incompatible with git, vim, and editor conflict resolvers.

## Decision

Use `merge3>=0.0.16`.

## Rationale

- Active project (released Oct 2025), directly maintained by the Breezy team
- MIT license — no commercial or copyleft concerns
- `merge_lines()` produces standard Git conflict markers when `name_a`, `name_b`, `start_marker`, `mid_marker`, `end_marker` are specified explicitly
- Minimal API surface — one class (`Merge3`), one method (`merge_lines()`)
- Zero-dependency itself (pure Python)
- The manual `difflib` implementation is error-prone for adjacent hunk cases and represents 100+ lines of code that would need its own tests

## Consequences

- Adds one PyPI dependency to `pyproject.toml`
- If `merge3` is abandoned, the `difflib` fallback documented in `research/stack.md` provides a contingency implementation path
- All conflict markers in user-facing files will be Git-compatible (`<<<<<<< ours`/`=======`/`>>>>>>> theirs`)
