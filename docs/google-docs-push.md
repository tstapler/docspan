# Google Docs Push — Implementation Plan

## Goal

Implement `GoogleDocsBackend.push(local_path, doc_id)` so that a local markdown file
is converted to Google Docs native format and written back to an existing document via
the Docs API. The pull direction (Docs → markdown) is already wired; this covers the
reverse.

---

## API surface

The Google Docs REST API exposes two relevant operations:

| Method | Endpoint | Purpose |
|---|---|---|
| `documents.get` | `GET /v1/documents/{documentId}` | Read current doc structure (needed for structural diff) |
| `documents.batchUpdate` | `POST /v1/documents/{documentId}:batchUpdate` | Write content via a list of `Request` objects |

There is no single "replace content" call. All writes go through `batchUpdate`, which
applies a sequence of atomic requests against the document's internal index positions.

---

## Content write strategies

### Option A — Delete-all then insert (simplest, chosen for v1)

1. `GET` the document to find the body end index.
2. Send a `batchUpdate` with two requests:
   - `deleteContentRange` — delete everything from index 1 to `endIndex - 1`
   - A sequence of `insertText` / `insertInlineImage` / `createParagraphBullets` etc.
     requests that rebuild the document from the converted markdown.

**Pros:** simple, no diffing logic, always produces a clean document.  
**Cons:** destroys all comments and suggestions on the existing document; resets cursor
positions for any collaborators currently in the doc.

This is acceptable for v1. A diff-based update (Option B) can be added later.

### Option B — Structural diff update (future)

Compute a diff between the current ADF-like structure and the desired structure, then
emit only the minimal `batchUpdate` requests to reconcile the delta. Preserves comments
and revision history. Significantly more complex — deferred.

---

## Markdown → Docs API request conversion

The Docs API uses positional `Request` objects, not an HTML or ADF blob. Each request
operates on a character index within the document body.

### Conversion pipeline

```
local .md file
  → MarkdownParser (reuse from confluence backend)
  → AST (list of MarkdownNode)
  → DocsRequestBuilder
  → list[Request]  (Google Docs API format)
  → documents.batchUpdate
```

### DocsRequestBuilder — node mapping

| AST node type | Docs API requests |
|---|---|
| `HeadingNode` (h1–h6) | `insertText` + `updateParagraphStyle` (HEADING_1–HEADING_6) |
| `ParagraphNode` | `insertText` + `updateParagraphStyle` (NORMAL_TEXT) |
| `BoldNode` | `insertText` + `updateTextStyle` (bold: true) |
| `ItalicNode` | `insertText` + `updateTextStyle` (italic: true) |
| `CodeInlineNode` | `insertText` + `updateTextStyle` (weightedFontFamily: Courier New) |
| `CodeBlockNode` | `insertText` + `updateParagraphStyle` (NORMAL_TEXT) + monospace style |
| `LinkNode` | `insertText` + `updateTextStyle` (link.url) |
| `BulletListNode` | `insertText` + `createParagraphBullets` (BULLET_DISC_CIRCLE_SQUARE) |
| `OrderedListNode` | `insertText` + `createParagraphBullets` (NUMBERED_DECIMAL_ALPHA_ROMAN) |
| `HorizontalRuleNode` | `insertText` (`\n`) — Docs has no native HR; use blank line |
| `ImageNode` | `insertInlineImage` (requires publicly accessible URL or Drive file) |
| `TableNode` | `insertTable` + cell population — complex, deferred |

### Index tracking

Each `insertText` shifts all subsequent indices. The builder must track a running
`cursor_index` (starting at 1) and update it after every insertion:

```python
cursor += len(text_inserted)
```

All requests are accumulated in order and sent in a single `batchUpdate` call.

---

## Implementation plan

### Phase 1 — Core push (no images, no tables)

**Files to create/modify:**

- `src/markgate/backends/google_docs/docs_request_builder.py` — new  
  Walks the markdown AST and emits a list of Docs API `Request` dicts.

- `src/markgate/backends/google_docs/client.py` — extend  
  Add `clear_document(doc_id)` and `batch_update(doc_id, requests)` methods.

- `src/markgate/backends/google_docs/backend.py` — implement `push()`  
  Wire parser → builder → client.

**Acceptance criteria:**
- `markgate push docs/test.md` with a known Google Doc ID updates the document.
- Headings render as Heading 1–3 in Docs.
- Bold, italic, inline code render correctly.
- Bullet and numbered lists render correctly.
- Links are clickable.
- Existing document content is replaced cleanly.

### Phase 2 — Image support

Google Docs inline images must be inserted via a publicly accessible URL or by
first uploading to Drive. Options:

1. Upload image files to a Drive folder (requires Drive write scope), then use the
   file ID as the image source in `insertInlineImage`.
2. Require images to be public URLs (simpler but limited).

Plan: default to option 2; add Drive upload path behind a config flag.

### Phase 3 — Table support

`insertTable` creates an empty table; cells are populated via `insertText` into
each cell's content range. Index arithmetic is complex. Deferred until Phase 1 and 2
are stable.

### Phase 4 — Structural diff update (Option B)

Deferred. Requires reading the current document structure, computing a node-level diff,
and emitting minimal update requests. Useful for preserving comments.

---

## OAuth scope requirements

The existing pull path uses:
- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/documents.readonly`

Push requires:
- `https://www.googleapis.com/auth/documents` (read/write)
- `https://www.googleapis.com/auth/drive` (read/write, for Phase 2 image upload)

The `auth_setup` wizard in `GoogleDocsBackend` must request the broader scopes when
push is configured. Pull-only mappings can keep the readonly scopes.

Update `src/markgate/backends/google_docs/auth.py`:
- Add `PUSH_SCOPES` constant
- `auth_setup(direction)` → request readonly or read/write scopes based on direction

---

## Known limitations (v1)

- Comments and suggestions on the existing document are destroyed on every push.
- Images require public URLs.
- Tables are not supported.
- The Docs API rate limit is 300 requests/minute/project. Large documents with many
  inline style changes may need request batching with backoff.

---

## Reference

- [Google Docs API — batchUpdate](https://developers.google.com/docs/api/reference/rest/v1/documents/batchUpdate)
- [Docs API — Request types](https://developers.google.com/docs/api/reference/rest/v1/documents/request)
- [Docs API — Document structure](https://developers.google.com/docs/api/concepts/structure)
- [Drive API — Files: create](https://developers.google.com/drive/api/reference/rest/v3/files/create) (for image upload)
