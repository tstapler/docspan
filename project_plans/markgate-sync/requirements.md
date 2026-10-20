# markgate-sync — Requirements

## Project context

markgate is a Python CLI (`markgate push/pull`) that syncs local markdown files with Google Docs
and Confluence. The codebase has:

- `src/markgate/backends/base.py` — abstract `Backend` with `push()/pull()` interface
- `src/markgate/backends/google_docs/` — pull works (HTML→markdown); push is `NotImplementedError`
- `src/markgate/backends/confluence/` — push/pull wired but `update_page` call has wrong signature
  (passes kwargs instead of a `ConfluencePage` object); comment client fully implemented
- `src/markgate/core/conflict.py` — stale code from fork, broken imports
- `src/markgate/core/sync_engine.py` — stale code from fork, broken imports (`gdrive_client` etc.)

## Feature areas

### 1. Google Docs push — structural diff with comment preservation

Implement `GoogleDocsBackend.push()` so that local markdown is written back to Google Docs
**without destroying existing comments or suggestions**.

**Strategy**: structural diff push
1. `GET /v1/documents/{documentId}` — read current doc structure
2. Parse the current doc into a comparable AST
3. Parse the local markdown into the target AST  
4. Diff the two ASTs
5. Emit only the minimal `batchUpdate` requests to reconcile the delta
6. Comments/suggestions that anchor to unchanged text ranges are preserved

This is more complex than delete-all-then-insert but is the correct approach for a tool
used in collaborative workflows.

**Pipeline**:
```
local .md → MarkdownParser → AST(target)
GET doc   → DocsStructureParser → AST(current)
AST diff  → DocsRequestBuilder → list[Request]
batchUpdate
```

**Node mapping** (markdown AST → Docs API requests):
- HeadingNode (h1–h6) → insertText + updateParagraphStyle (HEADING_1–HEADING_6)
- ParagraphNode → insertText + updateParagraphStyle (NORMAL_TEXT)
- BoldNode / ItalicNode → insertText + updateTextStyle
- CodeInlineNode → insertText + updateTextStyle (Courier New)
- CodeBlockNode → insertText + monospace style
- LinkNode → insertText + updateTextStyle (link.url)
- BulletListNode → insertText + createParagraphBullets (BULLET_DISC_CIRCLE_SQUARE)
- OrderedListNode → insertText + createParagraphBullets (NUMBERED_DECIMAL_ALPHA_ROMAN)
- HorizontalRuleNode → insertText newline
- ImageNode → insertInlineImage (public URL)
- TableNode → deferred

**OAuth scopes**: push needs `https://www.googleapis.com/auth/documents` (read/write).
Existing pull uses `documents.readonly` — `auth.py` must be updated.

**Files to create/modify**:
- `src/markgate/backends/google_docs/docs_structure_parser.py` — parse current Docs JSON → AST
- `src/markgate/backends/google_docs/docs_request_builder.py` — diff two ASTs → minimal Requests
- `src/markgate/backends/google_docs/client.py` — add `get_document()`, `batch_update()`
- `src/markgate/backends/google_docs/backend.py` — implement `push()`
- `src/markgate/backends/google_docs/auth.py` — add `PUSH_SCOPES`, update `auth_setup()`

### 2. Confluence backend — fix and complete

**2a. Fix update_page call** (`backend.py:57`)

Current code passes keyword args (`page_id=`, `title=`, `adf_content=`, `version=`) but
`ConfluenceClient.update_page()` expects a `ConfluencePage` object. Fix to construct and pass
a `ConfluencePage`.

**2b. Fix export_as_html reference** (`backend.py`)

`GoogleDocsBackend.pull()` calls `self._client.export_as_html()` but the method is named
`get_doc_content()` in `client.py`. Fix the method call.

**2c. Comments in pull — sidecar `.comments.md`**

When pulling a Confluence page, also fetch comments and write them to
`<local_path>.comments.md` alongside the main markdown file.

Format:
```markdown
# Comments: <page title>

## Inline comments

### [<id>] <author> — <date>

> Selection: "<anchored text>"

<comment body>

---

## Footer comments

### [<id>] <author> — <date>

<comment body>

---
```

