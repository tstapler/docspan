# Pitfalls research: gdocs-sectioned-sync

Agent 4 (Pitfalls), SDD Phase 2. Scope: what commonly breaks in "N files ↔ 1
source of truth" sync systems, Google Docs API specifics, and what docspan's
own code already flags as risk in this area.

## 1. Prior art: "N files ↔ 1 canonical document" sync failure modes

Patterns that recur across Sphinx/MyST multi-file docs-from-one-source tools,
i18n translation-file sync (e.g. gettext `.po` splitting, Crowdin/Weblate file
sync), and per-table logical-replication-to-file exports:

- **Identity drift at the split boundary.** The moment a section is
  identified by *position* (its index among siblings) rather than a stable
  key, reordering two sections looks identical to deleting both and inserting
  two new ones. Every one of these tools eventually grows a stable id
  (slug, UUID, checksum-of-heading-text) precisely because position is not a
  durable identity. This project's manifest is the right instinct — the
  failure mode to design against is falling back to positional identity
  silently when the stable key is ambiguous (e.g., two sections with the same
  heading text after a rename).
- **Edit vs. delete+insert ambiguity is fundamentally undecidable from content
  alone** when a section is both renamed and heavily edited in the same pull
  cycle. Tools that get this wrong either (a) always treat heading-text change
  as delete+insert (loses in-body edit history/comments tied to the old
  section) or (b) always treat it as a rename-in-place (silently merges two
  logically different sections if a user deletes section A and independently
  writes new section B with A's old heading). The manifest must carry an
  identity separate from the heading text (docspan already does this for
  headings via `headingId` — see §2) so rename can be distinguished from
  delete+insert without guessing.
- **Partial-sync desync accumulates silently.** In gettext/i18n sync tools,
  the classic failure is: one side edits a fragment, sync runs, but a crash or
  network error leaves 8 of 12 files updated and 4 stale referencing an old
  manifest revision — and nothing detects this on the next run because each
  file's own mtime/hash still looks "clean" relative to itself. The fix these
  tools converge on is a **manifest-generation atomicity boundary**: the
  manifest and all section files must be written (or none) as a unit, e.g.
  write to a temp directory and rename, and the manifest's own hash/version
  must be checked against actual files before push/pull will proceed. Design
  this in from day one — retrofitting atomic multi-file writes after shipping
  a naive "write file 1, write file 2, ... write manifest" pull is the classic
  costly redesign.
- **Cross-file references are the recurring "we forgot this" bug.** Sphinx/MyST
  cross-file `:ref:` links, i18n placeholder cross-references, and DB
  logical-replication foreign keys all hit the same issue: a reference that
  was a same-document backlink becomes a cross-file reference once split, and
  round-tripping it requires the reassembly step to know about *every* other
  file's identity, not just its own. docspan already has exactly this
  primitive for single-file cross-doc links (`cross_doc_links.py`) and
  same-doc heading anchors (`heading_anchors.py`); sectioning turns every
  intra-doc anchor into what is architecturally a cross-file link between
  section files. This is explicitly called out as a rabbit hole in
  requirements.md and confirmed as real by the codebase (§2, §3).
- **Split-boundary granularity mismatches are a constant support burden.**
  Any tool that splits "at heading level N" has recurring bug reports for
  content that spans a boundary: a table or image that visually sits under
  heading level N+1 but whose closing fence/tag is emitted after the next
  level-N heading token; a list that continues across a page/file break.
  Requirements.md already flags this ("Images/tables/cross-section links
  spanning the split boundary") — the concrete risk is that
  `docs_structure_parser.py`/`nodes_to_markdown.py` operate on a flat node
  list with no boundary concept at all today, so "spanning" has to be defined
  as "which heading's subtree owns this node," and tables/images are
  currently opaque single nodes (`DocsTableNode`, `DocsImageNode`) that can't
  be sub-split — an image or table cannot itself straddle two sections, but a
  section boundary drawn mid-list or mid-table-continuation needs an explicit
  rule, not an assumption inherited from the flat renderer.

## 2. Google Docs API specifics

### Index invalidation is already solved for single-file push — and the existing fix depends on invariants sectioned push must not break

`docs_request_builder.py`'s `build()` (around
[docs_request_builder.py:1133-1195](../../../src/docspan/backends/google_docs/docs_request_builder.py))
already handles batchUpdate index shift for one file: requests are grouped by
anchor index and executed **highest-anchor-first, write-backwards** so each
edit runs against document coordinates nothing has shifted yet. The commit
history shows this was hard-won: a comment at line 1181-1187 documents a
regression (issue #42) where a flat sort-by-`startIndex` broke because an
`updateParagraphStyle` for a paragraph appended *after* an insert point
carried a higher `startIndex` than the `insertText` it depended on, so it
sorted (and ran) before the text existed. The rule that fixed it — "within a
tied anchor, non-insert groups go first, insert groups go last" — is a subtle
invariant that a sectioned reassembly must preserve or re-derive, not just
"do the same thing but bigger":

- A sectioned push spans multiple sections' worth of adds/deletes/reorders in
  one document. Reordering two sections is not expressible as one contiguous
  replace — it's (at minimum) a delete of both ranges and re-insertion in the
  new order, which is exactly the multi-anchor, multi-group case the existing
  `groups` sort was built for, just with far more groups spanning larger
  distances. The existing write-backwards discipline should still be
  correct in principle (it doesn't assume adjacency), but it has only ever
  been exercised on diffs generated by comparing one current-doc parse
  against one target-doc parse from the *same* file. Sectioned push
  synthesizes the "target" by concatenating N files back together — any bug
  in how that concatenation orders/labels nodes before diffing will silently
  produce a target with worse locality (e.g., every section becomes its own
  far-flung diff instead of local edits), which is a correctness-preserving
  but performance/complexity risk: `_bounded_opcodes`'s `DiffTooExpensive`
  guard (docs_request_builder.py:53-113, thresholds at lines 43-45) exists
  *because* difflib's SequenceMatcher has cubic-ish worst-case behavior on
  duplicate-heavy input — reassembling many sections increases the odds of
  hitting exactly this guard on documents that were previously fine
  unsectioned (e.g., many short section headers or repeated boilerplate
  across sections reads to the matcher like the duplicate-run case it already
  refuses).
- **Full-document rewrite vs. targeted diff is a stated constraint
  mismatch.** Requirements.md's own feasibility risk says push reassembly
  "must produce a coherent full-document batchUpdate rewrite, unlike today's
  targeted diff-based push." But `build()` is *already* a full-document diff
  (it diffs `current` vs `target`, the complete parsed doc, not a line-range
  patch) — the "targeted" part is that today's target always comes from
  literally the same document's file. The real new risk isn't
  targeted-vs-full-rewrite, it's that the *target* now has to be assembled by
  concatenating N independently-edited files plus a manifest describing their
  order, and any error in that concatenation (wrong order, dropped section,
  duplicated section from a merge conflict) is diffed and pushed with the
  same confidence as a correct one — there is no cross-check today that the
  concatenated target is itself internally consistent (e.g., that manifest
  order matches on-disk files 1:1) before it's handed to `build()`.

### Heading anchors do not survive a delete+reinsert with a new opaque id — confirmed in code

`heading_anchors.py`'s own docstring says the `headingId` that both directions
of anchor resolution depend on lives on the paragraph, assigned by Google
Docs itself, and is looked up **by set membership**, not by any predictable
shape (heading_anchors.py:1-30). Nothing in the codebase computes or assumes a
stable headingId across a delete-then-reinsert of that paragraph — the
existing scheme entirely depends on the *same* paragraph continuing to exist
(equal/restyle path) rather than being deleted and a new one inserted, because
a genuinely new paragraph gets a genuinely new, unpredictable id from the API.
Section reordering implemented as delete-full-section + reinsert-in-new-place
(the natural implementation of "detect reorder" in a full-document rewrite)
would delete and recreate every heading paragraph in the reordered sections,
which:

1. **Regenerates every headingId in a reordered section**, and
2. Per `tabs.py`'s `heading_ids_by_tab` docstring
   (tabs.py:38-55: "the link is lost from the Doc the moment a text edit
   makes pass 1 rewrite that paragraph"), any cross-section anchor link
   pointing at a heading in a reordered section becomes a dead link
   immediately, with no distinct signal from an ordinary broken link — same
   failure surface as the tab-scoped cross-tab case that's explicitly called
   out as a known unfixed gap in that file's docstring.

This means: **reorder must be implemented as much as possible via in-place
paragraph mutation (Google Docs supports moving text ranges without deleting
the paragraph object, if the request sequence is structured that way)
rather than delete+reinsert, specifically to keep headingIds stable** — or
the manifest/anchor-resolution logic needs an explicit re-resolution pass
after every push that walks the fresh doc and updates any manifest-stored
headingId references. Either way this needs to be decided at design time,
not discovered during implementation — it changes what "detecting a reorder"
is even allowed to compile down to in `docs_request_builder.py`.

### `DiffTooExpensive` is a hard refusal, not a fallback

The exception's docstring (docs_request_builder.py:53-72) is explicit that
this project already had, and rejected, a "fall back to a heuristic diff"
option after that path caused a real regression (headingId mispairing,
PR #50/#67). A sectioned push that hits this guard on a large multi-section
document has no softer failure mode available today — it's a hard error to
the user with no partial-progress path. Since sectioned docs are explicitly
the "large docs" use case this feature targets, this guard is *more* likely
to be exercised, not less, and the UX for hitting it (mid-push, after some
sections' worth of local edits) needs explicit design: today it's a pre-push
computation failure with no doc mutation attempted yet, which is actually the
safe case — but only if sectioned push preserves "diff everything before
writing anything," see §4.

## 3. What docspan's own code and comments already flag in this area

- **`docs_request_builder.py:1889-1896` and `:2001-2007`**: two-pass alignment
  (`_align_for_styling`) generates its own **second** residue set
  (`pass2_residue`, backend.py:467-503) specifically because a second,
  post-pass-1 parse can find state pass 1's alignment can't safely represent.
  The comment says this is "distinct from plan.residue... and reported the
  same way for the same reason" — i.e., docspan already has two separate
  residue-tracking mechanisms for one document because getting alignment
  right in a single pass proved impossible. Sectioned push's target
  reassembly is architecturally a *third* source of "things that don't align
  cleanly," and the existing pattern (never drop silently, always surface as
  a residue/warning) should extend to it rather than inventing a new
  swallow-the-error path.
- **`comments.py`**: the sidecar format embeds a Drive comment id as an HTML
  comment (`<!-- id:{comment_id} -->`) in one `.comments.md` file per
  document today. Requirements.md scopes "per-section comment sidecars" in;
  the existing format has no notion of which section a comment's anchor range
  falls in — Drive comments carry a `quotedFileContent` text snippet, not a
  structural node reference, so mapping a comment to "which section file does
  this belong to" requires matching quoted text against section content,
  which is exactly the same fuzzy-matching problem class the structural
  diff already has to solve (and already has a `DiffTooExpensive`-style
  refuse-rather-than-guess philosophy for). A comment whose quoted text
  matches content in two sections (e.g., a repeated boilerplate phrase) is
  the concrete failure case to test.
- **`image_source.py` / `mermaid_renderer.py`**: both explicitly document the
  "resolution failure becomes a push warning, never a crash" pattern
  (image_source.py:7, :81, :125, :139; mermaid_renderer.py:67). This is the
  established idiom for "something didn't fit the model" in this codebase —
  sectioned sync's own new failure classes (ambiguous rename, comment
  spanning sections, boundary-straddling table) should follow it rather than
  raising, to stay consistent with how push already reports partial problems
  without blocking the whole push.
- **`_bounded_opcodes`/`DiffTooExpensive` thresholds are tuned against
  today's fixture sizes** (comment at docs_request_builder.py:38-42: "tuned
  against this file's own test fixtures... so ordinary documents never trip
  the guard, while a document built from a few thousand duplicate short
  lines/cells does"). A large sectioned document reassembled into one target
  for diffing is exactly the kind of input these thresholds were *not*
  validated against — this should be re-measured with a realistic large
  multi-section fixture during implementation, not assumed safe by extension.

## 4. Race conditions / partial-failure states

- **Pull crash mid-write (N files ↔ manifest).** If pull writes section files
  1..8 of 12 and then crashes (network error fetching the doc mid-stream,
  disk full, process killed), the directory is left with a stale or
  half-written manifest and a mix of old/new section files with no marker of
  which is which. Today's single-file pull has no analogous partial state —
  it writes one file, so it's atomic by accident (assuming a single
  `write()` call, which should be verified in `backend.py`'s pull path —
  worth confirming whether it already writes to a temp path and renames, or
  writes in place). Sectioned pull must write to a temp directory and
  atomically swap (rename) rather than write section files in place one at a
  time, specifically because a partial pull silently corrupts the *next*
  push's "current" baseline — push would diff against a doc-side state that
  doesn't match any of the files the manifest claims to describe.
- **Push partial batchUpdate failure.** Google's `batchUpdate` is documented
  to apply requests atomically per call, but docspan may already be issuing
  more than one `batchUpdate` call per push (worth confirming in
  `client.py`/`backend.py` push path — grep found retry logic around doc
  fetch, not around batchUpdate submission specifically). If a sectioned push
  synthesizes a request list large enough to require chunking across
  multiple `batchUpdate` calls (e.g. due to a Docs API request-count or
  payload-size limit), a failure partway through leaves the live Google Doc
  in a state that matches neither the old content nor the new sections —
  and, per §2, may have already regenerated some headingIds via delete+insert
  before the failure. This is the scenario most likely to require a manual
  recovery story (re-pull and diff against local files) and should be
  designed against by keeping each push's request list within one
  `batchUpdate` call if at all possible, matching how single-file push
  works today.
- **Concurrent edit between pull and push (existing optimistic-concurrency
  window).** `Pass2Alignment`'s docstring
  (docs_request_builder.py:123-127) already notes "the recomputation sat
  inside pass 2's optimistic-concurrency window" — i.e., docspan is already
  aware that the live doc can change between when push reads it and when it
  writes, for single-file push. Sectioned push's window is necessarily wider
  (more sections to reconcile, more local files to read before diffing), so
  the existing optimistic-concurrency exposure gets proportionally larger,
  not new in kind.

## 5. Design-from-day-one recommendations (to avoid a costly redesign)

1. **Section identity must be a manifest-owned opaque key, never inferred
   from heading text or position** — otherwise rename-detection and
   reorder-detection are unsolvable by construction (§1, §2).
2. **Manifest + all section files must be written/updated as one atomic
   unit** (temp dir + rename) on both pull and push, to eliminate the
   partial-write desync class entirely rather than detecting it after the
   fact (§4).
3. **Decide up front whether reorder is implemented as in-place move or
   delete+reinsert** — this single decision determines whether headingIds
   (and therefore all cross-section anchors stored in the manifest) survive
   a reorder, and is expensive to change after `docs_request_builder.py`'s
   request-generation logic is built around one choice (§2).
4. **Reuse the existing residue/warning idiom, don't invent a second one**,
   for every new sectioning-specific ambiguity (ambiguous rename, comment
   spanning sections, boundary-straddling content) — the codebase has
   already converged on "surface, never silently drop or crash" and a
   parallel mechanism would be inconsistent and harder to reason about (§3).
5. **Re-validate `DiffTooExpensive`'s thresholds against a large
   multi-section reassembled fixture before shipping** — they were tuned for
   today's single-file fixture sizes and sectioning targets exactly the
   large-document case most likely to approach them (§2, §3).
