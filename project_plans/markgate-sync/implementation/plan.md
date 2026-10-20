# Implementation Plan: markgate-sync

**Feature**: Implement Google Docs structural-diff push, fix Confluence backend, add three-way merge sync state
**Date**: 2026-06-01
**Status**: Ready for implementation
**ADRs**: ADR-001-merge3-dependency, ADR-002-base-content-sidecar-store

---

## Dependency Visualization

```
Phase 1: Foundation
  Task 1.1.1a: Delete stale files (conflict.py, sync_engine.py)
  Task 1.1.2a: Create core/state.py ──────────────────────────────┐
  Task 1.1.3a: Create core/merge.py                               │
  Task 1.1.4a: Add get_remote_version() to base.py               │
                                                                   ▼
Phase 2: Confluence fixes (parallel with Phase 3 after Phase 1)  Phase 4 (CLI)
  Task 2.1.1a: Fix update_page call in confluence/backend.py      │
  Task 2.1.2a: Fix export_as_html → get_doc_content reference     │
  Task 2.2.1a: Implement comment sidecar pull                     │
  Task 2.3.1a: Add get_remote_version() to confluence/backend.py──┤
                                                                   │
Phase 3: Google Docs push                                          │
  Task 3.1.1a: Extend client.py (get_document, batch_update)      │
  Task 3.1.2a: Update auth.py scopes                              │
  Task 3.2.1a: Create docs_structure_parser.py                    │
  Task 3.2.2a: Create docs_request_builder.py ───────────────────┤
  Task 3.3.1a: Implement GoogleDocsBackend.push()                 │
  Task 3.3.2a: Add get_remote_version() to google_docs/backend.py─┤
                                                                   │
Phase 4: CLI integration (requires Phase 1 complete)              │
  Task 4.1.1a: Integrate SyncState into push/pull commands ◄──────┘
  Task 4.2.1a: Add `markgate conflicts` subcommand
  Task 4.3.1a: Add merge3 to pyproject.toml + update .gitignore
```

---

## Phase 1: Foundation — State and Merge Infrastructure

### Epic 1.1: Clean up stale fork code and establish core abstractions

**Goal**: Remove broken imports from the fork, establish `SyncState` and `three_way_merge` as first-class core modules, and extend the Backend abstract interface.

---

#### Story 1.1.1: Remove stale fork files
**As a** developer, **I want** the stale `conflict.py` and `sync_engine.py` files deleted, **so that** broken imports don't interfere with the new implementation.

**Acceptance Criteria**:
- `src/docspan/core/conflict.py` does not exist
- `src/docspan/core/sync_engine.py` does not exist
- No other file imports from these modules
- `src/docspan/core/__init__.py` does not reference them

**Files**: `src/docspan/core/conflict.py`, `src/docspan/core/sync_engine.py`, `src/docspan/core/__init__.py`

##### Task 1.1.1a: Delete stale files and clean __init__.py (~3 min)
- Delete `src/docspan/core/conflict.py`
- Delete `src/docspan/core/sync_engine.py`
- Read `src/docspan/core/__init__.py`; remove any imports referencing `conflict` or `sync_engine`
- Run `grep -r "from docspan.core.conflict\|from docspan.core.sync_engine" src/` to verify no remaining references
- Files: `src/docspan/core/__init__.py`

---

#### Story 1.1.2: Implement SyncState
**As a** CLI tool, **I want** a `SyncState` dataclass with atomic load/save, **so that** push and pull operations can track what changed since the last sync.

**Acceptance Criteria**:
- `SyncState.load(path)` returns a `SyncState` with empty `mappings` if the file doesn't exist
- `SyncState.save(path)` writes atomically via `.tmp` + `os.rename()`
- `SyncState.get(local_path)` returns `MappingState | None`
- `SyncState.update(local_path, mapping)` upserts the entry
- `sha256_of_file(path)` returns hex digest of raw UTF-8 bytes
- `sha256_of_content(content: str)` returns hex digest of UTF-8 encoded content
- `MappingState` has fields: `doc_id`, `backend`, `last_synced_at`, `base_hash`, `remote_version`, `local_hash`

**Files**: `src/docspan/core/state.py`, `src/docspan/core/__init__.py`

##### Task 1.1.2a: Create src/docspan/core/state.py (~5 min)
- Create `MappingState` dataclass with fields from requirements (all `str`)
- Create `SyncState` dataclass with `mappings: dict[str, MappingState]`
- Implement `SyncState.load(path)`: if file missing, return `SyncState(mappings={})`. Parse JSON, reconstruct `MappingState` per key
- Implement `SyncState.save(path)`: serialize to dict, write to `path + '.tmp'`, call `os.rename(tmp, path)`
- Implement `SyncState.get(local_path) -> MappingState | None`
- Implement `SyncState.update(local_path, mapping)`
- Add `sha256_of_file(path: str) -> str` and `sha256_of_content(content: str) -> str` module-level helpers using `hashlib.sha256`
- Export `SyncState`, `MappingState`, `sha256_of_file`, `sha256_of_content` from `src/docspan/core/__init__.py`
- Files: `src/docspan/core/state.py`, `src/docspan/core/__init__.py`

---

