# Validation: docspan-mcp-query

**Date:** 2026-10-31  
**Reviewer role:** adversarial

---

## 1. Feasibility

### PASS — Core read path APIs

`get_document(include_tabs_content=True)`, `resolve_document_tab`, `list_tabs`, `DocsStructureParser.parse()`, and `render_nodes_to_markdown()` all exist with the exact signatures the plan references. `get_remote_version(doc_id)` exists on `backends/base.py` and is implemented by the Google Docs backend. These are not assumptions — they are verified against the source.

### PASS — XDG extension

`xdg.py` already has `state_dir_for_prefix` and `cache_home()`. The proposed `scratch_dir_for_prefix` follows the same pattern. Non-breaking, additive, correct.

### FAIL — `diff_since` is infeasible as designed

The plan says `diff_since` calls `get_remote_version`, and if the revision differs, "fetches doc, diffs pulled markdown against the version stored in scratch." The scratch copy is the _current_ last-pulled version, not the version at `rev`. There is no Google Docs API endpoint to fetch a document at an arbitrary historical `revisionId`. The `revisionId` field exposed by `documents.get` exists only for write-control (`requiredRevisionId` in `batchUpdate`) — there is no `documents.get(revisionId=...)` endpoint. Confirmed: `GoogleDocsClient` has no revisions API call. The only feasible implementation is "compare current live version against last-pulled scratch copy", which is a different and weaker semantics than "diff since a given rev". The `rev` input parameter in `DiffResult` would be a lie — the tool can only report "changed since last pull."

**Action:** Either redesign `diff_since` to compare live vs. scratch (rename the parameter to `since_last_pull` and drop `base_rev` from `DiffResult`), or cut FR-9 from v1.

### FAIL — `split_nodes` is misrepresented for `read_section`

The plan says `read_section` locates a section by `heading_path` "using `split_nodes`." The actual `split_nodes(nodes, split_level, ...)` API splits a node list at every heading of a _single given level_ (e.g. all `HEADING_2`s). It takes `split_level: str` ("HEADING_2"), not a path. It does not resolve a compound heading path like `"Tab Name > H2 Title > H3 Sub"` to a node range. There is no existing utility that does this. `read_section` will require new path-resolution logic — a traversal of heading nodes across levels to find the start node and walk until the next same-or-higher heading. This is a non-trivial implementation gap the plan glosses over by citing a reuse that doesn't exist.

**Action:** Add a new function `find_section_by_path(nodes, heading_path) -> (start_idx, end_idx)` as an explicit deliverable in Phase 4 (Task 8). It is not a reuse — it is new code.

### WARN — `list_comments(resolved?)` has an API coverage gap

`GoogleDocsClient.list_comments` always filters out resolved comments (hardcoded: `return [c for c in comments if not c.get("resolved")]`). The plan's tool accepts `resolved=True` to surface resolved threads, and says it falls back to the API. The API fallback will silently return empty results when `resolved=True` and no sidecar exists. A user who calls `list_comments(doc_id, resolved=True)` on a doc that has never been pulled (no sidecar) will get `[]` with no warning that resolved comments are API-inaccessible.

**Action:** When `resolved=True` and the sidecar is absent, return a `warning` field: `"Resolved comments require a pulled sidecar — run 'docspan pull' first."` Do not silently return `[]`.

---

## 2. Scope Creep Risks

### HIGH — `crawl` blast radius

`--depth 2` with a well-connected hub doc (common in engineering orgs) can easily reach 500+ unique docs before hitting the `--max-docs 100` default. 100 API calls with 0.5 s delay is 50 seconds of wall time — already borderline. The bigger risk: "follow Google Doc outbound links" will eagerly follow links to shared org-wide docs (style guides, templates, HR handbooks) that are unrelated to the agent's intent. There is no scoping filter: no domain-restriction, no mapping-aware exclusion (only pull docs that are mapped in `docspan.yaml`), no opt-in list. An agent calling `crawl` on a design doc could trigger 100 unrelated pulls.

