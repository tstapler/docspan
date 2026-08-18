# UX Research: gdocs-native-blockquotes

Reading order: this covers reader experience (the rendered Google Doc) and CLI-operator
experience (dry-run/push output). See `../requirements.md` for full scope.

## 1. Comparable patterns

Three tools that already face this exact "markdown blockquote → rich-text target" translation:

- **Pandoc → docx.** Pandoc maps a markdown blockquote to Word's built-in `Block Text`
  paragraph style: left/right indent, no border, no background tint by default (indent alone
  carries the signal). Customizing it means shipping a modified `reference.docx` with the
  `Block Text` style's indent tweaked in Word's Styles pane, then passing
  `--reference-doc=custom-reference.docx` — i.e., Pandoc's own answer to "which visual
  convention" is indent-only, configurable but not opinionated about border/color.
  ([Pandoc User's Guide](https://pandoc.org/MANUAL.html), reference-doc mechanism confirmed via
  [pandoc.org/demo](https://pandoc.org/demo/example33/16.2-input.html))
- **Notion callouts vs. blockquotes.** Notion treats these as two distinct block types:
  blockquote is indent + left border, no fill; callout is background tint + icon, no border.
  Round-tripping through markdown is lossy in both directions — a Notion callout exports as a
  plain markdown blockquote (icon/color dropped), and a markdown blockquote imports as a plain
  Notion blockquote, never auto-upgraded to a callout. This is direct precedent for docspan's own
  scope line: blockquote-as-border-and-indent and callout-as-tinted-icon-box are different
  affordances, and conflating them is a known lossy trap other tools fell into.
  ([blog.markdowntools.com](https://blog.markdowntools.com/posts/markdown-for-notion-what-actually-works),
  [tryfabric/martian](https://github.com/tryfabric/martian))
- **Confluence.** Legacy wiki-markup Confluence has no CSS-level "thin left border" primitive —
  the closest built-in is the Panel macro (full border box + optional title/background color),
  which is heavier than a markdown blockquote. Confluence Cloud's newer editor added a dedicated
  "Blockquote" content block that renders indent + left border, matching the CommonMark
  convention more directly than the old macro system did.
  ([Atlassian: Panel Macro](https://confluence.atlassian.com/doc/panel-macro-51872380.html),
  [WCMS Knowledge Base: Create a blockquote](https://uwaterloo.atlassian.net/wiki/spaces/WCMSKB/pages/43448698659/Create+a+blockquote))

**Convention that emerges across all three**: indent is the load-bearing, near-universal
convention for "this is a blockquote, not body text." A left border is common but treated as a
secondary/optional reinforcement, not the primary signal — and background tint or icon is
reserved for the *stronger* callout/admonition affordance (Notion's callout, Confluence's Info/
Tip/Warning macros), not for a plain quote. This validates the requirement's choice of
indent+border as "blockquote," distinct from a heavier callout treatment.

## 2. User mental model: is a rendering change on next push expected or surprising?

The docspan user is a CLI operator (often scripted/non-interactive, sometimes an LLM authoring
markdown on their behalf) whose mental model is almost certainly **"push should reflect the
markdown I wrote"**, not **"push should never touch a paragraph whose markdown didn't change."**
That said, two things make this specific change worth calling out rather than shipping silently:

- **The trigger is not "the markdown changed" but "the rendering rules changed."** A `>` line
  that has been byte-for-byte identical in the markdown file for months will get rewritten the
  *first time the file is pushed again* purely because docspan's own rendering logic changed —
  not because the author touched that paragraph. That's a different causal story than a normal
  diff-triggered edit, and the requirements doc treats it as a one-time migration cost (see
  `requirements.md`'s Risk Control section) precisely because it's surprising in that specific
  way.
- **The visible symptom (comment loss) is generic today.** The existing churn-note wording in
  `src/docspan/backends/google_docs/push_preview.py` (`render_churn_note`, ~line 197-206) says:

  > `ⓘ N paragraph(s) are rewritten with no text change (delete-and-reinsert) — the wording is
  > identical, but the paragraph is destroyed and recreated, so any comment anchored to it is
  > still lost.`

  This already fires for *any* delete-and-reinsert with unchanged text, for whatever reason
  (churn from unrelated diff-engine behavior, not just this migration). It answers "what" (your
  comment on this paragraph will be lost) but not "why this paragraph, why now" — a user seeing
  it after upgrading docspan has no way to tell "this is the one-time blockquote-style upgrade"
  from "some other bug is churning my paragraphs again." Recommendation below (item 3) is a
  minimal, in-repo way to close that gap without inventing new UX from scratch.

## 3. Dry-run / error UX for the migration case

Current wording (verified by reading
[`push_preview.py:197-206`](../../../src/docspan/backends/google_docs/push_preview.py)) is
generic and reason-agnostic — it does not attempt to explain *why* a no-text-change paragraph is
being rewritten, because today there's exactly one reason (diff-engine churn) and it's already
undifferentiated across causes.

For this migration, `find_churn_pairs`/`render_churn_note` is exactly the mechanism the
requirements doc names as "the existing `push --dry-run` comment-loss warning" (Risk Control
section) — every already-pushed blockquote paragraph will match this churn-pair pattern (text
unchanged, paragraph destroyed+recreated) the first time it's pushed post-migration. Two concrete
UX gaps worth closing, in order of value for the appetite of this project:

1. **Give the churn note a reason when one is knowable.** `DiffEntry`/`HighRiskParagraph` already
   distinguish `native_glyph` from `comment` as typed reasons (`Literal["comment", "native_glyph"]`
   in `push_preview.py:42`) — the same pattern extends cleanly to a `style_upgrade` (or similarly
   named) reason recorded on the entry when a blockquote paragraph is being rewritten to add the
   marker border/indent for the first time (legacy literal-`>`-text paragraph, no marker border,
   about to gain one). `render_churn_note` can then say something like: `"1 paragraph rewritten
   to add native blockquote styling (one-time upgrade) — comment on it is lost."` instead of the
   generic wording, for that specific case, while leaving the generic wording for true unexplained
   churn. This is a small, additive change to an existing typed-reason pattern, not new
   infrastructure — worth doing given the appetite (Medium, 1-2 weeks) and the blast-radius the
   requirements doc already flags around comment preservation.
2. **CHANGELOG/docs callout** (already required by the requirements doc's Risk Control section) —
   documenting the migration prominently in `docs/backends/google-docs.md` and the CHANGELOG so
   an operator who runs `--dry-run` and sees a wall of churn notes across many files has an
   independent way to recognize "this is the known one-time migration," not a regression.

Both are proportionate: the dry-run wording change is the higher-leverage fix since it's seen
at the moment of surprise; docs are the fallback for anyone who doesn't dry-run first.

## 4. Accessibility: is a color-only left border enough?

No — and the requirements doc's own design (indent *and* border, not border alone) already gets
this right, but it's worth stating explicitly as a validation, not an assumption:

- **Indentation is the primary signal and should stay that way.** It's a geometric/positional
  cue, not a color cue — legible regardless of color vision, monitor calibration, grayscale
  printing, or screen-reader-adjacent low-vision zoom tools. This matches what all three
  comparable tools in §1 treat as the load-bearing signal.
- **A left border, if used as a secondary/reinforcing cue, should still clear a reasonable
  contrast bar even though WCAG 1.4.11 Non-text Contrast (3:1 against adjacent background) is
  written for interactive UI components and doesn't literally bind static document content in a
  Google Doc.** Applying the same 3:1 heuristic anyway is good practice: a border color barely
  distinguishable from the page background (e.g., a light gray on white, ~1.6:1 in the classic
  failure case) would functionally decorate rather than signal, defeating the point of adding it.
  ([W3C: Understanding SC 1.4.11](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html))
- **Net guidance for the marker-border color choice** (relevant to the Open Question in
  `requirements.md` about border color/width): pick a border color with reasonable contrast
  against a white page background as a secondary reinforcement, but treat indent as sufficient
  and non-negotiable on its own — never design a variant where indent is dropped and border alone
  carries the "this is a quote" meaning.

## 5. Job-to-be-done: does indent+border fully satisfy why authors reach for `>`?

The job `>` is doing for the author is **"set this text visually apart from body text as a note,
warning, or aside"** — a callout job, not literally "quote someone." Markdown authors (and LLMs
imitating markdown conventions) reach for `>` far more often for asides/notes than for actual
attributed quotations, because CommonMark has no dedicated callout syntax and `>` is the closest
available primitive.

Indent + left border **does** satisfy the core of that job: it makes the paragraph visually
distinct from surrounding body text at a glance, which is the minimum bar the user's screenshot
complaint (bare `> note` reading as broken text) was failing. It does **not** fully satisfy the
richer callout job some authors implicitly want — GitHub-style `[!NOTE]`/`[!WARNING]` alert
semantics, a background tint, or an icon, the way Notion's callout or Confluence's Info/Tip/
Warning macros do. That gap is exactly what the requirements doc's Non-functional/Out-of-Scope
section already excludes ("changing how markdown_to_paragraph_parser handles blockquote *content*
styling... beyond what's needed to drop the text prefix"), and this research confirms that
framing is correct rather than a corner being cut: background tint/icon is a materially bigger
feature (parsing `[!NOTE]`-style admonition syntax, choosing a tint palette, an icon set) that
belongs in a future project, not folded into this one. Worth stating as an explicit **documented
non-goal** in the ADR this project produces, so a future feature request for "colored callout
boxes" isn't read as evidence this project under-delivered.

## Sources

- [Pandoc User's Guide](https://pandoc.org/MANUAL.html)
- [Pandoc demo: reference-doc mechanism](https://pandoc.org/demo/example33/16.2-input.html)
- [Notion markdown export quirks](https://blog.markdowntools.com/posts/markdown-for-notion-what-actually-works)
- [tryfabric/martian — Notion callout emoji mapping](https://github.com/tryfabric/martian)
- [Atlassian: Panel Macro](https://confluence.atlassian.com/doc/panel-macro-51872380.html)
- [WCMS Knowledge Base: Create a blockquote (Confluence Cloud)](https://uwaterloo.atlassian.net/wiki/spaces/WCMSKB/pages/43448698659/Create+a+blockquote)
- [W3C: Understanding SC 1.4.11 Non-text Contrast](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html)
- [Google Docs API: ParagraphStyle (borderLeft, indentStart)](https://developers.google.com/workspace/docs/api/how-tos/format-text)
- `src/docspan/backends/google_docs/push_preview.py:42, 197-206` (existing churn-note wording,
  read in this repo)
- `src/docspan/style_guide.py:16-19`, `src/docspan/cli/lint.py:27-34` (existing style-guide/lint
  wording that this project supersedes)
