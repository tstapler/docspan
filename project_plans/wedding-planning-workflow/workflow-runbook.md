# Workflow Runbook: Wedding Planning Doc Sync

**Purpose**: the exact, repeatable procedure for syncing the live wedding
planning Google Doc through docspan, so it doesn't have to be reconstructed
from memory during wedding week. Companion to
`project_plans/wedding-planning-workflow/feature-gap-report.md` (known
limitations) and `decisions/ADR-001`/`ADR-002` (why the tool behaves this
way).

Live doc id: `1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`.

---

## 1. The sync cycle

Run these steps in order, every time. Don't skip or reorder any of them.

1. **`docspan pull`** — pulls the live doc into your local markdown file.
2. **Claude summarizes** — ask Claude to read the pulled file and produce an
   `OwnerDigest` (format in §2 below). This is the "what's open and who owns
   it" step — don't re-read the whole doc yourself.
3. **Tyler edits locally** — make your edits in the local markdown file
   (toggle checkboxes, add schedule/housing notes, etc.).
4. **`docspan pull` again** — immediately before pushing, to catch any
   concurrent edits collaborators made while you were editing. If this
   produces a merge, review the conflict markers before continuing (see
   `research/ux.md` §4(b) — conflicts are reported by section/owner where
   possible, e.g. "2 conflicts in SCHEDULE (Fri) and Bekah's TODOs").
5. **`docspan push --dry-run`** — renders a real structural diff (`Preview:
   N change(s), N addition(s), N removal(s), N unchanged`) plus any `⚠`
   risk blocks. This is not a stub — read it.
6. **Review the dry-run output** — see §3 for what a `⚠` block means and
   what to do about it.
7. **`docspan push`** — the real push. `--force` is required if step 5/6
   showed a `⚠ COMMENT AT RISK` or `⚠ NATIVE CHECKBOX GLYPH` block and you've
   decided to proceed anyway (see §4).

Flags, as actually implemented in `src/docspan/cli/main.py`:

| Flag | Meaning |
|---|---|
| `docspan push --dry-run` | "Preview changes without writing" — calls `preview_push()` and prints `preview.render()`. Purely cosmetic; never decides whether a real push is allowed to proceed. |
| `docspan push --force` | "Proceed with a push even if push() flags a comment-risk paragraph." Required to get past a `⚠` block inside `push()` itself. Does **not** bypass the revision-conflict check. |

The very first time you run a real (non-dry-run) `push` against the live
doc, the CLI prompts:

```
⚠ Scratch-doc verification not recorded — proceed against live doc? [y/N]:
```

