# Pitfalls & Risks: gdocs-native-blockquotes

Sources: `docs/backends/google-docs.md` Limitations section; `git log` on
`docs_request_builder.py`/`docs_structure_parser.py`; commits 6205850, 052b64d,
e6db797, 31b4edd, cf36561, 8203c93.

## 1. Existing limitations that compound with this change

- **Table cells hold one paragraph** (`docs/backends/google-docs.md:61`) — a
  blockquote inside a table cell now needs both the single-paragraph
  constraint *and* border/indent styling applied on the "second pass" the doc
  already describes for inline formatting. If a blockquote is the *first*
  push into a newly created table cell, expect the same "styling lands on the
  next push, not this one" gap already documented for bold/links/anchors —
  but now silently invisible (a missing border reads as "not a blockquote"
  rather than "not bold yet", which is more likely to be reported as a bug).
  Also: `e6db797` shows Docs auto-inherits a *heading's* `namedStyleType` into
  a fresh table cell paragraph and docspan has to force `NORMAL_TEXT` on
  every cell fill. The same inheritance risk plausibly applies to
  `indentStart`/`borderLeft` — a cell adjacent to a blockquote could inherit
  its border/indent unless the fill path explicitly clears those fields too,
  not just `namedStyleType`. Needs verification against a live Doc.

- **Comments destroyed on delete+reinsert** — directly load-bearing here. Any
  already-pushed literal-`>` paragraph whose text changes on first push after
  this ships will be deleted and reinserted (per the requirements doc's own
  Risk Control section), losing anchored comments. `052b64d`'s
  "delete-and-reinsert churn" detector in `push_preview.py` should already
  surface this in `--dry-run`, but it currently only pairs churn within the
  *same opcode/edit_group* — a legacy blockquote whose restyle is expressed
  as a separate insert/delete pair (plausible here, since it's a text change,
  not a pure restyle) needs to be confirmed as still classified as churn, not
  silently reported as an ordinary remove.

- **Rate limiting (300 req/min)** — not directly a new limitations-doc
  interaction, but see §3 below: a full-repo re-push of many legacy
  blockquotes generates one delete + one insert (2 requests) per paragraph
  where today it's typically a no-op or single restyle request, roughly
  doubling requests on documents with many old-style quotes on their first
  post-migration push.

- **Blockquotes limitation entry itself** must be rewritten, not just
  softened — per requirements scope, but note the doc still needs a caveat
  for the *legacy* literal-`>` case, since old docs won't get borders until
  next edited.

## 2. Failure patterns from past diffing-engine fixes (docs_request_builder.py / docs_structure_parser.py)

`git log --oneline` on these two files shows ~35 fix commits, almost all in
one of three recurring shapes — all directly relevant to adding
`is_blockquote`/`quote_depth` as new identity-adjacent fields:

- **New paragraph metadata not folded into `_node_key` causes cross-type
  misalignment** (`6205850`, issue #54/#67 — closest precedent to this
  project). `render_prefix` was invisible to `_node_key`/`_content_key`, so a
  code-rendered paragraph and a plain paragraph with identical text got the
  same key and `SequenceMatcher` paired them across the boundary — corrupting
  a live heading's `headingId` in one reproduction. The requirements doc
  already prescribes the same fix shape for `is_blockquote`/`quote_depth`
  (in `_node_key` only, not `_content_key`) — but the docstring in
  `docs_request_builder.py:225-301` warns this is subtle: `_content_key`
  intentionally stays *blind* to `render_prefix` so `_repair` can still fold
  an unchanged code line back to "equal" against its prefix-less target.
  The same tension applies to blockquote depth: if depth is genuinely
  unchanged, `_content_key` must still say "equal" so a pure `is_blockquote`
  restyle doesn't get promoted to a delete+reinsert (which would defeat the
  entire comment-preservation point of this project). Get this ordering
  wrong and the new feature actively worsens the exact limitation it's
  trying to avoid regressing.

- **`_repair`'s global content-key pooling (PR #70, `8203c93`) can let an
  unrelated node win a slot on a coincidental match.** Since `_content_key`
  pools matches *across the whole document*, not just within one
  `replace` run, a blockquote paragraph and a non-blockquote paragraph
  with identical text (e.g. a quoted sentence that also appears verbatim
  outside a quote) become new candidates for cross-pairing once
  `_content_key` for paragraphs stays text-only per the requirements'
  own design. `_structural_score`'s style comparison is what's relied on to
  prevent this — confirm it also considers `is_blockquote`/`quote_depth` (or
  the border/indent style fields) as a scoring signal, or a body paragraph
  could be misclassified as a restyle target for a same-text blockquote and
  gain a border it shouldn't have.

- **Multi-commit whack-a-mole on one feature is the norm, not the
  exception** (`31b4edd`'s render-glyph fix took 12 follow-up commits over
  the PR: whitespace-stripping fix, newline-double-count fix,
  doc_end_index-clamp comment correction, ambiguous-prefix warning,
  pull-path residue leak). Expect the same shape here: indent/border writes
  interacting with `_delete_bounds`'s trim logic, replace-branch newline
  insertion, and pass-2 style alignment (`cf36561`) are all separate code
  paths that each need their own check for the new fields, not one
  central fix.

