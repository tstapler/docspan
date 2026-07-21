# ADR-002: Comment-risk flagging via a read-only `quotedFileContent` substring match, not a full anchor-range decode

**Date**: 2026-07-18
**Status**: Accepted

## Context

`requirements.md`'s in-scope item 1 asks to "verify comment-anchoring survives a pull→push cycle for comments anchored to specific text spans." `research/features.md` and `research/architecture.md` confirm the underlying failure mode precisely: `DocsRequestBuilder.build()` diffs paragraphs as whole units keyed on `(style, text, is_list_item)`; any paragraph classified `"replace"`/`"delete"` gets a `deleteContentRange` covering its full index range, which detaches or drops any comment anchored inside it. `research/features.md` §1 also confirms this is a known, unresolved limitation of the Google Docs/Drive API itself (multiple Google issue-tracker reports of "Original content deleted" on anchored comments even when created correctly via the API) — not something specific to docspan's implementation.

A fully correct fix would require:
1. Reading each comment's exact anchor range (character offsets within the document), and
2. Re-architecting `DocsRequestBuilder` to diff at sub-paragraph granularity so an edit that doesn't touch the anchored span never triggers a full paragraph delete+reinsert.

Both are out of reach within a Small (1–2 day) appetite:
- The Drive `comments().list()` API's `anchor` field is an opaque, undocumented internal range-encoding format (not simple `startIndex`/`endIndex` integers comparable to `DocsParagraphNode.start_index`/`end_index`). Decoding it reliably was not something research could confirm as feasible without a dedicated spike, and `research/build-vs-buy.md`'s own Open-Questions answer states this "will need to be hand-written against the Drive API v3 comments resource, and does require live-doc testing" — i.e., it's unverified, not a known-quantity task.
- Sub-paragraph diffing is a genuine architecture change to `docs_request_builder.py`'s core algorithm, carrying real regression risk to code that is otherwise already correct and tested (`research/pitfalls.md` §1's index-shift-ordering invariant).

Options considered:

1. **Full anchor-range decode + sub-paragraph diff.** Solves the problem for real; requires reverse-engineering an undocumented format and a core diff-engine rewrite under time pressure, against the one document the whole wedding party depends on.
2. **Read-only risk flag via `quotedFileContent.value` substring match.** Before a push, fetch open comments' `quotedFileContent.value` (a *documented*, stable field — the text Google itself echoes back as "what this comment is anchored to") and check whether it appears as a substring inside any paragraph the diff is about to delete/replace. If so, flag it and fail closed (require `--force`) — but do not attempt to prevent the loss structurally.
3. **Do nothing — rely on the existing README-documented limitation and Tyler's own memory of where comments are.** Zero new code.

## Decision

Use **Option 2**. Add `GoogleDocsClient.list_comments()` and `find_high_risk_paragraphs()` (`push_preview.py`) as described in `plan.md` Phase 1 Epic 1.2. This is read-only — it never modifies, resolves, or attempts to re-anchor a comment. It converts "silent, undetected loss" into "loud, requires-explicit-override loss," which is the actual, achievable safety property for this appetite.

## Rationale

- Option 1's core blocker is that the `anchor` field format is unverified and undocumented; committing to it risks burning the entire appetite on a research spike with no guaranteed payoff, then shipping untested comment-preservation logic against a live, family-shared document 11 days before the wedding. Rejected on risk/appetite grounds, not on principle — it may be worth revisiting after 7/29 with no deadline pressure.
- Option 3 fails requirements.md's explicit ask to "verify comment-anchoring survives" — doing nothing new isn't verification, and it leaves `research/pitfalls.md` §0's core finding (the dry-run stub was supposed to be this safety net and isn't) completely unaddressed.
- Option 2 uses only documented, stable API surface (`quotedFileContent.value`), is small enough to implement and test within the appetite (see `plan.md` Story 1.2.2), and directly answers the emotional job identified in `research/ux.md` §5: "a tool that's occasionally wrong in a way that's loud and recoverable... is emotionally fine; a tool that's wrong in a way that's silent... is the actual failure mode to design against." A blocked push costing Tyler two minutes of manual review is a non-event; this ADR explicitly optimizes for that trade.

## Known Limitations of This Approach (accepted, not hidden)

- **Substring matching is an approximation, not a semantic anchor check.** It can produce false positives (e.g. `research/features.md`'s own example: the quoted text `"inner"` is technically a substring of the unrelated word `"dinner"` — in that specific case the flag is still correct because it happens to be the same paragraph, but this is coincidental, not guaranteed in general) and false negatives (if Google normalizes whitespace/quotes differently between `quotedFileContent.value` and the paragraph's live `textRun.content`, or if a comment spans multiple paragraphs).
- **The flag is a warning, not a guarantee.** Even with the `⚠ COMMENT AT RISK` warning heeded and manually resolved, or bypassed via `--force`, the underlying delete+reinsert mechanism (ADR-001's consequence) can still drop the comment — Story 2.2.1b's live scratch-doc test records whether this actually happens in practice, and that finding is carried into `feature-gap-report.md` as a confirmed (not theoretical) risk either way.
- These limitations are deliberately documented in `feature-gap-report.md` (Story 3.2.1) rather than silently accepted, per the project's own scope-cut discipline.

## Consequences

- `GoogleDocsClient` gains one new read method (`list_comments`); no write/mutation capability related to comments is added anywhere.
- **Revised after architecture review (BLOCKER)**: the risk check is evaluated inside `GoogleDocsBackend.push()` itself, from a `PushPlan` built by `push()`'s own single `get_document()` + `list_comments()` fetch, immediately before `batch_update()` — not by the CLI calling `preview_push()` first and deciding separately whether to invoke `push()`. The earlier design (CLI-layer `preview_push()` gate, then `push()` re-fetching independently) created a TOCTOU window where a comment added between the two fetches was invisible to the check that was supposed to catch it; see architecture-review.md and `plan.md` Story 1.2.3. `preview_push()` still exists, but only for `--dry-run` rendering — it is never consulted to decide whether a real write proceeds, so its own `get_document`/`list_comments` round trip only happens on an explicit `--dry-run` invocation, not on every real push.
- A real (non-dry-run) `push()` therefore makes: one `get_document()` call (needed anyway to build the diff/requests), one `list_comments()` call before `batch_update()` (to compute the risk flag), and — new, per the `CommentCountBackstop` — one more `list_comments()` call immediately after a successful `batch_update()`, purely to compare open-comment counts before/after as an orthogonal, exact backstop to the substring heuristic below. This is acceptable latency for a single-user, on-demand CLI (`requirements.md`'s Non-functional Requirements already state no Performance SLO applies).
- **A residual, smaller TOCTOU gap remains and is accepted, not hidden**: even with the check folded into `push()`'s own fetch, a comment added by a collaborator in the (now much smaller — milliseconds, within one CLI invocation) window between `push()`'s fetch and `batch_update()` actually landing is still invisible to the pre-write check, because Drive comments are metadata outside the Doc's `revisionId`/`writeControl`. The `CommentCountBackstop` catches this *after* the fact (reports it, doesn't prevent it). This residual risk is documented explicitly in `feature-gap-report.md`, per adversarial-review.md's Concern, rather than left as an implicit assumption.
- A future cycle, if it wants true anchor-range decoding and sub-paragraph diffing, can build on top of this: `HighRiskParagraph` already isolates exactly which paragraphs need that deeper treatment, so this ADR's approach is a strict subset of, not a dead end relative to, Option 1.