#### Story 1.1.3: Implement three-way merge
**As a** sync engine, **I want** a `three_way_merge(base, theirs, ours)` function, **so that** concurrent edits to the same file can be reconciled or flagged.

**Acceptance Criteria**:
- `three_way_merge(base, theirs, ours)` returns `MergeResult(merged, has_conflicts, conflict_count)`
- When only `theirs` differs from `base`, result is `theirs` with no conflicts
- When only `ours` differs from `base`, result is `ours` with no conflicts
- When both differ non-overlappingly, result combines both changes, no conflicts
- When both differ at the same lines, conflict markers `<<<<<<< ours` / `=======` / `>>>>>>> theirs` appear
- `has_conflicts` is `True` iff merged content contains at least one `<<<<<<< ` line
- `conflict_count` equals the number of conflict blocks

**Files**: `src/docspan/core/merge.py`, `src/docspan/core/__init__.py`

##### Task 1.1.3a: Create src/docspan/core/merge.py (~4 min)
- Add `merge3` to `pyproject.toml` dependencies (in Task 4.3.1a — do this first if merge.py is created before pyproject update)
- Create `MergeResult` dataclass: `merged: str`, `has_conflicts: bool`, `conflict_count: int`
- Implement `three_way_merge(base: str, theirs: str, ours: str) -> MergeResult`:
  ```python
  from merge3 import Merge3
  base_lines = base.splitlines(keepends=True)
  ours_lines = ours.splitlines(keepends=True)
  theirs_lines = theirs.splitlines(keepends=True)
  m3 = Merge3(base_lines, ours_lines, theirs_lines)
  merged_lines = list(m3.merge_lines(
      name_a='ours', name_b='theirs',
      start_marker='<<<<<<< ours',
      mid_marker='=======',
      end_marker='>>>>>>> theirs',
  ))
  merged = ''.join(merged_lines)
  conflicts = sum(1 for line in merged_lines if line.startswith('<<<<<<<'))
  return MergeResult(merged=merged, has_conflicts=conflicts > 0, conflict_count=conflicts)
  ```
- Export `MergeResult`, `three_way_merge` from `src/docspan/core/__init__.py`
- Files: `src/docspan/core/merge.py`, `src/docspan/core/__init__.py`

---

#### Story 1.1.4: Extend Backend abstract interface
**As a** sync engine, **I want** `Backend.get_remote_version(doc_id)` as an abstract method, **so that** the conflict detection algorithm can compare remote versions without knowing the backend type.

**Acceptance Criteria**:
- `Backend.get_remote_version(doc_id: str) -> str` is declared `@abstractmethod`
- Docstring explains: Google Docs returns `revisionId`; Confluence returns `str(version.number)`
- Existing `push()`, `pull()`, `auth_setup()`, `validate_config()` signatures are unchanged

**Files**: `src/docspan/backends/base.py`

##### Task 1.1.4a: Add get_remote_version() abstract method to base.py (~2 min)
- Read `src/docspan/backends/base.py`
- Add after `pull()` abstract method:
  ```python
  @abstractmethod
  def get_remote_version(self, doc_id: str) -> str:
      """
      Return an opaque version token for the current remote document state.
      - Google Docs: returns doc['revisionId'] (opaque string)
      - Confluence: returns str(page['version']['number']) (monotonic integer as string)
      Used by == comparison to detect remote changes between syncs.
      """
  ```
- Files: `src/docspan/backends/base.py`

---

## Phase 2: Confluence Backend — Fix and Complete

### Epic 2.1: Fix broken Confluence push

**Goal**: `markgate push` for Confluence maps correctly updates pages using the `ConfluencePage` object.

---

#### Story 2.1.1: Fix update_page kwarg call
**As a** user, **I want** `markgate push notes.md` to correctly update a Confluence page, **so that** pushing doesn't raise a TypeError on keyword argument mismatch.

**Acceptance Criteria**:
- `backend.push()` constructs a `ConfluencePage` object and passes it to `self._client.update_page(page)`
- The `ConfluencePage` is initialized with: `id=doc_id`, `title=title`, `content=adf_doc` (ADF dict), `parent_id` (from page response), `version=version` (not `version+1` — `to_api_data` increments)
- No `page_id=`, `title=`, `adf_content=`, `version=` kwargs passed to `update_page`
- Push returns `PushResult(status="ok", ...)` on success

**Files**: `src/docspan/backends/confluence/backend.py`

##### Task 2.1.1a: Fix ConfluenceBackend.push() to use ConfluencePage object (~3 min)
- Read `src/docspan/backends/confluence/backend.py`
- Locate the `self._client.update_page(page_id=..., title=..., ...)` call at line ~58
- Replace with:
  ```python
  from docspan.backends.confluence.models.page import ConfluencePage
  parent_id = page.get("parentId") or page.get("ancestors", [{}])[-1].get("id", "")
  confluence_page = ConfluencePage(
      id=doc_id,
      title=title,
      content=adf_doc,
      parent_id=parent_id,
      version=version,  # to_api_data(for_update=True) increments by 1
  )
  self._client.update_page(confluence_page)
  ```
- Files: `src/docspan/backends/confluence/backend.py`

---

#### Story 2.1.2: Fix Google Docs pull method name (note: this is a Google Docs bug, mis-grouped here for convenience as it is a quick fix)
**As a** user, **I want** `markgate pull` for Google Docs to not raise `AttributeError`, **so that** pulling a Google Doc works correctly.

