# UX Design: gdocs-native-blockquotes

Source: `../requirements.md`, `../research/ux.md`, `../implementation/plan.md` (Epic 4,
`HighRiskParagraph`/`style_upgrade`, Story 4.3). This document specifies the three user-facing
surfaces this feature touches, the interaction/rendering model for each, and testable UX
acceptance criteria.

## Surface inventory

| # | Surface | Type | Treatment |
|---|---|---|---|
| 1 | Rendered blockquote in the Google Doc | Visual/reading surface (Google Docs UI) | Full (wireframe + flow + error states) |
| 2 | `docspan push` terminal warning (`style_upgrade` reason; fires with or without `--dry-run`) | CLI output | Condensed |
| 3 | `STYLE_UPGRADE_COUNT=<N>` stdout line + `--fail-on-comment-loss` flag | CLI output / scripted-consumer contract | Condensed |

`lint`/`style_guide` changes (Epic 5) are deletions of existing text, not new UI. Surface 3
(plan.md Story 4.3, added after a pre-mortem P1 finding) does introduce one new flag,
`--fail-on-comment-loss` — the "no new flags" characterization used in earlier drafts of this
document no longer holds and has been corrected everywhere below.

---

## Surface 1: Rendered blockquote in the Google Doc

### 1.1 Wireframe — visual treatment by case

Indent + left border is the primary/secondary signal pair validated in `research/ux.md` §1 and
§4: **indentation is the load-bearing cue** (geometric, color-vision-independent); the left
border is a secondary reinforcement that must still clear reasonable contrast against white
page background. Neither is ever presented alone as the *only* signal — indent is present on
every variant below.

```
Case A — plain, depth 1
┌────────────────────────────────────────────────┐
│ Body text above the quote.                      │
│                                                  │
│      ┃ This is a callout or note. It reads as   │
│      ┃ visually set apart from body text via    │
│      ┃ indent + a colored left border.           │
│                                                  │
│ Body text below the quote.                      │
└────────────────────────────────────────────────┘
    ^indentStart      ^borderLeft (BLOCKQUOTE_BORDER_MARKER)

Case B — nested, depth 2 (`> >`)
┌────────────────────────────────────────────────┐
│      ┃ Outer quote line (depth 1).               │
│      ┃                                           │
│      ┃     ┃ Inner nested quote line (depth 2)   │
│      ┃     ┃ — indent stacks cumulatively per    │
│      ┃     ┃ level; border repeats at each       │
│      ┃     ┃ nesting level, not just the outer.  │
└────────────────────────────────────────────────┘
       indent(1)    indent(1)+indent(2)

Case C — list inside a quote (`> - item`)
┌────────────────────────────────────────────────┐
│      ┃ Intro line inside the quote.              │
│      ┃  • First bullet, indented past the        │
│      ┃    quote's own indent (list indent and    │
│      ┃    quote indent stack, per requirements.md│
│      ┃    Rabbit Holes).                          │
│      ┃  • Second bullet.                          │
└────────────────────────────────────────────────┘

Case D — fenced code block inside a quote (`> ``` `)
┌────────────────────────────────────────────────┐
│      ┃ Note before the code:                     │
│      ┃ ┌──────────────────────────────────────┐ │
│      ┃ │ def example():                        │ │
│      ┃ │     return 42                          │ │
│      ┃ └──────────────────────────────────────┘ │
│      ┃ (monospace font styling from existing     │
│      ┃  code-run rendering, unchanged; quote      │
│      ┃  indent+border still wraps the whole run) │
└────────────────────────────────────────────────┘