**Action:** Add a `--scope` option: `all` (current behavior), `mapped` (only pull docs whose `doc_id` appears in any project mapping), or an explicit `--allow-domain` list. Default to `mapped` to prevent runaway crawls.

### MEDIUM — `search_docs` `scope` parameter is underspecified

FR-6 says scope can be "all mapped docs, or a named subset." "Named subset" is not defined anywhere — there is no namespace, tag, or grouping concept in the mapping config. As written, implementing `scope` beyond `all` would require adding a grouping concept to the config schema. That is a separate feature.

**Action:** Limit `scope` in v1 to an explicit `list[doc_id]` filter (caller provides IDs) rather than a named group. Remove the "named subset" language from FR-6 or defer it explicitly to v2.

---

## 3. Backwards Compatibility

All three flagged files are safe:

- `core/xdg.py`: additive function. No existing call sites change.
- `cli/main.py`: `mcp_app` is a new sub-typer mounted via `add_typer`. Existing `push`, `pull`, `sync`, `migrate-sectioned` commands are untouched. Confirmed by reading the current command surface.
- `backends/base.py`: plan adds no changes to the abstract base; `get_remote_version` already exists.

One edge case: `docspan mcp` as a top-level command must not shadow any existing command. The current CLI has no `mcp` command — safe. If a future version adds it, the `invoke_without_command=True` pattern on `mcp_app.callback` would need revisiting.

---

## 4. Acceptance Criteria Gaps

| AC | Status | Issue |
|----|--------|-------|
| AC-1 (outline without body) | Testable | None. |
| AC-2 (read_section) | Partially testable | "only that section's text" is ambiguous: does the section include sub-headings? Add criterion: "text includes all content under that heading until the next same-or-higher-level heading." |
| AC-3 (search_docs) | Testable | None. |
| AC-4 (resolve_links) | Testable | None. |
| AC-5 (docspan index) | Testable | Missing: "subsequent runs skip unchanged docs" (incremental behavior). Add this as a separate verifiable criterion. |
| AC-6 (crawl --depth 2) | Partially testable | "all docs it links to" — does AC-6 include docs linked from the depth-1 docs (depth-2 traversal), or only direct links? The phrasing covers both depth-1 and depth-2 but doesn't explicitly confirm 2-hop traversal. Clarify. |
| AC-7 (url field) | Untestable as stated | "Stable tab+heading fragment link" can't be verified without knowing the exact Google Docs URL fragment format for headings. Add a concrete expected form: `https://docs.google.com/document/d/{doc_id}/edit?tab={tab_id}#{heading_id}`. |
| AC-8 (MCP server starts) | Testable | None. |
| AC-9 (no pull/push regression) | Testable | Requires existing test suite to still pass. Confirm CI runs existing tests after any change. |
| AC-10 (auth reuse) | Testable | Missing: behavior when auth is expired at server start. Add criterion: "if credentials are expired, `docspan mcp` exits with a clear error before accepting any tool call." |

**Missing AC — auth expiry mid-session:** No acceptance criterion covers auth refresh failure during a long-running server session (the most likely production failure mode). Add: "If a Google auth token refresh fails during a tool call, the tool returns a structured error with message 'Auth expired — run docspan auth setup' without crashing the server."

**Missing AC — duplicate headings:** The adversarial review in the plan addresses this but there is no AC for it. Add: "If `read_section` is called with a heading_path that matches two headings in the same tab, it returns an error listing both matching heading IDs."

---

## 5. Dependency Risk

### `mcp>=1.0.0,<3` is too broad

PyPI currently has 60+ releases of `mcp` across `1.0.0` to `2.3.0`. The `FastMCP` class the plan relies on was introduced in `1.1.0` (not `1.0.0`). A user who installs exactly `mcp==1.0.0` will get an `ImportError` for `FastMCP`. The lower bound should be `>=1.1.0`.

