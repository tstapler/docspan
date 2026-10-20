# UX Research: Mermaid Diagram Sizing

Research Agent 5 (UX). Reasoning/comparative research — no user testing performed; claims below
are labeled INFERRED where they rely on general design-pattern knowledge rather than a directly
opened primary source.

## 1. Comparable UX patterns: how do other tools size embedded diagrams?

The near-universal convention for "diagram/image embedded in a text document" is: **scale to fit
the content column width, capped at the image's natural size, and center it.** This is the CSS
`max-width: 100%; height: auto; display: block; margin: 0 auto` pattern, and it shows up
independently across tools that solved this problem for prose-with-figures:

- **GitHub Markdown rendering** (`.md` files, PR bodies, issues): images render at their natural
  pixel size up to the container width, then scale down to fit — never centered by default (GFM
  has no auto-center), but capped at column width. GitHub's own docs and diagram-in-README
  convention rely on this cap so a diagram doesn't overflow the reading column. INFERRED from
  general familiarity with GitHub's rendered-markdown CSS; not independently re-verified in this
  session.
- **Notion**: image/embed blocks default to filling the content column width (not a fixed pixel
  value) and are left-aligned by default, but Notion gives the reader inline resize handles and a
  quick "center" alignment toggle — the point is the *default* is full-column-width, not "small."
- **Confluence's own native editor** (not docspan's synced markdown pipeline, but Confluence
  authoring UI): pasted/embedded images and Confluence's native diagram macros (Gliffy, draw.io,
  and Confluence's own Mermaid-adjacent diagram macros) default to filling available body width up
  to a cap, and offer one-click "center" alignment from the image toolbar. This matches the
  requirements doc's observation that Confluence already centers mermaid diagrams — docspan is
  behind its own host platform's native convention here, only on the fixed-800px-width half of it.
- **Google Docs "Insert > Drawing"**: a drawing/image inserted this way is *not* auto-fit to page
  width — it inserts at whatever size the drawing canvas was set to, left-aligned, and the user
  drags to resize/center manually. This is actually the most instructive comparison for Google
  Docs specifically: it shows that Google Docs' own native insertion flow has the *same* problem
  docspan has today (small, misaligned, manual-fix-required) — Google Docs offers no better native
  pattern to imitate. There is no Google-native "auto-fit inserted image to page width" behavior to
  mirror; the fix has to be applied at push time by docspan itself, because Docs won't do it later.
- **Other markdown-to-Confluence/Docs sync tools** (e.g. `markdown-confluence`, Pandoc's
  docx/gdoc writers): Pandoc's default image handling in docx/odt output scales images down only if
  they exceed the page's printable width, and does not auto-center without an explicit attribute
  (`{ width=100% }` or a `\centering` wrapper in LaTeX-adjacent tooling) — again reinforcing
  "fit-width + explicit-center" as the two independent knobs every tool ends up needing.

**Consensus (INFERRED but convergent across all of the above):** for a diagram embedded inline in
a prose document (as opposed to a full-bleed hero image or a gallery), the community default is
**fit to content-column width, capped by aspect ratio, centered** — not "as large as possible
regardless of column" and not "small thumbnail." Requirements' 6.5in/468pt target for Google Docs
is exactly this pattern. There is no tool surveyed here where "full content width + center" is
considered visually wrong for a diagram; it is the expected default. The judgment call is only
*at what point does "full width" stop being safe* — see §4.

## 2. Reader mental models: zoom asymmetry between Google Docs and Confluence

This is the most important finding for scoping the fix's *value*, not just its mechanics.

- **Google Docs has no native zoom-on-image affordance in the viewing/reading flow.** The
  document-level zoom control (View > Zoom, or the toolbar %) scales the *entire page layout*, not
  an individual image, and most readers don't reach for it mid-read for one figure. A diagram
  rendered small is a **hard readability blocker**: the only recovery is Edit mode, click the
  image, drag a resize handle — an editing action a read-only viewer literally cannot perform, and
  even an editor is unlikely to do for someone else's shared doc. So for Google Docs, "fit to
  content width" is not a polish improvement — it is the only mechanism by which a diagram becomes
  legible without an edit action. This maps directly to the requirements' framing of "genuinely
  hard to read in place."
