# Research: Feature Landscape — wedding-planning-workflow

Scope: (1) prior art for safely syncing a markdown copy of a live collaborative
doc, (2) edge cases in docspan's actual Google Docs backend given the real
wedding doc's structure, (3) Tyler's unstated needs.

All code references are to `/home/tstapler/Programming/markgate/src/docspan/`
(the `docspan` package; `docspan` in `/home/tstapler/Programming/docspan` is a
symlink to this same repo — markgate is the renamed docspan project per the
"CLI rename" commit `81d9c99`).

---

## 1. Prior art: safe sync of a live collaborative doc

**docspan already implements a git-like safety layer for the pull side** —
this is the closest "prior art" and it's already in-repo, not external:

- `src/docspan/core/orchestrator.py`: `orchestrate_pull` compares remote
  `revisionId` and local content hash against a stored `MappingState`
  (`src/docspan/core/state.py`) to classify each pull as `first-sync`,
  `fast-forward` (remote changed, local didn't — safe overwrite),
  `local-only` (local changed, remote didn't — pull is skipped, forces you to
  push first), or a genuine **three-way merge** when both sides changed.
- `src/docspan/core/merge.py`: `three_way_merge` uses the `merge3` package —
  the same algorithm as `git merge-file`/`diff3` — producing
  `<<<<<<< ours / ======= / >>>>>>> theirs` conflict markers directly in the
  markdown file, resolved later via `docspan conflicts resolve` (CLI in
  `src/docspan/cli/main.py`, `conflicts_app`).
- A content-addressed base store (`save_base_content`/`get_base_content` in
  `orchestrator.py`) keeps the merge-base text per sync cycle, keyed by
  sha256 — this is the same "keep a common ancestor around" trick git uses,
  and it already gives the tool the raw material for a cheap diff-since-last-
  sync (see §3).

This is architecturally the right pattern and matches what standalone
Obsidian↔Google-Docs bridges do **not** appear to do:

- `lupiter/obsidian-gdocs`, `iloveitaly/obsidian-google-docs`,
  `zxc3309/google-docs-obsidian-sync` (searched via WebSearch) sync folders/
  notes to/from Docs, but none document any 3-way-merge or conflict-marker
  behavior — they read as pull-clobbers-local or push-clobbers-remote tools,
  not merge-aware. None mention comment preservation at all.
- Generic "git-based note-taking" tools (git itself, Obsidian Git plugin)
  don't apply here directly because the remote isn't a text file — it's a
  live Google Doc with API-mediated structure (paragraphs, bullets, inline
  comments) that must be re-serialized through Docs' object model, not just
  diffed as text on the remote side.

**Key gap vs. this prior art:** docspan's merge protection is one-directional.
The three-way merge (`_merge_pull`) protects **Tyler's local edits** against
being clobbered by a fresh pull. But `push()` (`backend.py:49`) has no
equivalent safety for the **remote/collaborators**: it fetches the live doc
fresh at push time and does a blunt structural diff-and-replace (see §2) —
there is no git-style "merge into remote" step, no dry-run preview of the
actual batchUpdate requests, and (confirmed by search) no known open-source
Docs bridge does this well either — comment-anchor preservation across
programmatic edits is a known-fragile area of the Docs/Drive API itself
(Google issue tracker reports "Original content deleted" on anchored
comments even when comments are created correctly via the API). This is a
genuinely underserved problem, not something to expect off-the-shelf.

---

## 2. Edge cases and failure modes (grounded in code + the real doc structure)

### Checklists — currently cannot round-trip at all
- **Pull and push use two entirely different pipelines**, which is itself a
  risk: `pull()` exports HTML and runs it through `DocumentConverter.
  html_to_markdown` (markdownify + a custom regex/margin-based nested-list
  reconstructor, `converter.py:86-395`). `push()` instead fetches the live
  Docs JSON and runs a **structural diff**: `DocsStructureParser` (JSON) vs.
  `MarkdownToParagraphParser` (mistune AST) vs. `DocsRequestBuilder`
  (`backend.py:49-74`). Nothing guarantees these two paths treat the same
  content the same way.
- `DocsStructureParser._parse_paragraph` (`docs_structure_parser.py:107-110`)
  only reads `paragraph.bullet.nestingLevel` — it never reads or exposes
  checked/unchecked state, and Google's own bullet-preset enum has a real
  `BULLET_CHECKBOX` preset (confirmed via search) that this code never
  references anywhere in the repo (`grep -rn "glyphType|CHECKED|checklist|
  listProperties"` returns nothing outside Confluence).
- `MarkdownToParagraphParser` calls `mistune.create_markdown(renderer=None)`
  with **no plugins** (`markdown_to_paragraph_parser.py:91`), so mistune's
  task-list plugin is off. A line like `- [x] Whatsapp group` is parsed as an
  ordinary bullet whose literal text is `"[x] Whatsapp group"` — checked
  state is just text, not structure.
- `DocsRequestBuilder._text_key` keys equality on
  `(style, text, is_list_item)` (`docs_request_builder.py:18-20`), so
  toggling a checkbox (`[ ]`→`[x]`) is indistinguishable from any other text
  edit: it triggers a `replace` opcode → **delete the whole paragraph and
  reinsert it** (`docs_request_builder.py:72-80`), not an in-place update.
- Even if the code did detect checkbox state, `_make_insert_requests`
  (`docs_request_builder.py:115-155`) always calls `createParagraphBullets`
  with `bulletPreset: "BULLET_DISC_CIRCLE_SQUARE"` — hardcoded, never
  `BULLET_CHECKBOX`. **There is currently no code path that can create or
  preserve a real Google Docs checkbox on push**, regardless of markdown
  input. This confirms the risk flagged in requirements.md and is the single
  biggest blocker for "Checklist state round-trips correctly."
- Nested checklist sub-items (`  - [ ] Print permit for Thursday`) compound
  this: nesting comes from mistune's block nesting via `_walk_list_items`
  (`markdown_to_paragraph_parser.py:24-61`), which recurses correctly for
  plain nesting_level, but still has no checkbox awareness at any level.

### Comments anchored mid-paragraph — currently zero handling
- `grep -rn -i "comment" src/docspan/backends/google_docs/*.py` returns
  **nothing**. The Drive `comments` API (or Docs `comments` field) is never
  called — not on pull, not on push. There's no code that reads existing
  anchors, quoted text, or resolves/preserves them.
- The real exposure is push's `replace` opcode: any paragraph whose
  `(style, text, is_list_item)` changed gets `deleteContentRange` +
  `insertText` (`docs_request_builder.py:72-80`, `93-155`). A comment
  anchored to the word "inner" inside "gathering for dinner" lives in that
  paragraph's text range. If *anything* in that paragraph changes (even
  something unrelated to the commented span, e.g. Tyler edits a nearby
  checklist item's status word in the same paragraph, or reflows the
  sentence), the whole paragraph is deleted and a fresh one is inserted —
  which is very likely to orphan or silently drop the comment (Google's own
  issue tracker shows this exact "Original content deleted" failure mode
  even for comments created correctly). Only paragraphs classified as
  `equal` by `difflib.SequenceMatcher` (byte-for-byte identical
  style/text/is_list_item) are safe by omission — not by design.
