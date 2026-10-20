# UX Research: wedding-planning-workflow

Research Agent 5 (UX) — SDD Phase 2

Scope note: this is a single-user, CLI + Claude-conversation workflow, not a GUI. The five standard UX dimensions are reinterpreted below for that shape. Ground truth for docspan's *current* CLI behavior was pulled directly from `/home/tstapler/Programming/docspan/src/docspan/cli/main.py` and `README.md`, not assumed — several findings below are gaps in what exists today, not just design opinions.

---

## 1. Comparable UX patterns

The relevant reference class isn't "todo app UX," it's **CLI tools that summarize state change for one operator under time pressure**: `git status`, `gh pr view`, `terraform plan`, `lazygit`, and LLM digest patterns (daily-standup bots, changelog generators).

Patterns worth borrowing:

- **`git status`'s three-tier grouping**: staged / unstaged / untracked, each with a one-line action hint ("use `git add` to..."). Translates directly to: **open / done-since-last-pull / blocked-or-gap**, each with a suggested next action.
- **`terraform plan`'s `+`/`-`/`~` diff glyphs with a summary line** ("Plan: 2 to add, 1 to change, 0 to destroy"). This is the model for "what changed since last pull" — a compact glyph-per-item list *plus* a one-line count summary at the top, so Tyler can stop reading after the summary line if nothing surprising is there.
- **`gh pr view`'s grouped-by-reviewer status**: reviewers are listed with their verdict, not the diff re-explained per reviewer. Translates to: **group by owner (Bekah/Tyler/Ann/Nora), not by document section** — Tyler's actual question after a pull is almost always "what do *I* still owe," secondarily "what does *Bekah* still owe," not "what's in the SCHEDULE section."
- **Digest tools (e.g. Linear's daily digest, Slack channel summaries) put unresolved/overdue items first, resolved items last or omitted entirely.** The summary should not re-list `- [x]` items Claude has already reported as done in a prior pull — only note them if they *changed state* since the last summary (newly checked off).
- **Conventional commit / changelog tooling's "since last tag" framing**: docspan already has `.markgate-state.json` / `.markgate-base` tracking the last-synced content. The summarizer should use that same base to compute "changed since last pull," not re-diff the whole document against nothing every time.

Anti-pattern to avoid: dumping the entire doc back at Tyler as prose ("Here's a summary of your wedding planning document...") — that's slower to scan than the doc itself. The digest must be *shorter* than reading the raw markdown, every time, or Tyler will stop trusting/using it.

---

## 2. User mental model

**Yes — grouped by owner, sorted by due date (soonest first, no-date items last), with a diff since last pull, is the right default.** This matches how Tyler already experiences the doc (sub-headers per person) and how he'll actually consume the output (a scan, not a read) 11 days out from the event when checks are frequent and time-boxed.

Expected shape of "after summarize" output:

```
Since last pull (3 changes):
  ✓ Bekah checked off "Whatsapp group"
  + Ann added "confirm florist delivery window" (no owner/date yet)
  ~ Schedule: Fri dinner time moved 6pm → 6:30pm

Open items by owner (8 total):
  Tyler (3)      — 2 due before Fri, 1 no date
  Bekah (2)      — 1 due Thu, 1 no date
  Ann (2)        — no dates set
  Unowned (1)    — "Splitwise" (no sub-header, needs an owner)

Gaps flagged:
  - "Splitwise" has no owner sub-header — assign or it won't show in anyone's digest
  - HOUSING section unchanged since last pull (not re-summarized)
```

What Tyler would expect / want:
- **A stable, terse header format he can pattern-match in a second** — same shape every run, so scanning gets faster with repetition (this is the same reason `git status` output format is memorized, not read).
- **Silence about unchanged sections.** The single biggest annoyance risk named in the task: re-summarizing HOUSING or SCHEDULE in full every run when nothing changed there. Diff-since-last-pull must actually gate what gets narrated, not just be a nice-to-have framing on top of a full re-summary.
- **Items with no owner or no due date surfaced as a distinct "gap" bucket, not silently sorted to the bottom of someone's list or dropped.** Since owner is inferred from a markdown sub-header (not a real field), Claude *will* sometimes get this wrong or hit truly unassigned items — that ambiguity needs to be visible, not smoothed over.
- **No date-math re-litigated every run for items already flagged.** If "confirm florist" was already flagged as no-date last time and nothing changed, don't re-flag it as a "new" gap — but it's fine (even correct) to keep it visible in the open-items list since it's still open.

What would annoy him: verbose restated context ("As you know, your wedding is coming up..."), re-deriving the whole task list from scratch narratively instead of running off the diff, and burying the 2-3 things that actually need a decision from him under a wall of unchanged status.

---

## 3. Accessibility

N/A / minimal. Single sighted user, personal CLI + terminal + Claude conversation, no other consumers of the interface. Standard terminal readability practices apply (don't rely on color alone if output might be piped/logged without color — docspan already does icon-plus-color, e.g. `✓`/`✗` plus green/red in `main.py`, which is a fine baseline) but no WCAG-level analysis is warranted here.

---

## 4. Error states

These three scenarios are the actual UX-critical surface of this feature — a personal task-tracker digest is low-stakes if wrong, but a bad push against the live, family-shared doc is not. Findings are grounded in what the current CLI does today, not idealized behavior.

### (a) Dry-run diff shows something that looks like it would delete a collaborator's comment or content

**Current state is a real gap, not just a UX polish item.** Reading `main.py` lines 107-111: `docspan push --dry-run` today does **not** produce a content diff at all — it prints exactly one line per mapping (`dry-run  wedding.md → [google_docs] <doc_id>`) and continues to the next mapping. There is no per-paragraph diff, no comment-loss warning, nothing to actually review. The requirements.md's risk-control step ("always review `docspan push --dry-run` output before the real push") is currently unenforceable — there's nothing informative to review. **This should be treated as an in-scope docspan fix, not deferred as a workflow-level mitigation**, since the whole safety plan depends on it.

What the dry-run *should* surface, UX-wise, once implemented:
- A structural diff at the paragraph/list-item level (add/change/remove), not a raw text diff — matches how the push is actually built (`docs_request_builder.py` already does structural diffing via `difflib.SequenceMatcher` on node keys; the dry-run path should reuse that machinery and render it, not skip it).
- **Any removal or edit of a paragraph known to carry an anchored comment gets a distinct, impossible-to-miss marker** (not just folded into a generic `~` change line) — e.g. `⚠ COMMENT AT RISK: paragraph has 1 open comment ("Bekah: ...") and will be edited/removed`. This is the single highest-value warning this tool can produce, per the README's own documented sharp edge ("comments on edited paragraphs are lost on push").
- Default behavior on detecting a comment-at-risk paragraph: **do not silently push it.** Either hard-stop the push for that mapping with a clear message ("1 paragraph with an open comment would be modified — resolve manually in Google Docs or use `--force` to proceed anyway") or require an explicit confirmation. Given the stakes (family member's comment lost days before the wedding), fail closed, not open.
- Plain-language framing, not internals-speak: "would delete Ann's comment on 'confirm florist delivery window'" beats "paragraph diff: DELETE node at index 14, orphaning commentId c8..." Tyler should never need to know what a `structural diff` or `batchUpdate` is to understand the warning.

### (b) Pull-again reveals someone edited the doc while Tyler was mid-edit locally

Current state: this path already exists and is reasonably good — `pull` outcomes include `local-only` ("has local changes not yet pushed. Pull skipped. Push first or use 'docspan conflicts resolve'") and `merged` (auto three-way merge, with conflict markers written into the file and a count reported if merge wasn't clean). This is the right shape; the UX task is presentation, not new mechanism:

- **Merged-cleanly should stay a one-liner** (it already is: `merging... Merged cleanly.`) — don't make Tyler read a diff for something that resolved itself.
- **Conflicts should say *what* is conflicting in owner/section terms, not just a count and a file path.** Today: `Merge conflicts (2) written to wedding.md`. Better: name the section/owner where the conflict landed if it's cheaply derivable (e.g. "2 conflicts in SCHEDULE (Fri) and Bekah's TODOs") so Tyler can triage urgency before even opening the file — a schedule conflict two days before a rehearsal dinner is more urgent than a resolved-later housing note.
- Conflict markers in the file itself are the correct git-style mechanism (matches his existing mental model if he's ever touched a merge conflict) — no new format needed.
- The `local-only` block-pull message already tells him the right next step (push first, or `conflicts resolve`) — keep that pattern; it's the correct "tell them the fix, not just the problem" shape.

### (c) Push partially fails (some mappings succeed, some error)

Current state: `push` already loops per-mapping, reports `✓`/`✗` per mapping with a status line and optional message, and exits nonzero if any failed (`had_error` → `typer.Exit(1)`) — this is a solid foundation. Since this workflow has effectively **one mapping** (the single planning doc), true partial-failure-across-mappings is less likely to occur here than in a multi-doc setup, but the doc itself has structurally distinct sections (TOC, tables, checklists) that could fail independently within a single push if the request-builder rejects part of the batch.

UX recommendations:
- If a push partially applies (e.g. Google's batchUpdate rejects some ops but others already committed — need to confirm with the batchUpdate semantics in planning phase whether operations are atomic or can partially apply), **the CLI must say explicitly what did and didn't land**, not just "error: push failed." A silent partial-write against a live shared doc is the worst-case outcome for this whole project — Tyler would have no way to know the doc is now in a mixed state without manually re-reading it.
- On any push error, the message should point at the concrete recovery action already built into docspan (`.orig` backup, re-pull to see current remote state) rather than requiring Tyler to know internals: "Push failed after partially applying N of M changes. Doc may be in a mixed state — run `docspan pull --dry-run` to see current remote state before retrying." (Note: this again depends on `--dry-run` actually rendering a diff — see (a).)
- Never let a failed push look like a successful one. The existing `✗` red icon convention should be preserved and, given the stakes here, arguably the error case should print louder / require an explicit "reviewed" acknowledgment before Tyler moves on in the same Claude conversation (i.e., Claude should not casually say "done!" after a push if the underlying CLI exit code was nonzero).

**Cross-cutting principle for all three:** every error message must be actionable in one line without Tyler needing to open docspan's source or understand batchUpdate/AST/state-file internals. He is optimizing for zero cognitive load under wedding-week time pressure — "here's what's wrong and here's the exact command to run next" is the bar, not "here's a stack trace."

---

## 5. Job-to-be-done

**Functional job**: Know, in under a minute, what's still open and who owns it; safely get local edits back into the live doc without corrupting collaborator content. The workflow (pull → summarize → edit → pull-again → dry-run → push) directly serves this, *provided* dry-run is made real (see 4a) and the summarizer actually diffs-since-last-pull instead of re-narrating the whole doc (see 2).

**Emotional job**: Confidence that nothing gets lost, days before his own wedding. This is the dominant job — the functional task-tracking is almost secondary to the anxiety-reduction. Every UX decision above (fail-closed on comment risk, explicit "what changed" framing, silence on unchanged sections, plain-language error recovery) serves this directly: the tool's job is to let Tyler *stop holding the whole doc's state in his head* and trust the digest instead. A tool that's occasionally wrong in a way that's loud and recoverable (a clear warning, a blocked push) is emotionally fine; a tool that's wrong in a way that's silent (a comment quietly dropped, a partial push) is the actual failure mode to design against — it's exactly the kind of thing that would surface days later as "wait, where did Ann's comment go?" with no time left to fix it gracefully.

**Social job**: Not looking incompetent in front of family/wedding party who rely on this doc. Concretely this means: collaborator edits/comments must survive invisibly (they should never notice docspan was involved at all — the doc should just look like Tyler kept it perfectly up to date), and if something *does* go wrong, Tyler needs to catch it via the CLI *before* a family member notices in the live doc, not after. This reinforces the fail-closed stance on comment-at-risk pushes: a blocked push that costs Tyler two minutes of manual cleanup in Google Docs is a non-event; a comment that silently vanishes and gets noticed by Bekah first is a real social cost during an already high-stress week.