- **Confluence has a native click-to-zoom lightbox on inline images** (confirmed general
  Confluence behavior — clicking any embedded image opens a full-screen zoomed overlay, no edit
  permission required, available to any reader). This means the *hard* readability blocker that
  exists in Google Docs does not exist in Confluence: a Confluence reader who finds an 800px
  diagram too small has a one-click, zero-permission escape hatch today.
- **Conclusion for Confluence:** the fix there is closer to **aesthetic/consistency** than
  **functional/readability**. The problem statement itself frames Confluence's issue as "reads
  small on modern wide viewports," not "unreadable" — consistent with this. Widening Confluence's
  diagram to content-width instead of a fixed 800px chiefly fixes (a) the diagram looking
  disproportionately small relative to a wide reading column on modern displays, and (b) scan-ahead
  legibility — a reader deciding *whether to bother clicking to zoom* is evaluating the thumbnail,
  and a diagram that already reads clearly at a glance needs zero extra reader action, which is
  still a net improvement even though zoom exists as a fallback.
- **Recommendation:** treat the two backends' identical "fit width + center" *implementation* as
  serving two different-weight jobs: for Google Docs it removes a hard blocker (an accessibility
  and comprehension issue), for Confluence it removes friction and a stale/inconsistent-look
  problem. Both are worth doing; only Google Docs' half should be described as fixing a
  functional defect versus a purely visual one if this ever needs to be prioritized against other
  work.

## 3. Accessibility (WCAG/alt-text)

- **Larger raster images generally help low-vision readers, not hurt them** — WCAG's relevant
  guidance here is 1.4.4 (Resize Text) and 1.4.8/1.4.10 (Reflow), which are about *not blocking* the
  reader from enlarging content, not about penalizing large default sizes. A diagram that starts
  at a legible size by default reduces the number of readers who need assistive zoom at all. There
  is no WCAG success criterion that treats "image is large" as a failure condition; the concerns
  WCAG raises for images are almost entirely about **text alternatives**, not size. Making the
  image bigger is at worst neutral and more often a net accessibility improvement (larger text
  labels inside the rendered diagram become legible without magnification).
- **The actual accessibility gap in docspan's mermaid pipeline is the alt text, and this fix should
  not touch it — but should not make it worse either.** Confirmed by reading
  `_mermaid_image_node()` in
  `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py:326-344`:
  ```python
  alt=f"mermaid diagram {digest}",
  ```
  The docstring at lines 329-334 explains this is deliberate: `alt` is a content-hash string used
  as part of `(alt, width_pt, height_pt)` identity key for the diff engine in
  `docs_request_builder.py`, so an unchanged diagram is recognized as unchanged across pushes. It
  is **not** meant to be, and does not function as, real assistive-technology alt text — a screen
  reader announcing "mermaid diagram a1b2c3d4e5f6" conveys no content. This is a pre-existing gap,
  out of scope for a sizing-only change, but worth flagging explicitly: **if a future width/height
  change to this function accidentally altered the `alt` string format, it would break the
  diff-identity mechanism** (an alt-string change would look like "diagram changed" even when only
  size changed) — so the sizing fix must set `width_pt`/`height_pt` without touching the `alt=`
  line, and the identity key's docstring comment should be re-read by whoever implements this to
  avoid regressing diff behavior. This is a code-correctness note surfaced during UX research, not
  a new accessibility requirement — true alt-text generation is a separate, larger scoped problem
  (it would require sending the mermaid source or rendered SVG text through a captioning step) and
  is out of scope per the requirements doc's explicit "no new config knob" scope statement.

## 4. Edge case: full-width + tall aspect ratio pushing content below the fold

