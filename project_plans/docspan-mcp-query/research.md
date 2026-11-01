# Research: docspan-mcp-query

**Date:** 2026-10-31
**Branch:** tstapler/triage-9fd5d925-dd06-454e-833d-875cedf59869

---

## 1. Codebase Audit

### 1.1 Backend ABC — `src/docspan/backends/base.py`

`Backend` is an abstract base class with these abstract methods:
- `push(local_path, doc_id, **kwargs) -> PushResult`
- `pull(doc_id, local_path, **kwargs) -> PullResult`
- `create(title, **kwargs) -> CreateResult`
- `auth_setup(config_path) -> None`
- `get_remote_version(doc_id) -> str` — returns an opaque version token (revisionId for Google Docs, page version number for Confluence)
- `validate_config() -> None`

Two concrete-but-optional methods exist for backends that support them:
- `push_sectioned / pull_sectioned` — currently Google Docs only
- `create_tab` — currently Google Docs only

**Relevance to MCP tools:** `get_remote_version` (returns `doc["revisionId"]`) is exactly the revision token needed for `diff_since` and incremental FTS index updates. Every `PullResult` carries a `status` field (`ok`, `conflict`, `error`, `skipped`, `warning`) but no revision ID — the revision must be fetched separately via `get_remote_version` or read from the raw doc before a pull.

### 1.2 Google Docs Backend — `src/docspan/backends/google_docs/backend.py`

Key findings:

**Auth initialization** (lines 115–144): `_ensure_client()` is called lazily on the first API call. It supports three auth paths: explicit service-account credentials path, env-var dual-account, or per-user OAuth. The client (`GoogleDocsClient`) is instantiated once and reused for the lifetime of the backend instance — this is the right model for a long-running MCP server.

**pull() method** (line 1364): Two code paths:
1. Default (`tab_id=None`, `pull_strategy="auto"`): Drive HTML export via `files.export` — lossy, cannot target a specific tab.
2. Structural (`tab_id` set, or `pull_strategy="structural"`): `get_document` + `resolve_document_tab` + `DocsStructureParser.parse` + `render_nodes_to_markdown`. This is the tab-aware, higher-fidelity path.

For `fetch_doc` and `outline`, the structural path is required regardless of whether `tab_id` is set, because it returns per-paragraph metadata (heading style, heading_id) that the Drive HTML export discards.

**get_remote_version()** (line 1899): Makes a `get_document` call and returns `str(doc["revisionId"])`. For the MCP use-case this is wasteful — the same `get_document` call needed for outline/pull already includes `revisionId` in the response body. A `fetch_doc` implementation should read `revisionId` from the doc dict rather than making a second API call.

**`_write_comment_sidecar`** (called at line 1450 inside `pull()`): writes `{local_path}.comments.md` containing the Drive comment list. The MCP `list_comments` tool can read this sidecar file rather than hitting the API, when the doc has been recently pulled.

### 1.3 GoogleDocsClient — `src/docspan/backends/google_docs/client.py`

**`get_document`** (line 119): Always calls with `includeTabsContent=True` by default — the response includes all tabs' content under `document.tabs[*].documentTab`. The `revisionId` field is top-level in the response dict.

**`_with_backoff`** (line 106): Exponential backoff on HTTP 429 with up to 5 retries, capped at 64 seconds. Already sufficient for query tools; crawl operations that issue multiple sequential `get_document` calls will still need inter-call spacing (see Risk section).

**`list_comments`** (line 267): Issues one `drive.comments().list()` call returning `comments(id,content,quotedFileContent,resolved,author(displayName))`. Currently excludes resolved comments via post-filter. The MCP `list_comments` tool should expose a `resolved=True/False/None` parameter.

### 1.4 Tabs — `src/docspan/backends/google_docs/tabs.py`

**`resolve_document_tab(doc, tab_id)`** (line 97): Returns `(resolved_doc, resolved_tab_id, warning)`. When `tab_id=None` and the doc has more than one tab, `warning` is set (not an error) — the returned `resolved_doc` exposes the first tab's content. This function is the correct entry point for per-tab pulls in `fetch_doc`.

**`list_tabs(doc)`** (line 87): Returns `[TabInfo(tab_id, title), ...]` for all tabs. This is exactly what `outline` needs to enumerate tab names before parsing per-tab content.

