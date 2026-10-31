# Plan: pull-reverse-cross-doc-links

## Overview

Add a `ReverseDocLinkResolver` to `cross_doc_links.py` that maps a Google Docs edit URL back to
the repo-relative path of a mapped file (plus readable heading slug). Wire it into `pull()` and
`pull_sectioned()` via a `mappings` kwarg, applied as a post-processing pass over the pulled
markdown string — consistent with the existing `upgrade_heading_id_anchors()` pattern.

## Task Breakdown

### Task 1: `parse_doc_url()` in `cross_doc_links.py`

Add a pure function that parses a Google Docs edit URL into its components:

```python
@dataclass(frozen=True)
class DocUrl:
    doc_id: str
    tab_id: Optional[str]   # None if not present
    heading_id: Optional[str]  # raw heading id e.g. "h.abc123", or None

def parse_doc_url(url: str) -> Optional[DocUrl]:
    """Return DocUrl if url is a docs.google.com/document/d/<id>/edit URL, else None."""
```

The regex must handle all four canonical forms push produces (with/without `?tab=` and
`#heading=`). Non-matching URLs (external, non-Docs) return `None`. Unit-testable in isolation.

### Task 2: `ReverseDocLinkResolver` in `cross_doc_links.py`

```python
class ReverseDocLinkResolver:
    def __init__(
        self,
        mappings: List["Mapping"],
        fetch_headings: FetchHeadings,
        source_local_path: str,
        cache: Optional[Dict[tuple, Optional[TargetHeadings]]] = None,
    ): ...

    def reverse(self, url: str) -> tuple[str, Optional[str]]:
        """Return (rewritten_url_or_path, warning_detail).

        If url is not a reversible Doc URL, returns (url, None) — left verbatim.
        warning_detail is non-None only for AC-7 (ambiguous) events.
        """
```

Logic inside `reverse()`:
1. Call `parse_doc_url(url)` — if `None`, return verbatim.
2. Find matching mappings: `remote_id == doc_id AND (tab_id == None OR tab_id == parsed.tab_id)`.
   - AC-4: no match → return verbatim.
   - AC-7: >1 match → return verbatim + warning.
3. Compute `posixpath.relpath(target.local, posixpath.dirname(source_local_path))`.
4. If `parsed.heading_id`:
   - Fetch target headings via `_headings_for(doc_id, tab_id)` (same caching pattern as
     `CrossDocLinkResolver`).
   - Look up slug via `heading_id_to_slug` dict.
   - AC-13: id not found → emit `logger.warning(...)`, return path without fragment.
   - Found → append `#slug` to path.
5. Return `(relative_path, None)`.

### Task 3: `reverse_cross_doc_links()` post-processing function

```python
_DOC_URL_IN_LINK_RE = re.compile(
    r'\]\((https://docs\.google\.com/document/d/[^)"\s]+)\)'
)

def reverse_cross_doc_links(
    markdown: str,
    resolver: ReverseDocLinkResolver,
) -> tuple[str, list[str]]:
    """Rewrite Doc edit URLs back to repo-relative paths.

    Returns (rewritten_markdown, list_of_warning_details).
    """
```

For each `](URL)` match:
- Call `resolver.reverse(url)` → `(replacement, warning)`
- If `replacement != url`: substitute in markdown
- Collect warnings

This is a pure string transformation — no I/O. Works on any markdown string.

### Task 4: Wire `mappings` kwarg into `pull()` (structural path)

`backend.py` structural pull path (lines 1415–1488):

After `markdown_content = render_nodes_to_markdown(nodes)` and image recovery, add:

```python
mappings = kwargs.get("mappings")
if mappings:
    reverse_resolver = cross_doc_links.ReverseDocLinkResolver(
        mappings, self._fetch_target_headings, local_path
    )
    markdown_content, reverse_warnings = cross_doc_links.reverse_cross_doc_links(
        markdown_content, reverse_resolver
    )
    # append reverse_warnings into the messages list
```

### Task 5: Wire `mappings` kwarg into `pull()` (Drive-export path)

`backend.py` default pull path (lines 1490–1570):