**Acceptance Criteria**:
- `GoogleDocsBackend.pull()` calls `self._client.get_doc_content(doc_id)` (not `export_as_html`)
- Pull returns `PullResult(status="ok", ...)` on success for a valid doc ID

**Files**: `src/docspan/backends/google_docs/backend.py`

##### Task 2.1.2a: Fix export_as_html reference in google_docs/backend.py (~2 min)
- Read `src/docspan/backends/google_docs/backend.py`
- Read `src/docspan/backends/google_docs/client.py` to confirm the correct method name
- Replace `self._client.export_as_html(doc_id)` with the correct method name (e.g. `self._client.get_doc_content(doc_id)`)
- Files: `src/docspan/backends/google_docs/backend.py`

---

### Epic 2.2: Confluence comment sidecar pull

**Goal**: When pulling a Confluence page, write inline + footer comments to `<local_path>.comments.md` (only if comments exist).

---

#### Story 2.2.1: Implement comment sidecar generation
**As a** user, **I want** `markgate pull <page_id> notes.md` to create `notes.comments.md` alongside the markdown, **so that** I can review Confluence comments locally without opening the browser.

**Acceptance Criteria**:
- `notes.comments.md` is written only if at least one inline or footer comment exists
- If no comments exist, no sidecar file is created (not even an empty one)
- Inline comments have `> Selection: "<anchored text>"` block if `inlineProperties.originalSelection` is present
- Footer comments omit the selection block
- Format matches the specification in requirements (H1 title, H2 sections, H3 per comment with `[id] author — date`)
- If the v2 inline-comments endpoint returns 404, fall back to v1 `get_comments()` method

**Files**: `src/docspan/backends/confluence/backend.py`

##### Task 2.2.1a: Add _write_comment_sidecar() and integrate into pull() (~5 min)
- **Before writing code**: Read `src/docspan/backends/confluence/services/confluence/comment_client.py` to confirm:
  1. `ConfluenceCommentClient` constructor signature (likely `ConfluenceCommentClient(config: ConfluenceConfig)`)
  2. Method names: `get_page_inline_comments(page_id)`, `get_page_footer_comments(page_id)`, `get_comments(page_id)` (v1 fallback)
  3. The response format per comment (id, version.createdAt, version.authorId, body.storage.value, inlineProperties.originalSelection)
- Add `self._comment_client: Optional[object] = None` field and `_ensure_comment_client()` lazy-init method to `ConfluenceBackend`:
  ```python
  def _ensure_comment_client(self):
      if self._comment_client is None:
          self._ensure_client()  # ensure _client is initialized first
          from docspan.backends.confluence.services.confluence.comment_client import ConfluenceCommentClient
          cfg = self.config.get("backends", {}).get("confluence", {})
          # Use same ConfluenceConfig as _client
          self._comment_client = ConfluenceCommentClient(self._client._config)  # adjust attribute name per actual client
  ```
- Add private method `_write_comment_sidecar(local_path, page_title, inline_comments, footer_comments)` to `ConfluenceBackend`:
  - Builds markdown string per requirements format
  - Writes to `str(local_path) + '.comments.md'`
- Modify `ConfluenceBackend.pull()` to:
  1. After writing the main markdown file, call `self._ensure_comment_client()`
  2. Try `self._comment_client.get_page_inline_comments(doc_id)` — catch HTTP 404 specifically and fall back to `self._comment_client.get_comments(doc_id)`; catch all other exceptions and warn (do not fail the pull)
  3. Call `self._comment_client.get_page_footer_comments(doc_id)` — catch exceptions and warn
  4. If either list is non-empty, call `_write_comment_sidecar()`
- Files: `src/docspan/backends/confluence/backend.py`

---

### Epic 2.3: Confluence get_remote_version()

**Goal**: `ConfluenceBackend` implements the new abstract method for version tracking.

---

#### Story 2.3.1: Add get_remote_version() to ConfluenceBackend
**As a** sync engine, **I want** `ConfluenceBackend.get_remote_version(doc_id)` to return the current page version number as a string, **so that** the conflict detection algorithm can compare remote state.

**Acceptance Criteria**:
- Returns `str(page['version']['number'])` from `self._client.get_page(doc_id)` response
- Returns a non-empty string for any valid Confluence page ID
- Raises the underlying `ConfluenceApiError` on network/auth failure (does not swallow exceptions)

**Files**: `src/docspan/backends/confluence/backend.py`

##### Task 2.3.1a: Implement get_remote_version() in confluence/backend.py (~2 min)
- Add to `ConfluenceBackend`:
  ```python
  def get_remote_version(self, doc_id: str) -> str:
      self._ensure_client()
      page = self._client.get_page(doc_id)
      return str(page['version']['number'])
  ```
- Files: `src/docspan/backends/confluence/backend.py`

---

## Phase 3: Google Docs Push — Structural Diff

### Epic 3.1: Google Docs client and auth extensions

**Goal**: Extend `GoogleDocsClient` with `get_document()` and `batch_update()` methods; update auth scopes for write access.

---

#### Story 3.1.1: Extend GoogleDocsClient
**As a** push implementation, **I want** `client.get_document(doc_id)` and `client.batch_update(doc_id, requests)`, **so that** the backend can read current doc structure and submit changes.

