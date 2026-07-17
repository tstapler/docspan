# Plan: Google Docs table push + inline styles on insert

## Problem

The Google Docs push path drops two things, both blocking real-world design-doc use:

1. **Inline styles/links are lost on insert.** `MarkdownToParagraphParser` flattens inline
   tokens to plain text (no spans), and `DocsRequestBuilder._make_insert_requests` never applies
   text styles — the `_make_text_style_requests` helper exists but is unused. So bold, italic,
   inline code, and **links** vanish on push.
2. **Tables are dropped entirely.** The markdown parser skips `table` tokens and
   `DocsStructureParser` skips table elements (`# table … silently skipped`), so markdown tables
   never reach the doc, and a doc that already has a table can't be diffed against.

## Goals

- Preserve inline **links, bold, italic, monospace** when inserting/replacing paragraphs.
- Render markdown tables as real Google Docs tables on push.
- Parse existing Google Docs tables back into the node model so the structural diff is
  **idempotent** (pushing an unchanged table produces no requests; it isn't re-inserted).

## Non-goals (v1)

- Rich inline styling *inside* table cells — v1 fills cells with plain text (cell links/bold become
  plain text). Prose links/formatting are fully preserved. Rich cells are a fast-follow.
- Image push, nested tables, cell merges.

## Design

### Inline styles (Part 1)
- Parser: add `_spans_from_inline(children, …)` that walks mistune inline tokens
  (`text`/`strong`/`emphasis`/`link`/`codespan`) into ordered `TextSpan`s, propagating
  bold/italic/link/monospace through nesting. `node.text = "".join(span.text)` so the existing
  diff key (keyed on text) is unchanged.
- Builder: in `_make_insert_requests`, after `insertText`, walk `node.spans`, compute UTF-16
  offsets, and emit `updateTextStyle` (via `_make_text_style_requests`) for any styled span.

### Tables (Part 2)
- New `DocsTableNode(rows: List[List[str]], start_index, end_index)` in the structure module.
- Parser emits `DocsTableNode` for `table` tokens (header row + body rows, cell = plain text).
- `DocsStructureParser` parses live `table` elements into `DocsTableNode` (rows from
  `table.tableRows[].tableCells[].content` paragraphs), with real start/end indices.
- `DocsRequestBuilder.build` now diffs a mixed `List[Union[DocsParagraphNode, DocsTableNode]]`.
  Table diff key = `("__table__", tuple(tuple(row) for row in rows))`.
  - **insert** → emit `insertTable` (rows×cols) at the insert index (Pass 1).
  - **delete** → `deleteContentRange` over the table span.
  - **equal** → no request (idempotent).
- **Cell fill is two-pass** (robust vs. fragile predicted indices): Pass 1 inserts empty tables;
  `backend.push` re-fetches the doc, and `build_table_fill_requests(doc, queued_tables)` locates
  empty tables in document order and emits reverse-ordered `insertText` per cell. Cell indices come
  from the *real* re-fetched JSON, so no index guessing.

## Verification

- Unit tests (no network) for: span extraction, styled-insert requests, markdown→table node,
  live-table→node parsing, table insert/delete/equal diffing, and cell-fill request generation
  against a sample table JSON.
- **Live smoke test still required** before trusting cell fill end-to-end — needs docspan Google
  credentials (service account or OAuth token). Tracked as the last step.