- **Recommendation: accept the tradeoff — full-width-by-aspect-ratio, uncapped on height, is the
  right default.** Reasoning:
  - The alternative today (tiny, fully-visible-on-screen diagram) already fails the reader: a
    diagram small enough to fit entirely above the fold is also usually too small to *read* its
    text labels, especially for anything with more than a handful of nodes. "Fully visible but
    illegible" is not actually a better outcome than "legible but requires scrolling" — scrolling
    is a normal, low-cost, well-understood reading action; squinting or manually resizing (Google
    Docs) or clicking to zoom while losing surrounding context (Confluence) is a worse one.
  - This mirrors how every comparable tool in §1 behaves: none of them cap diagram height to fit
    one screen. GitHub, Notion, and Confluence's native macros all let a wide-aspect or tall-aspect
    image run past the fold; scroll-past-a-large-figure is the standard document-reading affordance
    for exactly this situation, not a defect.
  - A diagram that is unusually tall for its width (many vertical steps) usually corresponds to a
    process with genuinely many steps — compressing it to fit a screen would make an already-dense
    diagram denser and harder to read, which directly contradicts the feature's goal.
  - **One caveat worth naming for the implementer, not necessarily solving now:** an *extremely*
    tall diagram (say, aspect ratio beyond roughly 1:3 or 1:4 width:height) scaled up to full
    468pt/6.5in width could produce a diagram many pages tall in Google Docs, which starts to feel
    like a real layout problem rather than "scroll a bit further." The requirements doc doesn't ask
    for a max-height cap, and adding one would reintroduce a config-knob-shaped decision the
    requirements explicitly rule out ("no new config knob") — so the recommendation is to ship
    width-driven proportional scaling with no height cap for v1, and treat "should extremely tall
    diagrams get a secondary height cap" as a follow-up only if real diagrams in practice hit that
    pathological ratio, not a launch-blocking concern.

## 5. Job-to-be-done

**Functional job:** "Let me understand this diagram's content without leaving my current reading
flow or taking an extra action (zooming, resizing, opening a separate file)." Fit-width + center
directly satisfies this for the common case — most mermaid diagrams (flowcharts, sequence
diagrams, simple state machines) have aspect ratios that read comfortably at content-column width.

**Emotional job:** "Don't make me feel like the tool that generated this document didn't care
about how it displays." A tiny, left-stuck diagram reads as an unpolished/broken artifact — it
signals neglect even when the content is otherwise fine. Centering and right-sizing removes that
signal; this is a low-stakes but real trust/quality-perception effect, similar to why misaligned
or oddly-sized images in a shared doc read as "someone pasted this in a hurry."

**Does sizing+centering fully satisfy the job, or does something else matter more?**
- For the *typical* diagram (a handful of nodes, roughly landscape aspect ratio), fit-width +
  center is sufficient on its own — no caption or external link is needed to fulfill the job.
- For the *edge case* in §4 (unusually tall/dense diagrams), sizing alone doesn't fully close the
  gap: a reader who has to scroll several screens through one diagram loses the "comfortable read"
  feeling even if each individual part is legible. A caption ("Diagram: <name>, N steps") or a
  short surrounding-text cue would help orient a reader before they scroll into a long diagram, but
  this is **not required for this feature's scope** — it's a nice-to-have that would matter more if
  the follow-up in §4 (secondary height handling) is ever pursued. For v1, sizing + centering is
  the right and sufficient scope; a link-to-view-full-size-externally affordance is unnecessary
  because, per §2, both backends already have an escape hatch for a reader who wants more context
  (Docs: zoom the whole page or ask an editor to resize; Confluence: native click-to-zoom) — this
  feature's job is to make that escape hatch unnecessary in the common case, not to build a new one.

## Sources / basis

- Direct code read: `_mermaid_image_node()`,
  `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py:326-344` (confirmed `alt`
  format and its diff-identity purpose via the function's docstring).
- Requirements doc: `project_plans/mermaid-diagram-sizing/requirements.md`.
- All comparative-tool claims (GitHub, Notion, Confluence native editor, Google Docs "Insert >
  Drawing", Pandoc) are INFERRED from general, widely-documented product behavior familiar from
  prior use of these tools — no live re-verification against each product was performed in this
  session (no browser/WebFetch access to authenticated product UIs was exercised for this
  research). If a stakeholder needs a verified citation for any specific comparative claim above,
  flag it for a follow-up check against that product's current UI rather than treating this
  document's characterization as re-confirmed today.
