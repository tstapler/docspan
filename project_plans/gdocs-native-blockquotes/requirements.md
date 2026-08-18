# Requirements: gdocs-native-blockquotes

**Date**: 2026-08-17
**Type**: feature addition
**Complexity**: 3 — system design

## Problem Statement
On the Google Docs backend, a markdown `>` blockquote pushes as a plain paragraph with a literal `>` character prefixed onto the text (`markdown_to_paragraph_parser.py`'s `_walk_block_quote`/`_prefix_node_text`). Google Docs has no native blockquote paragraph style, so this was a deliberate round-trip-fidelity tradeoff — but it means the Doc visibly shows `> some text` instead of a styled callout, which reads as broken formatting to anyone viewing the Doc (a user reported this from a screenshot). LLM-authored markdown keeps reaching for `>` for notes/callouts without knowing it renders this way on this backend.

Two mitigations already shipped (uncommitted in this repo, prior work in this session): a `docspan lint` command flagging `>` usage on `google_docs`-mapped files, and a `docspan style-guide` command that ships authoring guidance inside the installed package for consumers to embed in their own repos. This project is the third, harder mitigation: make the *rendering itself* correct by using Google Docs' native `indentStart`/`borderLeft` paragraph-style fields to produce a real indented/bordered callout, instead of just warning authors away from `>`.

## Baseline
Today, a pushed `> note` line renders in the Google Doc as a plain paragraph reading literally `> note` — no indent, no border, no visual distinction from body text. Pull reconstructs the blockquote purely incidentally: the literal `> ` text survives push → Docs → HTML export → pull unchanged, and happens to be valid CommonMark on the way back in. There is no blockquote-aware code anywhere in the pull path (`nodes_to_markdown.py`) today — confirmed by grep, only `table`/`heading`/`list_item`/`paragraph`/`image` dispatch keys exist.

## Users / Consumers
- docspan users pushing markdown (often LLM-authored) to Google Docs who use `>` for notes/callouts and expect it to look like one.
- docspan's own structural-diff engine (`docs_request_builder.py`), which must keep classifying paragraph identity/restyle-vs-rewrite correctly once blockquote paragraphs carry new style attributes.
- Anyone with existing Google Docs previously pushed under the old literal-`>`-text scheme — their documents must not break or silently lose comments beyond the documented v0.1.0 comment-loss limitation.

## Success Metrics
- A pushed `>` blockquote (including nested `> >` quotes, and a list or fenced code block inside a quote) renders in the Google Doc as an indented, left-bordered callout — no literal `>` character visible in the Doc body.
- Pulling that same Doc reconstructs the original markdown `>` syntax byte-for-byte (round-trip fidelity preserved).
- A Doc pushed under the *old* scheme (literal `>` text, no border) still pulls correctly without modification — no forced migration on read.
- `docspan lint`'s blockquote rule and `style_guide.py`'s Google Docs guidance are updated/removed to reflect that `>` now renders correctly, so they stop flagging non-issues.
- Existing test suite plus new push/pull round-trip tests for plain, nested, list-in-quote, and code-in-quote cases pass.
- A CI/scripted `docspan push` consumer can detect style_upgrade/comment-loss without parsing prose: the `STYLE_UPGRADE_COUNT=<N>` stdout line and `--fail-on-comment-loss` exit-code behavior are both covered by tests.

## Appetite
Medium (1–2 weeks)
*(Scope must fit the appetite. If it doesn't fit, cut scope — do not move the deadline.)*
Re-affirmed: implementation/plan.md's Step 6 Summary rolls up to 17 stories/63 tasks, but per-task
time estimates sum to ~4.5 hours of estimated work, comfortably within this Medium appetite —
including Story 4.3 (below), added after a pre-mortem P1 finding. Scope was not cut to protect the
deadline because the estimate still fits; if that estimate proves wrong during implementation, cut
scope rather than extend the timeline, per the rule above.

## Constraints
- Must not regress the structural diff's comment-preservation guarantee for any paragraph whose content is genuinely unchanged (non-blockquote or already-migrated blockquote).
- Google Docs API constraint (confirmed via web search): `borderLeft`/`borderBetween` (`ParagraphBorder`) cannot be partially updated — the full border object (color, width, dashStyle, padding) must be resent on every write that touches it.
- Must work within the existing `updateParagraphStyle` request shape already used for `namedStyleType` (extend the `fields` mask, don't replace the pattern).

## Non-functional Requirements
- **Performance SLO**: not specified — this changes request payload shape, not request volume; existing 300 req/min rate-limit handling applies unchanged.
- **Scalability**: not applicable.
- **Security classification**: internal (docspan is a CLI tool operating on user-authorized Docs).
- **Data residency**: no special requirements.

## Scope
### In Scope
- New `DocsParagraphNode` fields (`is_blockquote`, `quote_depth`) carrying blockquote identity without embedding it in `node.text`.
- Push-side: `markdown_to_paragraph_parser.py`'s `_walk_block_quote` stops prefixing literal `"> "` text; sets the new fields instead. `docs_request_builder.py`'s paragraph-insert (~line 2508) and restyle (~line 2704) `updateParagraphStyle` call sites emit `indentStart` (scaled by `quote_depth`) and a full `borderLeft` `ParagraphBorder` object using a distinctive, docspan-owned width/color as an identity marker, plus an extended `fields` mask.
- Pull-side: `docs_structure_parser.py` recognizes that marker border/indent combination when parsing a live paragraph and sets `is_blockquote`/`quote_depth` accordingly. `nodes_to_markdown.py` gains a genuine `_group_blockquote_runs` grouping stage (mirroring the existing `_group_code_runs`) plus a `"blockquote"` dispatch-key renderer that reconstructs `"> " * quote_depth` markdown prefixes at render time.
- Full nested-quote support (`> >`, arbitrary depth via indent scaling) and lists/fenced code blocks inside a quote, matching current fidelity.
- Legacy fallback: a paragraph with literal `> ` text but no marker border still round-trips exactly as it does today (no forced rewrite on pull-only workflows).
- `_node_key`/`_content_key` updates in `docs_request_builder.py` so `is_blockquote`/`quote_depth` participate in identity (`_node_key`) but not in restyle-vs-rewrite classification (`_content_key`), analogous to how `render_prefix` and image `src` are already excluded/included per the documented precedent.
- An ADR (following the `ADR-001`/`ADR-003` precedent) documenting the design and the one-time migration/comment-loss tradeoff for already-pushed blockquotes.
- Updates to `docs/backends/google-docs.md`'s Limitations section, `style_guide.py`'s Google Docs guidance, and `lint.py`'s blockquote rule (remove or narrow it, since the underlying rendering problem is fixed).
- Test coverage: push→pull round-trip for plain/nested/list-in-quote/code-in-quote blockquotes; a legacy-literal-text pull test; a restyle-vs-rewrite diff-classification test.
- A `--fail-on-comment-loss` CLI flag on `docspan push` (default off, no behavior change for existing callers) and a structured `STYLE_UPGRADE_COUNT=<N>` stdout line emitted alongside the existing human-readable warning, so CI/scripted callers can detect style_upgrade/comment-loss programmatically instead of it being silent (pre-mortem P1 remediation; see `implementation/plan.md` Epic 4 Story 4.3).

### Out of Scope
- Any change to Confluence's blockquote handling (already native, unaffected).
- Retroactively rewriting already-pushed Docs proactively (migration is lazy — happens naturally on next push of an edited file, not a bulk migration tool).
- Changing how markdown_to_paragraph_parser handles blockquote *content* styling (bold/italic/links inside a quote) beyond what's needed to drop the text prefix — existing span handling is reused as-is.

## Rabbit Holes
- Border-object "full resend" semantics interacting with the existing restyle code path — need to confirm a restyle-only change (e.g. depth changes on an edit) always resends the complete `ParagraphBorder`, not a partial diff, or Google's API will reject/silently ignore it.
- Choosing a marker border color/width that Google Docs won't silently normalize or merge with an adjacent paragraph's border (Docs sometimes coalesces borders between consecutive paragraphs — needs empirical verification against a live document, not just API docs).
- Nested quote depth via `indentStart` scaling interacting with an already-nested list (a blockquote containing a bullet list) — two independent indent sources (list nesting level + quote depth) stacking in `docs_request_builder.py`'s existing list-indent logic.
- Nothing in `nodes_to_markdown.py` currently groups paragraphs by anything other than contiguous code runs — `_group_blockquote_runs` needs to correctly interleave with `_group_code_runs` when a code block appears inside a quote (both grouping stages need to compose, not fight each other in the pull registry).

## Alternatives Considered
- **Style-guide + lint only** (already shipped, uncommitted in this repo): cheapest, but only tells authors to avoid `>` — doesn't fix rendering when `>` is used anyway (e.g. from content docspan doesn't control, like a pasted doc or an LLM that ignores the guidance).
- **Bold lead-in convention** (`**Note:** ...`) as the sole recommended pattern: already the workaround suggested by the style guide; doesn't address documents that already use `>` or third-party content docspan ingests.
- **Skip native styling, just strip `>` and italicize**: rejected — loses the visual "this is a callout" signal entirely rather than fixing it.

## Feasibility Risks
- Google's documented "borders cannot be partially updated" constraint could interact badly with the diff engine's granular per-request style updates if not handled by always resending the full object — needs a spike/prototype against a real Doc early in Phase 3/5, not just docs-reading.
- `_node_key`/`_content_key` changes touch the core diffing engine that protects comment preservation across the whole backend — any mistake here risks regressing an already-fragile, well-tested invariant (this is the highest-blast-radius part of the change).
- No existing pull-side grouping precedent handles two interleaved grouping stages (code runs and blockquote runs) — `_group_code_runs`'s current implementation needs to be read in full before designing `_group_blockquote_runs`'s interaction with it (not yet done as of this writing).

## Observability Requirements
Standard request logging sufficient. No new metrics/alerts — docspan is a CLI tool with no running service; push/pull already report warnings (e.g., dry-run comment-loss warnings) to the terminal, which is the existing pattern this reuses.

## Risk Control
Rely on the existing `push --dry-run` comment-loss warning, which already fires for any paragraph that gets deleted and reinserted (which every already-pushed blockquote will, once its text changes on first push after this ships). Document the one-time migration cost prominently in the CHANGELOG and in `docs/backends/google-docs.md`. No new feature flag or opt-in — ship directly, consistent with how prior structural-diff behavior changes in this codebase have shipped.

## Open Questions
- Exact marker border color/width to use, and whether Google Docs coalesces/normalizes borders between adjac2ent paragraphs in a way that breaks marker-based detection — needs empirical verification (Phase 2 research or a Phase 5 spike against a real test Doc).
- Whether `_group_code_runs` and a new `_group_blockquote_runs` can compose (code block inside a quote) without a larger refactor of the pull registry's grouping architecture — needs the full `_group_code_runs` implementation read before Phase 3 planning finalizes the approach.
- Whether `lint.py`'s blockquote rule should be deleted outright or narrowed (e.g., kept for other backends that might lack native support in the future) once this ships.
