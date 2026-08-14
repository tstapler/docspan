# Feature Landscape Research: gdocs-sectioned-sync

Agent 2 (Features) — SDD Phase 2

## 1. Existing docspan patterns this feature must extend

### Three-way merge (`src/docspan/core/merge.py`, `src/docspan/core/orchestrator.py`)

`three_way_merge()` (`merge.py:14`) is a thin wrapper around the `merge3` package operating on
**whole-file text**, line-by-line: `Merge3(base_lines, ours_lines, theirs_lines).merge_lines(...)`,
with conflicts marked by `<<<<<<< ours` / `=======` / `>>>>>>> theirs` and counted by scanning
for the marker line. It has no concept of structure — it doesn't know about headings, sections,
or nodes, only text lines.

`orchestrate_pull()` (`orchestrator.py:204`) drives the decision tree per mapping, keyed off two
booleans (`remote_changed`, `local_changed`) computed by comparing hashes against the last
recorded `MappingState`:
- neither changed → `up-to-date`
- remote only → `_fast_forward_pull` (just overwrite local)
- local only → `local-only` (no-op, local wins by inaction)
- both changed → `_merge_pull` (`orchestrator.py:303`): writes local content to a `.orig` sidecar,
  pulls remote into a temp file, three-way-merges `base/theirs/local`, writes the merged result,
  records new state.

State is one `MappingState` per `local_path` (`local_hash`, `base_hash`, `remote_version`,
`doc_id`) in a single `SyncState`, persisted to one state file per config
(`get_state_path`/`STATE_FILENAME`). The merge base is content-addressed
(`save_base_content`/`get_base_content`, sha256-keyed files under `BASE_STORE_DIR`) — reusable
as-is per section file, since it's already keyed by content hash, not by mapping identity.

**Implication for sectioned mode**: this entire machinery is 1:1 (one local path ↔ one remote
doc ↔ one state entry ↔ one base blob). Sectioned mode turns this into N:1. The design must
decide whether each section file gets its own `MappingState` entry (reusing this code almost
unchanged, keyed by a synthetic per-section local path) or whether a new state shape is needed.
Reusing per-section `MappingState` entries, keyed by section file path, appears to let
`three_way_merge` and the base-store keep working per-file with no changes — the new work is
purely in how sections are split out of / reassembled into the single remote document's node
stream, not in the merge/state layer itself.

### Comment sidecar (`src/docspan/backends/google_docs/comments.py`)

Pure functions: `format_comments_markdown(title, comments)` renders Drive `comments.list` results
into a single `{doc}.comments.md` file, grouped into `## Open` / `## Resolved`, each comment
tagged with `<!-- id:{comment_id} -->` and (if unresolved) an editable `Reply:` / `Resolve:`
directive block. `parse_reply_directives(markdown_text)` splits the sidecar back into per-comment
blocks on the id marker and extracts `ReplyDirective(comment_id, reply, resolve)` for
`docspan comments respond` to act on.

**Implication**: comments from the Drive API don't carry a heading/section reference — only an
anchor into the document's content range (quoted text). To split one `.comments.md` into
per-section sidecars, the split must map each comment's `quotedFileContent`/anchor position to
the section whose content range contains it — likely via the same node-index boundaries used
for the markdown split. This is a many-to-one lookup problem, not a rewrite of the
render/parse functions themselves: `format_comments_markdown` and `parse_reply_directives`
can probably run unchanged, just invoked once per section with a pre-filtered comment list. The
open risk is a comment anchored to text that spans a section boundary (rare but possible) or a
comment on a heading itself (which section does it belong to — the one it starts, or the one
above?).

### Structural parser and stable heading identity (`docs_structure_parser.py`, `heading_anchors.py`)

`DocsStructureParser` (`docs_structure_parser.py:278`) already parses a fetched Google Doc into a
flat list of typed nodes — `DocsParagraphNode`, `DocsTableNode`, `DocsImageNode` — and critically,
`DocsParagraphNode.heading_id` (`docs_structure_parser.py:188`) carries the Docs API's own
`paragraphStyle.headingId`, present on every heading paragraph and stable across edits to a
heading's *text* (renaming a heading doesn't change its Docs-assigned id, only deleting and
re-adding it does).

