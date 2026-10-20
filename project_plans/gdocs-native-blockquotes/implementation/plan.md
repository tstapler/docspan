# Implementation Plan: gdocs-native-blockquotes

Complexity: 3 (system design). Source: `../requirements.md`, `../research/*.md`.

## Step 0.5 — Creative Alternatives

| # | Approach | Strength | Weakness |
|---|----------|----------|----------|
| A | **Style-guide + lint only** (status quo, tightened wording) | Zero code risk, ships in an hour | Doesn't fix the actual bug — `> note` still renders as literal broken text; just tells the author not to do the thing docspan itself should handle |
| B | **Strip `>` and italicize content** (render blockquote as `_text_` with no border/indent) | Simple one-field change (`italic` span), no new `ParagraphStyle` fields, no diff-engine risk | Loses the "set apart as an aside" visual signal entirely (research/ux.md's job-to-be-done); indistinguishable from an author who just wanted italics; not what any comparable tool (Pandoc/Notion/Confluence) does |
| C (chosen) | **Native indent + `borderLeft` styling, docspan-owned marker, lazy migration** | Matches the cross-tool convention (research/ux.md §1); round-trips losslessly once migrated; reuses proven `_node_key`/`_content_key`/SequenceMatcher machinery already in the codebase | Highest implementation cost of the three; introduces new empirical unknowns (border omission-defaulting, coalescing, table-cell inheritance) that need a live-Doc spike; legacy docs pay a one-time comment-loss cost on first post-migration push |

Approach C is selected — A doesn't fix the reported bug, B under-delivers relative to established convention and this project's own requirements scope. C's extra cost is bounded by the lazy-migration risk control already accepted in requirements.md.

## Step 1 — System Type

Bidirectional sync/diff engine extension: a new paragraph-identity dimension (`is_blockquote`, `quote_depth`) threaded through parse → diff → request-emit (push) and parse → group → render (pull). No new service, no new storage; entirely within the existing `google_docs` backend module boundary (`src/docspan/backends/google_docs/`). Extends an existing Strategy-per-dispatch-key rendering system and an existing SequenceMatcher-based diff engine — not a green-field design.

## Step 2 — Domain Glossary

| Term | Definition |
|---|---|
| `is_blockquote` | New boolean field on `DocsParagraphNode` marking a paragraph as a (possibly nested) blockquote line, independent of its literal text. |
| `quote_depth` | New `int` field on `DocsParagraphNode` (0 = not a quote) giving nesting depth, mirroring `"> " * n` markdown nesting. |
| `BLOCKQUOTE_BORDER_MARKER` | The exact, docspan-owned `ParagraphBorder` value (color/width/dashStyle/padding) written to `borderLeft` and used on pull to recognize a docspan-authored blockquote, distinct from any human-applied Docs UI border. |
| `BLOCKQUOTE_INDENT_PT_PER_LEVEL` | The `Dimension` (points) added to `indentStart` per unit of `quote_depth`, applied cumulatively for nested quotes. |
| `_blockquote_paragraph_style_fields(node)` | New shared helper (architecture.md) computing `(paragraphStyle_dict, fields_mask_list)` for a node's `is_blockquote`/`quote_depth`, called from both the insert-path and restyle-path `updateParagraphStyle` sites so the two never drift. |
| `_node_key` | Existing `docs_request_builder.py` projection used by `difflib.SequenceMatcher` to decide "same node" for alignment; will include `is_blockquote`/`quote_depth`. |
| `_content_key` | Existing `docs_request_builder.py` projection used by `_repair` to fold a pure restyle back to `equal`; stays text-only — `is_blockquote`/`quote_depth` are deliberately excluded, matching the `render_prefix` precedent. |
| `_group_blockquote_runs` | New pull-side grouping stage in `nodes_to_markdown.py`, the outer partition of a node sequence into blockquote runs vs. passthrough nodes; recursively invokes `_group_code_runs` on each run's inner nodes before rendering. |
| `BlockquoteNodeRenderer` | New `MarkdownRenderRegistry` entry (dispatch key `"blockquote"`) that reconstructs `"> " * quote_depth` prefixes at render time from a grouped run's inner rendered lines. |
| Legacy blockquote | A previously-pushed paragraph whose markdown/text still carries a literal `"> "` prefix but whose live Doc paragraph has no `BLOCKQUOTE_BORDER_MARKER`/indent — detected, not migrated, until next edited push. |
| `style_upgrade` | New value added to `HighRiskParagraph.reasons: List[Literal[...]]`, marking a paragraph rewritten solely to add native blockquote styling for the first time (no textual change from the author). |
| Marker-border coalescing | Docs UI visual behavior where certain border sub-fields (`borderTop`/`borderBottom`/`borderBetween`) merge across adjacent paragraphs; resolved by research as N/A to `borderLeft`, which always renders per-paragraph. |

10 glossary terms.

## Step 3 — Pattern Decisions

| Component | Pattern chosen | Alternative rejected | Reason |
|---|---|---|---|
| Style-field computation (push) | Single shared helper function (`_blockquote_paragraph_style_fields`) called from both call sites | Duplicate inline dict construction at each `updateParagraphStyle` site | Two independent implementations of the same marker constant is exactly the "whack-a-mole" failure shape pitfalls.md documents (12-commit precedent, `31b4edd`) — one source of truth for the marker fields prevents drift between insert and restyle paths |
| Paragraph identity (diff) | Extend existing `_node_key`/`_content_key` key-projection functions (Strategy-like key objects already in place) | New dedicated `BlockquoteIdentity` value object/class | The codebase's own precedent (`render_prefix`, image `src`) is field-level tuple extension, not a new abstraction; introducing a class here diverges from a working, well-tested pattern for no benefit |
| Pull-side grouping | Outer/inner composition of two independent single-purpose grouping stages (`_group_blockquote_runs` wraps, recursively re-invokes `_group_code_runs`) | Merge blockquote and code-run detection into one combined grouping function | Composition preserves `_group_code_runs` untested-change risk at zero (confirmed compatible by reading its predicates — none inspect `is_blockquote`); a merged function would need re-validating both behaviors together |
| Pull-side rendering | New `MarkdownRenderRegistry` entry (`"blockquote"` dispatch key), same Strategy-object-per-key pattern as existing 5 renderers | Special-case `if` branch inside `render_nodes_to_markdown` | Consistent with the existing extensible registry (Strategy pattern) — adding a 6th key is the designed extension point, an inline branch would be an inconsistent one-off |
| Dry-run UX | Extend `HighRiskParagraph.reasons` typed `Literal` with `"style_upgrade"` (type-driven design: closed sum type) | Add a free-form `notes: str` field | `Literal` sum type keeps `render_high_risk`'s per-reason rendering exhaustive and type-checkable; a free-text field would let call sites drift into inconsistent wording, the exact thing the typed-reasons pattern was introduced to prevent |
| Legacy detection (migration) | Lazy/implicit — detected at diff time via existing churn-pairing (`find_churn_pairs`) plus a new `style_upgrade`-reason tag, no explicit migration command | Explicit `docspan migrate-blockquotes` CLI subcommand | requirements.md's Risk Control section already commits to lazy migration + no feature flag; an explicit command is unrequested scope and duplicates what push already does on every affected paragraph |
| lint.py / style_guide.py | **Delete** `find_blockquote_issues`/the corresponding `LintIssue`-producing rule and the "Don't use `>` blockquotes" bullet in `GOOGLE_DOCS_STYLE_GUIDE` outright | Narrow the rule (e.g., only fire for nested/table-cell quotes still known-broken) | `lint.py`'s own module docstring already scopes this check specifically to google_docs (Confluence renders blockquotes natively); once google_docs also renders blockquotes natively, there is no backend left for the rule to protect against — keeping a narrowed version "for future backends" is speculative and untestable YAGNI, and a stale lint rule actively contradicts the new behavior it's supposed to be warning about |

## Step 4 — Migration Plan

Lazy, in-place, no feature flag (per requirements.md's Risk Control, confirmed unchanged by this research pass):

- Ship the new push/pull code paths together — behind no flag. From release day, every *new* blockquote pushed writes native styling.
- A **legacy blockquote** (literal `> ` text already in a pushed Doc, no marker present) is left untouched until that specific paragraph is next pushed with any change (including a no-op-looking whitespace/re-render change if the diff engine treats the loss of the `> `-embedded-text as a text change, which it will, since the markdown source stops emitting the literal prefix).
- On that push, the paragraph is deleted and reinserted (the existing delete+reinsert path, since removing the literal `"> "` from `node.text` is itself a text change, not a pure restyle) — this is a one-time, per-paragraph cost, consistent with requirements.md's accepted tradeoff. Any comment anchored to that paragraph is lost, same as any other delete+reinsert today (`docs/backends/google-docs.md`'s documented limitation).
- No bulk/eager migration tool is in scope (rejected in Step 3's Pattern Decisions table).
- `docs/backends/google-docs.md`'s Limitations section is updated (Story 3.3) to describe both the new native rendering and this one-time legacy-migration caveat, and the CHANGELOG entry documents it prominently, per research/ux.md item 2.

## Step 5 — Observability Plan

No new metrics/logging infrastructure exists in this codebase to extend (confirmed: `google_docs` backend has no telemetry emission today). Observability for this feature is entirely CLI-surfaced:

- `push --dry-run` output: the extended `HighRiskParagraph`/`render_high_risk`/`render_churn_note` warnings (Epic 4) are the only observability surface — an operator running dry-run sees exactly which paragraphs are about to be rewritten for `style_upgrade` before it happens.
- Existing test suite (unit, mocked Docs responses) plus the required manual live-Doc spike (Epic 0) are the verification observability for this feature — there is no runtime dashboard to build.

## Step 6 — Risk Control (carried from requirements.md, restated with owners)

| Risk | Mitigation | Story |
|---|---|---|
| Comment loss on legacy migration | Documented, accepted one-time cost; dry-run warns via `style_upgrade` reason | Epic 4 |
| Border omission-defaulting behavior unverified | Live-Doc spike before Epic 2/3 land | Epic 0 |
| Border coalescing (resolved: `borderLeft` doesn't coalesce) | No spike needed for this specific question; documented in ADR-001 | ADR-001 |
| False-positive marker detection (human-applied border coincidentally matches) | Choose a distinctive, documented color/width combination (Epic 0 spike output feeds the constant); accepted as probabilistic, not exact — documented as an explicit non-goal-of-perfection in ADR-001 | Epic 0, ADR-001 |
| Table-cell style inheritance onto adjacent borders | Live-Doc spike; if confirmed, cell-fill path must explicitly clear `indentStart`/`borderLeft` alongside existing `namedStyleType` force | Epic 0, Epic 2 |
| `_repair` global content-key cross-pairing (pitfalls.md item 2) | Confirm `_structural_score` weighs `is_blockquote`/`quote_depth` before relying on `_content_key` staying text-only; add explicit test | Epic 1 |
| Payload/batchUpdate size cap on many-legacy-quote documents | Not verified this pass; flagged as Unresolved Question below, not blocking initial ship (requirements.md treats it as accepted risk, not a blocker); documented as a known risk in `docs/backends/google-docs.md` | Unresolved Questions, Story 3.4 |
| List-nesting indent stacking with quote-depth indent (Rabbit Hole) | Composition rule decided and documented explicitly: additive by construction, since list indent (Bullets preset, tab-derived) and quote indent (`paragraphStyle.indentStart`) are independent fields; verified against a live Doc, not assumed | Story 2.6 |

## Unresolved Questions

| # | Question | Blocks | Owner |
|---|---|---|---|
| 1 | Exact `BLOCKQUOTE_BORDER_MARKER` color/width/dashStyle and `BLOCKQUOTE_INDENT_PT_PER_LEVEL` value | Epic 0 spike must run before Epic 2/3 tasks that hardcode the constant | Implementer, live-Doc spike |
| 2 | Does omitting a `ParagraphBorder` sub-field on write leave it unset or reset to a Docs default? Mitigated (not fully resolved) by Story 3.1's sub-field-only comparison, which tolerates extra Docs-echoed defaults regardless of the answer — but the spike should still record the actual behavior for Epic 2's write-side request shape. | Epic 2's insert/restyle request shape (whether the full object must always be resent) | Implementer, live-Doc spike |
| 3 | Does a table cell inherit an adjacent blockquote's `indentStart`/`borderLeft` the way it inherits `namedStyleType`? | Epic 2's cell-fill path (Story 2.4) | Implementer, live-Doc spike |
| 4 | Undocumented per-`batchUpdate` payload size cap for documents with many legacy quotes | Not blocking ship; flagged as a known risk in `docs/backends/google-docs.md` (Story 3.4) rather than left completely undocumented | Follow-up, not this project |
| 5 | Does `_structural_score` need an explicit `is_blockquote`/`quote_depth` scoring term, or does existing style comparison already cover it? | Epic 1, Story 1.3 (must be answered by reading `_structural_score`'s body, not yet done in this research pass) | Implementer |
| 6 | If `BLOCKQUOTE_BORDER_MARKER` is ever changed in a future release, documents pushed under the old marker silently stop being recognized on pull, with no designed migration path (ADR-001 Consequences). Left unresolved here: designing that path is speculative before a concrete need exists (no first-class Docs custom-paragraph-style-id API today) and would be new scope, not a gap in this project's delivery. | None — accepted as a documented future-maintainer note, not a blocker | Follow-up, not this project |

## Scope Decision: list-containing-a-quote (`- > note`)

**Out of scope for this project.** `_walk_list_items`'s generic `else` branch (`markdown_to_paragraph_parser.py:243-244`) calls `_spans_from_inline([child])` on a `block_quote` child token today, which already silently mis-renders (confirmed by direct code read, matching features.md's finding) — this is a **pre-existing** gap, not one introduced or worsened by this project. The requirements doc scopes this project to "quote containing a list" (the in-scope direction) and its Out-of-Scope section does not mention the reverse case; fixing it requires new logic in `_walk_list_items` unrelated to the border/indent styling work here. Filed as a follow-up idea, not a story in this plan, to keep this project's blast radius matched to its stated appetite (Medium, 1-2 weeks).

## Dependency Visualization

```
Epic 0 (Spike: live-Doc verification)
   |
   +--> Epic 1 (Domain model: DocsParagraphNode fields, _node_key/_content_key)
   |        |
   |        +--> Epic 2 (Push: parser + request-builder styling)
   |        |        |
   |        +--> Epic 3 (Pull: structure parser + markdown renderer)
   |
   +--> Epic 4 (UX: HighRiskParagraph style_upgrade reason)
            ^ depends on Epic 2 existing (needs a real style-change signal to detect)
   |
   +--> Epic 5 (Cleanup: lint.py / style_guide.py deletion, docs)
            ^ depends on Epic 2+3 being functionally complete (don't delete the warning
              before the fix it warns about actually ships)
```

---

## Epic 0: Live-Doc Spike (Unblocks all styling work)

### Story 0.1: Determine marker constant and border-write semantics
**Acceptance Criteria:**
- Given a throwaway real Google Doc, When a paragraph is written with a candidate `borderLeft` (color, width, dashStyle) and `indentStart`, Then re-fetching the document via `documents.get` shows the exact same values back (or documents the actual defaulting behavior if some sub-field is dropped).
- Given two adjacent paragraphs both carrying the candidate `borderLeft`, When rendered in the Docs UI, Then they render as two independent left borders, not one coalesced border (confirming the already-resolved research finding empirically, since `borderLeft` genuinely differs from `borderTop`/`borderBottom`/`borderBetween`).
- Given a blockquote paragraph inserted as the first paragraph of a freshly created table cell, When the cell-fill request completes and the document is re-fetched, Then it's recorded whether the adjacent cell paragraph shows any inherited `indentStart`/`borderLeft`.

**Tasks:**
1. Write a standalone throwaway script (not committed) using existing `google_docs_client.py` auth helpers to send one `batchUpdate` with a candidate `borderLeft`/`indentStart` `updateParagraphStyle` request against a test Doc. (3 min)
2. Re-fetch via `documents.get` and diff the returned `paragraphStyle` against the request sent. (3 min)
3. Repeat with two adjacent paragraphs to observe coalescing. (3 min)
4. Repeat inside a freshly created table cell to observe inheritance. (3 min)
5. Record the confirmed `BLOCKQUOTE_BORDER_MARKER` value and inheritance finding directly into `src/docspan/backends/google_docs/docs_structure_parser.py`'s planned constant location (Epic 1, Story 1.1) as a code comment citing this spike. (2 min)
6. Commit the spike's raw request/response JSON (the `batchUpdate` request and the subsequent `documents.get` response) as a small fixture file under the test tree, and add a manual/skip-tagged integration test that can re-run the same request against a live Doc later — so if Google's API behavior ever drifts, there's a re-runnable check rather than only a code comment citing a one-time finding (adversarial-review.md concern: an uncommitted, one-person manual spike has no durable regression coverage). (5 min)
8. **(Pre-mortem P1 #1)** Repeat tasks 1–2 against at least one additional Google account/Workspace domain beyond the implementer's own (e.g. a second test account in a different Workspace), and record whether the re-fetched `borderLeft`/`indentStart` values match byte-for-byte; if a domain normalizes color representation or rounds a value differently, that becomes a documented Epic 0 finding (not an assumption) before `BLOCKQUOTE_BORDER_MARKER` is fixed in Story 1.1. The committed fixture test from task 6 is wired into the project's regular CI test run (not left skip-tagged for manual-only execution) using recorded fixture data, so drift is caught on every run rather than only when someone remembers to run it manually. (8 min)
7. Against the same throwaway Doc, view the pushed paragraph in grayscale/print-preview and spot-check the chosen `BLOCKQUOTE_BORDER_MARKER` color with a contrast checker, recording both results as spike notes — verifies ux.md's UX acceptance criteria #1 (indent alone distinguishes the quote without color vision) and #2 (border contrast ratio ≥ 3:1 against white). (5 min)

---

## Epic 1: Domain Model — Identity Fields

### Story 1.1: Add `is_blockquote`/`quote_depth` fields and the shared marker constants
**Acceptance Criteria:**
- Given a `DocsParagraphNode` instance, When constructed with `is_blockquote=True, quote_depth=2`, Then both fields are readable attributes with those exact values, following the same per-field diff-key commenting style as the existing fields (`docs_structure_parser.py:147-188`).
- Given a `DocsParagraphNode` constructed with an illegal combination (`is_blockquote=False, quote_depth=2` or `is_blockquote=True, quote_depth=0`), When constructed, Then `__post_init__` raises `ValueError` — closing the illegal-state pair flagged by architecture-review.md concern 1 without a wider field-shape change (rejected: collapsing to a single derived field would ripple into every story in this plan that sets both fields; a construction-time invariant is the cheaper fix for the same defect).
- Given the module `docs_structure_parser.py`, When read, Then it defines `BLOCKQUOTE_BORDER_MARKER: dict` and `BLOCKQUOTE_INDENT_PT_PER_LEVEL: float` module-level constants with values fixed by Epic 0's spike, each with a one-line comment citing the spike finding, and a docstring/comment stating this module is the sole owner of both constants.
- Given `src/docspan/backends/google_docs/docs_request_builder.py`, When it needs either constant (Story 2.2's `_blockquote_paragraph_style_fields`), Then it imports them from `docs_structure_parser` rather than redefining or copying their values — a test asserts `docs_request_builder.BLOCKQUOTE_BORDER_MARKER is docs_structure_parser.BLOCKQUOTE_BORDER_MARKER` (object identity, not just equal values), resolving architecture-review.md's undeclared-ownership-direction concern.

**Tasks:**
1. Add `is_blockquote: bool = False` and `quote_depth: int = 0` fields to `DocsParagraphNode` in `src/docspan/backends/google_docs/docs_structure_parser.py` (near the existing field list, L147-188), with inline comments following the file's existing "part of diff key or not" annotation style. (3 min)
2. Add a `__post_init__` on `DocsParagraphNode` enforcing `is_blockquote == (quote_depth > 0)`, raising `ValueError` on violation, with a one-line comment explaining these two fields are an intentionally-paired invariant rather than independent. (3 min)
3. Add `BLOCKQUOTE_BORDER_MARKER` and `BLOCKQUOTE_INDENT_PT_PER_LEVEL` module-level constants in `docs_structure_parser.py` (values from Epic 0), with a comment declaring this module as the single owner; import both by name into `docs_request_builder.py` at the top-level (no re-definition, no copied literal). (2 min)
4. Add a unit test in the existing `docs_structure_parser` test file constructing a `DocsParagraphNode` with the new fields and asserting default values are `False`/`0` for backward compatibility with all existing call sites that don't pass them, plus a test asserting the `__post_init__` invariant raises on both illegal combinations. (5 min)
5. Add a unit test (either module's test file) asserting `docs_request_builder`'s imported constants are the *same object* as `docs_structure_parser`'s, per the ownership acceptance criterion above. (3 min)

### Story 1.2: Thread `is_blockquote`/`quote_depth` through `_node_key` (identity) without touching `_content_key`
**Acceptance Criteria:**
- Given two `DocsParagraphNode`s with identical `text` but `is_blockquote=True, quote_depth=1` vs `is_blockquote=False, quote_depth=0`, When `_node_key` is called on each, Then the returned tuples differ.
- Given the same two nodes, When `_content_key` is called on each, Then the returned tuples are identical (text-only), so a pure blockquote-styling restyle can still fold to `equal` via `_repair`.

**Tasks:**
1. Edit `_node_key` in `src/docspan/backends/google_docs/docs_request_builder.py` (L225-285) to append `(node.is_blockquote, node.quote_depth)` to its returned tuple, updating its docstring to document the addition alongside the existing `render_prefix`/`is_code_line` precedent. (4 min)
2. Confirm (do not edit) `_content_key` (L328-367) remains text-only for paragraphs — add a one-line docstring note explicitly stating `is_blockquote`/`quote_depth` are excluded here by design, mirroring the existing `render_prefix` exclusion note. (2 min)
3. Add a unit test asserting the Given-When-Then above directly against `_node_key`/`_content_key`. (4 min)

### Story 1.3: Verify `_structural_score` accounts for blockquote identity in `_repair`'s cross-document pooling
**Acceptance Criteria:**
- Given a document containing a blockquote paragraph with text "See the docs" and a separate non-blockquote paragraph with identical text "See the docs" elsewhere, When `_repair` runs its content-key pooling pass, Then the blockquote paragraph is not misclassified as a restyle target using the non-blockquote paragraph's style (or vice versa).
- **(Pre-mortem P1 #3, hard requirement, not conditional)** This regression test must pass and be committed, and Task 1's finding on `_structural_score`'s existing behavior must be written down in the Epic 1 PR description, before Epic 2 is allowed to merge — "if needed" language describing Task 2 refers only to whether a *code change* to `_structural_score` is required, not to whether the read (Task 1) or the regression test (Task 3) can be skipped. Both are mandatory regardless of Task 1's outcome.

**Tasks:**
1. Read `_structural_score`'s full body in `docs_request_builder.py` (locate via `sg`/Grep — not yet read this pass) to determine whether it inspects `node.style`/paragraph-level style fields that would already catch this, or needs an explicit `is_blockquote`/`quote_depth` term added. Record the finding in the Epic 1 PR description regardless of outcome — this is a mandatory task, not optional. (5 min)
2. If Task 1 shows a gap, add `is_blockquote`/`quote_depth` equality as a scoring term in `_structural_score`. (5 min, only the code change itself is conditional on Task 1's finding)
3. Add a regression test reproducing the Given-When-Then scenario above (two same-text paragraphs, one blockquote, one not, in the same document). This test is a mandatory Epic 2 merge gate, not an optional follow-up, regardless of whether Task 2 required a code change. (5 min)

---

## Epic 2: Push — Parser and Request Builder

### Story 2.1: `_walk_block_quote` stops prefixing text, sets `is_blockquote`/`quote_depth` instead
**Acceptance Criteria:**
- Given the markdown `"> hello\n"`, When parsed by `_walk_block_quote` in `markdown_to_paragraph_parser.py` (L363-401), Then the resulting `DocsParagraphNode.text` is exactly `"hello"` (no `"> "` prefix) and `is_blockquote=True, quote_depth=1`.
- Given nested markdown `"> > nested\n"`, When parsed, Then the resulting node has `text == "nested"` and `quote_depth == 2`.
- Given `"> "` (an empty quote line), When parsed, Then the resulting node has `text == ""`, `is_blockquote=True, quote_depth=1` — and (see Story 2.5) is not dropped by `projection.py`'s blank-paragraph rule.
- Given a fenced code block inside a quote (`"> \`\`\`python\ncode\n\`\`\`\n"`), When parsed, Then `_walk_block_quote`'s `block_code` branch calls `_nodes_from_code_block(child, emit_language_marker=True, ...)` (not the current default `False`), the emitted marker node carries `is_blockquote=True`/the enclosing `quote_depth` (not left at the type's defaults), and the resulting node sequence round-trips through push→pull with the `python` language tag intact (architecture-review.md blocker: today's call site never threads `emit_language_marker`, so `lang` always resolves to `None` on pull).

**Tasks:**
1. Rewrite `_walk_block_quote` (L363-401) to stop calling `_prefix_node_text` and instead set `is_blockquote=True`/`quote_depth=<depth>` on each produced `DocsParagraphNode`. (5 min)
2. In the same rewrite, change the `block_code` branch's `_nodes_from_code_block(child)` call to `_nodes_from_code_block(child, emit_language_marker=True)`, mirroring the top-level `parse()` call site (`markdown_to_paragraph_parser.py:518`), and set `is_blockquote=True`/`quote_depth=<depth>` on the returned marker node exactly as on the other code-line nodes it emits, so `_group_blockquote_runs` (Story 3.2) includes the marker in the run before `_group_code_runs` looks for it recursively. (4 min)
3. Verify (with a test, not by assumption) that `_is_language_marker` (`nodes_to_markdown.py:317-325`) still matches once the marker node carries `is_blockquote=True` — its docstring currently claims list items/blockquotes are "marker-less on purpose" specifically because `_prefix_node_text` broke the single-span invariant; update that docstring/guard to reflect that blockquotes now emit a real marker while list items still don't, since `_prefix_node_text` is no longer in the blockquote path. (4 min)
4. Delete `_prefix_node_text` (L345-360) after confirming (via `sg`/Grep) it has no other callers. (3 min)
5. Add/update unit tests for plain, nested, empty-line, and code-in-quote blockquote parsing per the four Given-When-Then cases above, including an explicit assertion that the fence's language tag survives a full push→pull round trip (closing the gap in the required round-trip test matrix). (6 min)
6. Add a quick regression test confirming `_walk_list_items`'s pre-existing `- > note` mis-render bug (generic `else` branch calling `_spans_from_inline([child])` on a `block_quote` token, `markdown_to_paragraph_parser.py:243-244`) doesn't silently change failure *mode* now that `_walk_block_quote` no longer emits a literal `"> "` prefix — assert the current (broken) output shape so a future change to that branch is a deliberate decision, not an unnoticed regression (adversarial-review.md concern: this project doesn't fix that bug, but must not make it silently worse). (4 min)

### Story 2.2: `_blockquote_paragraph_style_fields` shared helper
**Acceptance Criteria:**
- Given a `DocsParagraphNode` with `is_blockquote=True, quote_depth=2`, When `_blockquote_paragraph_style_fields(node)` is called, Then it returns a `paragraphStyle` dict containing `indentStart` scaled to `2 * BLOCKQUOTE_INDENT_PT_PER_LEVEL` and `borderLeft` equal to `BLOCKQUOTE_BORDER_MARKER`, plus a `fields` list `["indentStart", "borderLeft"]`.
- Given a node with `is_blockquote=False`, When called, Then it returns an empty dict and empty fields list (so non-blockquote paragraphs are unaffected).

**Tasks:**
1. Add `_blockquote_paragraph_style_fields(node) -> Tuple[dict, List[str]]` to `src/docspan/backends/google_docs/docs_request_builder.py` near `_restyles`/`_make_style_update_requests` (around L2643). (5 min)
2. Add a unit test for both branches (blockquote / non-blockquote) per the Given-When-Then above. (3 min)

### Story 2.3: Wire the helper into insert-path and restyle-path `updateParagraphStyle` requests
**Acceptance Criteria:**
- Given a new `DocsParagraphNode` with `is_blockquote=True, quote_depth=1` being inserted (no prior live node), When `_build_insert_requests` emits its `updateParagraphStyle` request (L2507-2513), Then the request's `paragraphStyle` includes the blockquote fields merged alongside `namedStyleType`, and `fields` is `"namedStyleType,indentStart,borderLeft"`.
- Given an existing live node with `is_blockquote=False` being restyled to a target node with `is_blockquote=True, quote_depth=1` (text unchanged), When `_restyles` is evaluated, Then it returns `True` (today it only compares `style`/`is_list_item` — must be extended), and `_make_style_update_requests` emits the merged blockquote fields in its `updateParagraphStyle` request.

**Tasks:**
1. Extend `_restyles` (L2643-2666) to also return `True` when `current_node.is_blockquote != target_node.is_blockquote or current_node.quote_depth != target_node.quote_depth`. (3 min)
2. Extend `_make_style_update_requests` (L2668-2724) to call `_blockquote_paragraph_style_fields(target_node)` and merge its dict/fields into the existing `updateParagraphStyle` request construction (L2702-2709), independent of the existing `if current_node.style != target_node.style` guard (since a pure blockquote change must still fire this branch). (5 min)
3. Extend the insert path (`_build_insert_requests`, L2507-2513) similarly, merging `_blockquote_paragraph_style_fields(node)`'s output into the existing request dict/fields string. (4 min)
4. Add unit tests for both insert-new-blockquote and restyle-existing-to-blockquote per the Given-When-Then above, using mocked request assertions (existing test pattern in this file's test suite). (5 min)

### Story 2.4: Table-cell fill path clears blockquote fields explicitly (contingent on Epic 0 finding)
**Acceptance Criteria:**
- Given Epic 0's spike confirms table-cell inheritance of `indentStart`/`borderLeft` (Unresolved Question 3), When a non-blockquote paragraph is filled into a table cell adjacent to a blockquote, Then the cell-fill request explicitly clears `indentStart`/`borderLeft` alongside the existing `namedStyleType` force (mirroring the `e6db797` precedent).

**Tasks:**
1. Locate the table-cell-fill request construction (the code path forcing `NORMAL_TEXT`, referenced in pitfalls.md re: `e6db797`) via `sg`/Grep. (3 min)
2. If Epic 0 confirmed inheritance, add explicit `indentStart`/`borderLeft` clearing to that request; if not confirmed, skip this task and link the spike's raw before/after `documents.get` JSON (committed per Story 0.1's fixture task) in the PR description as the evidence for "not applicable" — an unsubstantiated assertion in the PR body is not sufficient (adversarial-review.md concern: a live-Doc finding checked by only one person needs a citable artifact, not an honor-system note). (5 min)

### Story 2.6: Decide and implement the list-in-quote indent-stacking composition rule
**Acceptance Criteria:**
- Given `docs_request_builder.py:2657-2662`'s existing comment (confirmed by reading the code: list-item indentation is derived by `CreateParagraphBulletsRequest` from leading-tab count in the paragraph's *text*, not from any `paragraphStyle.indentStart` docspan sets), and `_blockquote_paragraph_style_fields` (Story 2.2) setting `paragraphStyle.indentStart` purely from `quote_depth`, When a `DocsParagraphNode` is both `is_list_item=True` (with some `nesting_level`) and `is_blockquote=True` (with some `quote_depth`), Then the composition rule is: the two indent sources are independent and additive by construction — `indentStart` carries only the quote-depth contribution, and the Bullets preset applies its own nesting-level indent *relative to* whichever `indentStart` baseline the paragraph already has, so no combined-indent computation is written in docspan code. This is the documented decision (not left implicit) closing requirements.md's Rabbit Hole and adversarial-review.md's blocker.
- Given a live Doc, When a blockquote-containing bullet list is pushed and re-fetched (extending Epic 0's spike), Then the rendered result visually shows both the quote's left border/indent and the list's own bullet indent stacked, confirming the additive-by-construction claim empirically rather than assuming it from the code comment alone.

**Tasks:**
1. Read `docs_request_builder.py`'s list-indent code path in full (the `CreateParagraphBulletsRequest`/`nesting_level` handling referenced at L2657-2662) to confirm no other code path independently sets `indentStart` for list items that would conflict with the blockquote helper's write. (4 min)
2. Record the additive-by-construction decision above directly in this plan and as a code comment at `_blockquote_paragraph_style_fields` (Story 2.2) noting it composes with list nesting without extra logic. (3 min)
3. Extend Epic 0's Story 0.1 spike (add a 5th sub-check) to push a blockquote containing a bullet list to the throwaway test Doc and re-fetch, recording whether the visual stacking matches the decision above; if it does not, this task blocks Epic 2/3 exactly as Epic 0's other findings do. (4 min)
4. Add a unit test asserting a node with both `is_list_item=True`/`nesting_level=1` and `is_blockquote=True`/`quote_depth=1` produces both the expected Bullets-preset request (unchanged from today's list handling) and the expected `indentStart`/`borderLeft` fields from `_blockquote_paragraph_style_fields` in the same `updateParagraphStyle`/insert request, with neither field clobbering the other. (5 min)

### Story 2.5: `projection.py` blank-paragraph-drop rule gets a blockquote carve-out
**Acceptance Criteria:**
- Given a `DocsParagraphNode` with `text == ""` and `is_blockquote=True`, When `projection.project()` runs (the rule at `projection.py:148`), Then the node is **not** dropped (an empty quote line `>` is meaningful structure, unlike an ordinary blank paragraph).
- Given a `DocsParagraphNode` with `text == ""` and `is_blockquote=False`, When `projection.project()` runs, Then existing drop behavior is unchanged.

**Tasks:**
1. Edit the condition at `src/docspan/backends/google_docs/projection.py:148` to add `and not node.is_blockquote` to the blank-paragraph-drop check. (2 min)
2. Add a unit test for both branches per the Given-When-Then above. (4 min)

---

## Epic 3: Pull — Structure Parser and Markdown Renderer

### Story 3.1: `_parse_paragraph` recognizes the marker and sets `is_blockquote`/`quote_depth`
**Acceptance Criteria:**
- Given a live Docs API paragraph JSON with `paragraphStyle.borderLeft` equal to `BLOCKQUOTE_BORDER_MARKER` and `paragraphStyle.indentStart` equal to `1 * BLOCKQUOTE_INDENT_PT_PER_LEVEL`, When `_parse_paragraph` (`docs_structure_parser.py:518-604`) processes it, Then the constructed `DocsParagraphNode` has `is_blockquote=True, quote_depth=1`.
- Given a paragraph with some other, non-matching `borderLeft` (a human-applied callout border), When parsed, Then `is_blockquote=False, quote_depth=0` (no false positive).
- Given a live Docs API paragraph JSON whose `borderLeft` carries every sub-field docspan writes (`color`, `width`, `dashStyle`) equal to `BLOCKQUOTE_BORDER_MARKER`'s but with an *extra* sub-field Docs echoed back that the literal constant doesn't mention (e.g. a normalized `padding` default), When parsed, Then it still matches — detection compares only the sub-fields docspan actually writes, not whole-dict equality (architecture-review.md concern: blanket `==` breaks the moment Docs echoes back any Docs-side default docspan didn't specify, which is exactly the open question in Unresolved Question 2).

**Tasks:**
1. Add a helper (e.g. `_detect_blockquote_depth(paragraph_style: dict) -> int`) in `docs_structure_parser.py` that compares only `borderLeft`'s `color`/`width`/`dashStyle` sub-fields against the corresponding sub-fields of `BLOCKQUOTE_BORDER_MARKER` (not the two dicts wholesale), and derives depth from `indentStart / BLOCKQUOTE_INDENT_PT_PER_LEVEL`. Decide and document this sub-field comparison explicitly as part of Epic 0's spike output (Story 0.1 task 5), rather than leaving it implicit. (5 min)
2. Call this helper from `_parse_paragraph` (around L593-604) and pass its result into the `DocsParagraphNode(...)` constructor call. (3 min)
3. Add unit tests for match, non-match, and extra-echoed-sub-field cases per the Given-When-Then above, using representative Docs API JSON fixtures. (6 min)

### Story 3.1b: Legacy literal-text pull test (no forced migration on read)
**Acceptance Criteria:**
- Given a live Docs API paragraph JSON with plain `paragraphStyle` (no `borderLeft`, no `indentStart`) and text `"> legacy note"`, When pulled end-to-end (structure parse → markdown render), Then the rendered markdown is unchanged: `"> legacy note"` — confirming no blockquote-aware code path (`is_blockquote`/`_group_blockquote_runs`/`BlockquoteNodeRenderer`) fires for it, matching requirements.md's Success Metric ("A Doc pushed under the old scheme... still pulls correctly without modification") and its explicitly required "legacy-literal-text pull test" (adversarial-review.md blocker: no story previously implemented this required coverage).

**Tasks:**
1. Add a fixture representing a legacy Docs API paragraph: plain `paragraphStyle`, literal `"> legacy note"` text, no marker border/indent. (2 min)
2. Add an end-to-end test (structure parser → `render_nodes_to_markdown`) asserting the output text is byte-identical to the legacy input, and that the parsed node has `is_blockquote=False, quote_depth=0`. (4 min)
3. Add a second case for a legacy *nested* quote (`"> > legacy nested"`, still plain style) confirming the same passthrough behavior at depth. (3 min)

### Story 3.2: `_group_blockquote_runs` pull-side grouping stage
**Acceptance Criteria:**
- Given a node sequence `[para(is_blockquote=False), para(is_blockquote=True, depth=1), para(is_blockquote=True, depth=1), para(is_blockquote=False)]`, When `_group_blockquote_runs` processes it, Then it returns `[("node", node0), ("blockquote", 1, [node1, node2]), ("node", node3)]`.
- Given a blockquote run whose inner nodes include a code-block-marker sequence (three lines forming a fenced code block per `_group_code_runs`'s existing detection), When `_group_blockquote_runs` processes the outer sequence, Then the returned `"blockquote"` tuple's inner list has already had `_group_code_runs` applied to it (containing a nested `("code", lang, [...])` tuple) **with `lang` equal to the original fence's language** (e.g. `"python"`, not `None`) — this requires Story 2.1's `emit_language_marker=True` fix on the push side; without it `lang` is always `None` and this criterion cannot pass, confirming the outer/inner composition from Step 3's Pattern Decisions actually preserves fidelity, not just structure.

**Tasks:**
1. Add `_group_blockquote_runs(nodes)` to `src/docspan/backends/google_docs/nodes_to_markdown.py` near `_group_code_runs` (L339-399), following the same `("node", node)` / `(kind, ...)` tuple convention, keyed on `node.is_blockquote`/`node.quote_depth` contiguity. (5 min)
2. Inside each detected blockquote run, recursively call `_group_code_runs` on the inner node sublist before appending it to the returned tuple. (3 min)
3. Add unit tests for both Given-When-Then cases above (plain run grouping; composed with a nested code run). (5 min)

### Story 3.3: `BlockquoteNodeRenderer` and registry wiring
**Acceptance Criteria:**
- Given a `("blockquote", 1, [node_a, node_b])` grouped tuple where `node_a`/`node_b` render to `"first line"` and `"second line"` respectively, When `BlockquoteNodeRenderer` renders it, Then the output is `"> first line\n> second line\n"`.
- Given a nested case `("blockquote", 2, [...])`, When rendered, Then each output line is prefixed `"> > "`.
- Given `render_nodes_to_markdown` (L521-538) is called on a full node sequence containing a mix of blockquote and non-blockquote nodes, Then it consults `_group_blockquote_runs` as the outer stage (composing with `_group_code_runs` per Story 3.2) rather than calling `_group_code_runs` directly on the raw sequence.

**Tasks:**
1. Add `BlockquoteNodeRenderer` class to `nodes_to_markdown.py`, following the existing renderer-class convention (`TableNodeRenderer`/`HeadingNodeRenderer`/etc.), producing `"> " * depth` prefixed lines from its inner grouped/rendered content. (5 min)
2. Register it in `_build_pull_registry` (L508-515) under a new `"blockquote"` dispatch key. (2 min)
3. Update `render_nodes_to_markdown` (L521-538) to call `_group_blockquote_runs` as the outer grouping stage instead of calling `_group_code_runs` directly. (4 min)
4. Add unit tests for plain and nested rendering, and an end-to-end round-trip test: markdown in with a plain/nested/list-in-quote/code-in-quote blockquote (per requirements.md's required round-trip test matrix) → push-shaped nodes → pull-shaped nodes → markdown out, asserting byte-identical output — for the code-in-quote case this explicitly includes the fence's language tag (e.g. ` ```python `) surviving the round trip, per Story 2.1/3.2's fix. (6 min)

### Story 3.4: Update `docs/backends/google-docs.md` Limitations section
**Acceptance Criteria:**
- Given the current Limitations entry describing blockquotes rendering as literal text, When updated, Then it instead describes native indent+border rendering and explicitly documents the one-time legacy-migration comment-loss caveat from the Migration Plan (Step 4).

**Tasks:**
1. Edit `docs/backends/google-docs.md`'s blockquote Limitations entry to describe the new behavior and the legacy-migration caveat, plus one sentence flagging the unquantified `batchUpdate` payload-size-cap risk (Unresolved Question 4) for documents with many legacy quotes migrating in one push, so it isn't a total surprise if hit (adversarial-review.md concern). (5 min)
2. Add a CHANGELOG entry describing the change and the one-time migration behavior, per research/ux.md item 2. (3 min)

---

## Epic 4: UX — `style_upgrade` Dry-Run Reason

### Story 4.1: Extend `HighRiskParagraph.reasons` with `"style_upgrade"`
**Acceptance Criteria:**
- Given the `Literal` type on `HighRiskParagraph.reasons` in `src/docspan/backends/google_docs/push_preview.py:42`, When read after this change, Then it is `Literal["comment", "native_glyph", "style_upgrade"]`.
- Given a `DiffEntry` representing a legacy blockquote paragraph (current node: literal `"> "` text, no marker; target node: `is_blockquote=True`, marker fields set) being rewritten via delete+reinsert with unchanged rendered content, When `find_high_risk_paragraphs` (L46-101) processes it, Then the returned `HighRiskParagraph` includes `"style_upgrade"` in its `reasons` list.
- Given `render_high_risk` (L123-157) renders a `HighRiskParagraph` whose only reason is `"style_upgrade"`, Then the output includes wording distinguishing it from the generic comment-loss warning, e.g. `"paragraph rewritten to add native blockquote styling (one-time upgrade)"`.
- Given a document with several (e.g. 5+) legacy blockquote paragraphs all migrating in the same push, When `render_high_risk` renders the resulting `HighRiskParagraph` list, Then the warnings stay legible — summarized/counted (e.g. "5 paragraphs rewritten to add native blockquote styling") rather than N near-identical repeated blocks flooding the terminal (adversarial-review.md concern: the "one-time, per-paragraph" framing understates that a single push can migrate every blockquote in a doc at once).

**Tasks:**
1. Widen the `Literal` type at `push_preview.py:42` to include `"style_upgrade"`. (2 min)
2. Extend `find_high_risk_paragraphs` (L46-101) to detect the legacy-blockquote-upgrade case: a `remove`/`change` `DiffEntry` where the current text (after stripping a leading `"> "`, if present) equals the target text and the target is a blockquote — reusing the existing `entry.current_text`-comparison pattern already used for `native_glyph` detection. (5 min)
3. Extend `render_high_risk` (L123-157) with a new rendering block for `"style_upgrade"`, following the existing per-reason block pattern; when more than a small threshold of `style_upgrade` entries are present in the same render call, collapse them into a single summarized count line instead of one block per paragraph. (5 min)
4. Add unit tests for detection and rendering per the Given-When-Then above, including a multi-paragraph (5+) case asserting the summarized rendering. (6 min)

### Story 4.3: Machine-readable comment-loss signal for non-interactive pushes
**(Pre-mortem P1 #2)** `style_upgrade`/comment-loss warnings today only surface as terminal text via `--dry-run`, invisible to CI/CD pipelines or scripted `docspan push` runs that don't read stdout. This story closes that gap.

**Acceptance Criteria:**
- Given a `docspan push` (with or without `--dry-run`) that would trigger one or more `"style_upgrade"`/comment-loss `HighRiskParagraph` entries, When the command completes, Then stdout includes a structured, greppable count line (e.g. `STYLE_UPGRADE_COUNT=<N>`) in addition to the existing human-readable `render_high_risk` text, so scripts can detect it without parsing prose.
- Given a new `--fail-on-comment-loss` CLI flag, When passed and at least one `style_upgrade`/comment-loss entry is detected, Then the command exits non-zero instead of its normal exit code, and the flag is off by default (no behavior change for existing callers).
- Given `docs/backends/google-docs.md`, When updated, Then it documents both the structured count line and the `--fail-on-comment-loss` flag as the supported mechanism for CI-driven consumers to detect comment loss.

**Tasks:**
1. Add a `--fail-on-comment-loss` flag to the `push` CLI command, defaulting to `False`. (3 min)
2. After `find_high_risk_paragraphs` runs, emit a structured `STYLE_UPGRADE_COUNT=<N>` line to stdout (count of entries whose reasons include `"style_upgrade"`), alongside the existing `render_high_risk` output. (4 min)
3. When `--fail-on-comment-loss` is set and the count is nonzero, exit with a non-zero status after printing the existing warnings. (3 min)
4. Add unit tests: structured count line present/absent per entry count; exit code unchanged by default; exit code non-zero only when the flag is passed and count > 0. (6 min)
5. Document both mechanisms in `docs/backends/google-docs.md` (Story 3.4's Limitations section is the natural home). (3 min)

### Story 4.2: Align with `find_churn_pairs`/`render_churn_note` (resolve prompt-vs-research tension explicitly)
**Decision recorded here** (per Pending Task 6 in the task brief): `HighRiskParagraph.reasons` is the primary carrier (Story 4.1), consistent with the explicit prompt instruction and the existing typed-reasons infrastructure. `find_churn_pairs`/`render_churn_note` (research/ux.md's suggested integration point) is **also** updated, but only to consult the same underlying detection — not as a second, independent detection path — to avoid the two mechanisms disagreeing about which paragraphs are "style upgrades."

**Acceptance Criteria:**
- Given a churn pair (matched remove/add `DiffEntry` with byte-identical rendered text) where the removed entry's `HighRiskParagraph` (from Story 4.1) has `"style_upgrade"` in its reasons, When `render_churn_note` (L197-206) renders that pair, Then it emits the specific wording from research/ux.md item 1 (`"paragraph rewritten to add native blockquote styling (one-time upgrade) — comment on it is lost"`) instead of the generic churn wording.
- Given a churn pair with no `style_upgrade` reason (ordinary unrelated churn), When rendered, Then the existing generic wording is unchanged.

**Tasks:**
1. Extend `render_churn_note` (`push_preview.py:197-206`) to accept/check whether a churn pair's associated `HighRiskParagraph` (from Story 4.1's detection) carries `"style_upgrade"`, and branch its wording accordingly. (5 min)
2. Add a unit test for both branches (style-upgrade churn wording vs. generic churn wording). (4 min)

---

## Epic 5: Cleanup — Remove Superseded Lint/Style-Guide Rules

### Story 5.1: Delete `lint.py`'s blockquote rule
**Acceptance Criteria:**
- Given `src/docspan/cli/lint.py`, When this story is complete, Then `find_blockquote_issues` and `_BLOCKQUOTE_LINE` no longer exist in the file, and no `LintIssue` is produced for a `>`-prefixed line.
- Given the existing lint test suite, When run after deletion, Then no test still asserts a blockquote `LintIssue` is produced (those tests are deleted, not left failing).

**Tasks:**
1. Delete `_BLOCKQUOTE_LINE`, `find_blockquote_issues`, and its call site(s) in `src/docspan/cli/lint.py`. (3 min)
2. Delete the corresponding test case(s) asserting a blockquote lint warning. (3 min)

### Story 5.2: Remove the blockquote bullet from `GOOGLE_DOCS_STYLE_GUIDE`
**Acceptance Criteria:**
- Given `src/docspan/style_guide.py`'s `GOOGLE_DOCS_STYLE_GUIDE` string (L16-19), When this story is complete, Then the "Don't use `>` blockquotes for callouts/notes" bullet is removed (not merely reworded to something inaccurate, since blockquotes now render correctly).

**Tasks:**
1. Remove the blockquote bullet from `GOOGLE_DOCS_STYLE_GUIDE` in `src/docspan/style_guide.py`. (2 min)
2. Update any style-guide snapshot/golden-file test asserting the old guide text. (3 min)
3. Add a one-line CHANGELOG note describing how to spot lingering `>` misrendering manually (e.g. via `push --dry-run` output or visual Doc inspection) in case a rendering edge case is found post-ship, now that the lint rule that would have caught misuse is gone (adversarial-review.md minor). (2 min)

---

## Step 6 Summary

- **Epics:** 6 (0 through 5)
- **Stories:** 17 (added 2.6 — list-in-quote indent-stacking composition rule; 3.1b — legacy-literal-text pull test)
- **Tasks:** 63
- **Glossary terms:** 10
- **Flagged decisions requiring explicit sign-off:**
  1. List-containing-a-quote (`- > note`) — explicitly out of scope, filed as follow-up, not a story. (Distinct from quote-containing-a-list, which Story 2.6 now covers.)
  2. `lint.py`/`style_guide.py` — delete outright (Epic 5), not narrow.
  3. `style_upgrade` UX — lands on `HighRiskParagraph.reasons` (Story 4.1) as the primary/authoritative signal, with `render_churn_note` (Story 4.2) consuming that same signal rather than an independent detection path; multi-paragraph migrations are summarized rather than rendered one-block-per-paragraph.
  4. `is_blockquote`/`quote_depth` illegal-state pair — closed via a `__post_init__` construction-time invariant (Story 1.1), not by collapsing to a single derived field, to avoid rippling the field-shape change through every story that constructs both fields.
  5. Marker-border detection compares only the `color`/`width`/`dashStyle` sub-fields docspan writes (Story 3.1), not whole-dict equality, so a Docs-echoed extra default sub-field doesn't break detection.
  6. `BLOCKQUOTE_BORDER_MARKER`/`BLOCKQUOTE_INDENT_PT_PER_LEVEL` are owned by `docs_structure_parser.py` and imported (not redefined) by `docs_request_builder.py` (Story 1.1).
  7. Six Unresolved Questions remain (see table above): four gated on the Epic 0 live-Doc spike (which blocks all of Epic 2/3), one (`_structural_score`, Q5) gated on a read not yet performed in this planning pass and deferred to Story 1.3's first task, and one (Q6, future marker-rotation migration path) accepted as a documented future-maintainer note rather than in-scope work.
