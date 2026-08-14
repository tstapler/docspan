# ADR-002: Section reorder is implemented as an in-place move, not delete+reinsert

## Status
Accepted (implementation detail of the move primitive is an open spike — see Consequences)

## Context
When a sectioned mapping's local directory reflects two sections having swapped order (or a section relocated elsewhere in the document) relative to the stored manifest, `docspan push` must translate that into Google Docs batchUpdate requests.

The most naive implementation — delete the moved section's content range and reinsert it (as fresh markdown) at its new position — is the same mechanism the existing single-file push already uses for ordinary content edits (`_build_push_plan`'s diff-and-emit pipeline, `docs_request_builder.py`). But `heading_id` is confirmed (via `heading_anchors.py`'s docstring and `tabs.py:38-55`) to **not survive delete+reinsert**: Google Docs assigns a fresh `heading_id` to any newly-inserted heading paragraph, even if its text is byte-identical to what was deleted.

This matters because:
- ADR-001 makes `heading_id` the manifest's identity key. Regenerating it on every reorder would mean the manifest's identity mapping goes stale on the very operation (reorder) it exists to detect.
- Any cross-section anchor link pointing at that heading (`heading_anchors.py`'s anchor resolution) would silently break — the link would point at a `heading_id` that no longer exists — with no error surfaced at push time, only a broken link discovered later.
- This is exactly the kind of failure mode research (`research/pitfalls.md`) flags as needing a design-time decision, since it changes what "detecting a reorder" has to compile down to in `docs_request_builder.py`, not something safe to discover mid-implementation.

## Decision
Reorder of a section (a move that changes position without changing content) is implemented as an in-place move of the existing content range, preserving its `heading_id`, rather than as delete-old-content + insert-new-content-from-markdown. Add/delete/reorder classification (Task 3.2.1 in `implementation/plan.md`) uses `difflib.SequenceMatcher` over `heading_id` sequences (stored-manifest-order vs. current-local-directory-order) specifically so that "moved" is a distinguishable classification from "deleted + inserted," and the move path is only taken for entries the classifier marks as moved-not-changed.

The concrete Docs API batchUpdate request shape for the move itself is **not yet finalized** — `docs_request_builder.py` today only expresses insert/delete/style-update requests, no "move a range" primitive. Task 3.2.2 in the implementation plan is an explicit design spike to determine whether the Docs API exposes a true move-equivalent request, or whether the safest available approximation is copy-content-to-new-position followed by delete-of-the-old-range *only after* the insert succeeds (still avoiding a naive delete-then-reinsert-from-markdown, which is what would regenerate the `heading_id`).

## Consequences
- Reorder-only pushes are more complex to implement than pure insert/delete, and the existing write-backwards, highest-anchor-first request ordering in `docs_request_builder.py:1133-1195` (itself hard-won via a documented regression, issue #42) must be re-derived for whatever move requests Task 3.2.2 produces — it must not be assumed to "just scale" to a new request type.
- If the Docs API spike (Task 3.2.2) concludes there is no reasonable move-equivalent primitive, this ADR will need to be revisited with a fallback decision (e.g. accepting `heading_id` churn on reorder and documenting it as a known limitation, with cross-section anchors re-resolved post-push). That fallback is explicitly out of scope to design now — recorded as Unresolved Question 5 in `implementation/plan.md`.
- Edit-and-rename-in-the-same-cycle (a heading both moved and heavily content-edited at once) remains an accepted, named limitation — the classifier cannot always disambiguate "moved-and-edited" from "deleted-and-a-different-section-inserted-at-that-position" from content alone. This ADR does not attempt to resolve that; it only commits to preserving identity for the unambiguous move case.
