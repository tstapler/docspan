# Research: Mermaid Fence Collapse on Structural Pull

## Code Paths Verified

### Structural pull entry point (`backend.py` ~L1395)

```python
if tab_id is not None or pull_strategy == "structural":
    doc = self._client.get_document(doc_id)
    doc, _resolved_tab_id, _warning = resolve_document_tab(doc, tab_id)
    parser = DocsStructureParser()
    nodes = parser.parse(doc)
    ...
    markdown_content = render_nodes_to_markdown(nodes)
    result = recover_structural_mermaid_images(
        markdown_content, image_nodes, markdown_path=local_path
    )
```

`local_path` here is the `local_path` argument passed into `pull()` — correct for
first-sync and fast-forward, but a temp path in the merge scenario.

### `resolve_document_tab` fix (#140, `tabs.py` L172-176)

```python
resolved = dict(doc)
resolved["tabs"] = []
resolved["body"] = doc_tab.get("body", {})
resolved["lists"] = doc_tab.get("lists", {})
resolved["inlineObjects"] = doc_tab.get("inlineObjects", {})
```

Fix is present. `DocsStructureParser.parse()` falls to the `elif "body" in doc:` branch
(because `tabs=[]` is falsy) and reads `doc["inlineObjects"]` which is now correctly set.

### `_parse_image_paragraph` (`docs_structure_parser.py` L598-611)

```python
object_id = image_element.get("inlineObjectId")
embedded = (
    inline_objects.get(object_id, {})
    .get("inlineObjectProperties", {})
    .get("embeddedObject", {})
)
src = embedded.get("contentUri") or embedded.get("imageProperties", {}).get(
    "contentUri", ""
)
return DocsImageNode(src=src or "", ...)
```

If `object_id` is not in `inline_objects`, `embedded = {}` and `src = ""`. This is the
original #140 bug. The fix in `resolve_document_tab` should prevent this — unless
`documentTab.inlineObjects` is absent from the API response.

### `recover_structural_mermaid_images` (`pulled_image_recovery.py` L203-239)

**Bug 1 — count guard aborts globally when any src is empty:**

```python
matches = list(re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", markdown_content))
if not matches or len(matches) != len(image_nodes):
    return ImageRecoveryResult(markdown=markdown_content, ...)
```

The regex `[^)]+` requires ≥1 char between parens. An `![]()` output from empty `node.src`
won't match. With two diagrams both producing `![]()`, `matches=[]` and `image_nodes` has
two entries → guard fires → the broken `![]()` markdown is returned unchanged.

**Bug 2 — sidecar skipped when png_bytes is None:**

```python
png_bytes = fetched_by_url.get(node.src) if node.src else None
if png_bytes is not None:
    diagram = lookup_mermaid_source(png_bytes)
    if diagram is None and markdown_path is not None:
        diagram = mermaid_cache_sidecar.lookup(markdown_path, png_bytes)
    ...
```

When `node.src` is empty OR when the HTTP fetch fails, `png_bytes=None` and the entire
three-tier lookup is skipped. The sidecar is never consulted.

**Bug 3 — fallback emits empty src:**

```python
else:
    pieces.append(f"![{alt}]({node.src})")
```

When `node.src=""`, the fallback is `![]()`, not a diagnostic placeholder.

### `_merge_pull` (`orchestrator.py` L761-809) — THE BUG LOCATION

```python
with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
    tmp_path = tmp.name
tmp_result = backend.pull(
    mapping.remote_id, tmp_path, tab_id=mapping.tab_id, pull_strategy=mapping.pull_strategy,
)
```

`backend.pull()` receives `local_path=tmp_path`. Inside pull, `markdown_path=local_path=tmp_path`.
The sidecar lookup calls `mermaid_cache_sidecar.lookup(tmp_path, png_bytes)` which looks for
`{tmp_path}.mermaid-cache.yaml` — a file that doesn't exist. The committed sidecar at
`{mapping.local}.mermaid-cache.yaml` is never found.

This is distinct from first-sync and fast-forward paths, which call
`backend.pull(mapping.remote_id, mapping.local, ...)` with the real path.

### Fix pattern already exists in `pull_sectioned()`

`pull_sectioned()` accepts a `canonical_dir` parameter for exactly this purpose: so the
sidecar lookup uses the real directory even when writing to a temp location. The same pattern
needs to be applied to `backend.pull()` for non-sectioned mappings.

### `mermaid_cache_sidecar.py`

- `sidecar_path(markdown_path)` → `Path(markdown_path + ".mermaid-cache.yaml")`
- `lookup(markdown_path, png_bytes)` → `load(markdown_path).get(sha256(png_bytes).hexdigest())`
- `load()` returns `{}` silently if file missing or corrupt

Key scheme is `sha256(png_bytes)` — requires image bytes to look up. No `object_id`-based
index exists. Changing the key scheme would break backward compatibility.

## Root Cause Summary

The `![]()` symptom requires `node.src == ""`. Two paths lead there:

**Path A (primary — matches #140):** `inlineObjects` missing or empty from the API response for
the tab, so `DocsStructureParser` can't resolve the object. Despite the #140 fix in
`resolve_document_tab`, the API may not populate `documentTab.inlineObjects` under some
conditions (e.g. a freshly-created tab, a document fetched without `includeTabsContent=True`
— though `client.py` defaults to `include_tabs_content=True`).

**Path B (alternative):** If the #140 fix is working and `node.src` is a real contentUri, then
the sidecar-in-merge bug (Bug 2) would produce `![alt](contentUri)` rather than `![]()`. But
the merge logic (git-style three-way merge) with base=mermaid-fence, remote=contentUri-image,
local=mermaid-fence should keep the local fence (no local change relative to base). This only
degrades to `![]()` if the merge result itself is corrupted or the wrong diff is used.

**Most likely actual scenario:** The structural pull is running on the merge path AND the
`inlineObjects` edge case is present simultaneously, causing `node.src=""` and thus `![]()`.
The sidecar-in-merge bug prevents recovery even when the bytes would be fetchable.

## Related Files

| File | Role |
|------|------|
| `src/docspan/backends/google_docs/tabs.py` | `resolve_document_tab` — the #140 fix |
| `src/docspan/backends/google_docs/docs_structure_parser.py` | Parses API JSON into nodes |
| `src/docspan/backends/google_docs/pulled_image_recovery.py` | `recover_structural_mermaid_images` |
| `src/docspan/backends/google_docs/mermaid_cache_sidecar.py` | Sidecar read/write |
| `src/docspan/backends/google_docs/backend.py` | `pull()` — passes wrong `markdown_path` on merge |
| `src/docspan/core/orchestrator.py` | `_merge_pull()` — passes `tmp_path` to `backend.pull()` |