- **`_align_for_styling` must parse through `projection.project()`, not
  raw** (`cf36561`, issue #53) — if blockquote detection/marker stripping is
  added as a projection-layer step (analogous to the render-glyph strip),
  any consumer of the re-fetched live doc that still parses raw (there was
  exactly one such consumer, `backend.preview_push`, called out as a
  deliberate exception) needs to be re-audited so it isn't silently broken
  by the new field the same way pass-2 span alignment was.

## 3. Google Docs API risks

- **`ParagraphBorder` full-resend confirmed independently**: per the
  Google Docs API reference for `ParagraphStyle.borderLeft`
  (https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#ParagraphBorder),
  there is no partial-merge semantics documented for border sub-fields the
  way there is for scalar `ParagraphStyle` fields via `fields` masks generally
  — the object itself has no per-subfield mask; the whole `ParagraphBorder`
  (color, width, dashStyle, padding) is one field entry in the mask
  (`borderLeft`) and must be supplied whole on any write that includes it.
  Omitting a sub-field of `ParagraphBorder` in the request most likely
  resolves to Docs' hidden defaults for that sub-field, not "leave unset" —
  this is the requirements doc's own stated assumption (Constraints section)
  and could not be independently confirmed beyond documentation reading in
  this pass; **the requirements doc itself flags this as needing a live-Doc
  spike, not just docs-reading, which is correct and still open.**

- **Payload size**: a `borderLeft` object (nested color/RgbColor, width,
  dashStyle, padding) is roughly 5-8x the JSON size of the current
  `namedStyleType`-only `updateParagraphStyle` request. Google's Docs API
  rate limit is expressed in *requests/minute*, not payload bytes, so this
  is not expected to trip the 300 req/min limit any faster per se — but if
  Google enforces an undocumented per-`batchUpdate` payload cap (common
  pattern across Workspace APIs, not confirmed here for Docs specifically),
  documents with very many quoted paragraphs re-pushed at once could hit it
  sooner than today. Not verified against a real large document in this
  research pass — flag as an open risk for the Phase 5 spike alongside the
  border-coalescing check the requirements doc already schedules there.

- **Re-pushing many legacy blockquotes at once**: because every legacy
  blockquote's *first* post-migration push is a delete+reinsert (2 requests
  instead of a would-be 1 restyle request), a document with N legacy quotes
  roughly doubles the paragraph-touching request count for that one push
  only. Combined with `batchUpdate` being atomic (per `31b4edd`'s finding —
  a single rejected request fails the *entire* batch), a document with many
  legacy blockquotes is more exposed than average to a single bad border
  value or size limit aborting the whole push with nothing written.

## 4. Pull-direction detection risks

- **Border coalescing / normalization** is explicitly still an open question
  in the requirements doc itself (Open Questions, Rabbit Holes) — this
  research did not find any existing precedent in this codebase for
  detecting/grouping by border, since no border-based feature exists yet
  (`grep` for `borderLeft`/`ParagraphBorder`/`indentStart` in
  `src/docspan/backends/google_docs/` returns zero hits pre-this-project).
  There is no internal prior-art fix commit to check failure modes against;
  this is genuinely new surface. The two named risks are real and unmitigated
  today:
  - **False merge**: Docs visually coalescing adjacent paragraph borders
    into one rendered block could make the pull-side "contiguous run"
    detector treat two logically separate `>` blocks (e.g. separated by a
    blank line in the source markdown) as one contiguous quote, corrupting
    the paragraph-count-preserving round-trip the requirements demand.
  - **False positive**: any paragraph a human manually bordered in the Docs
    UI for an unrelated reason (e.g. a manual callout box) that happens to
    share color/width with docspan's marker would be misdetected as a
    docspan blockquote and get literal `> ` markdown prefixes injected on
    pull — silently corrupting content a docspan user never wrote as a
    quote. Mitigated only by choosing a sufficiently distinctive
    color/width combination, which is inherently probabilistic, not exact.
  - **Marker migration risk**: if the chosen marker ever needs to change in
    a future release (color deprecated, conflict discovered), old docs
    pushed under the old marker will silently stop being detected as
    blockquotes on pull with new code — same failure class as any
    version-pinned magic constant, and there is no existing precedent in
    this codebase for versioning a structural marker (the render-glyph
    fix in `31b4edd` uses Unicode category, not a hardcoded codepoint,
    specifically to dodge this — the blockquote marker doesn't have an
    equivalent "any value of this shape" fallback since arbitrary
    color/width combinations are also legitimate for non-docspan content).

## 5. Testing pitfalls

- **This codebase's own history shows fixes repeatedly found only against a
  real Doc, not caught by unit tests first**: `31b4edd`'s render-glyph
  saga explicitly says "the decisive [regression] was settled against the
  live API on a throwaway copy of a real document," and separately "Measured
  on a real design doc: 3 such paragraphs, 56 requests, HTTP 400, nothing
  written. The document was unpushable" — a failure mode that a mocked-response
  unit test would not have caught, because the mock would have to already
  know to include the glyph Google's real API silently injects. The same
  risk applies directly to this project: a hand-written mock `updateParagraphStyle`
  response is very likely to omit the specific quirks the requirements doc
  flags as unverifiable from docs alone (border-omission defaulting behavior,
  border coalescing/normalization, table-cell style inheritance onto
  adjacent borders). Any unit test asserting "the border round-trips" only
  proves the mock is self-consistent, not that Google's real API behaves
  that way.
- Concretely: the requirements doc's own "Feasibility Risks" and "Rabbit
  Holes" sections already call for a live-Doc spike in Phase 3/5 for the
  border partial-update and coalescing questions — this research confirms
  that's the right call, not overcaution, based on the `31b4edd` precedent
  where documentation reading alone produced an incorrect first fix (the
  "strip the glyph" version), and only a live-API repro surfaced the actual
  corruption.
- Recommend the round-trip tests required by the requirements doc (plain/
  nested/list-in-quote/code-in-quote) be run twice: once against a mocked
  Docs response for fast CI, and at least once manually/in a spike against
  a real test Doc before merge, specifically for the border-write and
  border-read paths — mirroring how `cf36561`, `31b4edd`, and `8203c93` each
  needed a real-document repro to find the actual bug shape, not just the
  hypothesized one.