**`flatten_tabs(tabs)`** (line 31): Recursively flattens nested `childTabs` — required for any doc with a tab hierarchy.

**`heading_ids_by_tab(doc)`** (line 42): Returns `{headingId: tabId}` across all tabs — used by `cross_doc_links.py` for cross-tab anchor resolution. `outline` can use this to associate a heading's `heading_id` with the tab it lives in, enabling stable per-section URLs.

### 1.5 Comments — `src/docspan/backends/google_docs/comments.py`

`format_comments_markdown(title, comments)` (line 50) produces the `.comments.md` sidecar. Structure: grouped into Open / Resolved sections, with `<!-- id:{comment_id} -->` markers and `quotedFileContent` for anchor context.

`bucket_comments_by_section(comments, sections)` (line 150) maps each comment to the section whose rendered text contains the comment's `quotedFileContent.value`. This is reusable for the MCP `list_comments` tool to return comments grouped by heading-path.

### 1.6 Cross-Doc Links — `src/docspan/backends/google_docs/cross_doc_links.py`

`parse_cross_doc_href(href)` (line 61): Identifies relative cross-doc hrefs. For the MCP `resolve_links` tool, Google Docs outbound links are absolute URLs (`https://docs.google.com/document/d/{doc_id}/edit?...`), not relative paths — so this function is not the right entry point. Instead, link extraction needs to walk the `DocsParagraphNode.spans` list for nodes with `url` attributes, or walk the raw document JSON for `link.url` elements in `textRun.textStyle`.

`CrossDocLinkResolver` (line 157): Caches per-(doc_id, tab_id) heading lookups — useful if `resolve_links` expands to resolving fragment anchors in linked docs.

### 1.7 Section Splitter — `src/docspan/backends/google_docs/section_splitter.py`

`split_nodes(nodes, split_level, existing_entries)` (line 62): Takes a projected node list and splits at every node where `node.style == split_level`. Returns `[Section(heading_id, title, nodes, slug)]` in document order, preamble first.

The `Section` dataclass (line 36) provides `heading_id` (the Docs-assigned ID, stable across renames), `title` (raw heading text), `slug` (filename-safe, deduplicated), and `nodes`. The `heading_id` is exactly what's needed for a stable section anchor in a URL (`#heading-id` or mapped to a slug).

For the `outline` tool: calling `split_nodes` at the finest heading level present (e.g., `HEADING_2`) and then reading `Section.title` gives a flat heading list. For a true heading tree, `DocsStructureParser` already emits nodes with `style` in `{HEADING_1..HEADING_6, NORMAL_TEXT, TITLE}`, so tree construction is a pass over the node list grouping by style rank — no new parser logic needed.

### 1.8 XDG Paths — `src/docspan/core/xdg.py`

Existing XDG layout:

| Purpose | Path |
|---------|------|
| Config (tokens, central config) | `$XDG_CONFIG_HOME/docspan` → `~/.config/docspan` |
| State (sync state, content-addressed base) | `$XDG_STATE_HOME/docspan` → `~/.local/state/docspan` |
| Cache (regenerable: mermaid PNGs) | `$XDG_CACHE_HOME/docspan` → `~/.cache/docspan` |

**For the FTS index:** `cache_home()` (`~/.cache/docspan/`) is the correct location — the index is a regenerable artifact built from pulled docs, not authoritative state. Path: `~/.cache/docspan/fts5.db` or `~/.cache/docspan/{prefix}/fts5.db` if per-project isolation is wanted.

**For the `fetch_doc` scratch dir:** Two choices:
1. `.scratch/` at repo root — already gitignored (`.gitignore:75`), used by other docspan tooling for local debug artifacts.
2. `~/.cache/docspan/{prefix}/scratch/` — user-global, survives across cwd changes, avoids polluting repos checked out on shared machines.

The scratch-directory convention from `memory/MEMORY.md` says `.scratch/` is gitignored and used for local debug/snapshot artifacts. For a CLI-driven `fetch_doc`, `.scratch/` is reasonable for single-project use; for the MCP server (which may be launched from an arbitrary cwd), `cache_home() / prefix / "scratch"` is safer.

### 1.9 Config Model — `src/docspan/config.py`