The larger risk is `<3`: with 30 releases in the 1.x series alone, the SDK has undergone significant API changes. The `mcp.run(transport="stdio")` call signature changed between versions. The plan references `mcp 2.3.0` as the reference implementation but doesn't pin a tested floor within the 2.x series. Between 1.x and 2.x the tool registration pattern changed.

**Recommendation:** Narrow to `>=2.0.0,<3` to exclude the volatile 1.x series entirely, document the tested version (2.3.0) in a comment, and add a CI job that tests against both `mcp==2.0.0` and `mcp==2.3.0` to catch API drift.

### Protocol version vs SDK version

The plan does not address the MCP protocol version that Claude Code currently speaks. The `mcp` Python SDK declares its supported protocol version in the handshake. If a user installs an older SDK (e.g. `mcp==1.15.0`) that speaks an older protocol version, Claude Code may silently ignore the server or fail tool calls without a useful error. This is a real user-facing footgun.

**Action:** Document in the user-facing `docspan mcp` help text: "Requires `mcp>=2.0.0`. Install with `pip install docspan[mcp]`." Add a runtime version check at server startup that prints a warning if the installed SDK version is below the tested floor.

---

## 6. Testing Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| Auth expiry mid-session | HIGH | `test_mcp_auth.py` covers the decorator behavior, but no test exercises the scenario: server starts, N calls succeed, call N+1 raises `RefreshError` during `get_document`. This is the most likely production failure path. |
| `stale=True` flag absent from `SearchHit` model | HIGH | The adversarial review (§5) says each hit includes `stale=true` when `pulled_at` is older than 24 hours. The `SearchHit` Pydantic model in §3 has no `stale: bool` field. This is a plan/model inconsistency that will surface as a bug at implementation time. Add `stale: bool = False` to `SearchHit`. |
| Duplicate headings in `read_section` | MEDIUM | The adversarial review addresses this but no test file covers it. The error path (return matching `heading_id` values) is specified but has no test. |
| Crawl depth limits | MEDIUM | No test for: `--depth 0` (pull target only, no traversal), `--max-docs` ceiling hit mid-traversal (partial result), or a cycle in the doc graph (A links B, B links A). Visited set prevents infinite loops, but the partial-result summary behavior under the `--max-docs` limit is untested. |
| Empty index | LOW | The adversarial review says an empty index returns a `warning` field. `SearchHit` has no `warning` field; a top-level warning would need to be returned alongside the (empty) hits list. The response shape isn't defined. |
| Tab-aware `fetch_doc` per-tab isolation | LOW | No test confirms that `fetch_doc` called without a `tab?` on a 3-tab doc produces a warning AND writes only the first tab's content, not all tabs merged. |

---

## 7. Go/No-Go Recommendation

**Proceed with modifications.** The plan is well-researched and the core architecture is sound. Two issues are blockers that must be resolved before implementation starts:

**Blocker 1 — `diff_since` infeasible:** The Google Docs API cannot return document content at an arbitrary historical `revisionId`. Either redesign `diff_since` to compare current vs. last-pulled scratch (and rename parameters to reflect that) or cut FR-9 from v1. If kept, the `rev` input parameter becomes "rev at last pull" (opaque string passed back from a prior `outline` call), not a user-supplied revision identifier.

**Blocker 2 — `split_nodes` reuse is incorrect:** `read_section` needs new section-by-path resolution logic. This is a real implementation task, not a reuse. Add it explicitly as a deliverable in T8.

**Pre-implementation fixes (not blockers, but address before writing code):**

1. Narrow `mcp` lower bound to `>=2.0.0,<3`; add `stale: bool = False` to `SearchHit`.
2. Add `--scope mapped` as default to `crawl` to prevent runaway link traversal.
3. Add the missing acceptance criteria: mid-session auth expiry, duplicate heading error, AC-5 incremental behavior, AC-7 concrete URL format.
4. Document the `resolved=True` + no-sidecar gap in `list_comments` with an explicit warning.
