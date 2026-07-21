# Build vs. Buy: Checklist Round-Trip + Summarize/Edit/Push Workflow

**Research Agent 6** — SDD Phase 2, project `wedding-planning-workflow`
**Date**: 2026-07-18

## TL;DR

Build both, from scratch, on top of what's already a dependency. There is no
library or SaaS product that fits the Small/hard-deadline appetite better than
~1 day of hand-written code against docspan's existing structures — and the one
place a "buy" option looked promising (native Google Docs `BULLET_CHECKBOX`
checkboxes) turns out to be a trap: **the Docs API cannot reliably read back
checkbox checked/unchecked state**, so adopting it would create the exact kind
of silent data-loss bug requirements.md is trying to avoid. Represent checklist
state as literal text (`- [ ]` / `- [x]`, or a checked/unchecked glyph in plain
text) so it flows through the existing, already-tested diff/index-shift
machinery in `docs_request_builder.py` unchanged.

---

## 1. Existing OSS library or framework

### 1a. Higher-level Google Docs API wrapper (for the checklist push/pull work)

**Finding**: No such wrapper is a dependency today, and none is worth adding.
`pyproject.toml` depends directly on `google-api-python-client>=2.108.0` (raw
REST bindings) plus `google-auth*`. Searched for community wrappers
(e.g. `gdoc-down`, `docx-to-gdoc` style tools, Apps-Script-only helpers) — the
ecosystem around the Docs API is thin, and every wrapper found is either a
JS/Apps-Script tool or a thin convenience layer that still round-trips through
the same raw `documents.batchUpdate` request shapes docspan already builds in
`docs_request_builder.py`. There's nothing that abstracts away the checklist
problem, because the problem isn't an ergonomics gap — it's an API gap (see 1c).

**Pros of a wrapper**: none identified that outweigh the cost of vetting a new,
lightly-maintained dependency 11 days before a hard deadline.

**Cons**: new dependency risk, no evidence it handles checklists better,
would still need custom code on top for the diff/index-shift logic docspan
already owns.

**Verdict**: **Not recommended.**

### 1b. Markdown task-list parsing on the local-file side

**Finding**: docspan already depends on `mistune>=3.0` (see `pyproject.toml`
line 48) and uses it in
`src/docspan/backends/google_docs/markdown_to_paragraph_parser.py` via
`mistune.create_markdown(renderer=None)` to get AST tokens. mistune ships a
**built-in `task_lists` plugin**
(`mistune/plugins/task_lists.py`, confirmed present in the installed
mistune package at
`.venv/lib/python3.14/site-packages/mistune/plugins/task_lists.py`) that is
not currently enabled. Enabling it via
`mistune.create_markdown(renderer=None, plugins=["task_lists"])` rewrites any
list item matching `^\[[ xX]\]\s+` into a `task_list_item` token with
`attrs: {"checked": bool}` and strips the checkbox markup from the leading
text — i.e., it already does exactly the `- [ ]`/`- [x]` detection
`markdown_to_paragraph_parser.py`'s `_walk_list_items()` would otherwise have
to hand-roll with regex.

**Pros**: zero new dependency (mistune is already required), battle-tested
regex/edge-case handling (nested lists, mixed `[X]`/`[x]`), one-line plugin
enable, output token shape (`type: "task_list_item"`, `attrs.checked`) slots
directly into the existing `_walk_list_items()` token-walking code with a
small `elif` branch.

**Cons**: none of note — this is a strict win.

**Verdict**: **Recommended.** Adopt `mistune`'s `task_lists` plugin for the
markdown→AST side instead of hand-parsing `- [ ]`/`- [x]` text.

### 1c. Native Google Docs checkbox representation (`BULLET_CHECKBOX`)

This sits under "library" only in the sense that it's the built-in Docs API
feature that a naive read of `createParagraphBullets` would lead you toward
adopting. Verified via targeted research (not assumption, since
requirements.md flagged this as an explicit Open Question):

- `createParagraphBullets` supports `bulletPreset: "BULLET_CHECKBOX"`, so you
  *can* create a native checkbox glyph on push.
