# Plan: Fix Mermaid Fence Collapse on Structural Pull

## Overview

Three independent bugs compound to produce the `![]()` symptom. Each has a targeted fix.
A fourth change adds the missing warning. All changes are in `pulled_image_recovery.py`,
`backend.py`, and `orchestrator.py`; no schema, API contract, or sidecar file format changes.

---

## Fix 1 — Pass canonical path for sidecar lookup on merge path

**Files:** `src/docspan/backends/google_docs/backend.py`,
`src/docspan/core/orchestrator.py`

### Change in `backend.py`

Add an optional `sidecar_path_override: Optional[str] = None` parameter to `pull()`.
Inside the structural branch, pass it to `recover_structural_mermaid_images`:

```python
def pull(
    self,
    doc_id: str,
    local_path: str,
    tab_id: Optional[str] = None,
    pull_strategy: Optional[str] = None,
    sidecar_path_override: Optional[str] = None,  # NEW
) -> PullResult:
    ...
    if tab_id is not None or pull_strategy == "structural":
        ...
        result = recover_structural_mermaid_images(
            markdown_content,
            image_nodes,
            markdown_path=sidecar_path_override or local_path,  # CHANGED
            appendix_entries=appendix_entries,
        )
```

### Change in `orchestrator.py`

In `_merge_pull()`, pass the real mapping path as the override:

```python
tmp_result = backend.pull(
    mapping.remote_id,
    tmp_path,
    tab_id=mapping.tab_id,
    pull_strategy=mapping.pull_strategy,
    sidecar_path_override=str(mapping.local),  # NEW
)
```

**Why `sidecar_path_override` not `canonical_dir`:** `pull_sectioned()` uses `canonical_dir`
because it assembles sections; `pull()` operates on a single file, so the full file path is
the cleaner contract.

---

## Fix 2 — Per-image degradation instead of global abort in the count guard

**File:** `src/docspan/backends/google_docs/pulled_image_recovery.py`

