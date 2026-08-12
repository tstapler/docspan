# ADR-003: Do not implement comment re-anchoring/migration on push — richer warning only

**Date**: 2026-08-12
**Status**: Accepted

## Context

`docspan` issue #92 asks whether a comment anchored to a paragraph that push
resolves to delete+insert (per `_repair`'s docstring in
`docs_request_builder.py`: any diff opcode that doesn't collapse to `"equal"`
becomes a literal `deleteContentRange` + `insertText`) can be preserved
across that push, either by mutating the
Drive comment's existing `anchor`/`quotedFileContent` via `comments().update`,
or by creating a replacement comment via `comments().create` with a custom
`anchor`/`quotedFileContent` pointing at the reinserted paragraph.

A live-API spike against a real Drive document/comment was the intended way
to answer this, but no OAuth credentials or test Drive account are available
in this sandboxed environment, so no live `comments().update`/`comments().create`
call was made. This is stated plainly, not glossed over: the finding below
rests on documented API contract and this repo's own prior investigation,
not a fresh live call. **This is a hard environmental blocker, not a gap in
effort** — closing it for real requires a human to supply live OAuth
credentials plus a scratch Drive document/comment outside this sandbox and
run the two calls below; no amount of further reasoning in this environment
can substitute for that. If/when that access exists, the spike is exactly
two calls:
```
comments().update(fileId=doc_id, commentId=c.id, fields="anchor", body={"anchor": target_anchor})
comments().create(fileId=doc_id, body={"content": c.content, "anchor": target_anchor, "quotedFileContent": {"value": snippet}})
```
followed by reloading the comment in the Docs UI to see whether it renders anchored.

### Confidence labeling

- **UNVERIFIED (this session)**: whether a live `comments().update`/`comments().create`
  call against a real document actually behaves as documented — no such call was made here.
- **VERIFIED (secondary source, source 1 below)**: Google's own API reference and guide
  text, read directly from developers.google.com, states anchors are immutable and that
  `update`'s only documented-writable field is `content`.
- **VERIFIED (this repo's own prior work, source 2 below)**: `project_plans/bidirectional-comments/plan.md`
  §2 already reached and recorded the same conclusion, citing a third-party reproduction
  (`googleworkspace/cli#169`) as its own evidence, independent of this ADR.

Two sources answer the question decisively without a live call:

1. **Google's own guide**, [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments):
   anchors are documented as immutable ("Anchors are immutable, and their
   position relative to the content of a document cannot be guaranteed
   between revisions"), and — separately — Google Workspace editor apps
   (Docs included) render an API-set `anchor` as an **unanchored** comment in
   the UI, regardless of whether the anchor value itself is well-formed.
2. **This repo's own prior research**, `project_plans/bidirectional-comments/plan.md`
   §2 (dated before this ADR, marked "High confidence — documented +
   reproduced"): "`anchor` is ignored on Google Docs — a new anchored
   comment renders as 'Original content deleted' / no highlight," citing
   Google's guide plus a reproduced case at
   [googleworkspace/cli#169](https://github.com/googleworkspace/cli/issues/169).
   That plan already reached the same conclusion for *new* top-level
   comments (ship unanchored-only, v1); this ADR extends the same finding
   to the *migrate-an-existing-comment* case #92 asks about.

Both sources agree, from two different angles (official docs; independent
reproduction), that neither `comments().update` nor `comments().create` can
produce a comment that Google Docs' own UI will render as anchored to
arbitrary text. `comments().update`'s only documented writable field (per
its REST reference) is `content` — no update path for `anchor` is
documented at all.

Options considered:

1. **Re-anchor via `comments().update`.** Blocked: `anchor` isn't a
   documented-writable field on `update`, and even if it were, "anchors are
   immutable" per Google's own guide.
2. **Recreate via `comments().create` + resolve/delete the original.**
   Technically possible to call, but the new comment renders **unanchored**
   in the Docs UI (source 1 and 2 above) — a materially different, arguably
   worse UX than today's silent loss: instead of nothing, the user gets a
   stray document-level comment, under a new `id`/`createdTime`, that fires
   a fresh "new comment" notification to every watcher, with no visual
   anchor to the paragraph it's about.
3. **Warn-and-proceed only (status quo mechanism, richer message).** No new
   Drive write capability, no new failure mode, no notification-spam
   regression. Ship the AC2 enrichment (list every at-risk comment per
   paragraph, not just the first) on top of this.

## Decision

Use **Option 3**. No comment migration ships. `find_high_risk_paragraphs`/
`render_high_risk` (`push_preview.py`) are extended to enumerate every
at-risk comment per flagged paragraph (id, author, snippet) instead of
stopping at the first match, but the underlying warn-before-`--force`
mechanism from ADR-002 is unchanged.

## Rationale

- Option 1 is foreclosed by the API contract itself, not by appetite or
  risk tolerance — there's no live spike result that could reverse this;
  the field is undocumented-as-writable and documented-as-immutable.
- Option 2 is technically shippable but trades a *quiet* known limitation
  for a *loud* new one (unanchored stray comment + notification spam) —
  worse for the exact "silent vs. loud" trade ADR-002 already reasoned
  through, but on the wrong side of it: ADR-002 chose loud-and-blocking
  over silent-and-undetected specifically because a blocked push costs the
  user two minutes, which is a good trade; recreate-based migration would
  cost watchers a false "new comment" notification and the user a
  duplicate, unanchored comment, which is not a favorable trade for a
  problem that's still only partially solved (the anchor is still gone).
- Option 3 keeps the fix surface exactly where ADR-002 already put it —
  read-only risk detection — and directly satisfies #92's fallback
  instruction: "If re-anchoring isn't feasible, improve the existing
  warn-before-force message."

## Consequences

- AC5 ("if migration ships, a comment is preserved across push") is
  satisfied by this ADR's explicit decision not to ship migration, per
  #92's own conditional phrasing ("If migration ships...").
- No new Drive API write surface is added; `PUSH_SCOPES` is unchanged
  (already covers today's `create_reply` calls).
- `push_preview.py`'s `HighRiskParagraph`/`find_high_risk_paragraphs`/
  `render_high_risk` gain a richer, multi-comment rendering (AC2) without
  changing which paragraphs get classified as high-risk (AC3) or how the
  post-push open-comment-count backstop works (AC4).
- If Google ever documents a writable `anchor` field on `comments().update`,
  or changes how Workspace editors render API-set anchors, this decision
  should be revisited — it is contingent on the current, cited API
  behavior, not a permanent architectural constraint.
