# Plan: docspan-mcp-query

**Date:** 2026-10-31  
**Branch:** `tstapler/triage-9fd5d925-dd06-454e-833d-875cedf59869`

---

## 1. Architecture

### 1.1 Module Layout

```
src/docspan/
├── core/
│   └── xdg.py                    # add scratch_dir_for_prefix(prefix) → Path
├── mcp/
│   ├── __init__.py
│   ├── server.py                 # FastMCP entry point; registers all tools
│   ├── models.py                 # Pydantic input/output models shared across tools
│   ├── auth.py                   # auth-error wrapper; catches RefreshError
│   ├── index.py                  # SQLite FTS5 index: open, ingest, search, incremental update
│   ├── outline.py                # heading tree extraction from parsed node list
│   └── tools/
│       ├── __init__.py
│       ├── mappings.py           # list_mappings
│       ├── fetch.py              # fetch_doc
│       ├── outline_tool.py       # outline
│       ├── section.py            # read_section
│       ├── search.py             # search_docs
│       ├── comments.py           # list_comments
│       ├── links.py              # resolve_links
│       └── diff.py               # diff_since
```

New CLI commands are added to `src/docspan/cli/main.py` (or a new `mcp_app` sub-typer mounted there):

```python
mcp_app = typer.Typer(name="mcp", help="MCP server and index commands")
app.add_typer(mcp_app)

@mcp_app.command("serve")    # invoked as: docspan mcp serve
@app.command("index")        # invoked as: docspan index
@app.command("crawl")        # invoked as: docspan crawl <url>
```

The command `docspan mcp` (no sub-command) also starts the server directly for
ergonomics — `mcp_app.callback(invoke_without_command=True)`.

### 1.2 New Dependency

`pyproject.toml` optional extras:

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0.0,<3"]
```

Install with `pip install docspan[mcp]`. The `mcp` package (PyPI `mcp` 2.3.0, MIT
license) brings `anyio` as its main transitive dependency; both are Python 3.9+
compatible and on the public PyPI index (satisfying NFR-6).

### 1.3 XDG Change

Add to `src/docspan/core/xdg.py`:

```python
def scratch_dir_for_prefix(prefix: str) -> Path:
    """Scratch dir for MCP fetch_doc artifacts: ~/.cache/docspan/{prefix}/scratch/"""
    return cache_home() / prefix / "scratch"