**Acceptance Criteria**:
- `get_document(doc_id)` calls `docs_service.documents().get(documentId=doc_id).execute()` and returns the raw dict
- `batch_update(doc_id, requests)` calls `docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()` and returns the response
- `batch_update` applies exponential backoff on `HttpError` with status 429 (max 5 retries, max 64s wait with jitter)
- Existing `get_doc_content()` (Drive export) and `export_as_html()` are unchanged

**Files**: `src/docspan/backends/google_docs/client.py`

##### Task 3.1.1a: Add get_document() and batch_update() to GoogleDocsClient (~4 min)
- Read `src/docspan/backends/google_docs/client.py` fully
- Add `_with_backoff(fn, max_retries=5, max_backoff=64)` private method using truncated exponential backoff:
  ```python
  import time, random
  from googleapiclient.errors import HttpError
  def _with_backoff(self, fn, max_retries=5, max_backoff=64):
      for n in range(max_retries):
          try:
              return fn()
          except HttpError as e:
              if e.resp.status == 429:
                  wait = min(2**n + random.random(), max_backoff)
                  time.sleep(wait)
              else:
                  raise
      raise RuntimeError("Max retries exceeded after rate limit backoff")
  ```
- Add `get_document(self, doc_id: str) -> dict` using `_with_backoff`
- Add `batch_update(self, doc_id: str, requests: list) -> dict` using `_with_backoff`
- Files: `src/docspan/backends/google_docs/client.py`

---

#### Story 3.1.2: Update OAuth scopes for push
**As a** user, **I want** `markgate push` for Google Docs to authenticate with write-capable OAuth scopes, **so that** the push doesn't fail with an insufficient-scope error.

**Acceptance Criteria**:
- `auth.py` defines `PUSH_SCOPES` including `https://www.googleapis.com/auth/documents`
- `auth.py` defines `PULL_SCOPES` with `https://www.googleapis.com/auth/documents.readonly`
- `auth_setup()` or the credentials flow uses `PUSH_SCOPES` (read-write) by default
- Existing credentials using `documents.readonly` are documented as insufficient for push

**Files**: `src/docspan/backends/google_docs/auth.py`

##### Task 3.1.2a: Update auth.py with PUSH_SCOPES and PULL_SCOPES constants (~3 min)
- Read `src/docspan/backends/google_docs/auth.py` fully
- Add constants:
  ```python
  PULL_SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]
  PUSH_SCOPES = [
      "https://www.googleapis.com/auth/documents",
      "https://www.googleapis.com/auth/drive.readonly",  # for file export fallback
  ]
  DEFAULT_SCOPES = PUSH_SCOPES  # use read-write by default
  ```
- Update any hardcoded scope string in the auth flow to use `DEFAULT_SCOPES`
- Files: `src/docspan/backends/google_docs/auth.py`

---

### Epic 3.2: Docs structure parser and request builder

**Goal**: Implement `DocsStructureParser` (Docs JSON → paragraph AST) and `DocsRequestBuilder` (diff two ASTs → minimal batchUpdate requests).

---

#### Story 3.2.1: Implement DocsStructureParser
**As a** push pipeline, **I want** `DocsStructureParser.parse(doc_dict)` to return a list of `DocsParagraphNode`, **so that** the current document state can be compared against the target AST.

**Acceptance Criteria**:
- Parses `doc['tabs'][0]['documentTab']['body']['content']` (falls back to `doc['body']['content']` for legacy single-tab docs)
- For each `StructuralElement` with `paragraph`, extracts: `style` (from `namedStyleType`), `text` (concatenated `textRun.content`, trailing `\n` stripped), `is_list_item` (from `bullet` presence), `nesting_level` (from `bullet.nestingLevel`, default 0), `start_index`, `end_index`
- Skips `table`, `sectionBreak`, `tableOfContents` elements silently
- A paragraph with multiple TextRuns is collapsed to a single text string (inline styles tracked in `spans` but diff uses text only)
- Empty paragraphs (single `\n` only) are included with `text=""`

**Files**: `src/docspan/backends/google_docs/docs_structure_parser.py`

##### Task 3.2.1a: Create docs_structure_parser.py (~5 min)
- Create `TextSpan` dataclass: `text: str`, `bold: bool`, `italic: bool`, `link: str | None`, `monospace: bool`
- Create `DocsParagraphNode` dataclass per research/features.md schema
- Create `DocsStructureParser` class with `parse(doc: dict) -> list[DocsParagraphNode]`:
  - Navigate to body content (handle both tabs-based and legacy structure)
  - For each structural element, dispatch on `paragraph` / `table` / other
  - For `paragraph`: extract style from `paragraphStyle.namedStyleType` (default `"NORMAL_TEXT"`), concatenate TextRun contents, strip trailing `\n`, extract bullet info, record `start_index`/`end_index`
- Files: `src/docspan/backends/google_docs/docs_structure_parser.py`

---

#### Story 3.2.2: Implement DocsRequestBuilder
**As a** push pipeline, **I want** `DocsRequestBuilder.build(current_nodes, target_nodes)` to return a list of Google Docs API request dicts, **so that** only changed paragraphs are rewritten.