Case E — legacy (pre-migration) blockquote, unchanged until next push
┌────────────────────────────────────────────────┐
│ > This still shows the literal `> ` prefix and  │
│ no indent/border — untouched until its file is  │
│ next pushed, for any reason (not necessarily an │
│ edit to this quote).                            │
└────────────────────────────────────────────────┘
```

### 1.2 Interaction flow

This is a rendering pipeline, not a click-driven UI — the "interaction" is the round trip between
the markdown author, `docspan push`/`pull`, and whoever reads the Doc in the browser.

| Step | Actor | Action | System response |
|---|---|---|---|
| 1 | Author (human or LLM) | Writes `> note` in markdown, saves file | No visible change yet — local file only |
| 2 | Author | Runs `docspan push` | New blockquote paragraph created with `indentStart` + `borderLeft` (`BLOCKQUOTE_BORDER_MARKER`) set; a legacy paragraph with unchanged markdown is left untouched (Case E) |
| 3 | Reader | Opens the Doc in Google Docs (browser or mobile) | Sees indented, left-bordered paragraph (Cases A-D) — reads as an intentional callout, not broken text |
| 4 | Reader | Adds a native Google Docs comment anchored to the blockquote paragraph | Comment persists normally as long as no future push deletes+reinserts that exact paragraph |
| 5 | Author | Pushes the file again for *any* reason — not necessarily an edit to the quote itself; even an unrelated change elsewhere in the file triggers this, because the markdown parser change makes every legacy blockquote's parsed node look different from what's on the Doc | Every legacy blockquote (Case E) in that file is deleted and reinserted with the new native styling in that same push, not just ones the author directly touched; any comment anchored to any of those old paragraphs would be lost (existing, documented delete+reinsert behavior — see Surface 2) |
| 6 | Author | Runs `docspan pull` on a Doc with native-styled blockquotes | Markdown file reconstructs `"> "` prefixes byte-for-byte from `is_blockquote`/`quote_depth`, independent of the visual border |

### 1.3 Error / edge-case handling

| Case | What the reader/author sees | Why / mitigation |
|---|---|---|
| Human manually applies a similar-looking left border in the Docs UI (not via docspan) | On next pull, may be misdetected as a docspan blockquote if it happens to match `BLOCKQUOTE_BORDER_MARKER` exactly | Documented, accepted non-goal-of-perfection (requirements.md, plan.md Risk Control) — marker chosen to be a distinctive, unlikely-to-collide value; not a silent data-loss risk since worst case is an extra `> ` prefix on pull, which is visible and correctable in the markdown source |
| Empty quote line (`>` with no text) | Renders as an indented/bordered empty line, not silently dropped | `projection.py`'s blank-paragraph-drop rule is carved out for `is_blockquote=True` nodes (plan.md Story 2.5) — an empty quote is meaningful structure |
| Blockquote paragraph inside a table cell | May or may not visually inherit indent/border from an adjacent quote depending on Docs' cell-style inheritance (open question, Epic 0 spike) | If inheritance is confirmed, cell-fill path explicitly clears `indentStart`/`borderLeft` so a non-quote cell never accidentally looks like a quote |
| List containing a quote (`- > note`, reverse nesting) | Pre-existing mis-render (spans collapsed, no indent/border) — **not fixed by this project** | Documented out-of-scope in requirements.md and plan.md; filed as a follow-up idea, not silently broken by this change (no regression, but no fix either) |
| Legacy quote's file never pushed again | Stays as literal `> text`, no border, forever | Explicit accepted behavior — no forced/eager migration; documented in `docs/backends/google-docs.md`. Note this is file-scoped, not quote-scoped: the *next* push of the file for any reason migrates it, whether or not the quote's own text changed |

### 1.4 UX acceptance criteria

1. A reader viewing a freshly pushed blockquote in the Google Docs UI can identify it as a
   callout/note within one glance, without needing color vision — indent alone is sufficient to
   distinguish it from body text (verifiable by viewing the Doc in grayscale/print-preview mode).
2. The left border, when visible, has a contrast ratio of at least 3:1 against the white page
   background (spot-check with a contrast checker on the chosen `BLOCKQUOTE_BORDER_MARKER` color).
3. A nested quote (depth 2) is visually distinguishable from a depth-1 quote by increased
   indentation — a reader can tell "this is a quote inside a quote" without reading the text.
4. A list inside a quote renders with the list's own bullet/number markers still visible and
   legibly indented past the quote's indent — no marker or text is clipped or overlapped.
5. A fenced code block inside a quote keeps its existing monospace/code-run visual treatment
   (unchanged from current behavior) while still appearing inside the quote's indent/border.
6. No dead end: a reader who doesn't understand why a paragraph is indented/bordered can find an
   explanation in `docs/backends/google-docs.md`'s updated Limitations/behavior section — this
   doc exit path is the criterion here, not an in-Doc affordance, since Google Docs' own rendering
   surface is not modifiable by docspan beyond the paragraph style itself.
7. A legacy (pre-migration) blockquote is not visually altered until its containing file is
   next pushed for any reason — a reader comparing before/after screenshots of a push of a file
   with no legacy quotes sees no change; a push of a file that *does* contain legacy quotes
   migrates all of them together, even ones whose own text was untouched by that push.
8. Round-trip fidelity is verifiable by a human: pulling a Doc containing all four cases (plain,
   nested, list-in-quote, code-in-quote) reproduces byte-identical markdown to what was originally
   pushed.

---

## Surface 2: `docspan push` terminal warning (`style_upgrade` reason)

Non-interactive CLI output — condensed treatment per the task instructions. This warning fires
identically on a real (non-dry-run) `push` and on `--dry-run`; the sample below uses `--dry-run`
for illustration only — see Surface 3 for the machine-readable `STYLE_UPGRADE_COUNT` line that
accompanies it on every run.

### Sample output

```
$ docspan push --dry-run docs/notes.md

