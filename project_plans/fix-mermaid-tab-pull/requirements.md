# Requirements: Fix Mermaid Fence Collapse on Tab-Scoped Structural Pull

## Problem Statement

A mapping configured with `tab_id` + `pull_strategy: structural` whose local markdown file
contains two mermaid fences — and a committed `.mermaid-cache.yaml` sidecar holding source for
at least one diagram — produces `![]()` for both fences after a pull. The same symptom was
reported and nominally closed as issue #140 (fixed in commit `6253c77`, v0.8.3). The symptom
recurs, indicating the fix was incomplete or is not reached under some conditions.

A subsequent push then writes the empty image links back to the Doc, blanking the rendered
diagrams permanently.

## Scope

This work covers the structural pull path only (triggered when `tab_id` is set OR
`pull_strategy: structural` is configured). The default Drive-HTML-export path
(`recover_pulled_images`) has a parallel but distinct implementation and is out of scope.

## Functional Requirements

### FR-1: Sidecar recovery must fire during merge-path pulls

When `_merge_pull()` in `orchestrator.py` calls `backend.pull()` with a temp file path,
the sidecar lookup must still consult `{mapping.local}.mermaid-cache.yaml` (the committed
sidecar next to the real output file), not `{tmp_path}.mermaid-cache.yaml` (which never exists).

### FR-2: Sidecar recovery must fire when contentUri fetch fails

If the HTTP fetch of a `contentUri` fails (expired URL, transient network error), the sidecar
must still be consulted using an alternative lookup key that doesn't require the image bytes.
The current implementation gates all three recovery tiers behind `if png_bytes is not None:`,
making the sidecar useless when the fetch fails.

### FR-3: Empty `node.src` must not silently break the recovery count guard

When `node.src` is empty string (the `![]()` output case), the regex
`r"!\[([^\]]*)\]\(([^)]+)\)"` in `recover_structural_mermaid_images` cannot match the empty
URL. The resulting `len(matches) != len(image_nodes)` mismatch triggers an all-or-nothing
early return, leaving the broken markdown unchanged even for images whose src is non-empty.
Recovery must degrade per-image, not abort globally.

### FR-4: Warn loudly when a mermaid fence would be replaced by an empty image

When a pull on the structural path would replace a `\`\`\`mermaid` fence (present in the
current local file) with an `![]()` or otherwise unrecoverable image link, emit a warning
visible at the CLI level (not just in DEBUG logs) before writing the result.

## Non-Requirements

- Changing the sidecar file format or key scheme (maintain backward compatibility)
- Fixing the default (Drive HTML export) pull path — it uses a separate code path
- Addressing image alt-text loss (a known, pre-existing gap in the Docs API)
- Fixing `preview_pull()` — same sidecar-path issue exists there, but preview never writes
  files so the blast radius is cosmetic only

## Acceptance Criteria

1. A pull on a tab-scoped structural mapping (merge scenario) restores mermaid fences from
   the committed sidecar even when the temp-path trick would normally prevent the lookup.
2. A pull where the contentUri HTTP fetch fails still restores mermaid fences from the sidecar
   for any diagram whose source is recorded there.
3. A pull that cannot restore a mermaid fence that was present in the previous pull result
   emits a `WARNING`-level log message naming the affected file and the number of lost fences.
4. All existing tests pass; new regression tests cover each of the three fix scenarios above.