**Acceptance Criteria**:
- Uses `difflib.SequenceMatcher` on `[(node.style, node.text) for node in nodes]` to produce opcodes
- `equal` opcodes → no text requests; only emit style update requests if `style` differs
- `delete`/`replace`/`insert` opcodes → emit `deleteContentRange` + `insertText` + `updateParagraphStyle` (and `createParagraphBullets` for list items)
- Requests in the returned list are sorted by **descending index** (write-backwards strategy)
- Never emits a `deleteContentRange` covering the document's terminal newline (last element `endIndex - 1`)
- `insertText` uses UTF-16 code unit counting (`len(text.encode('utf-16-le')) // 2`) for offset calculation
- For `updateTextStyle` requests: uses `fields` FieldMask (`"bold"`, `"italic"`, `"link"`, `"weightedFontFamily"`) — never `"*"`

**Files**: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 3.2.2a: Create docs_request_builder.py — core diff logic (~5 min)
- Create `DocsRequestBuilder` class
- Implement `_text_key(node)` → `(style, text)` tuple for SequenceMatcher comparison
- **Index strategy**: Use write-backwards approach. Current doc nodes carry `start_index`/`end_index` from the Docs JSON. The `build()` method processes all requests and sorts them by descending `start_index` at the end. Insert positions for new content are derived from adjacent current-doc nodes:
  - For `insert` after position `i` in current: insert at `current[i].end_index` (end of the preceding paragraph, before its trailing `\n`, i.e., `end_index - 1`)
  - For `replace` at current position `i..j`: delete `current[i].start_index` to `current[j-1].end_index`, then insert at that same start index
  - For pure `insert` with no preceding current node (insert at document start): insert at index `1` (body sentinel offset)
