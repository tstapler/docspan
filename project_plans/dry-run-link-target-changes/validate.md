# Validation: push --dry-run link-target-only change reporting

## Adversarial review of the plan

### Challenge 1: Does Option B actually match what `push()` does?

**Concern:** The design goal (from `_restyles()`'s docstring, line 3141) is that the
dry-run predicate and the write predicate stay on one definition so they never disagree.
Option B introduces a new `_span_changes()` helper for the preview that is *separate* from
`build_span_style_requests()` (which drives the real write). If they diverge, dry-run can
over- or under-report again.

**Verdict — MANAGEABLE.** `build_span_style_requests()` already iterates target nodes'
spans unconditionally; it will emit `updateTextStyle` for any span that differs from the
live document. `_span_changes()` is a preview helper that compares the same two spans in
the same direction. The shared input (current-side spans from `DocsParagraphNode.spans`,
target-side spans from the markdown-parsed node) guarantees they see the same data. The risk
is a future maintainer changing one without updating the other.

**Mitigation:** A unit test (`test_diff_summary_link_only_change_is_reported`) that
constructs the exact same node pairs and asserts both the preview and a mock request-builder
emit changes will catch divergence. The `_restyles()` pattern (one predicate shared by
diff and build) is worth following — but the existing code for pass-2 spans has never had
that shared predicate, so the best we can do is test both paths.

---

### Challenge 2: Span alignment when span lists differ in length

**Concern:** The markdown parser may split a span differently from the live doc. Example:
live doc has `[TextSpan(text="Visit the new docs", link="https://old.example.com")]`.
Markdown has `[TextSpan(text="Visit the ", link=None), TextSpan(text="new docs", link="https://new.example.com")]`.
Both `node.text` are `"Visit the new docs"` — same text, same `_node_key` match. But
`_span_changes()` must not crash or produce wrong output on length-mismatched span lists.

**Verdict — REAL RISK, mitigated by algorithm choice.** Aligning by character offset
(not by list index) handles this correctly. Implementation must: (a) build a flat mapping
from `(start_char, end_char) → (link, bold, italic, monospace)` for each side, then (b)
iterate the target's spans and look up each text substring in the current mapping.
If a character range exists on target but has no corresponding span on current (or vice
versa), treat the missing side as `link=None, bold=False, italic=False, monospace=False`.

This is more work than a naive `zip()`, but it is correct.

**Task 2 must specify this alignment approach explicitly** — the plan currently leaves
"align by text position" underspecified. Updated in the plan above.

---

### Challenge 3: `find_high_risk_paragraphs()` receives `DiffEntry` with new field

**Concern:** `find_high_risk_paragraphs()` (`push_preview.py:46`) loops over `entries` and
checks `entry.kind` and `entry.current_text`. A span-change `DiffEntry(kind="change", ...)` 
produced by Task 3 is a `"change"` entry — `find_high_risk_paragraphs()` will flag it for
comment/glyph risk the same as any other changed paragraph. Is that correct?

**Verdict — CORRECT BEHAVIOR.** If a paragraph has an open Drive comment and only its link
target is changing, the comment IS still at risk (the paragraph is being rewritten). The
existing CommentCrossReference check (substring match on `current_text`) correctly detects
this. No special handling needed.

---

### Challenge 4: "change" count in summary for span-only paragraphs

**Concern:** The summary line reads `Preview: N change(s), ...`. A span-only change
increments the "changes" counter (via `kind="change"`). The `~ text → text` renderer line
looks like `~ "Visit the docs" → "Visit the docs"` — both sides identical. A user unfamiliar
with the format might think "change" is a bug.

**Verdict — ACCEPTABLE, but improve the renderer.** The plan's Task 4 renderer should
emit a *different* prefix when `current_text == target_text` and `span_changes` is non-empty:
```
  ↳ link target changed (same text): "Visit the docs"
      old: https://old.example.com
      new: https://new.example.com
```
instead of the misleading `~ text → text`. This does NOT require a new `DiffEntry.kind`
value; it is purely a renderer decision. Alternatively, add a dedicated summary counter:
`N link/style update(s)` — but that requires a new field on `PushPreview` and is optional.

---

### Challenge 5: Table cell span changes remain invisible

**Concern:** The plan scopes `_span_changes()` to `DocsParagraphNode`, not `TableCell`.
A table whose internal link URLs change in markdown will still be misreported by dry-run.

**Verdict — KNOWN GAP, documented.** The plan already names this. It should be called out
in a TODO comment in `_span_changes()` and in `docs/backends/google-docs.md` when the fix
is shipped. Not a blocker for this ticket.

---

## Acceptance criterion cross-check

| AC | Status after plan |
|----|-------------------|
| 1. Link-target-only changes reported | ✅ Task 3 classifies them as `"change"` DiffEntries |
| 2. Count appears in summary | ✅ `kind="change"` increments the existing "changes" counter |
| 3. No false positives | ✅ Task 2 returns `[]` for identical spans; Task 5 test 4 verifies |
| 4. Old and new URLs shown | ✅ Task 4 renderer emits `↳ link "text": old → new` |
| 5. Bold/italic/monospace | ✅ `SpanChange` carries all four attributes; renderer handles each |
| 6. Existing tests still pass | ✅ `DiffEntry` new field has a default; no existing caller breaks |
| 7. New regression test | ✅ Task 5 adds 5 targeted tests |

## Validation verdict

The plan is sound. Span alignment (Challenge 2) is the only non-trivial implementation risk,
mitigated by specifying the character-offset algorithm. Challenge 4's renderer needs a minor
adjustment to avoid showing `~ text → text` when text is identical. All other concerns are
either already handled or explicitly documented as known gaps.

**Recommended pre-implementation action:** Add a failing test first (TDD) so the alignment
algorithm is exercised before any production code is written. This is the safest path to
correct span alignment.
