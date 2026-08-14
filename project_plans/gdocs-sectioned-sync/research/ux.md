# UX Research: gdocs-sectioned-sync (Agent 5)

Lens: this feature has no GUI. The "UX surface" is `markgate.yaml` config shape, CLI
output/error messages, on-disk directory/file layout and naming, and the manifest format a
human or an AI agent has to understand without a screen to look at.

Grounded against docspan's existing conventions (verified in-repo):
- State/sidecar files already use a dotted, tool-prefixed naming scheme:
  `.markgate-state.json`, `.markgate-base/`, `{file}.orig`, `{file}.comments.md`
  ([src/docspan/core/paths.py](../../../src/docspan/core/paths.py)).
- Results are a closed status enum surfaced per mapping:
  `PullResult.status: ok | conflict | error | skipped | warning`,
  `PushResult.status: ok | conflict | error | skipped | blocked | warning`
  ([src/docspan/backends/base.py](../../../src/docspan/backends/base.py)).
- `docspan pull` already writes a comments sidecar 1:1 next to the pulled file
  (`{file}.comments.md`), and `docspan status`/`conflicts list`/`conflicts resolve` are the
  existing CLI vocabulary a sectioned-mode design should extend, not replace (README.md).

## 1. Comparable UX patterns ("one logical document, many files")

| Tool | Split unit | Ordering signal | Index/manifest | Takeaway |
|---|---|---|---|---|
| Sphinx/MyST book build | one file per doc/chapter | explicit `toctree`/`_toc.yml` list, not filename | yes, required (`_toc.yml` or `index.rst`) | Order lives in one authoritative list, not filesystem sort — renames don't reshuffle position. |
| Jekyll/Hugo collections | one file per page | front-matter `weight`/`order`, or date-prefixed filename (`_posts`) | none required; ordering is a convention, not a file | Works for loosely-ordered content; breaks down exactly where "section order matters" — the case here. |
| po/gettext (`.po` per locale) | one file per translation unit set | N/A (files are peers, not ordered) | `.pot` template is the manifest-equivalent, holds msgids or | Closest existing "N files ↔ 1 logical resource" sync model with round-trip and conflict concepts (`msgmerge` = a 3-way merge, `#, fuzzy` = a conflict marker) — worth mirroring vocabulary (fuzzy/stale) for renamed-section states. |
| git-lfs pointer files | one small pointer file stands in for one large blob | N/A (1:1, no ordering) | the pointer file itself is inline metadata | Shows the "self-describing sidecar" pattern: the pointer is legible on its own (`oid sha256:...`, `size ...`) without a separate index. Contrast with a bare numeric manifest that means nothing out of context. |
| Obsidian vault | one file per note, front-matter for identity | none needed, notes are unordered | none (identity via YAML front-matter `id`/`aliases`) | Demonstrates *embedded* identity beating an external manifest for user-editable content: a user renaming a note title doesn't lose linkage because identity lives in the file's own front-matter, not the filename. |
| Monorepo packages (`packages/*/package.json`) | one dir per package | directory name = identity, not order-dependent | each package is self-describing; no root manifest lists "package order" | Order genuinely doesn't matter there — not a good analogy for *ordered* sections, but reinforces: don't invent a manifest to solve a problem (ordering) a directory listing already solves, unless order is not filename-derivable. |

Cross-cutting naming/ordering conventions that make a directory self-explanatory at a glance:
- **`NN-slug.md` (zero-padded numeric prefix + heading slug)** is the strongest single signal:
  `ls` sorts it into document order for free, and the slug tells a human/agent what's inside
  without opening the file — no tool required to see the doc's shape. This is the right default
  for the section files themselves.
- A **bare slug with no prefix** (`intro.md`, `deployment.md`) reads fine alone but loses order
  under `ls`/`git diff --stat`/a directory listing an agent globs — exactly the ordering info an
  agent needs before deciding what to read next. Reject as the primary scheme; it's the Hugo
  failure mode.