- Implement `build(current: list[DocsParagraphNode], target: list[DocsParagraphNode], doc_end_index: int) -> list[dict]`
  - Accept `doc_end_index` (from the last element's `end_index` in the parsed doc) to protect the terminal newline
  - Use `difflib.SequenceMatcher(None, [_text_key(n) for n in current], [_text_key(n) for n in target]).get_opcodes()`
  - For each opcode, call the appropriate `_make_*_requests()` helper
  - Collect all requests, sort descending by `startIndex` before returning
- Implement `_make_delete_requests(nodes: list[DocsParagraphNode], doc_end_index: int) -> list[dict]`: for each node, emit `deleteContentRange` from `node.start_index` to `node.end_index`; if `node.end_index >= doc_end_index`, cap at `doc_end_index - 1` to protect the terminal newline
- Implement `_make_insert_requests(nodes: list[DocsParagraphNode], insert_at_index: int) -> list[dict]`: emit `insertText` + `updateParagraphStyle` per node; emit `createParagraphBullets` for list items; `insert_at_index` is caller-computed from adjacent current nodes per the index strategy above
- Files: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 3.2.2b: Create docs_request_builder.py — style request helpers (~4 min)
- Add `_make_style_update_requests(current_node, target_node) -> list[dict]` for `equal` opcode with style-only changes: emit `updateParagraphStyle` request
- Add `_utf16_len(text: str) -> int` helper: `return len(text.encode('utf-16-le')) // 2`
- Add `_make_text_style_requests(text: str, style_attrs: dict, range_dict: dict) -> list[dict]` for inline bold/italic/link/monospace: emit `updateTextStyle` with appropriate `fields` mask
- Files: `src/docspan/backends/google_docs/docs_request_builder.py`

---

### Epic 3.3: GoogleDocsBackend push() and get_remote_version()

**Goal**: Wire the parser and request builder into `GoogleDocsBackend.push()` and implement `get_remote_version()`.

---

#### Story 3.3.1: Implement GoogleDocsBackend.push()
**As a** user, **I want** `markgate push notes.md` to write local markdown changes to Google Docs using the structural diff approach, **so that** comments on unchanged text are preserved.

**Acceptance Criteria**:
- `push()` pipeline: read markdown → parse to target AST → `get_document()` → parse to current AST → diff → `batch_update()`
- Returns `PushResult(status="ok", doc_id=doc_id, url=...)` on success
- Returns `PushResult(status="error", ...)` on any exception (with `message=str(e)`)
- If `batch_update` returns an empty `requests` list (no changes), skips the API call and returns `PushResult(status="skipped", ...)`
- Uses write-backwards sorted requests from `DocsRequestBuilder`
- **Documented limitation**: Comment anchor preservation is at paragraph granularity. A comment survives only if its entire host paragraph is unchanged (same style + same text). Any inline edit within a paragraph (e.g., making a word bold) causes the full paragraph to be replaced, which the Docs API will orphan comments on that paragraph. This is an inherent limitation of the paragraph-level diff strategy and is acceptable per requirements (which only require comments on "unchanged text" to survive — paragraph-level is the implementation boundary).

**Files**: `src/docspan/backends/google_docs/backend.py`

##### Task 3.3.1a: Implement push() in GoogleDocsBackend (~5 min)
- Add imports to `backend.py`: `DocsStructureParser`, `DocsRequestBuilder`, markdown parser
- Identify or create a `MarkdownToAstParser` that produces `DocsParagraphNode` list from markdown (reuse existing markdown parser from Confluence side adapted for Docs nodes, or implement a lightweight paragraph-level parser for the initial version)
- Implement `push(self, local_path, doc_id, **kwargs) -> PushResult`:
  ```python
  try:
      content = pathlib.Path(local_path).read_text()
      target_nodes = MarkdownToParagraphParser().parse(content)
      doc = self._client.get_document(doc_id)
      current_nodes = DocsStructureParser().parse(doc)
      builder = DocsRequestBuilder()
      requests = builder.build(current_nodes, target_nodes)
      if not requests:
          return PushResult(status="skipped", doc_id=doc_id, message="No changes detected")
      self._client.batch_update(doc_id, requests)
      url = f"https://docs.google.com/document/d/{doc_id}/edit"
      return PushResult(status="ok", doc_id=doc_id, url=url)
  except Exception as e:
      return PushResult(status="error", doc_id=doc_id, message=str(e))
  ```
- Files: `src/docspan/backends/google_docs/backend.py`

##### Task 3.3.1b: Create markdown_to_paragraph_parser.py — markdown→AST using mistune (~5 min)
- Read `pyproject.toml` to check if `mistune` is already a dependency (likely from the Confluence markdown side)
- If `mistune` is not present, add `"mistune>=3.0"` to `project.dependencies` in Task 4.3.1a (do not use a stdlib re-based fallback — mistune handles multi-line paragraphs, setext headings, and blank lines inside fenced code blocks correctly)
- Create `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py`
- Implement `MarkdownToParagraphParser.parse(content: str) -> list[DocsParagraphNode]`:
  - Use `mistune.create_markdown(renderer=None)` (AST renderer) to parse content into mistune's AST tokens
  - Walk top-level tokens:
    - `heading` (level 1–6) → `DocsParagraphNode(style=f"HEADING_{token['attrs']['level']}", text=extract_text(token), ...)`
    - `paragraph` → `DocsParagraphNode(style="NORMAL_TEXT", text=extract_text(token), ...)`
    - `list_item` inside `list` → `DocsParagraphNode(style="NORMAL_TEXT", is_list_item=True, nesting_level=..., ...)`
    - `block_code` → `DocsParagraphNode(style="NORMAL_TEXT", text=token['raw'], spans=[TextSpan(monospace=True, ...)])`
  - `start_index=0`, `end_index=0` for all target nodes (not meaningful for push targets)
- **Acceptance criteria for this parser** (add to Story 3.3.1 AC):
  - A blank line between paragraphs produces two separate nodes (not one)
  - `## Heading` produces `style="HEADING_2"`
  - `- item` produces `is_list_item=True`
  - A fenced code block produces a single node with `monospace=True` span
- Files: `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py`

---

#### Story 3.3.2: Add get_remote_version() to GoogleDocsBackend
**As a** sync engine, **I want** `GoogleDocsBackend.get_remote_version(doc_id)` to return the `revisionId`, **so that** the conflict detection algorithm can detect remote changes.

**Acceptance Criteria**:
- Returns `doc['revisionId']` from `self._client.get_document(doc_id)`
- Returns a non-empty opaque string
- Does not cache — always fetches fresh from the API

**Files**: `src/docspan/backends/google_docs/backend.py`

##### Task 3.3.2a: Implement get_remote_version() in google_docs/backend.py (~2 min)
- Add to `GoogleDocsBackend`:
  ```python
  def get_remote_version(self, doc_id: str) -> str:
      self._ensure_auth()
      doc = self._client.get_document(doc_id)
      return doc['revisionId']
  ```
- Files: `src/docspan/backends/google_docs/backend.py`

---

## Phase 4: CLI Integration — Sync State and Conflicts Command

### Epic 4.1: Integrate SyncState into push and pull

**Goal**: `markgate push` and `markgate pull` read/write `.markgate-state.json` and apply the conflict detection algorithm.

---

#### Story 4.1.1: Integrate SyncState into CLI push/pull
**As a** user, **I want** push and pull to track sync state automatically, **so that** subsequent operations detect which side has changed.

**Acceptance Criteria**:
- First pull creates `.markgate-state.json` with `base_hash`, `local_hash`, `remote_version`
- First push records state after successful write
- Second pull when only remote changed → fast-forward, no prompt, state updated
- Second pull when only local changed, remote unchanged → skip with `[yellow]warning[/yellow]` message; state not changed
- Pull when both changed → three-way merge written to file; conflict markers if needed; state updated (base reflects merged content)
- State file path is `<config_dir>/.markgate-state.json` (same directory as `markgate.yaml`)
- State file writes are atomic (via `SyncState.save()` which uses `.tmp` + rename)

**Files**: `src/docspan/cli/main.py`, `src/docspan/core/state.py`

##### Task 4.1.1a: Add _load_state() helper and update pull command (~5 min)
- Add `_get_state_path(config_path) -> str` helper: use `os.path.dirname(os.path.abspath(config_path))` if `config_path` is not None; otherwise use `os.getcwd()`. Append `/.markgate-state.json`.
- Add `_get_base_content(state_dir, base_hash) -> str` helper that reads from `<state_dir>/.markgate-base/<base_hash>.base`; returns `""` if file missing (triggers fast-forward fallback, not error)
- Add `_save_base_content(state_dir, content) -> str` helper that:
  1. Calls `os.makedirs(os.path.join(state_dir, '.markgate-base'), exist_ok=True)` — creates directory on first use
  2. Computes `sha256 = sha256_of_content(content)`
  3. Writes content to `<state_dir>/.markgate-base/<sha256>.base` (skip write if file already exists — content-addressed)
  4. Returns `sha256`
- Modify `pull()` command with the correct pre-pull detection order:
  1. Load `SyncState` before iterating mappings
  2. For each mapping, **before any backend call**:
     a. Read current local file content (if it exists) and compute `current_local_hash = sha256_of_content(local_content)` (or `""` if file missing)
     b. Look up `entry = state.get(mapping.local)`
     c. Call `remote_version = backend.get_remote_version(mapping.remote_id)` to check for remote changes
     d. If `entry` is None: proceed to pull (first sync)
     e. `remote_changed = (remote_version != entry.remote_version)`
     f. `local_changed = (current_local_hash != entry.local_hash)` (only meaningful if file exists)
     g. If not remote_changed and not local_changed: print `[dim]up to date[/dim]`, skip
     h. If remote_changed and not local_changed: proceed with fast-forward pull
     i. If local_changed and not remote_changed: warn and skip (do not call backend.pull)
     j. If both changed: fetch remote content, load base from `.markgate-base/`, call `three_way_merge()`, write merged content directly (do not call `backend.pull()` which would overwrite)
  3. After writing new content: compute hashes, save base content, update `SyncState`, call `state.save()`
- Files: `src/docspan/cli/main.py`

##### Task 4.1.1b: Update push command with state tracking (~4 min)
- Modify `push()` command:
  1. Load `SyncState` before iterating
  2. After `backend.push()` returns `status="ok"`:
     - Fetch `remote_version = backend.get_remote_version(mapping.remote_id)` (post-push version)
     - Compute `local_hash = sha256_of_file(mapping.local)` and `base_hash` (content as pushed)
     - Save `.markgate-base/<sha256>.base` with the pushed content
     - Update `SyncState` entry and save
- Files: `src/docspan/cli/main.py`

---

### Epic 4.2: Conflicts subcommand

**Goal**: `markgate conflicts list` and `markgate conflicts resolve <file> --accept remote|local|merged` give the user control over conflict resolution.

---

#### Story 4.2.1: Add conflicts subcommand
**As a** user, **I want** `markgate conflicts list` to show files with unresolved merge markers, **so that** I know what needs manual resolution.

**Acceptance Criteria**:
- `markgate conflicts list` scans all locally-tracked files (from state file) for lines matching `^<<<<<<< `
- Prints a table: file path, number of conflict blocks
- If no conflicts found, prints "No unresolved conflicts."
- `markgate conflicts resolve <file> --accept remote` re-fetches remote and overwrites local, updates state, removes `.orig` if present
- `markgate conflicts resolve <file> --accept local` restores from `.orig` backup (if present) or from base + local diff; updates state
- `markgate conflicts resolve <file> --accept merged` validates no `<<<<<<< ` lines remain, then updates state
- Before writing conflict markers in pull, saves pre-merge local content to `<file>.orig`

**Files**: `src/docspan/cli/main.py`

##### Task 4.2.1a: Add conflicts_app typer group with list and resolve commands (~5 min)
- Add `conflicts_app = typer.Typer(help="Manage merge conflicts.")` and `app.add_typer(conflicts_app, name="conflicts")`
- Add `@conflicts_app.command("list")`: load state, scan each mapped file for `^<<<<<<< `, display Rich table
- Add `@conflicts_app.command("resolve")` with `file: str` argument and `accept: str = typer.Option(...)` with choices `remote|local|merged`
  - `remote`: call `backend.pull()` to re-fetch, overwrite, update state, delete `.orig`
  - `local`: if `<file>.orig` exists, restore it; else read `base_content` from `.markgate-base/` and restore; update state
  - `merged`: check no conflict markers remain, update state (set `local_hash`, `base_hash` to current file content)
- Files: `src/docspan/cli/main.py`

---

### Epic 4.3: Dependency and project configuration

**Goal**: `merge3` is in `pyproject.toml`; `.markgate-state.json` and `.markgate-base/` are in `.gitignore`.

---

#### Story 4.3.1: Update project configuration
**As a** developer, **I want** `merge3` in the project dependencies and state files excluded from git, **so that** the package is installable and state doesn't leak into version control.

**Acceptance Criteria**:
- `pyproject.toml` lists `merge3>=0.0.16` under `[project.dependencies]`
- `.gitignore` (or existing `markgate`-level gitignore) includes `.markgate-state.json` and `.markgate-base/`
- `pip install -e .` completes without errors after the change

**Files**: `pyproject.toml`, `.gitignore`

##### Task 4.3.1a: Add merge3 dependency and gitignore entries (~2 min)
- Read `pyproject.toml`
- Add `"merge3>=0.0.16"` to `project.dependencies` list
- Read `.gitignore` (or create if absent at repo root)
- Add lines: `.markgate-state.json` and `.markgate-base/`
- Files: `pyproject.toml`, `.gitignore`

---

## Cross-Cutting Concerns

### Error Handling
- All backend `push()` and `pull()` methods catch `Exception` and return `PushResult`/`PullResult` with `status="error"`. This is the existing pattern — maintain it.
- `get_remote_version()` does NOT catch exceptions — callers in `cli/main.py` must catch and convert to user-facing messages.
- `SyncState.load()` returns empty state on file-not-found; raises `json.JSONDecodeError` on corrupt JSON (caller logs and continues with empty state).

### Retry Logic
- `batch_update()` in `GoogleDocsClient` uses `_with_backoff()` (exponential, 429-specific, max 5 retries, jitter, max 64s).
- All other API calls use the existing retry pattern in `GoogleDocsClient` (2s linear) unless they hit 429.
- Confluence client has its own retry logic (inherited from `BaseConfluenceClient`) — do not duplicate.

### Atomic State Writes
- `SyncState.save()` writes to `<path>.tmp` then calls `os.rename()` — atomic on POSIX systems.
- `.markgate-base/` files are write-once (content-addressed by sha256) — no race condition risk.
- The `.orig` backup is written before conflict markers — if the CLI crashes between writing `.orig` and writing conflict markers, the user has their pre-merge content intact.

### UTF-16 Index Arithmetic (Google Docs push)
- All index calculations in `DocsRequestBuilder` use `_utf16_len()` not `len(str)`.
- Requests sorted descending by `startIndex` before submission.
- Terminal newline (`doc_body.content[-1].endIndex - 1`) is never included in a `deleteContentRange`.

### `parent_id` on ConfluencePage update
- `parent_id` is required by `ConfluencePage` constructor but the update call doesn't need it for the API.
- Retrieve `parent_id` from `get_page()` response: try `page['parentId']` (v2 API), fallback to `page['ancestors'][-1]['id']` (v1). If neither exists (root page), use `""` as `parent_id`.

---

## Technology Validation

| Choice | Status | Notes |
|---|---|---|
| `merge3>=0.0.16` (PyPI) | VALIDATED | Active project (Oct 2025 release), Python port of Bazaar merge3, MIT license. Preferred over difflib-based manual impl. |
| `difflib.SequenceMatcher` for AST diff | VALIDATED | Stdlib, no dependency. Used for paragraph-level diff only (not three-way merge). |
| `insertText` + `deleteContentRange` | VALIDATED | Core batchUpdate request types per Docs API. Atomic batch semantics confirmed. |
| `updateParagraphStyle` with `namedStyleType` | VALIDATED | `HEADING_1`–`HEADING_6`, `NORMAL_TEXT` confirmed field values. |
| `createParagraphBullets` with `bulletPreset` | VALIDATED | `BULLET_DISC_CIRCLE_SQUARE` / `NUMBERED_DECIMAL_ALPHA_ROMAN` confirmed. |
| Write-backwards index sort | VALIDATED | Official Docs API recommendation for batched edits. |
| UTF-16 code unit counting | VALIDATED | Required for correct index arithmetic with emoji/CJK. |
| `.markgate-state.json` atomic write | VALIDATED | `os.rename()` is atomic on POSIX. |
| `.markgate-base/` content-addressed store | VALIDATED | Cleaner than inline base64; files are write-once (sha256-named). |
| Confluence v2 inline-comments endpoint | VALIDATED (with caveat) | May return 404 in edge cases; plan includes v1 fallback. |

---

## ADR Stubs

### ADR-001: Use merge3 package for three-way merge

**Context**: Three-way merge is needed for conflict detection. Options: `merge3` (PyPI), manual `difflib` implementation, `three-merge` (non-standard markers).

**Decision**: Use `merge3>=0.0.16`.

**Rationale**: Active project (Oct 2025), Git-compatible conflict markers via `merge_lines()`, minimal API surface, MIT license. Manual difflib implementation is ~100 lines of error-prone code.

**Consequences**: Adds one PyPI dependency. If abandoned, the difflib fallback documented in research/stack.md serves as a contingency.

---

### ADR-002: Store base content in .markgate-base/ directory

**Context**: Three-way merge requires the base content (content at last sync), not just a hash. Options: store inline in state JSON (base64), store as sidecar files in `.markgate-base/`.

**Decision**: Use `.markgate-base/<sha256>.base` files.

**Rationale**: State JSON stays human-readable. Large docs don't bloat the JSON. Content-addressed naming means no stale files accumulate for unchanged docs. Multiple local paths that sync to the same content reuse the same base file.

**Consequences**: `.markgate-base/` must be gitignored. On first run, the directory is created automatically. If deleted, the next pull cannot do a three-way merge and falls back to a fast-forward pull with a warning.

---

## Sequencing Summary

```
MUST be done first (blocking):
  Task 1.1.1a  Delete stale files
  Task 1.1.2a  Create state.py
  Task 1.1.3a  Create merge.py
  Task 1.1.4a  Extend base.py
  Task 4.3.1a  Add merge3 to pyproject.toml (needed by merge.py)

Then in parallel:
  Phase 2 (all Confluence tasks)
  Phase 3 (all Google Docs push tasks — 3.1 → 3.2 → 3.3)

Last (depends on everything above):
  Phase 4 CLI tasks (4.1 → 4.2)
```

**Total**: 3 Epics in Phase 1, 3 Epics in Phase 2, 3 Epics in Phase 3, 3 Epics in Phase 4
= **12 Epics | 17 Stories | 21 Tasks**
