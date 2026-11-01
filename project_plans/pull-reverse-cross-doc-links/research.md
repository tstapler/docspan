# Research: pull-reverse-cross-doc-links

## Existing Push-Side Machinery (source of truth)

`cross_doc_links.py` resolves push-side links. Key exports:

- `parse_cross_doc_href(href)` → `CrossDocHref | None` — identifies relative-path hrefs as
  cross-doc candidates.
- `resolve_local_mapping(source_local_path, href_path, mappings)` → `Mapping | None` — maps a
  relative path to a `Mapping` entry.
- `CrossDocLinkResolver.resolve(source_local_path, href)` → `CrossDocResolution` — full pipeline
  producing a Doc URL.
- `CrossDocResolution.url` — the final `https://docs.google.com/document/d/<id>/edit[?tab=t.xxx][#heading=h.yyy]`.

This is exactly what pull needs to invert.

## Existing Pull-Side Machinery

### Structural path (`pull_strategy="structural"` or `tab_id is not None`)

`backend.py:1395–1488`:
1. `get_document(doc_id)` → `resolve_document_tab(doc, tab_id)` → `DocsStructureParser().parse(doc)`
2. `project(nodes)` → `render_nodes_to_markdown(nodes)`
3. Links in `nodes_to_markdown.py:_render_spans()` (line 122) are written verbatim:
   `text = f"[{text}]({span.link})"` — `span.link` comes from the Docs API's `textRun.textStyle.link.url`.

### Default path (Drive HTML export)

`backend.py:1490–1570`:
1. `get_doc_content(doc_id)` → `DocumentConverter().html_to_markdown(html_content)`
2. `upgrade_heading_id_anchors(markdown, heading_id_to_slug(structural_nodes))` (line 1508) — already rewrites same-doc `#h.xxx` opaque fragments to readable slugs. Only covers same-doc anchors; cross-doc Doc URLs are full absolute URLs, unaffected.
3. Links are written verbatim by the HTML→markdown converter.

### `pull_sectioned()`

`backend.py:1582+`: uses the structural path exclusively. Each section goes through
`render_nodes_to_markdown` independently.

## Heading Reversal: Existing Tools

`heading_anchors.py`:
- `heading_slug_to_id(nodes)` → `Dict[str, str]` — push side (slug → `h.xxx`)
- `heading_id_to_slug(nodes)` → `Dict[str, str]` — pull side (inverse, `h.xxx` → slug)
- `upgrade_heading_id_anchors(markdown, id_to_slug)` — regex-based post-processing that rewrites
  `](#h.xxx)` to `](#slug)`. This is the *same-document* version of what we need for cross-doc.

The `heading_id_to_slug()` function is already used in the Drive-export path (line 1508). It just
needs to be applied to *target* document headings when reversing cross-doc Doc URLs.

## Approach Decision: Post-processing Regex

Two options:
1. **Post-processing regex** — after `render_nodes_to_markdown()` or `html_to_markdown()`,
   scan the markdown string for `](https://docs.google.com/document/d/<id>/edit...)` and replace.
2. **Node-level rewriting** — modify `render_nodes_to_markdown()` to accept a reverse resolver
   and rewrite `span.link` values at the span level.

Option 1 is preferred because:
- Consistent with the existing `upgrade_heading_id_anchors()` pattern (proven approach).
- Works identically across both pull paths (structural and Drive-export) with no changes to the
  rendering machinery.
- The `docs.google.com/document/d/<id>/edit` prefix is distinctive enough that regex matching is
  low-risk.
- `nodes_to_markdown.py` stays focused on rendering, not resolution.

## Doc URL Structure

The canonical form push produces:
```
https://docs.google.com/document/d/<doc_id>/edit
https://docs.google.com/document/d/<doc_id>/edit?tab=<tab_id>
https://docs.google.com/document/d/<doc_id>/edit#heading=<heading_id>
https://docs.google.com/document/d/<doc_id>/edit?tab=<tab_id>#heading=<heading_id>
```

Regex group: `https://docs\.google\.com/document/d/(?P<doc_id>[^/]+)/edit(?:\?tab=(?P<tab_id>[^#")\s]+))?(?:#heading=(?P<heading_id>[^")\s]+))?`

## `pull()` / `pull_sectioned()` kwargs Convention

Push uses `mappings` as a `**kwargs` key (validated in `_build_cross_doc_resolver()`). Pull
should adopt the same name and the same "absent = no-op" contract, so callers (`orchestrate_pull`,
CLI) can pass it symmetrically.

## Ambiguous Mapping Guard

If two mappings share `(remote_id, tab_id)`, `resolve_local_mapping()` (used for push) raises
`AmbiguousMappingError`. On pull, the same guard should apply: leave the URL verbatim and produce
a `PullResult` warning (not an error — the file is still written).

## Target Heading Fetch

To reverse `#heading=h.xxx` → slug, we need the target doc's nodes. The resolver must lazily
fetch target doc headings using the same `_fetch_target_headings()` infrastructure already in
`GoogleDocsBackend`, cached per `(doc_id, tab_id)` to avoid duplicate API calls (AC-12).

## Files Requiring Changes

| File | Change |
|------|--------|
| `src/docspan/backends/google_docs/cross_doc_links.py` | Add `parse_doc_url()`, `ReverseDocLinkResolver`, `reverse_cross_doc_links()` |
| `src/docspan/backends/google_docs/backend.py` | Wire `mappings` kwarg into `pull()` and `pull_sectioned()` |
| `tests/test_cross_doc_links.py` | Unit tests for `parse_doc_url`, `ReverseDocLinkResolver` |
| `tests/test_google_docs_backend.py` | Integration tests through `pull()` |
