# Validation: table-inline-style-roundtrip

## Plan completeness check

| Requirement | Addressed in plan? | Notes |
|---|---|---|
| Code spans in table cells survive first push | Yes — Fix 2 (re-fetch doc) | Approach is correct; extra API call acceptable |
| Link with code span renders as single link on pull | Yes — Fix 1 (`_render_spans` grouping) | Pure rendering fix; no API surface change |
| Warning includes cell coordinates | Yes — Fix 2 warning improvement | Depends on unplaced_table_cells return-type change |
| All existing tests pass | Implicitly required | No existing behavior changed; fixes are additive |
| New regression tests | Yes — listed per fix | First-push monospace, link fragmentation, warning coords |

## Risks and open questions

### Risk 1 — Re-fetch gating condition (Fix 2)
The plan says "gate the re-fetch on whether new tables were created." The simplest reliable
gate is: re-fetch whenever `build_table_fill_requests` returns any requests that target cells
that were empty in the pre-fill snapshot. This requires `build_table_fill_requests` to return
a flag or the caller to inspect the request list. Implementer should confirm this is
inspectable without adding a separate boolean return value (which would change the function
signature). **Risk: low — the request list is returned already; empty-cell targets are
distinguishable by index.**

### Risk 2 — `_render_spans` grouping edge cases
The grouping must handle:
- A single span with a link (no grouping needed; still emits `[text](url)` correctly)
- Spans where link changes mid-sequence (different URLs → separate groups → separate links)
- A span with `link=None` interspersed between two spans with the same link (should NOT merge
  across the None span; they become two separate links)
- Empty text spans (should be dropped or handled gracefully)

**Risk: medium** — the grouping logic is simple but has corner cases. Tests must cover all
four scenarios above.

### Risk 3 — Unplaced-cell warning type change
Changing `unplaced_table_cells`'s return type from `Set[TableCell]` to
`List[Tuple[int, int, TableCell]]` (or similar) touches `_render_unplaced_cells` and
`TestPushReportsWhatItCouldNotStyle`. Implementer should check whether `unplaced_table_cells`
is called from more than one place. **Risk: low — internal function, easy to refactor.**

### Risk 4 — First-push test requires fake document structure
`test_first_push_monospace_cell_styling_applied` needs to simulate an empty-cell document
(pre-fill) and assert that after the re-fetch path, `updateTextStyle` requests are emitted.
This likely requires either a two-response mock (one empty, one filled) or directly calling
`build_table_cell_span_requests` with a synthetically filled doc. The existing test harness in
`test_table_cell_spans.py` uses `FakeDocumentsClient`; implementer should extend it with a
two-call sequence. **Risk: medium — requires test infrastructure work.**

## Validation sign-off criteria

- [ ] `pytest tests/test_nodes_to_markdown.py` passes with new link-grouping tests
- [ ] `pytest tests/test_table_cell_spans.py` passes with new first-push and warning tests
- [ ] `pytest tests/` full suite passes (no regressions)
- [ ] Manual round-trip test: push a doc with a table containing `` `code` `` → pull → confirm
      backticks present (requires Google Docs credentials; can be deferred to integration test)
- [ ] Manual round-trip test: push a doc with `[Foo \`bar\` baz](url)` → pull → confirm single
      link (same caveat)