The current count guard (`len(matches) != len(image_nodes)`) aborts globally when any image
has an empty src (because `![]()` doesn't match `[^)]+`). Replace the global abort with
per-image degradation using a different matching strategy:

**Current approach:** regex-match the rendered markdown, then zip against `image_nodes`.
**New approach:** iterate `image_nodes` directly; re-render each image's markdown position
by tracking cursor, skipping empty-src images gracefully.

```python
def recover_structural_mermaid_images(
    markdown_content: str,
    image_nodes: List[DocsImageNode],
    fetch: Fetcher = _fetch_via_http,
    markdown_path: Optional[str] = None,
    appendix_entries: Optional[Dict[str, str]] = None,
) -> ImageRecoveryResult:
    # Build a lookup: src -> fetched bytes (only for non-empty srcs)
    unique_urls = list({node.src for node in image_nodes if node.src})
    fetched_by_url: Dict[str, Optional[bytes]] = {}
    if unique_urls:
        with ThreadPoolExecutor(...) as pool:
            for url, png_bytes in zip(unique_urls, pool.map(fetch, unique_urls)):
                fetched_by_url[url] = png_bytes

    # Regex that matches both non-empty AND empty URLs: !\[([^\]]*)\]\(([^)]*)\)
    #                                                               ^^^^ * not +
    matches = list(re.finditer(r"!\[([^\]]*)\]\(([^)]*)\)", markdown_content))
    if len(matches) != len(image_nodes):
        # Counts still don't align — structural assumption broken, no-op as before
        return ImageRecoveryResult(markdown=markdown_content, mermaid_restored=0, base64_deflated=0)

    mermaid_restored = 0
    pieces: List[str] = []
    cursor = 0
    for match, node in zip(matches, image_nodes):
        pieces.append(markdown_content[cursor : match.start()])
        alt = match.group(1)
        diagram = None
        png_bytes = fetched_by_url.get(node.src) if node.src else None
        if png_bytes is not None:
            diagram = _lookup_all_tiers(png_bytes, markdown_path, appendix_entries)
        if diagram is None and node.object_id and markdown_path is not None:
            # Tier 2b: sidecar lookup by object_id when bytes unavailable
            diagram = mermaid_cache_sidecar.lookup_by_object_id(markdown_path, node.object_id)
        if diagram is not None:
            pieces.append(f"```mermaid\n{diagram}\n```")
            mermaid_restored += 1
        elif node.src:
            pieces.append(f"![{alt}]({node.src})")
        else:
            pieces.append(f"![{alt}]()")  # preserve original (already broken)
        cursor = match.end()
    pieces.append(markdown_content[cursor:])
    return ImageRecoveryResult(markdown="".join(pieces), mermaid_restored=mermaid_restored, base64_deflated=0)
```

Key change: regex `[^)]*` (zero or more) instead of `[^)]+` (one or more) so `![]()` is
matched positionally.

---

## Fix 3 — Object-ID fallback in sidecar when bytes unavailable

**File:** `src/docspan/backends/google_docs/mermaid_cache_sidecar.py`

Add a secondary index keyed by `object_id` (the Docs API `inlineObjectId`, which is stable
across fetches). `record_many()` writes both the `sha256` key (existing) and an `oid:` key
(new) when `object_id` is available.

```yaml
# .mermaid-cache.yaml format (extended, backward-compatible):
sha256_hex_of_png: "diagram source"        # existing
oid:INLINE_OBJECT_ID: "diagram source"     # new (written alongside sha256 entry)
```

New functions:

```python
def record_with_object_id(
    markdown_path: str,
    png_bytes: bytes,
    diagram: str,
    object_id: Optional[str],
) -> None:
    """Write both sha256 and oid: keys to the sidecar."""

def lookup_by_object_id(markdown_path: str, object_id: str) -> Optional[str]:
    """Return diagram source keyed by oid:{object_id}, or None."""
    return load(markdown_path).get(f"oid:{object_id}")
```

`DocsImageNode` already carries `object_id` (confirmed in `docs_structure_parser.py` L616).
The push path in `backend.py` calls `mermaid_cache_sidecar.record_many()`; update it to also
write the `oid:` key using `record_with_object_id`.

---

## Fix 4 — Warning when mermaid fence is replaced by empty/unrecoverable image

**File:** `src/docspan/backends/google_docs/pulled_image_recovery.py` (or `backend.py`)

After `recover_structural_mermaid_images` returns, compare the previous local file content
(available in the merge scenario as the base) or the existing file against the new markdown.
Count mermaid fences lost.

Simpler alternative: emit the warning inside `recover_structural_mermaid_images` itself when
`diagram is None and not node.src`:

```python
if diagram is None:
    if not node.src:
        logger.warning(
            "Structural pull: inline image (object_id=%r) has no contentUri and no "
            "sidecar/appendix match — mermaid fence cannot be recovered. "
            "Check %s.mermaid-cache.yaml.",
            node.object_id,
            markdown_path or "<unknown>",
        )
    pieces.append(f"![{alt}]()")
```

Additionally, in `_merge_pull()` after the merge is written, count how many mermaid fences
the pre-merge local file had vs. the merged result, and warn if the count decreased.

---

## Implementation Order

1. Fix 1 (sidecar path on merge): 2 lines in `orchestrator.py`, 2 lines in `backend.py` —
   smallest blast radius, highest immediate value.
2. Fix 2 (count guard regex): touched only in `pulled_image_recovery.py`, self-contained.
3. Fix 3 (object_id sidecar key): requires coordinated change in `mermaid_cache_sidecar.py`
   and the push path in `backend.py`; do after Fix 2 to avoid rebase friction.
4. Fix 4 (warning): add last, after the recovery logic is stable.

## Tests to Add

| Test | Location |
|------|----------|
| `_merge_pull` uses `mapping.local` path for sidecar, not `tmp_path` | `tests/test_orchestrator.py` |
| `recover_structural_mermaid_images` restores fence when sidecar has source, even if contentUri fetch fails | `tests/test_pulled_image_recovery.py` |
| `recover_structural_mermaid_images` restores fence when `node.src` is empty but `object_id` sidecar key exists | `tests/test_pulled_image_recovery.py` |
| `recover_structural_mermaid_images` emits WARNING when diagram unrecoverable | `tests/test_pulled_image_recovery.py` |
| `mermaid_cache_sidecar.lookup_by_object_id` returns diagram when `oid:` key present | `tests/test_mermaid_cache_sidecar.py` |
| Full round-trip: push (records oid: key) → structural pull (fetch fails) → fence restored | `tests/integration/` |

## Adversarial Review

**Q: Changing the regex from `+` to `*` could match `![]()` spans that are NOT images.**  
A: A bare `![]()` in markdown is an image syntax with empty alt and empty src — there is no
other common use of that exact syntax. The structural parser only emits `![...]()` for image
nodes, so a `![]()` in the output is always a failed image. The count-guard (`len(matches) !=
len(image_nodes)`) still protects against mismatch.

**Q: Does `object_id` remain stable across pulls?**  
A: Yes. The Google Docs API `inlineObjectId` is assigned once when an object is inserted and
does not change across `documents.get` calls. It is scoped to the document (and stable across
tabs). This is a safe key.

**Q: The `oid:` prefix in the YAML is a schema change — does it break existing sidecars?**  
A: No. The `oid:` entries are additive. `load()` returns all keys; existing tools that only
read `sha256` keys see no change. Old entries without `oid:` simply never match `lookup_by_object_id`.

**Q: Fix 1 adds a new kwarg to `backend.pull()`. Any callers that positionally pass args?**  
A: `backend.pull()` is called with keyword args throughout the codebase (confirmed in
orchestrator.py). Adding a keyword-only default is backward-compatible.
