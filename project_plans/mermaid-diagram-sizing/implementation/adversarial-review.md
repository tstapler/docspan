# Adversarial Review: mermaid-diagram-sizing

**Date**: 2026-09-18
**Verdict**: CLEAN
**Note**: This is a re-review pass scoped to the 2 blockers from the prior round. Concerns/Minors from the prior round are carried forward unchanged below (not re-evaluated).

## Blockers

None. Both prior blockers are RESOLVED.

- **Blocker 1 (Success Metric verified only against a mocked test surface) — RESOLVED.** `plan.md` now includes Story 1.3.4 (`project_plans/mermaid-diagram-sizing/implementation/plan.md:395-409`), a manual verification task: push a real markdown file with a mermaid fence to a real/scratch Google Doc via the CLI and visually confirm (a) approximately full content width (~468pt), (b) horizontal centering, (c) idempotency on a second unchanged push. It's explicitly scoped as manual/non-CI-gated and framed as satisfying requirements.md's exact wording ("verified against a real doc push, not just a unit test of the request payload"), and is also carried into the Phase 6 verification checklist (`plan.md:417`: "Story 1.3.4's manual real-doc push was performed and visually confirmed ... not skipped in favor of the in-process tests alone"). This closes the gap: the plan no longer relies solely on mocked request-payload tests to claim the Success Metric is met.

- **Blocker 2 (non-goal mischaracterized; retroactive-resize swallow untested) — RESOLVED.** `plan.md` adds a "Known Limitation: Existing (Already-Pushed) Mermaid Diagrams Are Not Resized" section (`plan.md:72-82`) that traces the real mechanism rather than asserting an arbitrary scope cut. I independently verified each cited line against the current source, not just the plan's prose:
  - `docs_structure_parser.py:554-555` — pulled `DocsImageNode.width_pt`/`.height_pt` come from the Docs API's actual current `embeddedObject.size`. Confirmed verbatim.
  - `docs_request_builder.py:289` (`_node_key`) — `("__image__", node.alt, node.width_pt, node.height_pt)`. Confirmed verbatim; a size change does change the key.
  - `docs_request_builder.py:386` (`_content_key`) — `("__image__", node.alt)` only, no size. Confirmed verbatim; `alt` is the content-hash, so an unchanged diagram's `_content_key` still matches even when its size changed.
  - `docs_request_builder.py:3043-3095` (`_restyles` / `_make_style_update_requests`) — both explicitly early-return `False`/`[]` whenever either node is a `DocsImageNode`. Confirmed verbatim.
  The plan's synthesis — a size-changed-but-content-unchanged image gets folded to "equal" by `_repair` (via the `_content_key` match) and then has no restyle path available, so the new size is silently swallowed on every subsequent push — is exactly what the code does. The plan now states this as a traced, verified behavior of the existing diff engine, not an assumed non-goal.
  - Story 1.3.3 (`plan.md:377-391`) and Task 1.3.3a add a regression test asserting this is a safe no-op (zero requests, no exception) for a pulled/target pair whose sizes differ but whose `alt` matches — locking in the current behavior so a future `_repair`/`_content_key`/`_restyles` change can't silently flip it into a crash or corruption without a test failing. This is present as a planned task, not yet a merged test, which is appropriate for a plan document at this phase.

## Concerns
- No multi-node test for the new paragraph-centering index arithmetic (only single-node cases are specified in Story 1.2.1's acceptance criteria).
- Silent approximation for non-Letter page margins — `CONTENT_WIDTH_PT` is a fixed 468pt constant and does not read a doc's actual `documentStyle` margins, so diagrams in docs with non-default margins won't exactly fill the visible content width.
- Unbounded height with no guardrail when combined with the "always-fill" scaling decision — a diagram with an extreme native aspect ratio (very tall/narrow) will fill to full width and produce an arbitrarily tall image with no height cap.

## Minors
- Loosely-typed `Dict[str, object]` used in pseudocode (Task 1.1.3a's `updates: Dict[str, object]`) rather than a more precise type.
- The IHDR parser (`_png_pixel_dimensions`) doesn't validate the chunk length field before trusting the width/height fields that follow it.
- `test_mermaid_appendix.py`'s PNG fixture — already checked as fine, no action needed.