`MarkgateConfig` (line 201) holds:
- `backends: BackendsConfig` — with `google_docs: GoogleDocsConfig` and `confluence: ConfluenceConfig`
- `mappings: list[Mapping]` — each `Mapping` has `local`, `backend`, `remote_id`, `direction`, `tab_id`, `pull_strategy`, `sectioned`, `split_level`, `pageless`

`Mapping.remote_id` is the doc ID — a `None` means "not yet created." The `list_mappings` MCP tool should filter these out or flag them separately.

The central config at `~/.config/docspan/config.yaml` holds a `projects` registry (`{prefix: {markgate: "/path/to/docspan.yaml"}}`). The MCP server will need to locate the active config the same way the CLI does: via `resolve_active_project` (line 303), which checks explicit `--config`, `DOCSPAN_PREFIX` env var, cwd-inside-project heuristic, and `default_prefix`.

### 1.10 CLI Structure — `src/docspan/cli/main.py`

Top-level Typer app `app` with sub-typers: `auth_app`, `conflicts_app`, `config_app`, `comments_app`. The `docspan mcp`, `docspan index`, and `docspan crawl` commands will be added as either top-level commands on `app` or as a new `mcp_app = typer.Typer(...)` sub-typer.

The `_resolve(config_path, prefix)` helper (line 195) is the right internal API for resolving the active markgate config in new commands.

---

## 2. MCP Protocol Facts

The Model Context Protocol uses a stdio transport: a long-running process reads JSON-RPC 2.0 requests from stdin and writes responses to stdout. Each tool call arrives as:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "outline", "arguments": {"remote_id": "1abc..."}}}
```

And the server responds with:

```json
{"jsonrpc": "2.0", "id": 1,
 "result": {"content": [{"type": "text", "text": "..."}]}}
```

Tool discovery is via `tools/list`. The server advertises each tool's name, description, and input JSON Schema.

**Official `mcp` Python SDK** (PyPI: `mcp`, latest 2.3.0): Provides `FastMCP`, `Server`, and stdio transport wrappers. The `FastMCP` class supports decorator-based tool registration and handles the JSON-RPC framing. The package has no `cli` extra in 2.3.0 (the extras list is empty per PyPI metadata). Key dependency: `anyio` for async I/O — already compatible with Python 3.9+.

**Dependency decision:** Use the official `mcp` SDK. Implementing the JSON-RPC framing manually saves one dependency but requires maintenance of a non-trivial protocol surface. The SDK is MIT-licensed, published on PyPI, and the project already uses the public PyPI index exclusively (enforced in `pyproject.toml` line 118). Add as an optional `mcp` extra: `mcp = ["mcp>=1.0.0"]` — this way users who only need CLI push/pull don't pull in anyio.

---

## 3. SQLite FTS5

**Availability:** FTS5 is compiled into CPython's `sqlite3` stdlib module on all major platforms (macOS, Linux, Windows) since Python 3.6+. Verified on the development machine:

```python
import sqlite3
conn = sqlite3.connect(':memory:')
conn.execute('CREATE VIRTUAL TABLE t USING fts5(x)')
# → succeeds; no import errors
```

No additional dependency needed.

**Proposed schema:**

```sql
CREATE TABLE doc_meta (
    doc_id      TEXT NOT NULL,
    tab_id      TEXT NOT NULL DEFAULT '',   -- '' for single-tab docs
    revision_id TEXT NOT NULL,              -- doc["revisionId"] at index time
    title       TEXT,
    pulled_at   REAL,                       -- Unix timestamp
    PRIMARY KEY (doc_id, tab_id)
);

