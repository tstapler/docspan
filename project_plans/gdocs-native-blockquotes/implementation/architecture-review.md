# Architecture Review: gdocs-native-blockquotes
**Date**: 2026-08-17
**Verdict**: CLEAN

## Blockers
(none)

## Concerns
(none)

## Nitpicks
- Story 2.2's `_blockquote_paragraph_style_fields(node) -> Tuple[dict, List[str]]` (plan.md:194) and Story 2.3's example fields string (`"namedStyleType,indentStart,borderLeft"`, plan.md:199) still mix a `List[str]` return type with a comma-joined string consumer; confirm the merge point in `_make_style_update_requests`/`_build_insert_requests` does the join (and dedup, in case a future caller passes overlapping keys) rather than assuming call-site string concatenation stays correct by convention.
- Story 1.3's task 1 ("read `_structural_score`'s full body... not yet read this pass", plan.md:165) is correctly flagged as an open question rather than pre-answered — good practice, no change needed, just confirming it's tracked (also mirrored as Unresolved Question 5) and not silently dropped before implementation starts.

## Summary
CLEAN — 0 blockers, 0 concerns, 2 nitpicks carried forward (both non-blocking, previously noted).

## Repair-Loop Verification (this pass)

Checked only the 4 previously-blocked/concerned items against plan.md's actual story/task text (not summaries):

1. **BLOCKER — fence language lost in quote: RESOLVED.** Story 2.1's AC #4 (plan.md:178) explicitly requires `_walk_block_quote`'s `block_code` branch to call `_nodes_from_code_block(child, emit_language_marker=True, ...)`, the emitted marker node to carry `is_blockquote=True`/`quote_depth`, and the `python` tag to survive push→pull. Task 2 (plan.md:182) implements exactly this, mirroring the top-level `parse()` call site. Story 3.2's AC (plan.md:263) now explicitly conditions the nested `("code", lang, [...])` grouping on `lang` equal to the original language (not `None`), and states in-line that this "requires Story 2.1's `emit_language_marker=True` fix." Story 3.3 Task 4 (plan.md:280) explicitly includes the fence's language tag surviving round-trip in its test list. This is a genuine plan-body fix, not just a claimed one.

2. **CONCERN — illegal `is_blockquote`/`quote_depth` state pair: RESOLVED.** Story 1.1's AC #2 (plan.md:139) states construction with an illegal combination raises `ValueError` in `__post_init__`. Task 2 (plan.md:145) adds the `__post_init__` enforcing `is_blockquote == (quote_depth > 0)`. Matches ADR-001's decision (line 28) to prefer the invariant over collapsing to a single derived field.

3. **CONCERN — brittle marker dict-equality: RESOLVED.** Story 3.1's AC #3 (plan.md:244) explicitly requires detection to match even when Docs echoes an extra unrequested sub-field, i.e. comparison of only `color`/`width`/`dashStyle`, not whole-dict equality. Task 1 (plan.md:247) implements `_detect_blockquote_depth` comparing only those specific sub-fields. Matches ADR-001 (line 29).

4. **CONCERN — undeclared constant ownership: RESOLVED.** Story 1.1's AC #3-4 (plan.md:140-141) state `docs_structure_parser.py` defines and owns `BLOCKQUOTE_BORDER_MARKER`/`BLOCKQUOTE_INDENT_PT_PER_LEVEL`, that `docs_request_builder.py` imports (not redefines/copies) them, and require a test asserting object identity (`is`) between the two references. Task 3 (plan.md:146) implements the import direction. Matches ADR-001 (line 30).

All four items are resolved directly in plan.md's story/task text, consistent with ADR-001's corresponding decisions. No new issues surfaced in the patched sections. Verdict upgraded from BLOCKED to CLEAN.