The comment client (`ConfluenceCommentClient`) is already fully implemented. The backend
just needs to call `get_page_inline_comments()` and `get_page_footer_comments()` and
write the sidecar.

**Files to modify**:
- `src/markgate/backends/confluence/backend.py` — fix update_page, implement comment pull

### 3. Remote change integration — three-way merge

Support bidirectional sync with conflict detection and three-way merge resolution.

**State file**: `.markgate-state.json` in the project root (gitignore-able).

State schema per mapping:
```json
{
  "mappings": {
    "<local_path>": {
      "doc_id": "...",
      "backend": "google_docs|confluence",
      "last_synced_at": "<ISO-8601>",
      "base_hash": "<sha256 of content at last sync>",
      "remote_version": "<doc version/revision at last sync>",
      "local_hash": "<sha256 of local file at last sync>"
    }
  }
}
```

**Conflict detection algorithm**:
1. On pull: compare remote version to `remote_version` in state → remote changed?
2. On pull: compare local file hash to `local_hash` → local changed?
3. If only remote changed → fast-forward pull (no conflict)
4. If only local changed → skip pull unless `--force-remote`
5. If both changed → **three-way merge**:
   - base = content at `last_synced_at` (retrieved from state's `base_hash`)
   - theirs = remote content (fetched)
   - ours = local content (read from disk)
   - Use `merge3` / difflib-based merge to produce merged content
   - On clean merge → write merged file, update state
   - On conflict → write file with `<<<<<<`/`=======`/`>>>>>>` markers, report to user

**User-facing conflict resolution**:
- `markgate conflicts list` — show files with unresolved markers
- `markgate conflicts resolve <file> --accept remote|local|merged` — apply resolution

**Files to create/modify**:
- `src/markgate/core/state.py` — `SyncState` dataclass + `load()`/`save()` + hash helpers
- `src/markgate/core/merge.py` — `three_way_merge(base, theirs, ours)` → `MergeResult`
- `src/markgate/backends/base.py` — add `get_remote_version()` abstract method
- `src/markgate/backends/google_docs/backend.py` — implement `get_remote_version()`
- `src/markgate/backends/confluence/backend.py` — implement `get_remote_version()`
- `src/markgate/cli/main.py` — add `conflicts` subcommand, integrate state into push/pull
- `src/markgate/core/__init__.py` — export `SyncState`, `three_way_merge`
- `src/markgate/core/conflict.py` — delete (stale fork code, replaced by merge.py)
- `src/markgate/core/sync_engine.py` — delete (stale fork code)

### 4. Google Docs comments in push

When pushing to Google Docs, existing comments that anchor to text still present in the
new version must not be deleted. The structural diff approach (feature 1) achieves this
by design: only the changed portions are re-written, so anchors on unchanged text survive.

For comments that anchor to text that has been deleted or moved, they will be orphaned by
the Docs API automatically (standard Docs behavior).

No additional implementation needed beyond feature 1.

## Acceptance criteria

### Google Docs push
- [ ] `markgate push notes.md` with a known doc ID writes heading/paragraph/list/bold/italic/link
- [ ] A comment on unchanged text survives a push that modifies other paragraphs
- [ ] A comment on deleted text is gracefully orphaned (Docs API handles this)
- [ ] OAuth scopes are updated for push direction

### Confluence
- [ ] `markgate push notes.md` correctly updates a Confluence page (ConfluencePage object)
- [ ] `markgate pull <page_id> notes.md` creates `notes.md` with page body
- [ ] `markgate pull` also creates `notes.comments.md` with inline + footer comments
- [ ] If no comments exist, no sidecar file is written

### Remote change integration
- [ ] First push/pull creates `.markgate-state.json`
- [ ] Second pull when remote changed → fast-forward, no prompt
- [ ] Second pull when local changed, remote unchanged → skip with warning
- [ ] Pull when both changed → conflict markers in file + user warning
- [ ] `markgate conflicts list` shows unresolved files
- [ ] `markgate conflicts resolve <file> --accept remote` overwrites with remote version

## Out of scope

- Google Docs image upload to Drive (public URLs only)
- Google Docs table support
- Confluence push comments (round-tripping comments back to Confluence from local edits)
- OAuth interactive wizard for Google Docs (service account credentials remain)