Plan for docs/notes.md → Google Doc "Team Notes":
  ~ 1 paragraph restyled (native_glyph)
  ⚠ 1 paragraph rewritten to add native blockquote styling (one-time upgrade)
     — any comment anchored to it would be lost. See docs/backends/google-docs.md
     for details.
  ~ 1 paragraph rewritten with no text change (delete-and-reinsert, unrelated to
     the blockquote migration above)
  ✓ 3 paragraphs unchanged

(Every legacy blockquote in this file migrates together on its first push after
this feature ships — even one whose text you didn't touch — because pushing the
file for any reason now causes all of its legacy quotes to be rewritten in the
same pass, not just the one(s) you edited.)

ⓘ 1 paragraph(s) are rewritten with no text change (delete-and-reinsert) — the
  wording is identical, but the paragraph is destroyed and recreated, so any
  comment anchored to it would still be lost.

Run without --dry-run to apply.
STYLE_UPGRADE_COUNT=1
```

The `STYLE_UPGRADE_COUNT=<N>` line (Surface 3) is emitted last — after all warnings, the
"Run without --dry-run" hint, and any other trailing output, immediately before the process
exits — so a human scanning the transcript top-to-bottom sees it as the final line, and a script
can reliably find it by scanning from the end of stdout.

**NO_COLOR / non-UTF8 fallback:** when `NO_COLOR` is set, output is not a TTY, or the terminal's
encoding can't render `~`/`⚠`/`✓`/`ⓘ` (non-UTF8 locale), those glyphs are replaced with plain
ASCII text markers so the output stays legible and greppable — no color codes, no Unicode. The
`STYLE_UPGRADE_COUNT` line itself never carries color codes or glyphs in either variant (Surface 3):

```
$ NO_COLOR=1 docspan push --dry-run docs/notes.md

Plan for docs/notes.md -> Google Doc "Team Notes":
  [RESTYLE] 1 paragraph restyled (native_glyph)
  [WARN] 1 paragraph rewritten to add native blockquote styling (one-time upgrade)
     -- any comment anchored to it would be lost. See docs/backends/google-docs.md
     for details.
  [RESTYLE] 1 paragraph rewritten with no text change (delete-and-reinsert,
     unrelated to the blockquote migration above)
  [OK] 3 paragraphs unchanged

[INFO] 1 paragraph(s) are rewritten with no text change (delete-and-reinsert) -- the
  wording is identical, but the paragraph is destroyed and recreated, so any
  comment anchored to it would still be lost.

Run without --dry-run to apply.
STYLE_UPGRADE_COUNT=1
```

When a single push migrates many legacy quotes at once (plan.md Story 4.1's 5+ case), the
per-paragraph `⚠` lines collapse into one summarized count instead of flooding the terminal:

```
  ⚠ 7 paragraphs rewritten to add native blockquote styling (one-time upgrade)
     — any comments anchored to them would be lost. See docs/backends/google-docs.md
     for details.
