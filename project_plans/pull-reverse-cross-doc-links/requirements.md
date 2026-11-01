# Requirements: pull-reverse-cross-doc-links

## Problem Statement

When push resolves a repo-relative markdown link like `[see](OTHER.md#anchor)` to a Google Docs
tab URL (`https://docs.google.com/document/d/<id>/edit?tab=t.xxxx#heading=h.yyyy`), the resulting
Doc URL is written into the Google Doc. On pull, that URL is rendered back into markdown verbatim,
so every round-trip produces a noisy diff: the author's `OTHER.md#anchor` becomes the absolute
Doc URL even though the file is locally mapped.

## Goals

On pull, reverse-map any link whose URL is a known Google Docs document URL back to the
repo-relative path of the mapped file — including reversing `#heading=h.xxx` fragments back to
the readable markdown slug.

## Non-Goals

- Confluence backend (separate module; not in scope here)
- Links to unmapped Doc URLs (external Google Docs not in `docspan.yaml`) must be left verbatim
- Changing push behaviour (it is already correct per issue #64)

## Acceptance Criteria

1. **AC-1 (URL → path, no fragment):** A pulled link whose URL is
   `https://docs.google.com/document/d/<id>/edit` (no tab, no heading) is rewritten to the
   repo-relative path of the mapped local file, relative to the file being pulled.
2. **AC-2 (URL → path + tab):** A URL with `?tab=t.xxx` resolves to the mapped file that has
   `remote_id=<id>` and `tab_id=t.xxx`; rendered as `other.md` (tab is transparent in markdown).
3. **AC-3 (URL → path + heading slug):** A URL with `#heading=h.xxx` has its fragment reversed
   to the readable markdown slug by looking up the heading id in the target document's structure;
   rendered as `other.md#some-heading`.
4. **AC-4 (unmapped Doc URL left verbatim):** A Doc URL whose `<id>` matches no mapping is left
   exactly as the original URL string.
5. **AC-5 (non-Doc URL left verbatim):** Any URL that is not a `docs.google.com/document/d/...`
   URL is left exactly as-is.
6. **AC-6 (relative path is correct):** The produced relative path is correct relative to the
   file being pulled — `posixpath.relpath(target.local, source_dir)`.
7. **AC-7 (ambiguous mapping degrades gracefully):** If two mappings share the same `remote_id`
   (and same `tab_id`), the URL is left verbatim and a warning is appended to the `PullResult`.
8. **AC-8 (structural pull path applies rewriting):** The tab-scoped / `pull_strategy=structural`
   path in `pull()` applies the reverse-mapping before writing to disk.
9. **AC-9 (default Drive-export pull path applies rewriting):** The default `pull()` path
   (Drive HTML export → `html_to_markdown`) also applies the reverse-mapping.
10. **AC-10 (pull_sectioned applies rewriting):** `pull_sectioned()` applies the reverse-mapping
    to each section file produced.
11. **AC-11 (mappings kwarg accepted by pull/pull_sectioned):** `pull()` and `pull_sectioned()`
    accept a `mappings` kwarg (list of `Mapping`) mirroring the push-side convention; when absent,
    no reverse-mapping is attempted and existing behaviour is unchanged.
12. **AC-12 (target heading fetch cached):** The heading lookup for a given `(doc_id, tab_id)` is
    cached across all links in a single pull run (no duplicate API calls per target doc).
13. **AC-13 (heading id unknown → path only):** If `#heading=h.xxx` is present but the heading id
    is not found in the target's structure (e.g. heading was deleted), the link is written as the
    repo-relative path without a fragment (not left as the Doc URL), and the event is logged at
    WARNING level.
14. **AC-14 (tests):** Unit tests cover AC-1 through AC-7 for the core resolver; integration
    tests through `GoogleDocsBackend.pull()` cover AC-8 and AC-9.
