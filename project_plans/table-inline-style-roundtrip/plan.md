# Implementation Plan: table-inline-style-roundtrip

## Overview

Two independent fixes + warning improvement. Each is self-contained; they can be implemented
and reviewed separately, though they share the same test file additions.

---

## Fix 1 — Merge adjacent same-URL spans in `_render_spans` (Bug B — pull side)

**File**: `src/docspan/backends/google_docs/nodes_to_markdown.py`  
**Function**: `_render_spans` (lines 112–125)

**Change**: Replace the current per-span loop with a grouping loop:

```
1. Walk spans; group consecutive spans where span.link == current group's link (non-None).
2. For each group with a non-None link:
   a. For each span in the group, render inner formatting only
      (monospace → `…`, bold → **…**, italic → *…*) — no link wrapper yet.
   b. Concatenate inner-rendered texts.
   c. Emit `[concat](url)` as a single token.
3. For spans with link=None, render as before (monospace/bold/italic wrappers, no link).
```

The grouping reuses the existing render helpers; the only new logic is the grouping step
and deferred link wrapping.

**Test additions** (`tests/test_nodes_to_markdown.py`):
- `test_link_containing_code_span_renders_as_single_link` — `[Foo \`bar\` baz](url)`
- `test_link_containing_bold_and_code_renders_as_single_link` — `[**bold** \`code\`](url)`
- `test_adjacent_links_with_different_urls_stay_separate` — confirm no unintended merging

---

## Fix 2 — Apply table cell styling against post-fill document (Bug A — push side)

**Files**:
- `src/docspan/backends/google_docs/docs_request_builder.py` — `build_second_pass_requests`
- `src/docspan/backends/google_docs/backend.py` — `push()` pass-2 orchestration

**Change**:

In `build_second_pass_requests` (line 2620), split the second pass into two sub-batches:

```
Pass 2a: build_table_fill_requests → submit → re-fetch document if new tables were created
Pass 2b: build_span_style_requests + build_table_cell_span_requests
         (for the table-cell call, use the refreshed doc when new tables exist)
```

The gating condition "new tables were created" is already implicit: if
`build_table_fill_requests` emits any `insertText` requests into table cells that were empty
in the pre-fill `doc`, a new table exists. The simplest check: compare `len(table_fill_requests) > 0`
where those requests target cells whose pre-fill content was empty.

Alternative if the above is hard to detect: re-fetch unconditionally when any table cell
fill requests were emitted. One extra API call per push that writes table content — acceptable.

**Warning improvement** (`backend.py`, `_render_unplaced_cells`):
- Change `unplaced_table_cells` return type to include `(row_idx, col_idx)` per cell.
- Update `_render_unplaced_cells` to format: *"row 2, col 3: `code`"*.
- After Fix 2 is in place, this warning should only fire for pathological cases (cells whose
  text cannot be located even in the refreshed document).

**Test additions** (`tests/test_table_cell_spans.py`):
- `test_first_push_monospace_cell_styling_applied` — simulates a newly-created table (empty
  cells in pre-fill doc) and asserts that `updateTextStyle` requests with Courier New are
  emitted for the code-span cells.
- `test_unplaced_cell_warning_includes_coordinates` — asserts the warning string contains
  row/col info.

---

## Fix 3 — Warning actionability (improvement, no functional change)

Covered under Fix 2's warning improvement above. If Fix 2 is implemented, the warning becomes
a true edge-case signal rather than a routine first-push message.

---

## Implementation order

1. **Fix 1** (simpler, no API changes, pure rendering logic): implement + test
2. **Fix 2** (requires API re-fetch logic): implement + test
3. **Warning improvement**: implement as part of Fix 2

---

## Adversarial review

**Q: Does grouping in `_render_spans` break spans where the link text includes Markdown special
characters?**  
A: No — the existing per-span rendering of monospace/bold/italic already handles escaping;
only the `[…](url)` wrapper is deferred. No new escaping surface is introduced.

**Q: Will the extra `documents.get` in Fix 2 cause rate-limit issues?**  
A: Only triggered when new tables are created in a push. This is not the hot path (most pushes
update existing content). Acceptable tradeoff.

**Q: Could the re-fetched document have stale indices if Google Docs's write is not yet
consistent?**  
A: Google Docs API writes are synchronous — the response to a `batchUpdate` confirms all
mutations are applied. A subsequent `documents.get` sees the post-write state.

**Q: What if `_cell_placement` still fails after re-fetch (e.g., text was stripped)?**  
A: The existing `unplaced_table_cells` path still runs; with the improved warning it now
includes coordinates and a plain-English reason ("cell text not found in document after fill"),
making the residual case actionable.