```

This mirrors the existing `state_dir_for_prefix` pattern. Falls back to
`.scratch/` relative to cwd when `prefix` is `None` (matching the per-MEMORY.md
convention for local project use).

### 1.4 SQLite Schema (`mcp/index.py`)

```sql
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_id      TEXT NOT NULL,
    tab_id      TEXT NOT NULL DEFAULT '',
    revision_id TEXT NOT NULL,
    title       TEXT,
    pulled_at   REAL,
    PRIMARY KEY (doc_id, tab_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_sections USING fts5(
    doc_id      UNINDEXED,
    tab_id      UNINDEXED,
    heading_path,          -- e.g. "Tab Name > H2 Title > H3 Title"
    content,               -- section body text
    heading_id  UNINDEXED, -- Docs heading_id for stable URL fragment
    tokenize = 'porter unicode61'
);
```

Database location: `scratch_dir_for_prefix(prefix).parent / "fts5.db"` →
`~/.cache/docspan/{prefix}/fts5.db`.

---

## 2. Implementation Phases

### Phase 1 — Core read primitives (no MCP dependency)

Files: `mcp/outline.py`, `mcp/models.py`, change to `core/xdg.py`

- `outline.py::build_heading_tree(nodes, tabs)` — one pass over `DocsParagraphNode`
  list filtering `is_heading_style(node.style)`; groups by tab; accumulates
  `char_count` by summing text lengths until the next same-or-higher heading.
  Returns `list[HeadingNode]` (Pydantic model, see §3).
- `models.py` — all Pydantic I/O models: `HeadingNode`, `OutlineResult`,
  `SectionResult`, `SearchHit`, `CommentThread`, `DocLink`, `DiffResult`,
  `MappingEntry`, `FetchResult`.
- `core/xdg.py` — add `scratch_dir_for_prefix`.

No new external dependency; tests can run without `mcp` installed.

### Phase 2 — SQLite FTS5 index

Files: `mcp/index.py`, CLI command `docspan index`

- `DocspanIndex` class: `open(db_path)`, `is_stale(doc_id, tab_id, revision_id)`,
  `ingest(doc_id, tab_id, revision_id, title, sections)`, `search(query, limit)`.
- Incremental update: `is_stale` checks `doc_meta.revision_id`; if matching,
  `ingest` is skipped for that `(doc_id, tab_id)` pair.
- `docspan index` CLI command: resolves active project config, iterates all
  `Mapping` entries with non-`None` `remote_id`, calls `get_document` once per
  doc (reusing the response for both `revisionId` check and structural parse),
  calls `ingest` if stale.
- Rate guard: 0.5 s inter-call `time.sleep` between `get_document` calls.

### Phase 3 — MCP server scaffolding

Files: `mcp/server.py`, `mcp/auth.py`, CLI command `docspan mcp`

- `server.py`: instantiate `FastMCP("docspan")`, import all tool modules, call
  each module's `register(mcp_instance, backend_factory)` function.
- `auth.py`: `with_auth_retry(fn)` decorator; catches
  `google.auth.exceptions.RefreshError` and `TransportError`; returns an MCP
  tool error result with message `"Auth expired — run 'docspan auth setup'"`.
- `docspan mcp` CLI: resolves config, creates backend instances, starts the
  server with `mcp.run(transport="stdio")`.

### Phase 4 — Tool implementations

One module per tool group; each module exposes `register(mcp, backend_factory)`.
Implementation order (by dependency on previous phases):

1. `tools/mappings.py` — `list_mappings`: reads `MarkgateConfig.mappings`,
   returns `list[MappingEntry]`. No API calls; no auth needed.
2. `tools/outline_tool.py` — `outline`: calls `get_document`, `list_tabs`,
   then per-tab `resolve_document_tab` + `DocsStructureParser.parse` +
   `build_heading_tree`. Returns `OutlineResult`.
3. `tools/fetch.py` — `fetch_doc`: calls structural pull path directly
   (`get_document` → `resolve_document_tab` → parse → `render_nodes_to_markdown`
   → write to `scratch_dir_for_prefix`). Returns `FetchResult` (path + outline;
   no body text in response).
4. `tools/section.py` — `read_section`: reads the pulled markdown from scratch
   (triggering `fetch_doc` if absent), locates the section by `heading_path`
   using `split_nodes`, returns at most `max_chars` of text. Returns
   `SectionResult`.
5. `tools/search.py` — `search_docs`: queries `doc_sections` FTS5 table using
   `snippet(doc_sections, 2, '<b>', '</b>', '...', 16)`. Returns
   `list[SearchHit]`.
6. `tools/comments.py` — `list_comments`: reads `.comments.md` sidecar if
   present (prefers cached); falls back to `GoogleDocsClient.list_comments`.
   Uses `bucket_comments_by_section` to attach heading-path. Returns
   `list[CommentThread]`.
7. `tools/links.py` — `resolve_links`: walks raw `document.tabs[*].documentTab
   .body.content[*].paragraph.elements[*].textRun.textStyle.link.url`; filters
   for `docs.google.com` URLs; extracts `doc_id` and `tab` fragment. Returns
   `list[DocLink]`.
8. `tools/diff.py` — `diff_since`: calls `get_remote_version`, compares to
   supplied `rev`; if changed, fetches doc, diffs pulled markdown against the
   version stored in scratch. Returns `DiffResult` (changed headings + char delta
   per section).

### Phase 5 — `docspan crawl` command

File: `src/docspan/cli/crawl.py` (registered in `main.py`)

- `docspan crawl <url> --depth N --delay FLOAT`
- BFS traversal: dequeue a URL, call `resolve_links`, enqueue linked doc IDs not
  yet visited.
- Per doc: call `fetch_doc` (reusing backend instance), record edge
  `(source_doc_id, target_doc_id)` in `~/.cache/docspan/{prefix}/graph.json`.
- Rate guard: `--delay` (default 0.5 s) between API calls; respects `_with_backoff`
  on 429.
- On partial failure: log which docs failed, continue traversal, surface a summary
  at the end (best-effort, not fail-fast).

---

## 3. Data Models (`mcp/models.py`)

```python
from pydantic import BaseModel
from typing import Optional

class HeadingNode(BaseModel):
    level: int                  # 1–6
    text: str
    heading_id: Optional[str]   # Docs-assigned stable ID; None for title-only docs
    tab_id: str
    tab_name: str
    char_count: int             # characters in this section's body
    url: str                    # canonical URL with tab+heading fragment

class OutlineResult(BaseModel):
    doc_id: str
    title: str
    revision_id: str
    headings: list[HeadingNode]
    tabs: list[str]             # tab names in order

class FetchResult(BaseModel):
    doc_id: str
    tab_id: str
    local_path: str
    outline: OutlineResult
    warning: Optional[str]      # from resolve_document_tab when tab ambiguous

class SectionResult(BaseModel):
    doc_id: str
    heading_path: str
    text: str                   # up to max_chars
    truncated: bool
    url: str

class SearchHit(BaseModel):
    doc_id: str
    tab_id: str
    heading_path: str
    snippet: str                # FTS5 highlighted snippet
    url: str

class CommentThread(BaseModel):
    comment_id: str
    content: str
    author: str
    resolved: bool
    quoted_text: Optional[str]
    heading_path: Optional[str]
    url: str

class DocLink(BaseModel):
    target_url: str
    target_doc_id: str
    target_tab: Optional[str]
    anchor_text: str

class DiffResult(BaseModel):
    doc_id: str
    base_rev: str
    current_rev: str
    changed_sections: list[str]  # heading_paths of changed sections
    unchanged: bool

class MappingEntry(BaseModel):
    local: str
    remote_id: str
    backend: str
    direction: str
    last_synced: Optional[str]
    tab_id: Optional[str]
```

All models derive from `pydantic.BaseModel`; JSON-serializable by default
(required for MCP tool responses). Pydantic is already a docspan dependency.

---

## 4. Testing Strategy

### Patterns to follow

Existing tests use fixtures in `tests/fixtures/` (raw Google Docs API JSON
responses, markdown files). Each backend test creates a `FakeGoogleDocsClient`
or patches `GoogleDocsClient` at the test boundary. The `conftest.py` provides
`markgate_config` and `tmp_path` fixtures.

### New test files

| Test file | Coverage |
|-----------|----------|
| `tests/test_mcp_outline.py` | `build_heading_tree` with multi-tab fixture; char counts; single-tab doc |
| `tests/test_mcp_index.py` | `DocspanIndex.ingest`, `search`, `is_stale`; incremental skip when revision unchanged |
| `tests/test_mcp_list_mappings.py` | `list_mappings` with config fixture; filters `None` remote_id entries |
| `tests/test_mcp_fetch.py` | `fetch_doc` writes to scratch dir; returns outline not body; warns on multi-tab without tab_id |
| `tests/test_mcp_read_section.py` | `read_section` by heading_path; `max_chars` truncation; `truncated=True` flag |
| `tests/test_mcp_search.py` | `search_docs` returns ranked hits with FTS5 snippets |
| `tests/test_mcp_comments.py` | `list_comments` reads sidecar; falls back to API; `resolved` filter |
| `tests/test_mcp_links.py` | `resolve_links` extracts absolute `docs.google.com` URLs from raw paragraph JSON; ignores non-Doc URLs |
| `tests/test_mcp_diff.py` | `diff_since` with matching rev (unchanged=True) and changed rev |
| `tests/test_mcp_auth.py` | `with_auth_retry` catches `RefreshError` and returns structured error |

### Fixture strategy

- Reuse existing `tests/fixtures/` Google Docs API JSON where possible.
- Add `tests/fixtures/mcp/multi_tab_doc.json` — a minimal API response with 3
  tabs, headings at multiple levels, and outbound `docs.google.com` links in
  `textRun.textStyle.link.url`.
- `DocspanIndex` tests use `:memory:` SQLite (pass `db_path=":memory:"`).
- Tool tests mock `GoogleDocsClient.get_document` at the module boundary using
  `unittest.mock.patch`; no live API calls in CI.

---

## 5. Adversarial Review

**"The MCP server is a long-running process; what happens when auth expires?"**

OAuth tokens expire after 1 hour. `google-auth` refreshes automatically on the
next API call if a valid refresh token exists. The refresh can fail (no network,
revoked grant). The `with_auth_retry` wrapper in `mcp/auth.py` catches
`google.auth.exceptions.RefreshError` and `TransportError` and returns a
structured MCP error result — the server does not crash. The client sees a tool
error with a clear message (`"Auth expired — run 'docspan auth setup'"`). Service
accounts are lower risk (auto-refreshed from private key, no refresh-token
revocation path).

**"What if a doc has 50 linked docs — does crawl handle that gracefully?"**

`docspan crawl --depth 2` with a hub doc is the worst case: up to 50 × 50 = 2500
API calls at depth 2. The implementation uses BFS with a visited set (no
revisits), a configurable `--delay` (default 0.5 s), and the existing
`_with_backoff` for 429 responses. The traversal is breadth-first so it hits all
depth-1 docs before expanding depth-2. On sustained rate limiting (quota
exhausted), `_with_backoff` will exhaust its 5 retries and raise; `crawl` catches
this per-doc, logs it, and continues. The result summary reports which docs were
skipped. A `--max-docs N` flag (default 100) provides a hard circuit breaker.

**"How does `read_section` handle a heading that appears multiple times?"**

`split_nodes` in `section_splitter.py` uses `heading_id` (Docs-assigned, unique
per paragraph) rather than heading text to identify sections. The `heading_path`
parameter to `read_section` is matched against the `(tab_name > heading_text)`
path; if ambiguous (two headings with identical text in the same tab), the tool
returns an error listing all matching `heading_id` values and asks the caller to
qualify with one. The error payload includes the stable `url` for each match so
the caller can distinguish them.

**"What happens if the FTS index is stale and the user hasn't re-indexed?"**

`search_docs` queries whatever is in the index and includes a `pulled_at`
timestamp in each `SearchHit`. If the index is empty, it returns zero results
with a `warning` field: `"Index is empty — run 'docspan index' to build it"`. If
`pulled_at` is older than 24 hours, each hit includes a `stale=true` flag (not a
failure). This is the right degraded-mode behavior: stale results are better than
no results, and the staleness is surfaced rather than hidden.

**"Is the `mcp` package stable enough to depend on?"**

The `mcp` package (Anthropic-maintained, 2.3.0) is the reference implementation
of the Model Context Protocol SDK — MIT-licensed, on PyPI, and the SDK Claude
Code itself recommends for building MCP servers. API churn between minor versions
is the primary risk; the version pin `>=1.0.0,<3` accepts compatible releases
through the next major. Declaring `mcp` as an optional extra means users who only
need CLI push/pull are unaffected. If the SDK becomes a maintenance liability, the
JSON-RPC framing is simple enough to implement directly (confirmed in research),
but reaching for it now would cost meaningful implementation and test effort.

---

## 6. Task Breakdown

| # | Task | Files touched | Estimate |
|---|------|---------------|----------|
| T1 | Add `scratch_dir_for_prefix` to `core/xdg.py`; add `mcp` extra to `pyproject.toml` | `xdg.py`, `pyproject.toml` | XS |
| T2 | `mcp/models.py` — all Pydantic I/O models; `mcp/__init__.py` | `mcp/models.py` | S |
| T3 | `mcp/outline.py` — `build_heading_tree`; unit tests | `mcp/outline.py`, `tests/test_mcp_outline.py` | S |
| T4 | `mcp/index.py` — `DocspanIndex` (open, ingest, search, is_stale); unit tests | `mcp/index.py`, `tests/test_mcp_index.py` | M |
| T5 | `docspan index` CLI command; rate guard; fixture `multi_tab_doc.json` | `cli/main.py`, `cli/index_cmd.py`, `tests/fixtures/mcp/` | M |
| T6 | `mcp/auth.py` — `with_auth_retry`; `mcp/server.py` — FastMCP scaffold; `docspan mcp` CLI | `mcp/auth.py`, `mcp/server.py`, `cli/main.py` | S |
| T7 | `tools/mappings.py` (`list_mappings`) + `tools/outline_tool.py` (`outline`) + `tools/fetch.py` (`fetch_doc`) + tests | 3 tool files + 3 test files | M |
| T8 | `tools/section.py` (`read_section`) — scratch read, `split_nodes` reuse, `max_chars` truncation + tests | `tools/section.py`, `tests/test_mcp_read_section.py` | S |
| T9 | `tools/search.py` (`search_docs`) — FTS5 query, snippet, stale warning + tests | `tools/search.py`, `tests/test_mcp_search.py` | S |
| T10 | `tools/comments.py` (`list_comments`) — sidecar read + API fallback + `bucket_comments_by_section` + tests | `tools/comments.py`, `tests/test_mcp_comments.py` | S |
| T11 | `tools/links.py` (`resolve_links`) — raw JSON walk for absolute URLs + tests; `tools/diff.py` (`diff_since`) + tests | 2 tool files + 2 test files | M |
| T12 | `docspan crawl` CLI — BFS traversal, visited set, `--max-docs`, delay, graph.json, partial-failure summary | `cli/crawl.py`, `cli/main.py` | M |

**Estimate key:** XS < 1h output-token budget; S = 1–2h; M = 2–4h.

Total: 2 × XS + 7 × S + 4 × M ≈ 24–34 agent-hours of output.  
Parallelizable: T3–T4 (no shared state); T7–T11 tool pairs can be assigned to
parallel agents once T6 scaffolding is in place.

---

## 7. Open Questions Resolved

| Question | Decision |
|----------|----------|
| Index storage | `~/.cache/docspan/{prefix}/fts5.db` — user XDG cache, not repo dir |
| Semantic search | FTS5 only in v1; embeddings deferred to v2 |
| Confluence parity | Google Docs first; Confluence backend added in a follow-on (tools are backend-agnostic at the model layer) |
| Crawl quota | Best-effort with `--max-docs` circuit breaker and per-doc error recovery; not fail-fast |
| MCP SDK | Use `mcp>=1.0.0,<3` as optional extra; do not implement JSON-RPC framing manually |

---

## 8. Implementation deviations (post-validate)

- SDK: `mcp` 2.x renamed `FastMCP` to `MCPServer` (`mcp.server.mcpserver`); extra pinned `mcp>=2.0.0,<3` for Python >= 3.10 only.
- `diff_since(doc_id)` diffs live vs. the snapshot `fetch_doc` writes (no `rev` input); `read_section` uses new `sections.find_section_by_path`, not `split_nodes`.
- Layout is flatter than §1.1: logic in `mcp/service.py` (SDK-free), thin `mcp/server.py`; CLI in `cli/mcp_cmds.py`.
- `list_comments` uses `client.get_comments` (includes resolved), so the resolved-comments gap in validate §1 does not apply.
- `crawl --scope` defaults to `all` (matches the issue); `mapped` is opt-in; `--max-docs` defaults to 25.
