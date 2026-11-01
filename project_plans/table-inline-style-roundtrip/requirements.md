# Requirements: table-inline-style-roundtrip

**Source item**: 541a8298-c99a-42b4-9a05-4e37eaab2b36  
**Related issues**: #49, #38

## Problem

Two distinct but related bugs corrupt inline formatting during push→pull round trips:

### Bug A — Code spans in table cells lose backticks on first push
`\`code\`` inside a table cell is pushed as plain text `code` the *first* time that table exists
in a document. On pull, the monospace font that would normally trigger backtick reconstruction is
absent, so the user sees unformatted text.

Warning emitted: *"N table cell(s) kept their text but not their formatting"*

Root cause: `build_second_pass_requests` applies `build_table_cell_span_requests` against a
pre-fill document snapshot. A newly-created table is still empty at that point, so
`_cell_placement` cannot locate the cell text and emits no `updateTextStyle` request. Styling is
permanently lost for that push; a second push fixes it because the cells are then populated.

### Bug B — Link containing code span fragments into three adjacent links on pull
`[Foo \`bar\` baz](url)` is correctly represented internally as three `TextSpan` objects all
sharing `link=url`, but `_render_spans` wraps each span individually in `[…](url)`, producing
`[Foo ](url)[\`bar\`](url)[ baz](url)`.

Root cause: `_render_spans` in `nodes_to_markdown.py` applies the `[text](url)` wrapper per span
with no awareness of adjacent spans sharing the same link.

## Scope

1. Fix Bug A so that inline styles (including monospace/code) on newly-created table cells are
   applied correctly without requiring a second push.
2. Fix Bug B so that adjacent spans with the same link are merged into a single Markdown link
   during pull.
3. Improve the warning for unplaced cells to include cell coordinates (row/column index) and the
   reason why placement failed, so that if any residual cases remain they are actionable.
4. Add regression tests for both fixes.

## Out of scope
- Fixing unrelated link types from #38 (bookmark/named-range links that can't be expressed in
  Markdown at all).
- Changes to the Confluence backend.

## Acceptance criteria

1. A markdown file containing a table with `\`code\`` in a cell round-trips (push then pull)
   with the backticks preserved, even on the *first* push of a document that did not previously
   contain that table.
2. `[Foo \`bar\` baz](url)` round-trips as a single link (not three adjacent links).
3. The push warning for unplaced table cells includes the row/column coordinates and a plain-
   English reason why placement failed.
4. All existing tests continue to pass.
5. New unit tests cover: (a) first-push monospace-in-table-cell styling, (b) link-with-code-span
   pull rendering, (c) link-with-bold-and-code-span pull rendering.
