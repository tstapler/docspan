# Plan: push --dry-run link-target-only change reporting

## Chosen approach: Option B — SpanChange dataclass + DiffEntry extension

See `research.md` for the three options considered. Option B is chosen because it
surfaces which paragraph changed and what the old/new URLs are — the information users
need to act on a dry-run preview.

---

## Task breakdown

### Task 1 — Add `SpanChange` dataclass and `span_changes` field to `DiffEntry`

**File:** `src/docspan/backends/google_docs/docs_request_builder.py`

1. Define a new `@dataclass SpanChange` (above `DiffEntry`):
   ```python
   @dataclass
   class SpanChange:
       """One span whose inline attributes differ between current and target."""
       text: str
       old_link: Optional[str]
       new_link: Optional[str]
       old_bold: bool
       new_bold: bool
       old_italic: bool
       new_italic: bool
       old_monospace: bool
       new_monospace: bool
   ```
   Only populate fields that differ (all provided for completeness; renderer can filter to
   non-trivial ones). Initially scope to link target only if bold/italic/monospace add noise.

2. Add `span_changes: List["SpanChange"] = field(default_factory=list)` to `DiffEntry`.
   This is backward-compatible: existing callers construct `DiffEntry` without it; it
   defaults to empty.

**Estimate:** 1 h

---

### Task 2 — Add `_span_changes()` helper to `DocsRequestBuilder`

**File:** `src/docspan/backends/google_docs/docs_request_builder.py`

Implement a static method:

```python
@staticmethod
def _span_changes(
    current_node: DocsParagraphNode,
    target_node: DocsParagraphNode,
) -> List[SpanChange]:
    """Return span-level changes for two same-text nodes."""
```

Algorithm:
- Both nodes must be `DocsParagraphNode`; return `[]` for images or tables.
- Zip spans by text position. Because both nodes have the same `node.text`, iterate the
  target spans in order; for each span, find the corresponding current span (by text
  substring offset in the shared text). A simple approach: build two flat lists of
  `(text, bold, italic, link, monospace)` tuples from each side and diff them pairwise.
- Emit a `SpanChange` for every (text, idx) where any of link, bold, italic, monospace
  differs between current and target.
- Return the list (empty means no inline-style change).

**Edge cases:**
- A span that exists on one side but not the other (e.g. the current doc stored the whole
  sentence as one un-linked span, and markdown introduced a link in the middle) — treat as
  a link appearing from `None` on the current side.
- Anchor links (`#slug`) — include as-is; do not resolve; the dry-run is read-only.

**Estimate:** 3 h

---

### Task 3 — Call `_span_changes()` from `diff_summary()` for "equal" pairs

**File:** `src/docspan/backends/google_docs/docs_request_builder.py`

In `diff_summary()`, inside the `tag == "equal"` branch, after `_restyles()` returns
`False` (i.e., the paragraph-level attributes are unchanged), call `_span_changes()`:

```python
else:
    sc = self._span_changes(current[ci], target[ti])
    if sc:
        entries.append(
            DiffEntry(
                kind="change",
                current_text=_node_text(current[ci]),
                target_text=_node_text(target[ti]),
                style=_node_style(target[ti]),
                span_changes=sc,
                edit_group=edit_group,
            )
        )
    else:
        unchanged_count += 1
```

This ensures a span-only change is counted as a "change" in the summary line and
appears in `PushPreview.entries` for the renderer to display.

**Design note:** using the existing `kind="change"` with `current_text == target_text` is
intentional — it keeps the summary count accurate without a new category field. The renderer
(Task 4) will detect `span_changes` and display the URL detail beneath the text line.

**Estimate:** 1 h

---

### Task 4 — Render span changes in `PushPreview.render()`

**File:** `src/docspan/backends/google_docs/push_preview.py`

In `PushPreview.render()`, inside the `elif entry.kind == "change":` block:

```python
elif entry.kind == "change":
    lines.append(f"  ~ {entry.current_text} → {entry.target_text}")
    for sc in entry.span_changes:
        if sc.old_link != sc.new_link:
            old = sc.old_link or "(no link)"
            new = sc.new_link or "(no link)"
            lines.append(f"    ↳ link \"{sc.text}\": {old} → {new}")
        if sc.old_bold != sc.new_bold:
            lines.append(
                f"    ↳ bold \"{sc.text}\": {'on' if sc.new_bold else 'off'}"
            )
        # italic, monospace — same pattern
```

When `entry.current_text == entry.target_text` (span-only change), the `~ text → text`
line is redundant but harmless; it signals "this paragraph is changing". The `↳` sub-lines
tell the user what exactly differs.

**Estimate:** 2 h

---

### Task 5 — Write unit tests

**File:** `tests/test_push_preview.py` (and/or a new `tests/test_span_changes.py`)

Tests to add:

1. **`test_span_changes_link_only`** — two `DocsParagraphNode`s with identical text, one
   span; current has `link="https://old.example.com"`, target has `link="https://new.example.com"`.
   Assert `_span_changes()` returns one `SpanChange` with matching fields.

2. **`test_diff_summary_link_only_change_is_reported`** — call `diff_summary()` with one
   current node (linked to old URL) and one target node (linked to new URL, same text).
   Assert `entries` has one `DiffEntry(kind="change")` with non-empty `span_changes` and
   `unchanged_count == 0`.

3. **`test_push_preview_render_shows_link_change`** — build a `PushPreview` with the
   above `DiffEntry`, call `render()`, assert the output contains the old and new URLs.

4. **`test_span_changes_no_link_change`** — same text and same URL: `_span_changes()`
   must return `[]`. (Regression for false-positive.)

5. **`test_span_changes_bold_only`** — same text, bold toggled: one `SpanChange` returned
   with `old_bold != new_bold`.

**Estimate:** 3 h

---

### Task 6 — Run and verify the full test suite

```bash
uv run pytest tests/ -x -q
```

Confirm all existing tests still pass, new tests pass, no regressions.

**Estimate:** 30 min

---

## Implementation order

1 → 2 → 3 → 4 → 5 → 6 (sequential; each task builds on the previous).

## Risk notes

- **Span alignment is tricky.** If a markdown file adds or removes a link in the *middle*
  of a long span (splitting one span into three: pre-link text, linked text, post-link text),
  the current/target span lists have different lengths. `_span_changes()` must align by text
  position, not by index. A simple character-offset approach is sufficient — the nodes are
  known to have equal `.text`.

- **`DiffEntry` is consumed by `find_high_risk_paragraphs()` and `find_churn_pairs()`.**
  Both iterate `entry.kind` and `entry.current_text`. Adding `span_changes` to `DiffEntry`
  is backward-compatible; neither helper reads span data. Verified by code inspection.

- **Table cells also have spans.** The fix is scoped to `DocsParagraphNode` (pass 2 handles
  tables with `build_table_span_style_requests`). A table cell whose link target changes
  will still be invisible in the dry-run — document this as a known remaining gap.

- **No UI/CLI changes needed.** The output is rendered by `PushPreview.render()` which is
  called by the existing `--dry-run` rendering path in `cli/main.py`. No flag changes.