This only fires for the live doc id, never for `wedding-scratch.md` or
`--dry-run`, and only until you answer `y` once (it writes a local marker
file so it doesn't ask again). See §5 for what should be true before you
answer `y`.

---

## 2. OwnerDigest format

After each pull, Claude produces a digest — conversational output, not a
new CLI subcommand — following `research/ux.md` §2's shape:

- Grouped by owner (inferred from markdown sub-headers like `Bekah:`,
  `Tyler:`, `Ann:`), not by document section.
- Scoped to **what changed since the last pull** (`DiffSinceLastPull`) —
  unchanged sections are not re-summarized. Silence on an unchanged section
  is the correct, expected output, not a bug.
- Items with no owner sub-header or no due date go into a distinct **gaps**
  bucket — never silently dropped or smoothed into someone's list.
- Owner/due-date info is a side channel only. Claude never writes owner
  tags or due-date frontmatter back into the pushed markdown — collaborators
  never see structured metadata clutter in the live doc.

### Worked example (a): Tasks

Source markdown (excerpt):

```markdown
## Bekah:
- [x] Whatsapp group
- [ ] Confirm florist delivery window

## Tyler:
- [ ] Print permit for Thursday
- [ ] Book rental van

## Ann:
- [ ] Order corsages

## Due Wednesday (7/29)
- [ ] Splitwise
```

`Splitwise` sits under a date-only section heading, not a person sub-header
— it has no inferred owner.

Digest output:

```
Since last pull (2 changes):
  ✓ Bekah checked off "Whatsapp group"
  + Ann added "Order corsages" (no due date yet)

Open items by owner (5 total):
  Tyler (2)      — 2 due before Fri, 0 no date
  Bekah (1)      — no date
  Ann (1)        — no date
  Unowned (1)    — "Splitwise" (no owner sub-header, needs one)

Gaps flagged:
  - "Splitwise" has no owner sub-header — assign it or it won't show in anyone's digest
```

### Worked example (b): Schedule / Housing

Source markdown (excerpt):

```markdown
## Schedule
### Friday
- 6:30pm: rehearsal dinner at [venue] *(was 6pm — moved)*

## Housing
- Bekah has the lake house
- Nora + Ann share the guest cottage
```

These lines carry no `[ ]`/`[x]` marker and no owner sub-header in the
Tasks sense — the digest groups them by **day** (Schedule) or by
**person/property** (Housing) instead, and still gates on
`DiffSinceLastPull`:

```
Since last pull (1 change):
  ~ Schedule: Fri rehearsal dinner time moved 6pm → 6:30pm

Schedule (unchanged since last pull, not re-summarized):
  Housing unchanged since last pull (not re-summarized)
```

If Housing had also changed, it would get its own grouped block the same
way, e.g. `Housing: Bekah has the lake house (unchanged)` vs. a `~`/`+`
line if it changed.

**Food/catering** entries (e.g. `"Catering: Layers sandwiches for Thursday
lunch"`) are non-checklist prose in the same shape as (b) — they group the
same way Schedule/Housing lines do (by day or by vendor/venue), not like a
Tasks checklist line. No separate worked example is needed.

---

## 3. Reading a `⚠` block

`docspan push --dry-run` (and a blocked real push) render risk warnings via
`render_high_risk()`. Exactly two kinds exist, and a single paragraph can
show both:

```
⚠ COMMENT AT RISK: paragraph "gathering for dinner at 6pm" has an open comment
  from Nora Sullivan ("inner") and would be changed. Resolve manually in Google
  Docs, or re-run with --force to proceed anyway.
```

```
⚠ NATIVE CHECKBOX GLYPH: paragraph "[ ] Book florist" is a native Google Docs
  checkbox (checked/unchecked state not readable via the API) — editing it here
  would layer literal [x]/[ ] text on top of the existing glyph. Toggle this
  line by hand in Google Docs UI instead, or re-run with --force to proceed
  anyway.
```

A real (non-dry-run) push with either block present is blocked by default
(`status="blocked"`, red `✗`, nonzero exit) — `batch_update` is never
called. `--force` is required to proceed.

If a push succeeds but a comment turns out to have been lost anyway, the
CLI reports it as a yellow `⚠` (not a green `✓`) with a message like:

```
⚠ open comment count dropped (2→1) — ...
```

Never treat a `⚠`-status result as a clean success, even though the write
did go through.

**Caution — restating ADR-002 directly here so it isn't missed:** the
comment-risk check is a plain substring match of a comment's quoted text
against the paragraph's current text, not a real anchor decode. In testing,
the flag correctly caught a comment quoting `"inner"` inside the paragraph
`"gathering for dinner"` — but that only worked because `"inner"` happens to
be a literal substring of `"dinner"` **in the same paragraph**. That was
coincidental, not a systematic guarantee. The check can miss comments due to
whitespace/quote normalization differences, or comments anchored across
multiple paragraphs. **Treat the absence of a `⚠` block as "nothing detected
this time," not as "nothing is at risk."** When in doubt, open the live doc
and eyeball the paragraph before pushing.

---

## 4. Rules — do / don't

**`ManualFallbackGate`** — always available, at any point:

- DO edit the live Google Doc directly in the browser instead of using
  `docspan push` whenever you're unsure, a push is blocked, or you just
  don't want to deal with it. This is not a failure mode — it's the
  designed fallback, and it's always on the table.

**Isolate checklist-only pushes** — this is **operator discipline plus a
render-time nudge, not tool-enforced**. docspan does not reject or split a
mixed push:

- DO push checklist toggles (`- [ ]`/`- [x]` changes) in their own
  pull → dry-run → push cycle, separate from schedule/housing/food edits,
  especially near paragraphs you know carry open comments. This keeps each
  dry-run diff small and reviewable.
- DO pay attention if `docspan push --dry-run` prints a note like:
  `ⓘ This push mixes N checklist toggle(s) with M other edit(s) — consider
  pushing checklist-only changes separately (see workflow runbook).` — that
  note is the only thing docspan does about mixed pushes. It is a
  suggestion, not a gate.
- DON'T assume docspan will split or refuse a mixed push for you — it
  won't. Nothing stops a push that mixes checklist and non-checklist edits
  from going through in one shot if you run it.
- DON'T pass `--force` reflexively to clear a `⚠` block. Read the block,
  decide, then act — `--force` means "I've manually confirmed this specific
  paragraph is safe to overwrite," not "make the warning go away."
- DON'T skip `docspan push --dry-run`, even for a change that looks trivial.

---

## 5. Verified before first live push

This checklist must be filled in from Epic 2.2's actual scratch-doc results
(`project_plans/wedding-planning-workflow/implementation/plan.md` Story
2.2.1, Tasks 2.2.1a–2.2.1c) before treating the safety net above as trusted
against the live doc. **As of this writing, none of the items below have
been run** — Phase 0 (scratch doc setup + full-document checklist survey)
and Epic 2.2 (live scratch-doc round-trip verification) require Tyler's own
docspan Google credentials and direct action on the live/scratch Google
Docs, which no coding agent has performed. Do not treat any item below as
passed until you've personally run it and checked the box.

- [ ] Phase 0: `ScratchDoc` created (Task 0.1.1a) — NOT YET RUN, see plan.md Phase 0
- [ ] Phase 0: full-document `ChecklistParagraphSurvey` completed, every bullet paragraph surveyed (Task 0.1.2a) — NOT YET RUN, see plan.md Phase 0 / ADR-001
- [ ] Phase 0: per-paragraph findings recorded in ADR-001, mixed-doc fallback noted if applicable (Task 0.1.2b) — NOT YET RUN
- [ ] Epic 2.2 / Task 2.2.1a: non-comment checklist round-trip verified (pull → toggle → dry-run → push → pull, no `⚠`, re-pulled markdown shows the toggle) — NOT YET RUN, see plan.md Epic 2.2
- [ ] Epic 2.2 / Task 2.2.1b: comment-collision scenario verified — `⚠ COMMENT AT RISK` shown on dry-run, plain `push` (no `--force`) blocked and exits nonzero, comment still present in scratch doc afterward — NOT YET RUN
- [ ] Epic 2.2 / Task 2.2.1b (continued): `--force` re-run outcome recorded honestly — comment survived or was lost — NOT YET RUN (if lost, this must also be added to `feature-gap-report.md` per Task 2.2.1b's instruction)
- [ ] Task 2.2.1c: `ScratchVerificationMarker` file created (`<state_dir>/scratch-verified.marker`) after both 2.2.1a and 2.2.1b pass — NOT YET RUN. Do not create this file until both cycles above are actually confirmed passing; its presence is meant to mean "verified," not "attempted."

**Once you've run Phase 0 and Epic 2.2 yourself:** replace each `NOT YET
RUN` line above with the actual result (checked box + one-line finding,
e.g. "PASSED — checkbox toggled correctly, re-pull showed `- [x]
Splitwise`" or "FAILED — comment lost even with `--force`, see
feature-gap-report.md item 6"). Do not check a box without having actually
run the corresponding step.

---

## 6. Real cycle log

Requirements.md's Success Metric 1 ("zero collaborator edits/comments lost
across ≥3 real cycles") is measured against **real pull→edit→push cycles
against the live doc**, not the scratch-doc verification above — scratch-doc
testing is necessary but not sufficient evidence, since it's a synthetic
single-editor test. Fill in one row below after each of the first 3 real
live-doc sync cycles (and keep logging beyond 3 if useful).

| Date | What changed | Open comments before | Open comments after | Surprises | `--force` used? (Y/N) + why |
|---|---|---|---|---|---|
| _(fill in)_ | | | | | |
| _(fill in)_ | | | | | |
| _(fill in)_ | | | | | |

Instructions:

- Fill in "Open comments before/after" by checking the comment count
  reported in `push()`'s output (or by eyeballing the live doc) — this is
  what `CommentCountBackstop` is also checking automatically; use this log
  to confirm its finding matched what you observed, not just trust the tool
  silently.
- "Surprises" = anything that didn't match what the dry-run diff predicted,
  a `⚠` block that fired unexpectedly (or should have and didn't), or any
  moment you fell back to `ManualFallbackGate` instead of finishing via
  docspan.
- Success Metric 1 can only be considered met once at least 3 rows show
  zero comment/edit loss (before/after counts match, no unexplained
  surprises) — not before.