- A **manifest as the order authority, filenames as a *cache* of that order** (Sphinx's model)
  is more robust against reordering than depending on renumbering every file on every reorder —
  but only if the manifest is trivially readable, not opaque. Recommendation below.

## 2. User / agent mental model

What a user or agent expects on seeing a *directory* where a *file* used to be, for a mapping
they know is a Google Doc:
- That the directory **is** the doc — every file in it is doc content, nothing extraneous.
- That files are **named for what's inside them** (heading text, not opaque IDs) — this is the
  single biggest risk area: numeric-only names (`section-01.md`) or hash-based names
  (`3f9a2c.md`) fail this expectation immediately. `NN-slug.md` satisfies it.
- That there's **one obvious file to open per topic** — no ambiguity about which of two files
  covers "Deployment Safety."
- That reading **one file is self-sufficient** — the section's own headings/content, not "see
  manifest for context." (Detailed in Section 3.)

What would badly surprise them:
- A manifest file with a **non-obvious name or extension sitting in the directory being mistaken
  for section content** — e.g. `0.md` or `_index.md` sorting first and looking like it might be
  the intro section, when it's actually machine metadata. Recommendation: prefix it out of the
  markdown-file sort order entirely and make it structurally distinct (e.g. `_manifest.yaml`, not
  `.md`) so `grep`/glob patterns for section files (`*.md`) never accidentally pick it up, and a
  human opening "the first file alphabetically" doesn't land on metadata.
- **Renumbering unrelated files on a single insert/delete.** If inserting a new section 2
  requires renaming files 2 through N to N+1 through N+1, every unrelated file shows as
  "changed" in `git diff`/`git status`, which defeats the "clean diff" JTBD (Section 5) and
  silently produces spurious churn a reviewer has to explain away. This is the sharpest surprise
  risk in the whole feature — solve it explicitly (see Section 4/5).
- **Push silently reordering or dropping a section it couldn't match.** Given the manifest exists
  specifically to prevent silent mangling, a push that falls back to "best guess" without a loud
  warning defeats the feature's core emotional JTBD.
- **A section file that only makes sense next to the manifest** (e.g. a body with dangling
  cross-references resolved only via manifest lookup) — breaks "self-sufficient file" (Section 3)
  and specifically breaks the agent workflow this project was built for (see requirements.md's
  motivating case).

## 3. Agent-accessibility (no GUI, so this replaces WCAG here)

- **Self-sufficiency without the manifest**: an agent reading `03-deployment-safety.md` in
  isolation must get a coherent section — its own heading, its own body, and (per requirements.md
  Rabbit Holes) any cross-section link should be renderable as at minimum a visible reference
  (e.g. `See "Rollback Procedure" (section 05)`) rather than a bare relative markdown link that
  silently 404s when the agent hasn't fetched the whole directory. Filename in the visible link
  target is stronger than an opaque anchor ID an agent would have to cross-reference against the
  manifest to resolve.
- **The filename itself should carry the meaning an agent needs to decide "do I need to open
  this?"** without a first read — this is the strongest argument for slug-in-filename over
  ID-only naming; an agent doing a directory listing before deciding what to read gets a working
  table of contents for free, no manifest round-trip required.
- **Error messages must be actionable without source diving.** An agent hitting a sectioned-mode
  error should get, in the message itself: which section, which file path, and what action
  resolves it — not a stack trace or an internal exception name it would have to go read
  `docs_structure_parser.py` to interpret. Concretely this means push/pull status messages name
  the section file, not just "mapping docs/design-doc.md failed."
- **Manifest should be parseable by both a human skimming and a script/agent without ambiguity**
  — plain YAML/JSON with clear keys (`file`, `heading`, `id`, `order`), not a bespoke format an
  agent has to reverse-engineer from source.

## 4. Error/status states — recommended shapes

Extending the existing `ok | conflict | error | skipped | warning` (`blocked` for push) status
vocabulary rather than inventing a parallel one for sectioned mode:

- **Section renamed** (heading text changed, stable ID unchanged): status `ok`, but the message
  should say so explicitly rather than staying silent — e.g.
  `ok: section "Deployment Safety" renamed to "Deployment & Rollback Safety" (id sec-3, file 03-deployment-safety.md unchanged)`.
  Renaming the *file* to match a new heading is a scope decision (Section 2's renumbering-churn
  risk applies here too if the slug is baked into the filename) — flagging it as a UX rabbit hole
  the plan phase must resolve explicitly: keep the original filename stable once assigned
  (identity by ID, not slug) to avoid unrelated diff churn, and only offer a separate
  `docspan sections rename` (or similar) for a user who explicitly wants the filename to catch up.
- **Section deleted locally, still present remotely (or vice versa)**: status `conflict`
  (matches existing conflict semantics), message names the section and which side is missing it,
  e.g. `conflict: section "Appendix B" (id sec-9) exists in the Google Doc but its local file is missing — deleted locally, or pull was interrupted?`
  — the message should distinguish "ambiguous, needs a human/agent decision" from
  "unambiguous delete," matching `conflicts list`/`conflicts resolve` verbs already in the CLI.
- **Push conflict across multiple files**: report per-section, not as one opaque failure for the
  whole mapping — a table similar to `docspan status`'s mapping table, but scoped to sections,
  e.g. one line per affected section with its own status, so a user isn't left binary-searching
  which of 20 files caused the conflict.
- **Misconfigured `split_level`** (heading depth that doesn't exist in the doc): status `error`
  at pull time, message should say what was found instead —
  `error: split_level: 3 configured, but the deepest heading in this doc is level 2 — no H3 headings found to split on. Lower split_level or check the doc.`
  — actionable without opening the doc: tells the user/agent the actual max depth so they don't
  have to go inspect the doc themselves to guess a working value.

General principle across all four: every message names the concrete section (heading text and/or
file path) and the concrete next action, never just the mapping name — that's the difference
between an agent/human resolving it in one step versus one more round of investigation.

## 5. Jobs-to-be-done

- **Functional**: read or edit exactly one section fast — without loading, parsing, or holding
  in context the rest of a multi-thousand-word doc. This is the literal problem statement in
  requirements.md (the "hand-parse raw Google Docs API JSON" workaround this project replaces).
- **Emotional**: confidence that `docspan push` won't silently mangle the rest of the doc while
  reassembling N files back into one — i.e., trust in the round-trip fixpoint guarantee
  (pull→push with no edits = no diff) *carrying over* to the sectioned case, and trust that a
  detected ambiguity (rename vs. delete+insert) surfaces loudly rather than getting silently
  guessed. This job is actively at risk if file-renumbering-on-reorder produces spurious diffs
  that erode trust in "push does only what I asked."
- **Social**: a human reviewer can `git diff` one section's file in a PR and see only that
  section's edits — no noise from 19 unrelated section files. This job dies immediately if a
  single section insert/delete forces renumbering (and thus a diff) across every subsequent file
  in the directory — which is why stable, ID-based file identity (assigned once, never
  renumbered on reorder) is a UX requirement, not just an implementation nicety, and should be
  written into the plan phase as such.
