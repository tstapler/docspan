# Pitfalls Research: Index Arithmetic, Comment Anchors, Conflict Markers, and Rate Limiting

## Google Docs batchUpdate Index Arithmetic

### The Index Shift Problem

Indexes in the Docs API are UTF-16 code unit offsets from the beginning of the document segment. All requests in a `batchUpdate` are applied **sequentially in the order given**. Each insertion shifts all subsequent indexes upward by the character count of the inserted text.

**Concrete example** (from official docs):
- Insert 10 chars at index 25 → all indexes above 25 increase by 10.
- Next insert at original-index 50 must now target 50+10=60.
- Next insert at original-index 75 must target 75+10+10=95.

### The Write-Backwards Solution

The canonical fix: **sort all insertions by descending index** before submitting the batch. An insertion at a high index does not affect lower-index operations; processing from end-to-start means no precalculation is needed.

```python
# Sort deleteContentRange + insertText pairs by descending startIndex
requests.sort(key=lambda r: -(
    r.get('deleteContentRange', {}).get('range', {}).get('startIndex', 0) or
    r.get('insertText', {}).get('location', {}).get('index', 0)
))
```

### UTF-16 Surrogate Pairs

Indexes are measured in **UTF-16 code units**, not Unicode code points. Emoji and many CJK characters outside the Basic Multilingual Plane consume **two index units**. Python's `len(str)` on a string with emoji will undercount. Use `len(text.encode('utf-16-le')) // 2` to compute the correct index offset for inserted text.

**Invalid deletion**: Deleting one code unit of a surrogate pair raises a 400 error. Always delete complete grapheme clusters.

### Forbidden Deletion Ranges

The API returns 400 for:
- Deleting the last `\n` of a Body, Header, Footer, Footnote, TableCell, or TableOfContents.
- Deleting the start or end of a Table/TableOfContents/Equation without deleting the entire element.
- Deleting the `\n` immediately before a Table/SectionBreak without deleting the element itself.
- Deleting individual table rows or cells (only cell contents can be deleted).

**Practical implication**: When diffing paragraphs, never emit a `deleteContentRange` that ends at `doc_body.content[-1].endIndex`. The body's last element always ends with the terminal newline at index `endIndex - 1`; the safe deletion range is `[1, endIndex - 1]` for a full clear.

### insertText Cannot Target Table Start Indexes

Text (and images) must be inserted **within the bounds of an existing Paragraph**. If a diff places an insertion at a table boundary, adjust to insert in the preceding paragraph by targeting the paragraph's `endIndex - 1` (before the trailing `\n`).

### CreateParagraphBullets Removes Leading Tabs

`createParagraphBullets` uses the number of leading tab characters to determine nesting level, then **removes those tabs**, which changes subsequent indexes. Always apply bullet formatting **before** appending any text after the bullet paragraphs in the same batch; or calculate the removed tab count and adjust subsequent indexes accordingly.

## Comment Anchor Preservation Edge Cases

### How Anchors Work in Google Docs

Comments in Google Docs anchor to a character range via a `SuggestedTextStyle`/`bookmarkLink` mechanism that the API does not expose directly for reading. The Drive API v3 `comments` resource provides a comment's `anchor` as an opaque string that corresponds to an internal revision.

**Key insight**: Comment anchors reference the text content at a specific revision. When you modify text in that range, the comment anchor shifts if the API can reconcile it, or becomes orphaned if the text is deleted.

**Structural diff benefit**: By only deleting the ranges that differ between current and target, paragraphs with unchanged text retain their original index range. Comments anchored entirely within an unchanged paragraph survive.

### Anchor Invalidation Scenarios

1. **Unchanged paragraph, same position**: Comment survives (anchor range unchanged).
2. **Text inserted before anchored paragraph**: Comment survives if the API shifts the anchor (typically it does for insertions before, not within, the anchor range).
3. **Text modified within anchored range**: Comment is orphaned by the Docs API automatically.
4. **Anchored paragraph deleted**: Comment is orphaned.

**Implication**: The structural diff must be paragraph-granular, not character-granular, to maximize comment survival. Even changing a single character within a paragraph orphans comments on that paragraph.

### Confluence: Inline Anchors Broken on Full-Page Replace

When `update_page` replaces the entire ADF body (which is what the current Confluence push does), **all inline comment anchors are lost**. This is documented in Atlassian MCP Server issue #54 and is a known platform limitation with no workaround via the v2 API. The comment pull sidecar feature in requirements exists partly because of this.

## Merge Conflict Marker Formats

### Standard Git Format (use this)

```
<<<<<<< ours
[local content]
=======
[remote content]
>>>>>>> theirs
```

The `merge3.merge_lines()` method with explicit `name_a`, `name_b`, `start_marker`, `mid_marker`, `end_marker` parameters produces this format.

### merge3 Default Annotated Format (avoid for output)

The default `merge3.merge_annotated()` produces `<<<<\n`, `----\n`, `>>>>\n` with per-line `A | ` / `B | ` / `u | ` prefixes. This is useful for analysis but not for writing to user-facing files.

### Detection for `markgate conflicts list`

Scan local files for lines matching `^<<<<<<< ` (7 angle brackets + space). This is unambiguous and the standard used by git, vim, and all major editors.

### Conflict Resolution

`markgate conflicts resolve <file> --accept remote|local|merged`:
- `remote`: overwrite file with remote content (re-fetch from backend).
- `local`: overwrite with original local content (re-read from state's `base_hash` reference + re-apply local diff, or simply re-read the pre-merge file if a `.orig` backup was saved).
- `merged`: validate no `<<<<<<<` markers remain, then update state.

**Best practice**: Before writing conflict markers to a file, save the pre-merge local content to `<file>.orig` so `--accept local` can recover it without re-fetching.

## Google Docs API Rate Limits

### Confirmed Quotas (as of 2026)

| Quota | Limit |
|---|---|
| Read requests per minute per project | 3,000 |
| Read requests per minute per user per project | 300 |
| Write requests per minute per project | 600 |
| Write requests per minute per user per project | 60 |

The 60 write requests/minute/user limit is the most likely to be hit during bulk push operations. A single `batchUpdate` call counts as **one write request** regardless of the number of sub-requests in the batch — this is the key reason to batch all changes into a single call.

### Exponential Backoff Implementation

On `429: Too many requests`, implement truncated exponential backoff:

```python
import time, random

def with_backoff(fn, max_retries=5, max_backoff=64):
    for n in range(max_retries):
        try:
            return fn()
        except HttpError as e:
            if e.resp.status == 429:
                wait = min(2**n + random.random(), max_backoff)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")
```

The existing `GoogleDocsClient` has a retry loop with `RETRY_DELAY = 2` (linear backoff). **Replace this with exponential backoff** and specifically handle 429 separately from other HttpErrors.

### Pricing Warning (2026)

Google has announced that exceeding quota limits will incur charges to the GCP billing account later in 2026. The existing `GoogleDocsClient` retry pattern needs to be rate-limit-aware to avoid accumulating charges in production use.

### Docs vs Drive API Calls

`documents.get` (Docs API) counts against the read quota. `drive.files.export_media` (used in current `get_doc_content()`) uses the Drive API quota, not the Docs API quota — different quota pool. The new `get_document()` method using `docs_service.documents().get()` counts against the Docs API read quota (3000/min project limit).