`heading_anchors.py` separately provides `slugify()` — a github-slugger-compatible heading→slug
function used today for markdown anchor links (`[text](#slug)`), including duplicate-heading
disambiguation.

**This is the single most load-bearing finding for the design**: docspan already has both a
stable, Docs-native section identity (`heading_id`) and a human-readable, collision-safe naming
scheme (`slugify`) sitting one layer below the markdown conversion. A sectioned pull can walk the
parsed node list, cut at each heading paragraph whose level matches the configured split level,
and key each resulting section by its `heading_id` (for the manifest / add-delete-reorder
detection) while naming the file from `slugify(heading_text)` (with the existing duplicate-suffix
logic already handling collisions). This avoids inventing a second identity scheme — the manifest
"mechanism TBD" in the requirements can very likely just be a `heading_id → filename` map plus
an ordered list, keyed off machinery that already exists in the codebase.

Caveat: `heading_id` is assigned by Docs itself and is only stable as long as the paragraph isn't
deleted/reinserted. Docs also doesn't expose `headingId` on the HTML-export pull path the same
way the tab-scoped structural-parser path does (see `heading_anchors.py`'s own docstring: "Which
of `heading`/`headingId` a *read* returns depends on `includeTabsContent`... two existing pull
paths" — this is exactly the "Tab-scoped docs vs default HTML-export path" rabbit hole called out
in requirements.md). The default (non-tab) pull goes through Drive's HTML export and does *not*
go through `DocsStructureParser`/`heading_id` at all today — sectioned mode may force all
sectioned mappings onto the structural/tabs-aware path, which is itself a compatibility
consideration worth flagging to the architecture agent.

### Push is targeted-diff, not whole-document rewrite (`backend.py:377` `push()`)

`push()` builds a `PushPlan` by parsing the **live remote doc** into nodes, parsing **local
markdown** into the same node shape, diffing them, and emitting only the `batchUpdate` requests
needed to reconcile — a two-pass process (pass 1: text/structure diff; pass 2: table fills and
inline styling that need post-insert indices). It gates on `HighRiskParagraph` (open comment or
checkbox glyph) unless `force=True`, and re-checks comment counts after writing
(`CommentCountBackstop`).

