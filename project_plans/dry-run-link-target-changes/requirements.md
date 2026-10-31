# Requirements: push --dry-run doesn't report link-target-only changes

## Problem Statement

`docspan push --dry-run` misreports the scope of changes when a markdown file contains
link-target corrections (same display text, different URL). The preview shows
"0 change(s) … N unchanged" while the real push writes the corrected link URLs via its
pass-2 `updateTextStyle` requests. Users who rely on `--dry-run` for change verification
cannot trust its output when link targets differ.

## Background

The push pipeline has two passes:
- **Pass 1** — structural/text changes: insertions, deletions, paragraph-level restyling.
- **Pass 2** — inline-style changes: bold, italic, monospace, and link URL (`updateTextStyle`).

The current `diff_summary()` / `DiffEntry` system only models pass-1 changes. The diff key
(`_node_key`, `_content_key`) is derived exclusively from `node.text`; span-level information
(`TextSpan.link`, bold, italic, monospace) is never included. So any paragraph whose text is
identical but whose spans differ (e.g., a link target changed from `old-url` to `new-url`)
is classified as `unchanged` and produces no `DiffEntry`.

## Acceptance Criteria

1. **Link-target-only changes are reported in `--dry-run` output.**  
   When a markdown file changes only the URL in `[text](old-url)` to `[text](new-url)`, the
   dry-run preview must list the paragraph as changed (not as `unchanged`).

2. **Count of link-target-only changes appears in the summary line.**  
   The summary line (e.g. `Preview: 2 change(s), …`) must include such changes. A new category
   (e.g. "N link-target update(s)") or an existing category expansion is acceptable.

3. **The dry-run preview does not lie in either direction.**  
   Paragraphs with no change (text and all span attributes identical) must not be
   falsely flagged as changed.

4. **Preview output for a link-target change shows old and new URLs.**  
   Each flagged paragraph must display the old URL and the new URL so the user can review the
   correction before committing.

5. **Bold/italic/monospace-only span changes are also captured.**  
   Because they share the same root cause (span data excluded from the diff key), a correct fix
   covers all pass-2-only changes, not just link targets. If scoped to links only, this criterion
   may be deferred, but must be documented as a known remaining gap.

6. **Existing tests continue to pass.**  
   The change must not break any existing `test_push_preview.py`, `test_gdocs_push_pipeline.py`,
   or other backend tests.

7. **New regression test for link-target-only dry-run.**  
   At least one unit test in `tests/test_push_preview.py` (or a new file) constructs a scenario
   with same-text / different-URL nodes and asserts that the preview reports a change.

## Out of Scope

- Changes to the Confluence backend.
- Changing how the actual push (pass 2) works — it already handles link updates correctly.
- UI or CLI flag changes beyond adjusting the summary/entry rendering.