```

(The `style_upgrade`-specific line replaces the generic churn-note wording for exactly the
paragraphs matched to that reason, per `implementation/plan.md` Epic 4 / Story 4.2; unrelated
churn keeps the existing generic wording, shown here as the trailing `ⓘ` line only if other
non-style-upgrade churn coexists in the same run.)

### Acceptance criteria

- Given a legacy blockquote paragraph being pushed for the first time after this feature ships,
  `--dry-run` output names the reason (`style_upgrade`) in plain English distinct from the
  generic "no text change" churn wording, so an operator scanning a wall of warnings across many
  files can tell "this is the known one-time migration" from "something else is churning my doc."
- The wording makes clear that migration is file-scoped, not quote-scoped: the *first* post-ship
  push of a file — for any reason, including an edit to unrelated text — migrates every legacy
  blockquote in that file together, not only ones the author directly edited.
- The warning states the concrete consequence ("any comment anchored to it would be lost") in the
  same line as the cause — no separate lookup required to learn what's at stake. The wording is
  hedged ("would be lost"), not asserted as fact, because it fires for every migrated paragraph
  regardless of whether that specific paragraph actually has a comment anchored to it — an
  unconditional "is lost" would be wrong (and would erode trust in the warning) on paragraphs with
  no comments.
- The warning line itself includes a pointer to `docs/backends/google-docs.md` for details, so an
  operator doesn't have to already know that doc exists to find it.
- Ordinary (non-migration) churn continues to show the pre-existing generic wording unchanged —
  this feature must not silently mislabel unrelated churn as a style upgrade.
- The warning appears in the paragraph-level plan output with no interactive prompt; on its own it
  requires no exit-code change — `--dry-run`'s existing exit-code and summary-count behavior is
  unchanged unless the operator opts into `--fail-on-comment-loss` (Surface 3, below).
- This warning, and its `STYLE_UPGRADE_COUNT` companion (Surface 3), fire identically whether or
  not `--dry-run` is passed — there is no dry-run-only behavior here.
- Documentation exit path: an operator who doesn't run `--dry-run` first, or who is confused by
  the message, can find the same explanation in `docs/backends/google-docs.md`'s updated
  Limitations section and the CHANGELOG entry (plan.md Story 3.4) — no dead end even outside the
  terminal.

---

## Surface 3: `STYLE_UPGRADE_COUNT=<N>` stdout line and `--fail-on-comment-loss` flag

Non-interactive CLI/CI-consumer contract per `implementation/plan.md` Epic 4 Story 4.3
(pre-mortem P1 #2 remediation). This is the machine-readable counterpart to Surface 2's
human-readable warning — it exists so a script or CI job can detect style_upgrade/comment-loss
without parsing prose.

### Format / parse contract

- On every `docspan push` (with or without `--dry-run`), after `find_high_risk_paragraphs` runs,
  stdout includes exactly one line of the form `STYLE_UPGRADE_COUNT=<N>`, where `<N>` is the count
  of `HighRiskParagraph` entries whose `reasons` include `"style_upgrade"` (0 when none).
- The line is emitted once per run, always present (even when `N=0`), on its own line, with no
  color codes or glyphs — safe to `grep`/`awk` regardless of `NO_COLOR`/TTY/locale state.
- It is additive: it does not replace or reorder the existing human-readable `render_high_risk`
  output from Surface 2; scripted consumers should match `^STYLE_UPGRADE_COUNT=\d+$` and ignore
  the rest of stdout.
- Ordering: the line is emitted last, after all Surface 2 warnings and the "Run without --dry-run
  to apply." hint (dry-run) or the applied-push summary (real push), immediately before the
  process exits. No other trailing output follows it.

### `--fail-on-comment-loss` flag semantics

- New flag on `docspan push`. Default: off (`False`) — no behavior change for existing callers,
  scripts, or the interactive workflow described in Surface 2.
- **This is post-hoc detection, not prevention — the flag never blocks or gates the write.** On a
  real (non-`--dry-run`) push, the write executes first (any comment loss from a `style_upgrade`
  paragraph has already happened by the time the process is exiting), and only *then*, after
  printing its normal warnings and the count line, does the command exit non-zero if
  `STYLE_UPGRADE_COUNT` is greater than 0 (warnings are never suppressed to make room for the
  failure — an operator debugging a red CI job still sees which paragraphs triggered it). The
  non-zero exit makes already-occurred comment loss visible to CI; it does not undo it or stop it
  from happening.
- When passed and the count is 0, exit code is unaffected (normal success path).
- Works identically with and without `--dry-run` in the sense that both paths compute and report
  the same count, but the two differ in what the exit code says about the world: under
  `--dry-run --fail-on-comment-loss`, nothing is ever written (dry-run never writes, flag or no
  flag), so a non-zero exit is a true pre-check a CI job can act on *before* deciding whether to
  push for real. Under `--fail-on-comment-loss` alone (real push), the write has already
  completed by the time the same non-zero exit is produced — it reports comment loss that already
  occurred, it does not prevent it.

### Sample output

Same file, same doc, and the same paragraph counts as Surface 2's dry-run sample above — shown
here on a real (applied) push, with `--fail-on-comment-loss` set and the count non-zero:

```
$ docspan push --fail-on-comment-loss docs/notes.md
Plan for docs/notes.md -> Google Doc "Team Notes":
  ~ 1 paragraph restyled (native_glyph)
  ⚠ 1 paragraph rewritten to add native blockquote styling (one-time upgrade)
     — any comment anchored to it would be lost. See docs/backends/google-docs.md
     for details.
  ~ 1 paragraph rewritten with no text change (delete-and-reinsert, unrelated to
     the blockquote migration above)
  ✓ 3 paragraphs unchanged