**Implication**: this de-risks one of the stated "Feasibility Risks" ("push reassembly must
produce a coherent batchUpdate rewrite; today's push is targeted-diff-based"). Because the diff
is already computed over the *whole node stream*, not per-paragraph-in-place, reassembling N
section files into one concatenated markdown string (in manifest order) and feeding that through
the existing `push()` unchanged should Just Work for reordering and insertion/deletion of whole
sections — the diff engine doesn't care that the concatenated markdown came from one file or many;
it only sees the final merged text. The new work is entirely upstream of `push()`: producing the
correct concatenated markdown from the section directory + manifest, not inside the diff/request
builder. This significantly shrinks the risk surface for the "push" half of this feature.

## 2. Prior art: split-one-doc-into-many, git-based document collaboration

- **Sphinx/MkDocs multi-page docs**: identity is the *file path itself* (e.g. `docs/foo.md`) —
  there is no separate manifest; the table of contents (`toctree`/`nav:`) is the ordering
  mechanism and lives in a *different* file from the content, edited by hand. Add/delete/reorder
  is "add/remove/reorder a line in the toc file," which is trivially diffable in git. This is the
  cheapest possible model but has no equivalent of "one doc split by heading level" — pages are
  authored as separate files from the start, they're never *derived* from a single source that
  also needs to be reconstructed. Relevant lesson: separating "ordering intent" (a manifest/toc)
  from "section identity" (the file itself) keeps both diffable independently in git, which
  matters for the "git diff a single section cleanly" unstated need below.

- **Obsidian vault sync / Notion export-to-markdown tools**: Notion's page hierarchy already has
  stable page IDs, so its markdown/HTML exporters preserve identity via a file-per-block-id or
  file-per-page-id naming convention (often `Title <32-hex-id>.md`). The general pattern these
  tools converge on: **never rely on the filename or title alone as identity** — titles get
  renamed and collide; embed an opaque stable id (in the filename, frontmatter, or a sidecar) and
  treat the human-readable name as a derived, best-effort display label that can be
  regenerated on every pull without breaking identity. docspan's `heading_id` is exactly this
  kind of opaque stable id already.

- **git-based document collaboration (e.g. Beorg/Logseq block refs, Pandoc `--split-level`)**:
  Pandoc's own `--split-level` (used for EPUB generation) is the closest direct analogue —
  splits a single markdown source into N files at a given heading depth, N files back into one
  document on rebuild. It has no notion of an external system of record to reconcile against
  (it's one-directional, markdown → split files → single output), so it doesn't have to solve
  add/delete/reorder-detection *against a moving remote* — docspan's problem is strictly harder
  because the Google Doc itself can change independently of the split files (someone edits the
  doc directly in the Docs UI between pulls).

- **Confluence page-tree sync tools** (e.g. `md2conf`, various markdown→Confluence CLIs): these
  model page hierarchy as 1 markdown file : 1 Confluence page, with a frontmatter field carrying
  the Confluence page id — i.e., they sidestep the "one doc, N sections" problem entirely by
  making each section a *real, separate remote document* with its own id and version. This is
  explicitly out of scope here (requirements rule out anything resembling multiple Docs API
  documents), but it's worth naming as a design alternative that was likely considered and
  rejected implicitly by "the Google Doc" (singular) framing in the requirements — worth
  confirming with the user/PM in planning that a single Doc, not a Docs-per-section tree, is a
  hard constraint and not just the default assumption.

- **Common failure the above tools all had to solve**: a section renamed *and* edited in the same
  cycle looks identical, from a naive diff's perspective, to "old section deleted, new section
  inserted" — this is the classic ambiguity, and every tool above resolves it the same way: keep
  an *opaque* id that survives a text-only rename (Notion's block id, Pandoc doesn't need to
  because it's one-directional, Confluence tools use the page id). docspan's `heading_id` gives
  the same guarantee for a rename that doesn't delete-and-reinsert the paragraph, but offers no
  signal at all for a heading that a user deletes and immediately retypes (which Docs treats as a
  brand-new paragraph with a brand-new `heading_id`) — that case is indistinguishable from a
  genuine delete+insert without a secondary heuristic (e.g. content-similarity fallback), and
  should be named explicitly as an accepted limitation rather than solved outright, given the
  "Large" appetite and the rabbit-hole warning already in requirements.md.

## 3. Edge cases and failure modes to handle explicitly

1. **Section deleted remotely, local file has uncommitted edits.** Direct analogue of today's
   `local-only` outcome in `orchestrate_pull`, but at section granularity — the current
   3-way flow has no path for "the base disappeared." Needs an explicit decision: keep the local
   file as an orphan (flagged in the manifest, not pushed until user resolves) vs. silently drop
   it. Given "Must not change behavior for existing non-sectioned mappings" and the general
   docspan philosophy (local-only is a safe no-op, never destructive), orphaning-with-a-flag is
   more consistent with existing behavior than silent deletion.

2. **Two sections merged into one heading** (someone deletes a heading boundary in the Doc,
   merging what were two sections' content under one remaining heading). From the split side this
   reads as "one heading_id survived, one disappeared, and its body content is now inside the
   survivor's range" — needs to be distinguishable from "a section was deleted and its content
   discarded." A content-presence check (is the deleted section's text still present, just
   relocated?) is the natural heuristic, mirroring how the "rename vs delete+insert" ambiguity
   above gets an accepted-limitation treatment rather than a guaranteed-correct algorithm.

3. **Section moved to a different heading level** (H2 becomes H3, or vice versa) — changes
   whether it's a splittable unit at all under the configured split level. A promoted subsection
   (H3→H2) should probably become a *new* top-level section file; a demoted one (H2→H3) should
   fold into its new parent section. Both are legal outcomes of ordinary editing and shouldn't be
   treated as errors, just handled as structural changes the manifest diff reports.

4. **Concurrent pulls/pushes** — today's state file is a single JSON blob per config
   (`SyncState`/`STATE_FILENAME`) with no locking visible in `orchestrator.py`; a second `docspan
   pull`/`push` invocation mid-run already has a race in the non-sectioned case. Sectioned mode
   doesn't need to solve this from scratch, but it does raise the stakes: a manifest write that
   races with a concurrent section-file write is more likely to corrupt cross-file consistency
   (wrong section order, orphaned files) than a single-file race would. Worth flagging to the
   architecture agent as "no worse than today, but the blast radius per race is larger."

5. **Non-heading content before the first heading** (title, abstract, doc-level metadata) —
   every doc that isn't authored as "starts immediately with an H2" has a preamble. The split
   needs an explicit "section 0" / front-matter file convention, or documents that don't already
   put a heading first will silently lose their preamble on first sectioned pull.

6. **Images/tables/cross-section links spanning a split boundary** (named explicitly in
   requirements.md as a rabbit hole) — `heading_anchors.py`'s cross-references
   (`[A1](#a1-current-state)`) already resolve within a single document body; splitting into
   files means an anchor link from section B to a heading in section A needs either (a) a
   cross-file markdown link convention docspan invents, or (b) staying encoded as a Docs
   `headingId` link that's opaque to local editing but still round-trips correctly through push
   (since push re-parses whatever markdown link syntax is on disk). Given the "reuse the existing
   structural parser" constraint, leaning on the existing anchor-link resolution machinery
   (`heading_anchors.py`) rather than building a new cross-file link syntax is the
   lower-risk choice — but it means an agent editing one section file in isolation cannot always
   tell what a `#slug` link resolves to without other files being present, undercutting the
   "edit one file without understanding the manifest" goal below.

## 4. Unstated needs beyond the explicit requirements

- **Partial pull of a single section** without fetching/parsing the whole doc first. The
  requirements only specify "pull produces a directory of files" and say nothing about being
  able to refresh *one* section's file cheaply. Given the stated motivation (an agent that "only
  needs one section" shouldn't load the whole doc), a `docspan pull <mapping> --section <name>`
  (or per-file granularity) that still round-trips remote version tracking for that one section
  is a natural extension of the goal that isn't captured by "success metrics" as written — those
  only describe whole-mapping pull/push, not partial ones. Worth surfacing to the planning agent
  as a candidate scope addition (or explicit non-goal) rather than leaving it implicit.

- **Clean per-section git diffs.** A human reviewer's real workflow is `git diff` on a PR that
  touches one section. This is naturally satisfied *if* the split is stable (same section always
  lands in the same file, in the same position in the directory listing) — but only if renames
  don't cause spurious full-file rewrites. Whatever naming scheme is chosen (slug-based
  filenames) needs to keep a section's filename stable across content edits that don't touch the
  heading text — i.e. identity (heading_id) and naming (slug) must be allowed to diverge without
  forcing a rename+recreate in git history every time the manifest is regenerated.

- **An agent editing one file shouldn't need to understand the manifest format.** The manifest
  is machinery for docspan's own push-time reconciliation, not something a Claude Code session
  editing "the intro section" should need to read or reason about. This argues for keeping the
  manifest in a single well-known location (e.g. `.docspan-sections.yaml` alongside the
  directory) that push consults but that editing workflows never need to touch — consistent with
  how `comments.py`'s Reply:/Resolve: directives are designed to be editable by a human/agent
  without understanding Drive comment IDs or JSON shape.

- **Discoverability of "this mapping is sectioned."** An agent or human opening the target
  directory for the first time (without having read markgate.yaml) needs some signal that this
  isn't an ordinary set of independent markdown files but a synced group — otherwise a section
  file added by hand (not through `docspan pull`) has undefined behavior on next push. A
  README-like convention or a lint/warning in `docspan status` for "untracked file inside a
  sectioned mapping directory" is worth naming as a UX gap even though requirements.md doesn't
  ask for it.

## Key files referenced

- `src/docspan/core/merge.py` — three-way merge (line-based, whole-file)
- `src/docspan/core/orchestrator.py` — pull/push decision tree and state recording (`orchestrator.py:204`, `:303`)
- `src/docspan/backends/google_docs/comments.py` — comment sidecar render/parse
- `src/docspan/backends/google_docs/docs_structure_parser.py` — node model, `DocsParagraphNode.heading_id` (`:188`)
- `src/docspan/backends/google_docs/heading_anchors.py` — `slugify()`, anchor resolution, tab-scoped vs HTML-export path caveat
- `src/docspan/backends/google_docs/backend.py` — `push()` targeted-diff pipeline (`:377`)
- `src/docspan/config.py` — `Mapping` model (`:78`), candidate location for a new `sectioned`/`split_level` field