- **Reading it back does not work.** Multiple independent sources (Kanshi
  Tanaike's Google Cloud Community writeups, a Latenode community thread, and
  a Google Apps Script community thread) confirm the Docs API returns
  `GLYPH_TYPE_UNSPECIFIED` for checkbox bullets and the document JSON is
  **identical whether the box is checked or unchecked** — the only observable
  difference between the two states is the document's `revisionId`, not any
  field on the paragraph/bullet. There is no documented way, as of the
  current API version, to programmatically determine checked vs. unchecked
  state via `documents.get`, nor to toggle it via `batchUpdate`.

**Implication for the plan phase**: this directly resolves the "Rabbit Hole"
and "Open Question" in `requirements.md` about whether checklists are a
distinct bullet glyph. They are — but that glyph is a dead end for round-trip
*state*, because it's write-only from the API's perspective. The correct
design is to **not** use `BULLET_CHECKBOX` at all for state tracking. Instead,
keep checklist state as literal text content inside a normal bulleted (or
plain) paragraph — e.g. push `- [x] Book florist` as list-item text `[x] Book
florist` (optionally with a Unicode `☑`/`☐` for visual polish), so state is
just... text, and it flows through `docs_structure_parser.py`'s existing
plain-text extraction and `docs_request_builder.py`'s existing
insert/delete/diff logic with **no new index-shift risk** and no new Docs API
surface. Native visual checkbox glyphs can still be a "nice to have" purely
cosmetic push-time flag later, but must never be the source of truth for
checked/unchecked state.

**Verdict**: **Not recommended** as the state-representation mechanism.
Confirmed via research, not assumption — this should be written into
`plan.md` explicitly so a future implementer doesn't reach for
`BULLET_CHECKBOX` and then discover the read-back gap mid-implementation,
burning a day of the two-day appetite.

---

## 2. SaaS / managed API (Google Doc ↔ task tracker sync)

Checked: native Google Tasks integration, Zapier, Make.com templates.

