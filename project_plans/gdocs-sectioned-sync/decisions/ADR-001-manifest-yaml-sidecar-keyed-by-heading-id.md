# ADR-001: Manifest is a `_manifest.yaml` sidecar keyed by `heading_id`, never renumbered

## Status
Accepted

## Context
A sectioned mapping needs a durable record of (a) which on-disk section files correspond to which sections of the Google Doc, and (b) their canonical order, so that renames, reorders, and add/delete can be detected reliably instead of guessed from filenames or content.

Three candidate identity/order mechanisms were evaluated (`project_plans/gdocs-sectioned-sync/research/build-vs-buy.md`):

1. **Google Docs `NamedRange`/`namedRanges`** — visible to all collaborators, but has no Docs UI (a human can't see or fix it directly), names aren't required to be unique (so lookup-by-name isn't authoritative on its own — a `namedRangeId` still has to be tracked externally, meaning it doesn't eliminate the need for a manifest), and a single named range can silently split into multiple discontiguous ranges when a collaborator edits across its boundary — which complicates both splitting and reorder detection. It would also require new `CreateNamedRangeRequest`/`DeleteNamedRangeRequest` batchUpdate request types that `docs_request_builder.py` doesn't currently emit.
2. **Filesystem/filename order** (bare slug or numeric prefix as sole order authority) — `ls`/`git diff --stat` order is convenient for humans but is not a stable identity: renumbering files on every insert/delete makes every unrelated file show as changed in git history (research/ux.md's "sharpest UX risk").
3. **A flat, git-tracked YAML sidecar (`_manifest.yaml`) keyed by Google Docs' own persistent `heading_id`**, with `slug` and `filename` as companion, non-authoritative fields.

## Decision
The manifest is `_manifest.yaml`, a plain YAML file (not embedded front matter, not `.md`) living alongside section files in the sectioned mapping's directory. It is the single source of truth for section order and identity, keyed by `heading_id` (`DocsParagraphNode.heading_id`, `docs_structure_parser.py:188`). Filenames (`NN-slug.md`) are a human-readable cache of that order, not the authority — on push, manifest order wins over `ls` order or file-content order. `heading_id`s are assigned once (by Google Docs, not by docspan) and are never renumbered on reorder.

Written/read atomically via temp-file-then-`os.replace`, mirroring the existing pattern in `config.py:126-167`'s `save_config`.

## Consequences
- Manifest and section files must be kept in sync at all times; any code path that mutates one without the other risks the desync failure mode called out in pitfalls research. Atomic directory-level writes (Epic 6 in `implementation/plan.md`) exist specifically to bound this risk.
- `heading_id` does not survive delete+reinsert in Google Docs (confirmed via `heading_anchors.py` and `tabs.py:38-55`), so reorder must be implemented as an in-place move, not delete+reinsert — see ADR-002. This ADR and ADR-002 are coupled: choosing `heading_id` as identity is only safe because ADR-002 commits to preserving it across reorders.
- Manifest is git-trackable plain text, giving full diff visibility — but that also means a manifest is one more file a human could hand-edit incorrectly; a lint/warning for manifest/directory-contents mismatch is recommended future work (noted in `research/ux.md`'s "discoverability" unstated need) but not required for v1.
- `NamedRange` remains available as a *future, redundant* corroborating identity mechanism if manifest drift proves to be a real-world problem — explicitly not needed for v1, so this decision doesn't foreclose it.