ⓘ 1 paragraph(s) are rewritten with no text change (delete-and-reinsert) — the
  wording is identical, but the paragraph is destroyed and recreated, so any
  comment anchored to it would still be lost.

✓  docs/notes.md → https://docs.google.com/document/d/abc123/edit
STYLE_UPGRADE_COUNT=1
$ echo $?
1
```

The `✓  docs/notes.md → <url>` line is the applied-push summary referenced by the ordering rule
above — it's the same per-mapping result line the existing (pre-feature) push path already prints
(`docspan/cli/main.py`'s `{icon} {local} → {url}` line), not a new line introduced by this feature.

(Note: the `Plan for <file> -> <doc>:` header shown at the top of every sample in this document is
this document's placeholder wording for the announcement/plan-of-record line the CLI prints before
building requests, used identically for `--dry-run` and a real push — a real push still executes
against a computed plan, so "Plan for..." is deliberate, not a leftover from dry-run-only code.
This wording was not found verbatim in the current `src/docspan/cli/main.py`/`push_preview.py`
(today's dry-run output starts with `Preview: N change(s), ...`), so treat it as this document's
assumed future header text, not a confirmed existing CLI string — implementation should reconcile
the two before Story 4.x ships.)

### Sample output — nothing to migrate (`N=0`)

```
$ docspan push --fail-on-comment-loss docs/notes.md
Plan for docs/notes.md -> Google Doc "Team Notes":
  ✓ 4 paragraphs unchanged
✓  docs/notes.md → https://docs.google.com/document/d/abc123/edit
STYLE_UPGRADE_COUNT=0
$ echo $?
0
```

No `style_upgrade` paragraphs, `STYLE_UPGRADE_COUNT=0` is still printed (never omitted), and
`--fail-on-comment-loss` has no effect on the exit code — this is the normal success path.

### Acceptance criteria

- `STYLE_UPGRADE_COUNT=<N>` is present on every `push` run's stdout, with the correct count,
  independent of `--dry-run`, `--fail-on-comment-loss`, `NO_COLOR`, or terminal/locale state.
- A CI job can gate purely on the count line's value or on the process exit code (when
  `--fail-on-comment-loss` is set) without parsing any other line of output.
- `--fail-on-comment-loss` defaults to off; omitting it reproduces today's exit-code behavior
  exactly, satisfying requirements.md's "no behavior change for existing callers" constraint.
- Documented in `docs/backends/google-docs.md` as the supported mechanism for CI-driven consumers
  to detect comment loss (plan.md Story 4.3 acceptance criteria).