- Practical implication for the workflow: any Claude-driven edit that
  touches a paragraph containing (or adjacent-merged into, if paragraphs get
  combined/split) an anchored comment is a silent comment-loss risk today.
  Verifying "comment-anchoring survives a pull→push cycle" (in-scope item 1)
  will very likely fail on the first real test against the live doc, given
  no protective code exists.

### Links to sub-docs and Sheets — push silently drops them
- This is a concrete, previously-unflagged gap: `_extract_text_from_token`
  (`markdown_to_paragraph_parser.py:9-21`) extracts only the **text** of a
  markdown "link" AST node's children — it never reads the link's `href`/
  `attrs`, and `MarkdownToParagraphParser` never populates `TextSpan.link`
  (spans are always left as the default empty list for parsed nodes).
- `DocsRequestBuilder._make_insert_requests` never calls
  `_make_text_style_requests` (which *does* know how to emit
  `updateTextStyle` with a `link` field, `docs_request_builder.py:174-210`)
  — it's dead code, defined but unused. So even if a node carried link
  data, push wouldn't write it.
- Net effect: **any paragraph containing a markdown link to one of the 4
  linked per-day sub-docs or 3 Sheets that gets re-inserted (any `replace`
  or `insert` opcode touching that paragraph) will land in the live Doc as
  plain text, with the hyperlink destroyed.** Same applies to bold/italic/
  monospace formatting on any inserted/replaced paragraph — spans are parsed
  by neither MarkdownToParagraphParser (link) nor emitted by the request
  builder's insert path (all styles) except paragraph-level style and
  list-item-ness. This is worth surfacing explicitly since the doc's link
  list is called out as content that must survive even though syncing the
  linked docs themselves is out of scope — the *links inside the main doc*
  are in scope by default (they're just text/markdown in the pulled file).

### Deeply nested lists (housing, numbered sub-lists)
- Pull-side reconstruction (`converter.py:_reconstruct_nested_lists`) is a
  heuristic: it groups consecutive `<ul>` blocks by `lst-kix_*` class prefix
  and infers level from `margin-left: Npt` via `max(0, (margin // 36) - 1)`
  when no class-encoded level is present (`converter.py:121-134`). This is
  fragile at deep nesting (4+ levels, as in housing room assignments) if
  Google's HTML export margins don't fall on a clean 36pt-per-level grid, or
  if numbered/ordered sub-lists under bullets export differently than plain
  `<ul>` (the code only looks for `<ul>`, not `<ol>` — grep confirms no `<ol>`
  handling anywhere in `converter.py`). **Numbered lists nested under
  bullets are a real gap**: nothing in the pull path distinguishes ordered
  from unordered, and push's `DocsStructureParser`/`DocsRequestBuilder` path
  has no ordered-list concept either (`is_list_item` is a boolean, no
  "ordered" field) — a numbered sub-list, if it round-trips at all, will
  become a bulleted one.
- Tabs are deliberately preserved (not converted to spaces) in pulled
  markdown "because Obsidian uses tabs for list indentation"
  (`converter.py:65-66`) — worth verifying mistune (used on the push side)
  parses tab-indented nested lists the same way GitHub/Obsidian-flavored
  markdown does; tab-width interpretation differences between markdownify's
  output and mistune's input parsing are a plausible source of nesting-level
  drift across a pull→edit→push cycle.

### Tables / sectionBreak / tableOfContents
- `docs_structure_parser.py:64` explicitly comments "table, sectionBreak,
  tableOfContents are silently skipped." Since both pull's HTML path and
  push's JSON path independently drop these, a table already in the doc is
  likely stable (invisible on both sides, so no accidental delete) — but it
  means **Tyler's local markdown and Claude's summary of it are blind to any
  table content**, and if a collaborator adds a table between sync cycles,
  it silently won't show up in what Claude sees or reasons about at all
  (confirmed out-of-scope by requirements, but worth flagging as a blind
  spot rather than a safe no-op, since "no error" reads as "nothing to worry
  about" when it's actually "invisible content exists").

---

## 3. Tyler's unstated needs

- **Owner/due-date extraction must be a side channel, not written back into
  the doc.** The real doc uses informal sub-headers ("Bekah:", "Tyler:",
  "Ann:") and section groupings ("## Due Wednesday (7/29)") rather than
  structured fields. Tyler almost certainly wants Claude to *parse* these
  conventions for a summary view, but must **not** push structured metadata
  (owner tags, due-date frontmatter, etc.) back into the shared doc — that
  would show up as visible clutter to Nora/Bekah/Ann on their next open, and
  any such addition is exactly the kind of paragraph-level text change that
  (per §2) risks nuking a nearby comment anchor or reformatting a checklist
  line. The owner/due-date view should live in Claude's own summary output
  (chat response or a separate local file), never in the pushed markdown.

- **A diff/changelog between sync cycles is nearly free to build and is
  implied by the stated workflow.** The `pull→Claude-summarize→edit→
  pull-again→dry-run→push` cycle in scope item 2 only makes sense
  efficiently if Claude isn't re-reading the entire multi-page doc every
  time. The infrastructure already exists: `SyncState.base_hash` plus
  `get_base_content` (`orchestrator.py:50-68`) retain the pre-sync content.
  A `docspan diff` (or equivalent skill step) that diffs current pulled
  content against the last recorded base would give exactly the "what
  changed since I last looked" view Tyler needs — and would let Claude
  summarize only the delta instead of the whole doc, which is also the
  answer to "avoid re-summarizing unchanged content each time." Nothing
  today surfaces this; it needs to be added (small — the raw materials are
  already in `SyncState`).

- **`--dry-run` currently does nothing useful and won't satisfy the stated
  workflow.** `src/docspan/cli/main.py:107-111` (push) and `:163-167`
  (pull) — dry-run just prints a static one-line "would sync X → Y" without
  computing any diff, request list, or risk signal. Given the explicit
  workflow step "pull-again→dry-run→push" and the success metric "zero
  collaborator edits/comments lost," Tyler's unstated but clearly implied
  need is a dry-run that actually shows the `DocsRequestBuilder` output (or
  a human-readable rendering of it) — which paragraphs would be
  deleted/reinserted, and ideally flags any paragraph that overlaps a known
  comment anchor (would require adding comments-API calls, see §2) or
  contains a checklist item, before he commits to a real push. The current
  dry-run gives no basis for that judgment call.

- **A pre-push safety check against comments specifically is implied by the
  explicit "verify comment-anchoring survives" requirement**, but nothing in
  scope item 1 says *how*. Given comments aren't readable at all today (§2),
  an unstated minimum viable version is: before push, call Drive's
  `comments.list` for the doc, cross-reference anchor ranges against any
  paragraph the request builder is about to delete/replace, and warn (or
  refuse) rather than silently proceeding — this is likely the actual
  deliverable behind "verify/fix comment-anchoring," not just a manual
  eyeball test.

---

## Sources (WebSearch)
- [NestingLevel (Google Docs API v1)](https://developers.google.com/resources/api-libraries/documentation/docs/v1/java/latest/com/google/api/services/docs/v1/model/NestingLevel.html) — confirms `BULLET_CHECKBOX` preset exists and glyph semantics for checkboxes.
- [REST Resource: documents | Google Docs API](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents)
- [obsidian-gdocs (lupiter)](https://github.com/lupiter/obsidian-gdocs), [obsidian-google-docs (iloveitaly)](https://github.com/iloveitaly/obsidian-google-docs), [google-docs-obsidian-sync (zxc3309)](https://github.com/zxc3309/google-docs-obsidian-sync) — surveyed prior art, none document comment-preservation or 3-way merge behavior.
- [Insert, delete, and move text | Google Docs API](https://developers.google.com/workspace/docs/api/how-tos/move-text) — descending-index batch update ordering (docspan already does this correctly).
- [Anchored comments with Drive API not working](https://issuetracker.google.com/issues/357985444), [drive comments create: anchor field results in 'Original content deleted'](https://github.com/googleworkspace/cli/issues/169), [comment anchor property issue](https://issuetracker.google.com/issues/292610078) — confirms comment-anchor fragility is a known, unresolved Google API issue, not just a docspan gap.
