# Research: table-inline-style-roundtrip

## Code locations

### Bug A — Table cell inline styles lost on first push

| File | Lines | Role |
|---|---|---|
| `src/docspan/backends/google_docs/docs_request_builder.py` | 2620–2647 | `build_second_pass_requests` — orchestrates pass 2; all three calls share the same pre-fill `doc` |
| `src/docspan/backends/google_docs/docs_request_builder.py` | 1745–1798 | `build_table_fill_requests` — inserts text into cells (not yet applied when next call runs) |
| `src/docspan/backends/google_docs/docs_request_builder.py` | 1799–1843 | `build_table_cell_span_requests` — tries to locate cell text to get char index range |
| `src/docspan/backends/google_docs/docs_request_builder.py` | 1957–2004 | `_cell_placement` — searches textRun content for `cell.text`; returns `None` when cell is empty |
| `src/docspan/backends/google_docs/docs_request_builder.py` | 1845–1909 | `unplaced_table_cells` — accumulates cells where `_cell_placement` returned `None` |
| `src/docspan/backends/google_docs/docs_request_builder.py` | 3286–3316 | `_make_text_style_requests` — emits `updateTextStyle` with `weightedFontFamily: Courier New` |
| `src/docspan/backends/google_docs/backend.py` | 1267–1293 | `_render_unplaced_cells` — formats the warning; no row/column coordinates today |
| `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py` | 461 | `_cell_from_token` — builds `TableCell(text="code", spans=[TextSpan("code", monospace=True)])` |
| `src/docspan/backends/google_docs/docs_structure_parser.py` | 698–748 | `_parse_cell` — reads `weightedFontFamily.fontFamily == "Courier New"` to infer monospace |
| `src/docspan/backends/google_docs/nodes_to_markdown.py` | 128–130 | `_cell_markdown_text` — returns `cell.text` (plain) when `cell.spans` is empty |

**Why the second push works**: By then the cells already contain text, so `_cell_placement`
finds the content and returns a valid `(start, end)` range.

**Fix options considered**:

*Option 1 — Three-pass push (preferred)*: After `build_table_fill_requests`, re-fetch the
document (or derive indices analytically from the insert offsets), then apply
`build_table_cell_span_requests` against the post-fill snapshot. This keeps the existing
API structure clean and is the approach the existing comment flags as "left for its own change."

*Option 2 — Predict indices arithmetically*: `build_table_fill_requests` knows the exact
text it inserts and the insertion offset, so the start/end char indices for the new text are
computable without an extra API round-trip. More complex; index arithmetic in the Docs API
has sharp edges.

*Option 3 — Always push styling with a second batch* (current workaround): Keep the existing
flow; instead, detect "table just created" vs "table already populated" and skip the warning
when a second push would naturally fix it. Does not actually fix the bug.

Option 1 is cleanest and matches the existing comment's intent. An extra `documents.get` in the
pass-2 path adds one API call per push of a document with new tables — acceptable given it only
occurs on the first push of each new table.

### Bug B — Link with code span fragments on pull

| File | Lines | Role |
|---|---|---|
| `src/docspan/backends/google_docs/nodes_to_markdown.py` | 112–125 | `_render_spans` — applies `[text](url)` per span |
| `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py` | 165–200 | `_spans_from_inline` — propagates `link` into codespan children |
| `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py` | 203–217 | `_merge_spans` — only merges spans with identical `(bold, italic, link, monospace)` |

**Data model**: `TextSpan(text, bold, italic, link, monospace)`. Three spans with
`link=url` but differing `monospace` values remain separate after `_merge_spans`.

**Fix**: In `_render_spans`, group consecutive spans that share the same non-`None` `link`
value. Render the inner formatting (monospace → `` `…` ``, bold → `**…**`, italic → `*…*`)
for each span in the group, concatenate the formatted texts, then emit a single
`[combined_text](url)` wrapper. Spans with `link=None` are rendered individually as before.

### Warning actionability

`_render_unplaced_cells` currently produces a list of cell text snippets. The `TableCell`
dataclass carries row/col position if `_cell_placement` records it, but the unplaced set only
stores the `TableCell` object. To add coordinates, `unplaced_table_cells` would need to return
`(row_idx, col_idx, TableCell)` tuples, and `_render_unplaced_cells` would format them as
"row R, col C: `text`".

## Existing test coverage

| Test | File | What it covers |
|---|---|---|
| `test_bold_and_code_in_a_cell_survive` | `test_table_cell_spans.py:85` | Monospace spans survive push-side parse |
| `test_a_cell_monospace_span_with_a_backtick_escapes_via_a_longer_fence` | `test_table_cell_spans.py:654` | Monospace cell pull rendering |
| `TestPassTwoStylesCells` | `test_table_cell_spans.py:127` | Cell placement + styling (pre-populated cells only) |
| `TestPushReportsWhatItCouldNotStyle` | `test_table_cell_spans.py:321` | Warning wiring |

**Gaps**: No test for first-push new-table monospace styling. No test for link-with-codespan
fragmentation in pull rendering.

## Risk areas

- Extra `documents.get` call in pass 2 increases API quota usage for every push that creates a
  new table. Should be gated on "did pass 1 create any new tables?"
- Index arithmetic in the Docs API is notoriously tricky; analytical index prediction (Option 2)
  risks off-by-one errors from paragraph-end characters. The re-fetch approach avoids this.
- `_render_spans` change must not break existing rendering of spans that share no link (the
  common case) or spans with `link=None`.
