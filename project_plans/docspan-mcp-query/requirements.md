# Requirements: docspan-mcp-query

**Project name:** `docspan-mcp-query`  
**Item:** Add an MCP server and query tooling for outline, section reads, search, and doc-graph crawl  
**Date:** 2026-10-31

---

## Problem Statement

When an agent needs to synthesize information from a cluster of related Google Docs (decision doc, handoff doc, research doc, weekly-notes tabs), the only available path today is:

1. Search a separate knowledge-base tool for doc identifiers
2. Pull each doc's full text into context via `docspan pull`
3. Re-read targeted sections by scanning the already-loaded text

Each pull is a whole-document dump. Multi-tab docs make it worse: tab boundaries are lost and a single pull returns every tab merged together, making targeted extraction impractical without loading all content. There is no way for an agent to ask docspan a targeted question without pulling everything into context.

---

## Proposed Solution

Add a **stdio MCP server** (`docspan mcp`) that exposes read-first query tools over docspan's existing backends and mapping config, plus supporting CLI commands (`docspan index`, `docspan crawl`) and a local SQLite FTS5 index.

---

## Functional Requirements

### FR-1: MCP Server — `docspan mcp` command

Start a stdio-protocol MCP server. Compatible with the MCP specification (tool-call/result JSON over stdin/stdout). No HTTP transport in v1.

### FR-2: `list_mappings` tool

Return all mapped docs: local path, remote ID, backend, sync direction, last-synced timestamp. Source: `docspan.yaml`/`markgate.yaml` config files, same mapping objects used by pull/push.

### FR-3: `fetch_doc(remote_id_or_url, tab?, to_path?)` tool

Pull one doc (or one tab of a multi-tab doc) to markdown in a scratch directory. Return: the local file path and a heading outline (not the body text). Never dump body into the tool response by default.

### FR-4: `outline(remote_id_or_url)` tool

Return the document's heading tree with: tab names, heading levels and text, per-section character counts, and last-modified timestamp. Lets the agent choose which section to read before fetching body content.

### FR-5: `read_section(remote_id_or_url, heading_path, max_chars?)` tool

Return the text of a single section identified by heading path (`Tab > H2 > H3`). Respect `max_chars` budget (default 4096). Requires the doc to have been pulled to scratch first (or triggers a pull automatically).

### FR-6: `search_docs(query, scope?)` tool

Keyword and semantic search over pulled docs in the local FTS5 index. Return: heading path, snippet, and a stable section URL (with tab and heading fragment). Scope: all mapped docs, or a named subset.

### FR-7: `list_comments(remote_id_or_url, resolved?)` tool

Return comment threads with anchors and resolution status. Sources from the existing `.comments.md` sidecar mechanism in the google_docs backend.

### FR-8: `resolve_links(remote_id_or_url)` tool

Return outbound doc links (with tab fragments) found in the document, so an agent can walk a doc graph one hop at a time without reading the full document body.

### FR-9: `diff_since(remote_id_or_url, rev)` tool

Return what changed since a given revision identifier. Useful for "what's new since the last design review" queries.

### FR-10: Local index — `docspan index` command

Build and update a SQLite FTS5 index over pulled markdown, keyed by `(doc, tab, heading_path)`. Incremental on revision ID. Storage location: XDG cache dir (same pattern as existing `core/xdg.py`). Embeddings are out of scope for v1.

### FR-11: Doc-graph crawl — `docspan crawl <url> --depth N` command

Follow Google Doc outbound links, pull each linked doc into a scratch directory, and record doc-graph edges. Allows "read these five referenced docs" in one call.

### FR-12: Tab-aware pull

When pulling a multi-tab doc, produce one markdown file per tab (docspan already has a sectioned mode). Expose tab names in `outline` and `fetch_doc` responses.

### FR-13: Token-budget flags

`--max-chars` and `--outline-only` flags on `pull` and `read_section`, so the default behavior is context-efficient.

### FR-14: Citation output

Every tool result includes a stable URL with tab fragment and heading anchor, so downstream content can link to the exact section.

---

## Non-Functional Requirements

### NFR-1: No write tools in v1

`push`, `reply_comment`, and any mutation tool are explicitly out of scope for v1. The MCP server is read-only. Write tools sit behind an explicit opt-in flag reserved for v2.

### NFR-2: Attribution for agent-authored comments

Any future comment-reply tool must carry agent attribution so readers know it is agent-generated, not the user's own words.

### NFR-3: Rate-limit safety

Crawl and batch outline operations must respect Google Docs API quotas. Implement exponential back-off and surface quota errors clearly rather than silently dropping docs.

### NFR-4: Auth reuse

The MCP server must reuse the existing auth flow (`auth_setup`) and credentials store. No new auth mechanism.

### NFR-5: Python version compatibility

All new code must remain compatible with Python 3.9+ (same as existing codebase).

### NFR-6: Dependency discipline

New dependencies must be justified and minimal. SQLite FTS5 is part of the Python standard library. Any MCP SDK dependency must be evaluated against the project's public-index constraint.

---

## Acceptance Criteria

1. An agent can call `outline(doc_id)` and receive a heading tree with character counts and tab names for a multi-tab Google Doc without receiving the document body.
2. An agent can call `read_section(doc_id, "Tab Name > H2 Heading")` and receive only that section's text, not the full document.
3. An agent can call `search_docs("decision rationale")` and receive matching heading paths and snippets with stable section URLs.
4. An agent can call `resolve_links(doc_id)` and receive the list of Google Doc URLs referenced in the document, with tab fragments where available.
5. `docspan index` builds a SQLite FTS5 index over all pulled docs and updates incrementally on subsequent runs.
6. `docspan crawl <url> --depth 2` pulls the target doc and all docs it links to (up to depth 2) into a scratch directory.
7. All MCP tool responses include a `url` field with a stable tab+heading fragment link.
8. The MCP server starts via `docspan mcp` and communicates over stdio using the MCP protocol.
9. No existing `docspan pull` or `docspan push` behavior changes.
10. Auth reuses existing credentials; no new auth flow is needed to use the MCP server.

---

## Open Questions

1. **Index storage location:** Per-project (alongside `docspan.yaml`) vs. user XDG cache dir (`~/.cache/docspan/`)? User cache avoids polluting repos but requires a stable project identifier.
2. **Semantic search scope:** Is FTS5 plus heading-path boosting sufficient for v1, or is embedding-based search needed? Embeddings add a large dependency; defer to v2.
3. **Confluence parity:** Implement all tools for Confluence backend simultaneously, or ship Google Docs first and add Confluence in a follow-on?
4. **Crawl quota behavior:** What is the right behavior when a crawl hits API rate limits mid-traversal — fail-fast, save-and-resume, or best-effort with a partial result?
5. **MCP SDK choice:** Use the official `mcp` Python SDK or implement the protocol directly? The SDK adds a dependency; direct implementation avoids it but requires maintenance.
