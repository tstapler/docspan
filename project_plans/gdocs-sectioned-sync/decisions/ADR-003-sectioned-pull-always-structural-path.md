# ADR-003: Sectioned pull always uses the structural path, never Drive HTML export

## Status
Accepted

## Context
`GoogleDocsBackend.pull()` today has two distinct code paths (`backend.py`):
- **Default path** (`tab_id is None`, lines ~910-950): fetches the doc via Drive's HTML export and converts it with `DocumentConverter().html_to_markdown()`. `DocsStructureParser` is used here only throwaway, for anchor upgrading — its parsed node list is discarded, not what gets written to disk.
- **Structural path** (`tab_id is not None`, lines ~874-908): parses the doc into a flat `List[DocsParagraphNode]` via `DocsStructureParser`, projects it through `project()`, and writes the *parsed node list itself* (via `render_nodes_to_markdown()`) to disk.

Sectioned pull needs to partition the document at a configured heading level (`split_level`) into N separate markdown files. Drive's HTML export is an opaque, whole-document conversion — architecture research (`research/architecture.md`) confirmed it cannot be scoped to a heading range or otherwise handed a "start here, stop there" instruction. Only the structural path's flat node list is something `section_splitter.py` (a new module) can walk and cut at heading boundaries, because the split happens against the same node representation `render_nodes_to_markdown()` consumes.

## Decision
`backend.pull_sectioned()` always parses via `DocsStructureParser` + `project()` (the structural path), regardless of whether the mapping targets a tab or the default document — never via Drive HTML export, even though non-sectioned pull on the default (non-tab) path still uses HTML export today. Splitting happens after `project()` runs, not before, so each section's node list is already reduced to "what markdown can represent" form before being cut into groups — avoiding disagreement about residue handling at section boundaries (e.g. an empty paragraph immediately before a heading).

## Consequences
- Sectioned pull's output may have subtly different formatting-fidelity characteristics than a non-sectioned pull of the same document would have had via the default HTML-export path, since it's a genuinely different conversion pipeline (structural vs. HTML-export). This is judged acceptable because sectioned mode is opt-in and net-new — there is no existing sectioned-mode behavior to regress relative to.
- This makes the structural path (already used for tab-scoped docs) load-bearing for a second, independent reason (sectioning). Any future bug fix to the structural path's fidelity now has two justifications to preserve behavior for, and tests should cover both tab-scoped and sectioned-but-non-tab-scoped documents through it — two structural pull paths existing simultaneously (tab-scoping and sectioning) was explicitly flagged as a "Rabbit Hole" in requirements.md and needs a test matrix covering their interaction (a sectioned mapping targeting a specific tab).
- `render_nodes_to_markdown()`'s only stateful pass (`_group_code_runs`) was confirmed to be a local pass over the given list — safe to invoke once per section rather than once per document — so no shared cross-section state needs to be threaded through the splitter.
