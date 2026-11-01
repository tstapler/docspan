# Research: push --dry-run doesn't report link-target-only changes

## Codebase findings

### The two-pass push architecture

`push()` is split into two passes:

- **Pass 1** (`DocsRequestBuilder.build()`) — structural changes: paragraph insertions,
  deletions, and paragraph-attribute restyling (heading level, bullet, blockquote).
  Produces `insertText`, `deleteContentRange`, `createParagraphBullets`, etc.

- **Pass 2** (`DocsRequestBuilder.build_span_style_requests()`) — inline styling:
  link URL, bold, italic, monospace inside each span. Produces `updateTextStyle`.

`diff_summary()` (and therefore `DiffEntry` / `PushPreview`) only models pass 1.

### Root cause: span data is absent from the diff key

`_node_key()` (`docs_request_builder.py:237`) builds the sequence-matcher key from:
```python
("__para__", node.style, node.is_list_item, node.nesting_level,
 self._is_code_line(node), node.is_blockquote, node.quote_depth, node.text)
```
`node.spans` (a `List[TextSpan]`) is not included. `TextSpan.link` (the URL), `.bold`,
`.italic`, and `.monospace` are therefore invisible to the matcher.

`_restyles()` (`docs_request_builder.py:3137`) — the predicate applied to `"equal"`-tagged
pairs — compares only `style`, `is_list_item`, `is_blockquote`, and `quote_depth`. It does
not compare spans.

When a paragraph's text is identical but its spans differ (link target corrected, or
bold/italic/monospace toggled), `diff_summary()` increments `unchanged_count` and produces
no `DiffEntry`. `PushPreview.render()` reports "0 change(s)".

Meanwhile, `build_span_style_requests()` iterates every `target_node` with spans and emits
`updateTextStyle` requests — so the real `push()` writes the link correction silently.

### DiffEntry has no URL fields

```python
@dataclass
class DiffEntry:
    kind: Literal["add", "remove", "change", "unchanged"]
    current_text: Optional[str]
    target_text: Optional[str]
    style: str
    current_is_native_checkbox: bool = False
    edit_group: int = -1
```

No span information survives into `PushPreview` at all.

### The gap is acknowledged in backend.py

`backend.py:570-576` (comment inside `_build_push_plan`) explicitly notes that
bold/italic/monospace/link-only changes produce zero pass-1 diffs and zero requests — a
historical bug that used to cause `push()` to report `"skipped"`. The comment does not
address the dry-run misreporting.

### TextSpan data structure

```python
@dataclass
class TextSpan:
    text: str
    bold: bool = False
    italic: bool = False
    link: Optional[str] = None   # URL or "#slug"
    monospace: bool = False
```

Both `DocsParagraphNode.spans` and `TableCell.spans` carry this. The fix scope is
`DocsParagraphNode`; tables are a separate concern.

### PushPreview render path

`PushPreview.render()` (`push_preview.py:372`) produces the summary line:
```
Preview: {changes} change(s), {additions} addition(s), {removals} removal(s),
         {len(churn_pairs)} rewritten (no text change), {self.unchanged_count} unchanged
```

The summary is driven entirely by `DiffEntry` contents. Adding span-level change counts
requires either new `DiffEntry` fields, a parallel list, or a count field on `PushPreview`.

### Existing tests

`tests/test_push_preview.py` has no test for link-target-only changes in dry-run. Tests
cover: `find_high_risk_paragraphs`, `find_churn_pairs`, `PushPreview.render()`, and
`render_high_risk()` / `render_churn_note()`.

`tests/test_gdocs_push_pipeline.py` covers end-to-end push scenarios but also has no
test for the link-URL-only-changed dry-run case.

## Design options

### Option A: Extend `_restyles()` to compare span fingerprints

Add a spans comparison to `_restyles()`. Two `DocsParagraphNode`s with the same text but
different spans would then be classified as a "change" in `diff_summary()`.

**Pros:**
- Minimal surface area: one predicate touched.
- `DiffEntry` already has `kind="change"`, `current_text`, `target_text` — the existing
  renderer shows `~ old → new` which is at least visible (even if it only shows plain text,
  not the URL difference).
- Keeps `_restyles()` as the single truth shared between the preview and the write (the
  comment at line 3141 emphasises this invariant).

**Cons:**
- `_restyles()` is currently for *paragraph-level* attributes; extending it to span-level
  breaks that conceptual separation.
- The `DiffEntry` renderer would show `~ "Visit the docs" → "Visit the docs"` — same text
  both sides — with no indication that the URL changed. Users see a "change" but can't see
  what changed.

### Option B: Dedicated span-diff field on `DiffEntry` + new renderer

Add a `span_changes: List[SpanChange]` field to `DiffEntry`, where `SpanChange` is a small
dataclass capturing `(text, old_url, new_url, old_bold, new_bold, ...)`. Populate it in
`diff_summary()` for `"equal"`-tagged pairs where spans differ. Add a new renderer in
`PushPreview.render()` that formats these as e.g. `  ↳ link "Visit the docs": old-url → new-url`.

**Pros:**
- Users see exactly what changed (old URL vs. new URL).
- Clean separation of concerns; `_restyles()` stays paragraph-only.
- Extensible: the same mechanism can later surface bold/italic/monospace diffs.

**Cons:**
- More code: new dataclass, new renderer, new count in the summary line.
- `DiffEntry` grows; downstream consumers (tests, `find_high_risk_paragraphs`) need review.

### Option C: New `link_change_count` field on `PushPreview`, no `DiffEntry` change

Compute span differences in `diff_summary()`, count link-target changes, return the count
as a separate value, store it on `PushPreview`, and surface it in the summary line only.

**Pros:**
- Smallest `DiffEntry` change (none).
- Fast to implement.

**Cons:**
- Paragraph-level detail (which paragraph changed) is not surfaced — users know *a* link
  changed, not which one.
- The summary line becomes misleading about what happened.

## Recommendation

**Option B** is the right fix. The bug report says the preview "doesn't match what push does";
the meaningful fix is to tell the user *which* paragraph's link target changed and from what
to what. Option A's `~ text → text` line is technically visible but practically useless.
Option C satisfies the count without helping anyone act on the information.

The implementation cost is modest: a new `SpanChange` dataclass, a `span_changes` field on
`DiffEntry`, a helper `_span_changes()` in `DocsRequestBuilder`, and 10–20 lines in
`PushPreview.render()`.