After `markdown_content = upgrade_heading_id_anchors(...)` and image recovery, add the same
`reverse_cross_doc_links()` call before writing to disk. Apply *after* `upgrade_heading_id_anchors`
so same-doc anchor upgrading runs first (no ordering dependency, but keeps the two passes
semantically distinct).

### Task 6: Wire `mappings` kwarg into `pull_sectioned()`

`pull_sectioned()` produces section markdown files. After rendering each section's nodes to
markdown, apply `reverse_cross_doc_links()`. The resolver can be constructed once and shared
across all sections (same source dir) — avoids duplicate `(doc_id, tab_id)` fetches across
sections (AC-12 extended to pull_sectioned).

### Task 7: Unit tests for `parse_doc_url` and `ReverseDocLinkResolver`

Add to `tests/test_cross_doc_links.py`:

- `TestParseDocUrl`: all four URL forms, non-Docs URL returns None, malformed returns None.
- `TestReverseDocLinkResolver`:
  - URL → path, no fragment (AC-1)
  - URL + tab → path (AC-2)
  - URL + heading_id → path + slug (AC-3)
  - Unmapped doc_id → verbatim (AC-4)
  - Non-Doc URL → verbatim (AC-5)
  - Correct relative path (AC-6)
  - Ambiguous mapping → verbatim + warning (AC-7)
  - Unknown heading_id → path only (AC-13)

### Task 8: Integration tests through `pull()`

Add to `tests/test_google_docs_backend.py`:

- `test_pull_rewrites_cross_doc_url_to_relative_path` — structural path
- `test_pull_rewrites_cross_doc_url_with_tab_and_heading` — structural path with tab+heading
- `test_pull_default_path_rewrites_cross_doc_url` — Drive-export path
- `test_pull_without_mappings_kwarg_leaves_url_verbatim` — backward-compat guard

## Adversarial Review

**Risk 1: `posixpath.relpath` on absolute local paths**  
`mapping.local` may be an absolute path (e.g. `/home/user/project/docs/other.md`). `posixpath.relpath`
handles absolute paths correctly — tested in `conftest.py` fixtures where `tmp_path` paths are
absolute. No issue.

**Risk 2: Tab-matching ambiguity**  
A URL without `?tab=` should only match mappings with `tab_id=None`. A URL with `?tab=t.xxx`
should match mappings with `tab_id="t.xxx"` exactly. If two mappings have the same `remote_id`
but different `tab_id`, they are unambiguous — the tab discriminates them. The matcher must be:
`doc_id matches AND tab_id matches exactly (None == None, "t.xxx" == "t.xxx")`.

**Risk 3: Drive-export path ordering**  
`upgrade_heading_id_anchors()` rewrites same-doc `#h.xxx` fragments. `reverse_cross_doc_links()`
rewrites cross-doc `docs.google.com` URLs. The two patterns are mutually exclusive (different
regex patterns), so ordering doesn't affect correctness. Apply cross-doc reversal last for clarity.

**Risk 4: `_fetch_target_headings` requires an authenticated client**  
`_fetch_target_headings()` calls `get_document()` internally. In unit tests, this must be mocked.
The existing `test_cross_doc_links.py` pattern — passing a `fetch_headings_factory` lambda — 
is the correct approach for `ReverseDocLinkResolver` tests too.

**Risk 5: pull_sectioned source_local_path**  
`pull_sectioned` writes to section files like `NN-slug.md` under `local_dir`. The `source_local_path`
for relative path computation should be the individual section file's path, not the directory —
otherwise `posixpath.relpath` produces paths relative to a directory, not a file's directory
(which are the same). Confirmed: `posixpath.dirname("/dir/file.md") == "/dir"` and
`posixpath.dirname("/dir/") == "/dir"` (trailing slash stripped). Use `section_file_path`, not
`local_dir`, when constructing the resolver per-section.

**Risk 6: Backward compatibility**  
When `mappings` kwarg is absent (None or not passed), `pull()` and `pull_sectioned()` must behave
identically to today — no reverse pass, no API calls. Guard with `if mappings:` before building
the resolver.