CREATE VIRTUAL TABLE doc_sections USING fts5(
    doc_id UNINDEXED,
    tab_id UNINDEXED,
    heading_path,                           -- e.g. "Tab Name > H2 Title > H3 Title"
    content,                                -- section body text
    heading_id UNINDEXED,                   -- Docs heading_id for stable anchor
    tokenize = 'porter unicode61'
);
```

**Incremental update logic:** Before re-indexing a doc, check `doc_meta.revision_id` against the current `doc["revisionId"]`. If equal, skip. If different, delete all rows in `doc_sections` for `(doc_id, tab_id)`, re-parse, re-insert, and update `doc_meta.revision_id`.

**FTS5 snippet:** `snippet(doc_sections, 2, '<b>', '</b>', '...', 16)` returns highlighted 16-token context windows from the `content` column — directly usable for `search_docs` results.

---

## 4. Heading Outline Extraction

### Existing machinery

`DocsStructureParser.parse(doc)` emits a flat list of typed nodes. Each `DocsParagraphNode` carries:
- `style: ParagraphStyle` — `HEADING_1`..`HEADING_6`, `NORMAL_TEXT`, `TITLE`, etc.
- `heading_id: Optional[str]` — Docs-assigned stable ID (only set on heading paragraphs)
- `text: str` — rendered heading text (spans concatenated)

`is_heading_style(style)` (imported in `section_splitter.py` from `heading_anchors.py`) is the existing predicate.

`split_nodes(nodes, split_level)` groups nodes by heading — but only at one level at a time. A full outline needs a multi-level walk.

### What needs to be built

`outline` requires a **heading tree**, not a flat split. The implementation is a single pass over `parser.parse(doc)` filtering for `HEADING_1`..`HEADING_6` nodes:

```python
headings = [n for n in nodes if is_heading_style(n.style)]
```

Each entry in the tree: `(heading_id, level, text, char_count_of_section)`. Character counts require knowing where the next same-or-higher-level heading begins — one additional pass.

**Reuse potential:** `DocsStructureParser` + `resolve_document_tab` are already the correct primitives. No new parser is needed; the outline is a post-processing step over the parsed node list.

**Per-tab outline for multi-tab docs:** Call `list_tabs(doc)` to get tab names, then for each tab call `resolve_document_tab(doc, tab_id)` and parse. The raw `whole_doc` (before narrowing) must be captured first — this matches the existing pattern in `_build_push_plan` (line 368, `whole_doc=whole_doc` passed through).

---

## 5. Scratch Directory

The `.scratch/` directory at repo root is gitignored (`.gitignore:75`) and is the established convention for local debug/snapshot artifacts in this repo (per `memory/MEMORY.md`).

The `atomic_replace_dir` helper (`src/docspan/core/atomic_dir.py`) provides a safe swap for the underlying pull path — already used by `backend.pull_sectioned`. For `fetch_doc`, a simpler per-doc subdirectory under scratch is sufficient:

```
.scratch/fetch/{doc_id}/{tab_id or 'default'}/{slug}.md
```

However, when the MCP server is launched from an arbitrary cwd (e.g., a global `~/.config/claude/` or `/tmp`), `.scratch/` relative to cwd is wrong. The safer default:

```
~/.cache/docspan/{prefix}/scratch/{doc_id}/{tab_id}.md
```

falling back to `.scratch/` when no prefix is configured. This mirrors how other XDG paths resolve — `cache_home() / prefix / "scratch"`.

The existing `core/xdg.py::cache_home()` function returns `Path("~/.cache/docspan").expanduser()` and is the right base. Add a `scratch_dir_for_prefix(prefix)` function alongside `state_dir_for_prefix`.

---

## 6. Risk Areas

### 6.1 Google Docs API Rate Limits

The Google Docs API enforces **100 read requests per 100 seconds per user** and a separate per-project limit. Each `get_document` call consumes one quota unit; `drive.comments().list()` consumes one Drive API quota unit (separate quota, but also rate-limited).

**Crawl risk:** `docspan crawl --depth 2` on a doc with N outbound links issues at minimum N+1 `get_document` calls in the first level. At depth 2, this grows to O(N²) in the worst case. The existing `_with_backoff` (max 5 retries, truncated exp backoff to 64 s) handles transient 429s but does not add inter-call spacing. A crawl across even 20 docs is likely to trigger sustained rate limiting.

**Mitigation required:**
- Add a configurable inter-call delay to the crawl traversal (default: 0.5s between `get_document` calls).
- Implement a per-run request counter and surface remaining-quota estimates.
- Crawl should be breadth-first with a shared request budget, not purely depth-first.

**Index risk:** `docspan index` iterating over all mappings issues one `get_document` + one `list_comments` call per mapping. For a user with 50+ mappings, this is a 100+ API call burst. Mitigation: use `get_remote_version` (one `get_document` call) to check if the revision changed before issuing the full structural parse — since `get_remote_version` and a structural parse both call `get_document`, consider caching the response in memory within a single `index` run.

### 6.2 Auth Token Lifecycle in a Long-Running Process

The MCP server runs as a persistent process (launched once by the MCP client and kept alive). The OAuth token (stored at `~/.config/docspan/{prefix}/google_token.json`) has a 1-hour expiry. `google-auth-oauthlib` handles refresh automatically when the token is expired, but this requires:

1. A valid refresh token (present when the user runs `docspan auth setup` interactively).
2. Network access at refresh time.
3. Write access to the token path (to persist the refreshed token).

The existing `OAuthAuthenticator.get_credentials()` path in `backend.py` lines 131–136 calls `oauth.get_credentials()` which triggers refresh. Because the MCP server's backend instances are created lazily (on first tool call), the first tool call after >1 hour of idle will trigger a refresh attempt. This should work transparently with `google-auth`, but:

- A refresh failure in a long-running process surfaces as an exception on the tool call, not at server startup. The MCP server must catch `google.auth.exceptions.RefreshError` and return a structured error result (not a crash) so the MCP client can surface it to the user.
- Service-account credentials do not expire via the same mechanism — they issue short-lived access tokens from a private key. These are also refreshed by `google-auth` automatically and are lower risk.

**Recommendation:** Wrap all tool implementations in a `try/except` that catches `google.auth.exceptions.TransportError` and `google.auth.exceptions.RefreshError` and returns an MCP error result with a user-readable message directing them to run `docspan auth setup`.

### 6.3 Tab-Awareness Gaps in the Current Pull Path

The existing `pull()` method (line 1364) has two code paths:
1. Drive HTML export (`pull_strategy="auto"`, no `tab_id`): tab-unaware — always returns the first/default tab, with a warning surfaced when multiple tabs exist.
2. Structural (`pull_strategy="structural"` or `tab_id` set): tab-aware.

For `fetch_doc` and `outline`, the structural path is always required. This means:
- Every `fetch_doc` call issues a `get_document` with `includeTabsContent=True` and then calls `resolve_document_tab` once per tab of interest.
- For a 5-tab doc, a full outline requires 5 structural parses (one per tab), each reusing the same `whole_doc` dict — 1 API call total, 5 parsing passes. This is already how `heading_ids_by_tab` works (one doc, multiple parse passes).

**Gap:** `pull_sectioned` in the Google Docs backend (line 1582) does support per-tab pulls via `tab_id`, but `pull()` in "auto" mode does not. The `fetch_doc` tool should bypass `pull()` entirely and call the structural path directly (via `GoogleDocsClient.get_document` + `resolve_document_tab` + `DocsStructureParser`), rather than wrapping the high-level `pull()` method which carries Drive-export legacy.

**Existing warning signal:** `resolve_document_tab` sets `warning` when a multi-tab doc is pulled without an explicit `tab_id`. The MCP server should surface this warning in the tool response so agents know they may be missing tabs.

---

## 7. Summary of Key Findings

| Area | Finding | File:Line |
|------|---------|-----------|
| Revision token | `doc["revisionId"]` returned by `get_document`, also by `get_remote_version` | `backend.py:1899`, `client.py:119` |
| Auth lazy-init | `_ensure_client()` on first call — safe for long-running server | `backend.py:115` |
| Tab listing | `list_tabs(doc)` → `[TabInfo(tab_id, title)]` | `tabs.py:87` |
| Tab resolution | `resolve_document_tab(doc, tab_id)` | `tabs.py:97` |
| Heading nodes | `DocsParagraphNode.style` + `heading_id` from `DocsStructureParser.parse` | `docs_structure_parser.py:1` |
| Section split | `split_nodes(nodes, split_level)` → `[Section]` | `section_splitter.py:62` |
| Comment sidecar | `format_comments_markdown` + `bucket_comments_by_section` | `comments.py:50,150` |
| XDG cache | `cache_home()` → `~/.cache/docspan/` | `xdg.py:48` |
| FTS5 | Available in stdlib `sqlite3` (verified) | — |
| Rate limiting | `_with_backoff` (5 retries, 64s cap, 429-only) | `client.py:106` |
| .scratch/ | Gitignored at repo root; per-MEMORY.md convention for local artifacts | `.gitignore:75` |
| MCP SDK | `mcp` 2.3.0 on PyPI, no `cli` extra, MIT license | pypi.org/pypi/mcp |
| Config resolution | `resolve_active_project(prefix, config_path)` | `config.py:303` |
| Cross-doc links | Outbound Google Doc URLs are absolute, not relative — `parse_cross_doc_href` will reject them; need raw JSON walk instead | `cross_doc_links.py:61` |