**Finding**: Zapier has templates connecting Google Docs/Google Tasks/Todoist,
but every one of them operates on **document-level or task-level events**
(new doc created, new task created) — none parse or sync the *checklist items
inside a Doc's body text* to/from a task tracker. Google Tasks itself is a
wholly separate list data model with no native link to inline Docs checklist
paragraphs. Using any of these would mean introducing a second system of
record (a Zapier task list or Google Tasks list) that collaborators editing
the raw Google Doc would never see or touch — which requirements.md
explicitly rejects under "Alternatives Considered" ("a second system
fragments the source of truth days before the wedding") and restates as a
hard constraint ("collaborators must keep using the raw Google Doc").

**Pros**: zero-code, fast to wire up for the narrow case of "turn a new doc
into a task."

**Cons**: does not address the actual requirement (parsing/syncing checklist
items embedded in one continuously-edited Doc's body); introduces exactly the
fragmentation risk already ruled out; Zapier/Make subscription cost and OAuth
setup overhead for a throwaway 11-day personal tool; no path to the
"summarize by owner with due dates" requirement without still writing custom
parsing logic anyway.

**Verdict**: **Not recommended.** Fit is poor enough that this isn't a
close call — the checklist lives inside Doc body paragraphs, not as
separate task objects, and no SaaS sync product operates at that
granularity against Google Docs.

---

## 3. LLM-generated / hand-written vs. battle-tested library (summarization logic)

Two genuinely different risk profiles here, and they should be treated
differently:

**"Summarize open tasks grouped by owner with due dates"**: this is a
markdown-parse (via the mistune AST already in use) + groupby + an LLM prose
pass (Claude, in-session, not a library). There is no meaningful algorithmic
risk — it's list comprehension over already-structured `task_list_item`
tokens (owner/due-date extraction is a text-pattern problem, not a
correctness-critical one; worst case is a summary that needs a manual
once-over, not corrupted data). **Hand-written/LLM-generated code is clearly
fine here.** No library needed; this is exactly the kind of glue logic
appetite-sized projects should write directly rather than research further.

**The Docs `batchUpdate` index-shift handling in `docs_request_builder.py`**:
this is the opposite case. It's already implemented, using `difflib.
SequenceMatcher` opcodes plus a descending-startIndex sort to avoid the
classic "earlier delete shifts later indices" bug class. This is exactly the
kind of logic where a from-scratch reimplementation would carry real risk
(silently corrupting a live, shared document) — but it doesn't need to be
reimplemented. **Reuse it as-is.** The checklist feature should extend
`DocsParagraphNode`/`_text_key()` (e.g. include checked-state in the equality
key so a toggle registers as a change) and land new list-item text through
the existing `_make_insert_requests`/`_make_delete_requests` path, not add a
parallel code path. Because checklist state is being represented as literal
text (§1c), no new index-shift edge case is introduced — the existing,
already-diff-tested request builder handles it for free.

**Verdict**: **Recommended** to hand-write/LLM-generate the summarization
layer, and **recommended** to extend (not replace or duplicate) the existing
tested `docs_request_builder.py` diff logic for the checklist state changes.

---

## 4. Fork or adapt (reference-only research)

Looked for a comparable "Google Docs task-list sync" or "Obsidian ↔ Google
Docs" OSS project whose checklist-handling code could shortcut the "how does
the Docs API represent checklists" research (which is now already answered
directly in §1c, so this is lower-value than it would have been pre-research,
but still useful for the *implementation* phase):

- **`lupiter/obsidian-gdocs`** (TypeScript, Obsidian plugin) — bidirectional
  sync between Obsidian folders and Google Docs. Worth a skim for its
  `batchUpdate` request-building approach, but it's TypeScript against a
  different runtime and the repo description ("Can we sync between obsidian
  and google docs? Maybe!") signals early/experimental maturity — not
  something to trust code from, only to read for pattern confirmation.
- **`iloveitaly/obsidian-google-docs`** — push-only (one-directional), so it
  doesn't even face the pull-side checklist-state problem that's the hard
  part here.
- No Python project specifically handling Docs API checklist push/pull was
  found; this appears to be a genuinely under-tooled corner of the API
  (consistent with §1c's finding that the API itself doesn't support
  reading checkbox state — there's nothing for a library to wrap).

**Pros**: free 20-minute skim to sanity-check the request-shape approach
against another implementation.

**Cons**: nothing here is directly portable (language mismatch, and the
"how checklists are represented" question is already answered more
authoritatively by the API-limitation research in §1c than by reading a
TypeScript plugin's source).

**Verdict**: **Viable but low-value.** Optional 20-minute skim of
`lupiter/obsidian-gdocs`'s `batchUpdate` construction during planning, purely
as a sanity check — not on the critical path, not worth spending appetite
budget on.

---

## Summary Table

| # | Option | Verdict |
|---|---|---|
| 1a | Higher-level Google Docs API wrapper library | Not recommended |
| 1b | mistune `task_lists` plugin (already a dependency) | **Recommended** |
| 1c | Native `BULLET_CHECKBOX` for state tracking | Not recommended (API can't read state back) |
| 2 | Zapier/Make/Google Tasks sync (SaaS) | Not recommended |
| 3a | Hand-written/LLM summarization logic | Recommended |
| 3b | Reuse existing `docs_request_builder.py` diff/index-shift logic | Recommended (extend, don't replace) |
| 4 | Read `lupiter/obsidian-gdocs` for reference | Viable, optional, low-value |

## Direct answers to requirements.md's Open Questions

- *"Does the Google Docs API expose checklist items with a distinct
  type/glyph vs. plain bulleted lists with literal `[ ]`/`[x]` text?"* — Yes,
  a distinct glyph (`BULLET_CHECKBOX`) exists for **creation**, but it cannot
  be **read back** (checked/unchecked state is invisible to `documents.get`).
  Recommendation: don't use it for state; use literal text.
- *"Can comment-anchor preservation be verified without live-doc access in
  CI?"* — Out of scope for this build-vs-buy research (no library/SaaS
  question applies), but flagging for the plan phase: no OSS tool or Drive
  API convenience wrapper was found for comment-anchor verification either;
  this will need to be hand-written against the Drive API v3 `comments`
  resource, and *does* require live-doc testing since anchor-resolution
  behavior isn't mockable without real revision data.
