# Implementation Plan: wedding-planning-workflow

**Feature**: Make `docspan push --dry-run` real, add a `writeControl.requiredRevisionId` guard, verify/lock in a literal-text checklist round-trip, add read-only comment-risk **and native-checkbox-glyph-risk** flagging (both enforced inside `push()` itself), a pre-first-live-push confirmation tripwire, and ship the personal pull→summarize→edit→push workflow — scoped to what a Small (1–2 day) appetite can safely deliver before 7/29/2026, with a hard, dated go/no-go checkpoint on day 3.
**Date**: 2026-07-18
**Status**: Ready for implementation
**ADRs**: ADR-001-checklist-state-as-literal-text, ADR-002-comment-risk-flagging-not-anchor-preservation

---

## Step 0.5 — Creative Pass: Alternatives Considered

Three distinct shapes for fitting this inside the Small appetite were weighed before committing to an architecture:

**Approach A — "Fix everything the research found."** Full remediation: real dry-run, native `BULLET_CHECKBOX` support with pull-side HTML preprocessing, a Drive `comments` anchor-range decoder that actually preserves comments across a push (not just flags risk), and link/text-style preservation on edited paragraphs.
- *Strength*: closes every gap `research/*.md` surfaced; nothing left as a documented limitation.
- *Weakness*: at least two of these sub-problems (native-checkbox HTML shape, Drive comment anchor-range decoding) are unverified against the live API and would need their own research spikes *during* implementation — realistically multi-day, unverifiable-in-time work that risks shipping unverified code against the one document the whole wedding party depends on, 11 days out. Rejected: blows the appetite and risks the deadline.

**Approach B — "Dry-run-first, literal-text checklist, read-only comment-risk flag."** Fix the dry-run stub (the control the whole risk plan depends on) and the revision-race window first. Represent checklist state as opaque literal text (`[ ]`/`[x]`) — the scheme `research/build-vs-buy.md` confirms is the only one that round-trips reliably, since native `BULLET_CHECKBOX` checked/unchecked state cannot be read back from the API at all. Add comment-risk detection as a *read-only* substring cross-reference against Drive's documented `quotedFileContent.value` field (not a full anchor-range decode, which is undocumented/opaque). Everything else (tables, Sheets, images, native-checkbox pull support, full anchor preservation) becomes an explicit, written feature-gap entry.
- *Strength*: every sub-problem in this scope has a verifiable, small implementation using already-documented API fields and already-existing docspan machinery (`DocsRequestBuilder`'s diff, `client.py`'s gateway pattern) — fits 1–2 days with margin for live-doc verification.
- *Weakness*: does not fully solve comment-anchor preservation (a checklist toggle on a commented paragraph will still be flagged, not silently protected) — Tyler must still intervene manually on flagged paragraphs rather than getting a fully automatic safe push.

**Approach C — "Pull/summarize only; no push-path code changes this cycle."** Ship only the read side (pull → Claude digest), leave `--dry-run` as a stub, and have Tyler keep toggling checkboxes by hand in the Docs UI indefinitely.
- *Strength*: zero risk to the live doc — the safest option in absolute terms.
- *Weakness*: it directly fails requirements.md's explicit in-scope item 1 ("verify/fix checklist round-trip... verify comment-anchoring") and Success Metric 3, when Approach B demonstrably achieves both within the same appetite. Rejected as needlessly conservative given B is available.

**Decision: Approach B.** It is the only option that satisfies requirements.md's stated in-scope items without exceeding the Small appetite or asking Tyler to trust unverified code against the live doc. Rejected alternatives and reasons are also recorded per-component in the Pattern Decisions table below.

---

## Domain Glossary
*(Ubiquitous language — every domain term that appears as a type, method, or variable name. Exact names here must be used consistently in code, tests, and comments.)*

| Term | Definition | Notes |
|------|-----------|-------|
| `ChecklistLine` | A bulleted paragraph whose text begins with a literal `[ ]` or `[x]`/`[X]` marker | Not a distinct dataclass — a property of ordinary `DocsParagraphNode.text`, by design (see ADR-001) |
| `CheckedMarker` | The literal two/three-character substring `[x]`/`[ ]` (case-insensitive `x`) that encodes checked state as opaque text | Never derived from or written to a Docs `BULLET_CHECKBOX` glyph |
| `LiteralTextScheme` | ADR-001's decision to encode checklist state as plain paragraph text instead of the Docs API's checkbox bullet preset | Governs Phase 2 |
| `NativeCheckboxGlyph` | Google's `BULLET_CHECKBOX` `bulletPreset` value | Writable via `createParagraphBullets`; confirmed **unreadable** (checked/unchecked state) via `documents.get()` — explicitly not used as source of truth |
| `is_native_checkbox` | A new `bool` field (default `False`) added to `DocsParagraphNode`, resolved per-paragraph by `DocsStructureParser` via the same lookup Task 0.1.2a's survey script uses (`bullet.listId` → `document.lists[listId].listProperties.nestingLevels[n].glyphType == GLYPH_TYPE_UNSPECIFIED`) | **Not** part of the diff key (`style, text, is_list_item`) — a checklist toggle on a literal-text paragraph is diffed identically to before this field existed. Feeds `GlyphShapeCheck` only (via `DiffEntry.current_is_native_checkbox`), never `DocsRequestBuilder.build()`'s equality/opcode logic. See Task 1.2.2d, ADR-001 |
| `PushPlan` | The internal, single-fetch snapshot (`current_nodes`, `target_nodes`, `requests`, `doc`, `entries`, `unchanged_count`, `comments`, `high_risk`) built by `GoogleDocsBackend._build_push_plan()` from exactly one `get_document()` call | New dataclass, `push_preview.py`. `push()` and `preview_push()` each call `_build_push_plan()` independently — they never share a plan computed by the other, so the write path is always gated by data it fetched itself (see architecture-review.md Blocker 1) |
| `PushPreview` | The in-memory, human-renderable summary of what a `docspan push --dry-run` would show, derived from `preview_push()`'s own `PushPlan` | New dataclass, `push_preview.py`. **Read-only and cosmetic only** — it is never consulted by a real `push()` to decide whether to write; staleness here is acceptable because it never gates a write (per architecture-review.md's remediation) |
| `DiffEntry` | One row of a `PushPlan`/`PushPreview`: a paragraph's classification (`add`/`remove`/`change`/`unchanged`) plus its current/target text and style, plus `current_is_native_checkbox: bool` (default `False`) | New dataclass, `push_preview.py`. `current_is_native_checkbox` is copied from the current-side `DocsParagraphNode.is_native_checkbox` for `remove`/`change` entries only (an `add` entry has no current node, so it stays `False`) — feeds `GlyphShapeCheck`, never `DocsRequestBuilder.build()`'s equality/opcode logic. See Story 1.2.1/1.2.2 |
| `HighRiskParagraph` | A `DiffEntry` of kind `remove`/`change` that is high-risk for one or both of two independent reasons, tracked in `reasons: List[Literal["comment","native_glyph"]]`: (1) **`comment`** — current paragraph text contains the `quotedFileContent.value` substring of at least one open Drive comment (`CommentCrossReference`); (2) **`native_glyph`** — the entry's `current_is_native_checkbox` is `True`, i.e. `GlyphShapeCheck` found this paragraph is a native `BULLET_CHECKBOX` glyph, not literal text | New dataclass, `push_preview.py`. Computed from the already-built `DiffEntry` list (`entries`), never by re-running the diff a second time or re-fetching the doc — see Story 1.2.2. A single paragraph can carry both reasons at once (e.g. a native-glyph paragraph that also has an open comment); `comment_quoted_text`/`comment_author` are `Optional[str] = None` and only populated when `"comment"` is among `reasons` |
| `CommentSnapshot` | The list of open (`resolved=False`) comments returned by `GoogleDocsClient.list_comments()`, captured once per `PushPlan` | Read-only; never mutated or written back. A real `push()` calls `list_comments()` a second time, *after* `batch_update()` succeeds, purely to count open comments for the `CommentCountBackstop` — that second call never re-opens the risk-check decision, only reports an exact before/after delta |
| `RevisionGuard` | The `revisionId` fetched via `documents.get()` and threaded into `batchUpdate`'s `writeControl.requiredRevisionId` | Causes the whole batch to fail atomically (HTTP 400) if the doc changed since the guard was captured. `push()` catches this specific failure and returns `PushResult(status="conflict", message="The doc changed since your last pull — run \`docspan pull\` again")` instead of letting the raw `HttpError` propagate — see Story 1.1.2 |
| `ScratchDoc` | A Drive-copied duplicate of the live wedding doc (id `1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`) used to validate any push-path behavior before it is trusted against the live doc | Not committed to git; its id lives only in Tyler's local, gitignored `markgate.yaml` |
| `ForcePush` | The `force: bool` parameter threaded from the CLI's `--force` flag through `orchestrate_push()` into `GoogleDocsBackend.push(local_path, doc_id, force=...)`, required to proceed when `push()`'s **own** `PushPlan.high_risk` (from its own single fetch) is non-empty — regardless of whether the risk reason(s) are `"comment"`, `"native_glyph"`, or both | Without it, `push()` returns `PushResult(status="blocked", ...)` *before* calling `batch_update()` — the block is enforced inside the backend, not by a separate CLI-layer check against a separately-fetched snapshot. Distinct from `ScratchVerificationMarker`'s confirmation prompt (Story 1.2.5), which is a CLI-layer, one-time nudge unrelated to `high_risk` |
| `CommentCountBackstop` | An orthogonal, exact safety check: `push()` compares `len(comments)` from its `PushPlan` (before `batch_update()`) against a fresh `len(list_comments(doc_id))` (immediately after `batch_update()` succeeds), independent of the `CommentCrossReference` substring heuristic | Catches any comment loss the substring match missed (whitespace/quote normalization, multi-paragraph anchors) with zero false-negative risk of its own; when the count drops, `push()` returns `PushResult(status="warning", ...)` — **not** `"ok"` — with a `⚠` line appended to `.message`. The status escalation, not just the message text, is what matters: a `⚠` line on a `status="ok"` result would still render as a green `✓`/exit-0 success in the CLI (Story 1.2.4), which is exactly the "silent failure disguised as success" pattern this plan's `"blocked"`/`"conflict"` statuses already exist to avoid — see `research/pitfalls.md`/adversarial-review.md Concern, Task 1.2.3c |
| `PushResult.status == "warning"` | The escalated status `push()` returns when `batch_update()` succeeded but `CommentCountBackstop` detected a post-push drop in the open-comment count | New `Literal` member on `PushResult.status` (`src/docspan/backends/base.py`), alongside `"ok"`/`"conflict"`/`"error"`/`"skipped"`/`"blocked"`. Distinct from `"ok"` specifically so the CLI cannot show a green `✓`/exit 0 for a push that just lost a comment. Distinct from `"blocked"`/`"conflict"` because the write already succeeded — nothing was refused; a side effect was detected after the fact. Rendered by the CLI with its own `⚠`/yellow icon, never merged into either the green `"ok"`/`"skipped"` path or the red `"blocked"`/`"conflict"`/`"error"` path — see Story 1.2.4, Task 1.2.3c |
| `OwnerDigest` | Claude's derived-only, never-pushed summary of open checklist lines grouped by inferred owner sub-header and due date, **plus** a Schedule/Housing digest view over non-checklist paragraphs grouped by day/venue or person | Lives in the Claude conversation / workflow runbook, not in docspan Python code |
| `DiffSinceLastPull` | The delta between the freshly pulled markdown and the content recorded via `.markgate-state.json`'s `base_hash` / `get_base_content()` | Used to scope `OwnerDigest` to only what changed since the last summary |
| `ManualFallbackGate` | The documented, always-available escape hatch: edit the live Google Doc directly in the browser instead of using `docspan push` | Exercised whenever a `HighRiskParagraph` is flagged or a scenario (e.g. a native checkbox glyph found in Phase 0) is out of this cycle's scope |
| `CommentCrossReference` | The read-only, substring-based check that produces `HighRiskParagraph` entries with `reasons` including `"comment"`, run against the `DiffEntry` list already computed by `diff_summary()` (not a second, independent diff pass) | Deliberately not a full anchor-range decode — see ADR-002. One of two checks run inside `find_high_risk_paragraphs(entries: List[DiffEntry], comments: List[dict]) -> List[HighRiskParagraph]` — see also `GlyphShapeCheck`, run inside the same function |
| `GlyphShapeCheck` | The read-only, **live**, push-time check that flags any changed (`remove`/`change`) `DiffEntry` whose `current_is_native_checkbox` is `True`, using `push()`'s own single fetch — never a lookup against ADR-001's static, one-time survey table | Run inside `find_high_risk_paragraphs()` alongside `CommentCrossReference`, same enforcement point and same function signature (Story 1.2.2/1.2.3) — folds the failure mode pre-mortem.md #1 identified (layering a literal `[x]`/`[ ]` marker onto an already-native checkbox glyph with no warning) directly into `push()`'s existing fail-closed gate, rather than leaving it as pull-time-only disclosure. See also `MixedChecklistWarning`, which stays pull-time-only and cosmetic — `GlyphShapeCheck` is the enforcement counterpart |
| `ChecklistParagraphSurvey` | Phase 0's full pass over **every** `bullet`-bearing paragraph in the `ScratchDoc` (not a single sampled line), recording each paragraph's literal-text-vs-native-glyph finding individually | New throwaway-script output, feeds ADR-001's "Verification Evidence" table — see Task 0.1.2a |
| `MixedChecklistWarning` | A **pull-time-only**, cosmetic `WARN` log line (and a marked line in the pulled markdown, e.g. a trailing `<!-- docspan: native checkbox glyph, state not readable -->` comment) emitted for any paragraph `ChecklistParagraphSurvey` found to be a native `BULLET_CHECKBOX` glyph rather than literal text | Makes a mixed-representation doc's blind spots visible to Tyler on every pull, instead of only being documented once in ADR-001 and then forgotten — see Task 0.1.2b and ADR-001. **Deliberately does not itself gate `push()`** — it is disclosure only; the push-time enforcement counterpart that actually blocks a write is `GlyphShapeCheck`, which re-derives glyph shape live from `push()`'s own fetch rather than consulting this warning or ADR-001's table (closes pre-mortem.md #1) |
| `ScratchVerificationMarker` | A local, gitignored marker file (e.g. `<state_dir>/scratch-verified.marker`) recording that Story 2.2.1's scratch-doc round-trip verification (both the non-comment and comment-collision cycles) has passed at least once | Written by Tyler as a one-line manual step at the end of Task 2.2.1c once Tasks 2.2.1a/2.2.1b both pass; created automatically by the CLI itself the first time Tyler answers `y` to Story 1.2.5's confirmation prompt, so the prompt only fires once. Checked only in `cli/main.py`'s push path — never inside `GoogleDocsBackend.push()` — an interactive stdin prompt does not belong in the backend (Pattern Decisions ISP note). Closes pre-mortem.md #3 |

---

## Pattern Decisions

| Component | Pattern Chosen | Source | Alternative Rejected | Reason |
|-----------|---------------|--------|---------------------|--------|
| CLI/orchestrator overall shape | Transaction Script | PoEAA (Fowler) | Domain Model | Single-user CLI, no multi-aggregate business rules to encapsulate; a Domain Model adds indirection with no behavior payoff at this complexity/appetite. |
| Checklist state representation | Primitive opaque text marker inside `DocsParagraphNode.text` | Deliberate exception to type-driven-design | `ChecklistMarker`/`checked: bool` value object | No docspan code ever manipulates checked-state structurally — it stays opaque text end-to-end (owner/due-date parsing happens in Claude's conversation, not Python). A value object would encode an invariant nothing in this codebase reads or enforces — premature abstraction. See ADR-001. |
| Checklist write-path bullet preset | Keep existing `createParagraphBullets(BULLET_DISC_CIRCLE_SQUARE)`, unchanged | build-vs-buy.md research | `BULLET_CHECKBOX` bulletPreset | Docs API confirmed unable to read back checked/unchecked state (`documents.get()` returns `GLYPH_TYPE_UNSPECIFIED` regardless); adopting it would silently break Success Metric 3 the first time a collaborator toggles a real UI checkbox. See ADR-001. |
| Dry-run / push preview | New sibling method (`DocsRequestBuilder.diff_summary`) + a plain data-holder (`PushPreview`) built by a pure function | PoEAA — Query Object in spirit, but implemented as an extension of the existing Transaction Script | New `DocsRequestBuilder` subclass, or a Decorator around it | No polymorphic variation exists (one backend, one diff algorithm) — a subclass/Decorator would be structure without behaviorally justified variation. A sibling method keeps the already-tested `build()` untouched, minimizing regression risk on release-tested code. |
| Comment-risk detection | Read-only substring cross-reference: `quotedFileContent.value` against changed-paragraph text | This project (explicit trade-off) | Full Drive comment `anchor` range decode + sub-paragraph diffing | The `anchor` field format is undocumented/opaque (kix-internal range encoding); `quotedFileContent.value` is a documented, stable field. A substring check is cheap, verifiable within the appetite, and errs toward flagging (safe) rather than silently missing. See ADR-002. |
| **Comment-risk enforcement point** | **Guard clause inside `GoogleDocsBackend.push()` itself, evaluated against a `PushPlan` built from `push()`'s own single `get_document()` fetch, immediately before `batch_update()`** | Fail-fast / guard clause (Fowler); Dependency Inversion (the safety invariant belongs with the write, not with an arbitrary caller) | A CLI-layer check (`cli/main.py` calls `preview_push()` first, then decides whether to call `orchestrate_push`) | Rejected: `preview_push()` and `push()` each independently re-fetch the document, so a CLI-layer decision is made against a snapshot (T1) that can be stale by the time the guarded write actually happens (T2) — a comment added in that window is invisible to the check that was supposed to catch it. This was flagged as a BLOCKER in architecture-review.md. Folding the check into `push()` means *any* caller (CLI, a future script, a test) gets the same protection for free, and the check is always evaluated against the exact data the write is about to use. |
| `GoogleDocsClient` extensions (`list_comments`, revision-guarded `batch_update`) | Extend the existing class in place | PoEAA — Gateway (already established) | New `CommentsGateway`/`CommentsClient` class | `GoogleDocsClient` is already the single gateway to Docs/Drive for this backend; splitting comments into a separate gateway fragments one small, cohesive surface for no isolation benefit at this scale. |
| Exposing preview to the CLI | `preview_push()` added directly on `GoogleDocsBackend`, not on the abstract `Backend` — **used only for `--dry-run` rendering, never as the enforcement path for a real push** | Interface Segregation Principle (SOLID) | Add `preview_push()` (or a `dry_run` param) to the `Backend` ABC, forcing Confluence to implement it too | Confluence has no preview concept yet and no work item to build one this cycle; forcing an abstract method onto it would either break the abstraction or require a stub no one asked for. CLI uses `hasattr(backend, "preview_push")` to stay backend-agnostic — this is safe now because `preview_push()` is purely cosmetic; it no longer gates whether a real push is allowed to proceed (that gate is inside `push()` itself, which every backend already implements per the `Backend` ABC). |
| `writeControl.requiredRevisionId` guard | Guard clause / defensive parameter | Fowler refactoring vocabulary (not a GoF/PoEAA pattern) | DB-style optimistic-locking version column | No persistence layer exists to model versions in; the Docs API's own `writeControl` already provides the identical guarantee for free — reuse it, don't build a parallel mechanism. A stale-revision failure is caught and translated to `PushResult(status="conflict", message="The doc changed since your last pull — run \`docspan pull\` again")` rather than left as a raw `HttpError` — see Story 1.1.2. |
| `ScratchVerificationMarker` first-live-push check | CLI-layer, interactive, one-time confirmation prompt (not a `push()`-level guard clause) | Fail-fast in spirit, but deliberately UX-only | A `push()`-level hard block (like `HighRiskParagraph`/`--force`) | Rejected: `push()` must stay a pure, testable, backend-agnostic method with no interactive stdin — an established constraint of the existing test suite (`GoogleDocsBackend.push()` is unit-tested with mocks, never with stdin injection) and of the `Backend` ABC. A CLI-layer prompt is the cheapest mechanism that still hits the highest-risk moment (pre-mortem.md #3), without adding interactivity to a method other callers (tests, future scripts) rely on being deterministic. |

---

## Observability Plan
*(Personal CLI tool, single user — scoped down; no metrics/alerting infrastructure.)*

- **Logs**: extend the existing `logging.getLogger(__name__)` pattern (already used in `client.py`, `orchestrator.py`). Log `INFO`: comment count fetched, high-risk paragraph count found, whether `requiredRevisionId` was applied. Log `DEBUG`: the full `requests` list built by `DocsRequestBuilder.build()` — never printed to the console by default (keep terminal output scannable per `research/ux.md`; use `--verbose` or `DOCSPAN_LOG_LEVEL=DEBUG` if deeper inspection is needed, matching existing conventions — no new flag required if one already exists, otherwise this is out of scope to add).
- **CLI output**: extend the existing `rich` icon/color convention (`✓`/`✗`, green/red) with a new `⚠` (yellow/bold) marker for `HighRiskParagraph` lines, matching `research/ux.md` §4(a)'s recommendation. Every dry-run and every blocked real push must print a one-line summary count first (`git status`/`terraform plan`-style), followed by details — never a wall of unstructured diff text.
- **Metrics**: none — not applicable to a single-user, on-demand CLI.
- **Alerts**: none — not applicable.

## Risk Control

- **ManualFallbackGate is always available.** Every story in this plan preserves the ability to edit the live Google Doc directly in the browser at any point; nothing here is a one-way migration.
- **Verify on `ScratchDoc` first.** Every push-path code change (Phase 1 and Phase 2) must be exercised against the `ScratchDoc` (a Drive copy of the live doc) before it is ever run against doc id `1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`. No task in Phase 1/2 is "done" until its scratch-doc verification step (Story 2.2.1, plus per-story spot checks) has passed.
- **`ForcePush` required to override a `HighRiskParagraph` block, and the block is enforced inside `push()` itself.** `GoogleDocsBackend.push()` builds its own `PushPlan` (one `get_document()` fetch, one diff, one `list_comments()` call) and evaluates `high_risk` against that same fetch immediately before calling `batch_update()` — not against a separately-fetched CLI-layer preview. A real push never silently proceeds through a flagged paragraph; `--force` must be explicit, threaded through `orchestrate_push()` into `push(force=...)`, and its use is logged. This closes the TOCTOU gap architecture-review.md flagged as a BLOCKER: `preview_push()` (used only for `--dry-run` rendering) is a separate, independent, purely cosmetic read — it is never consulted to decide whether a real write proceeds. `high_risk` is populated by two independent, read-only checks run inside `find_high_risk_paragraphs()` — `CommentCrossReference` (open Drive comments) and `GlyphShapeCheck` (native `BULLET_CHECKBOX` glyphs found live, at push time, on the paragraph about to be changed) — closing pre-mortem.md #1's finding that a native-glyph paragraph could otherwise be silently double-marked with no warning.
- **`ScratchVerificationMarker` — a one-time confirmation tripwire before the very first live-doc push.** Independent of the `high_risk`/`--force` gate above (which handles per-push comment/glyph risk), `cli/main.py` also checks — only when a real push's `mapping.remote_id` is the live wedding doc id (`1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`), never for `wedding-scratch.md` — whether a local `scratch-verified.marker` file exists. If it doesn't, the CLI prints `"⚠ Scratch-doc verification not recorded — proceed against live doc? [y/N]"` and requires an explicit `y` before calling `orchestrate_push()` at all; a `y` also creates the marker so the prompt never repeats. This is a code-adjacent nudge, not a hard block — Tyler can still answer `y` with nothing verified — but it converts "remember to verify on scratch first" from pure memory into something the tool itself asks about at the single highest-consequence moment (pre-mortem.md #3). See Story 1.2.5, Task 2.2.1c.
- **`RevisionGuard` on every real push** (Phase 1, Epic 1.1) — closes the machine-timescale race window `research/pitfalls.md` §2 identified between `get_document()` and `batch_update()`, independent of CLI-level "re-pull first" discipline. On a stale-revision conflict, `push()` returns a clear `PushResult(status="conflict", message="The doc changed since your last pull — run \`docspan pull\` again")` instead of letting a raw Google API `HttpError` reach Tyler under time pressure (Story 1.1.2).
- **`CommentCountBackstop` — an orthogonal, exact check independent of the substring heuristic.** Immediately after a successful `batch_update()`, `push()` calls `list_comments()` again and compares the open-comment count to the count captured in its `PushPlan` before the write. A drop is reported as a `⚠` line in `PushResult.message` **and escalates `PushResult.status` from `"ok"` to `"warning"`** — even if `CommentCrossReference`'s substring match didn't flag anything — cheap, catches what substring-matching can miss (whitespace/quote normalization, multi-paragraph anchors), with no false-negative risk of its own. The status escalation matters as much as the message text: leaving `status="ok"` with only an appended message would still render as a green `✓`/exit-0 success in the CLI (Story 1.2.4) — exactly the "silent failure disguised as success" pattern this plan's `"blocked"`/`"conflict"` statuses already exist to avoid (see `research/ux.md`) — see Task 1.2.3c.
- **Isolate checklist-only pushes.** The workflow runbook (Phase 3) documents the procedural mitigation from `research/pitfalls.md` §4: when checking off tasks near known open comments, push that change alone rather than bundled with schedule/housing edits, so the (now-real) dry-run diff stays small and reviewable. **This is documented operator discipline, not tool-enforced** — one cheap, tool-level nudge exists (`PushPreview.render()` and `push()`'s blocked-message rendering both print a one-line note when a diff mixes checklist-marker changes with non-checklist changes, per Task 1.2.3b), but docspan does **not** reject or split a mixed push; hard enforcement (e.g. rejecting a "checklist-only" push that touches non-checklist text spans) would require classifying every paragraph and a new CLI mode, which is out of this Small appetite — see Unresolved Questions.
- **No feature-flag infrastructure** — not applicable to a personal CLI. "Rollback" = `git revert` the relevant commit, plus docspan's existing `.orig` backup and `.markgate-base` content-addressed store for local-file recovery (unchanged by this plan).
- **Hard, dated go/no-go checkpoint for the whole push-path fix (not just the checklist+comment-risk feature below).** If Phase 0 (Epic 0.1) and Phase 1 (Epic 1.1 RevisionGuard + Epic 1.2 PushPlan/high-risk gate) are not **all** complete — acceptance criteria met, not just "mostly working" — by end of day **2026-07-20** (day 3, counting 2026-07-18 as day 1, of the ~11-day runway to the 7/29/2026 deadline), Tyler stops sinking further time into the push-path fix immediately and falls back to Step 0.5's rejected **Approach C**: ship only the read side (pull → Claude digest via `OwnerDigest`), leave the push path exactly wherever it currently stands (untouched or partially built, but not used against the live doc), and hand-toggle every checklist/schedule/housing edit directly in the Google Docs UI for the remainder of wedding planning. This is a checkable trigger — a specific calendar date compared against specific stories' acceptance criteria — not a principle to re-litigate under sunk-cost pressure on 2026-07-20 itself (pre-mortem.md #2). It is stricter and earlier than the go/no-go gate below, which only covers the narrower checklist+comment-risk feature and has no fixed date.
- **Go/no-go gate for the checklist+comment-risk feature.** If, by go-time, Phase 2's live-scratch-doc verification (Story 2.2.1) has not passed, Tyler keeps toggling checkboxes by hand in the Docs UI and uses docspan purely for pull + summarize for that item — this matches `research/pitfalls.md` §4's explicit recommendation and is not a failure state, it's the designed fallback. (This is the narrower, undated fallback for Epic 1.2/Phase 2 specifically; see the hard, dated 2026-07-20 checkpoint above for the whole-cycle abort trigger.)

## Unresolved Questions
*(Each item must be resolved before the story that depends on it starts.)*

- [ ] Does the live wedding doc's checklist content use literal `[ ]`/`[x]` text, native `BULLET_CHECKBOX` glyphs, or (most likely, given multi-author/multi-month editing) a **mix of both across different paragraphs**? — blocks Story 2.1.1's scope decision — owner: Tyler, resolved by Phase 0's full-document `ChecklistParagraphSurvey` (Task 0.1.2a) against the `ScratchDoc`, not a single sampled line (see adversarial-review.md Blocker).
- [ ] Does Drive's `comments().list()` reliably return a non-empty `quotedFileContent.value` for comments anchored mid-paragraph in this specific doc (vs. only comments anchored to a whole element/table)? — blocks Story 1.2.2 — owner: Tyler, resolved empirically; `comments().list()` is read-only and safe to call directly against the live doc.
- [ ] Does the installed `google-api-python-client` 2.197.0 Drive v3 discovery document actually expose `drive_service.comments()` with the fields used in Task 1.2.2a, **and does the configured service account's OAuth/Drive scope actually permit reading comments on a doc it didn't create** (method existence and scope sufficiency are different checks)? — blocks Task 1.2.2a — owner: implementer, resolved by actually calling `list_comments()` against the `ScratchDoc` (not just confirming the method exists on the built service object) before Phase 1 work begins, so a scope gap (e.g. a 403 under `drive.file`) surfaces at the cheapest possible point rather than mid-Phase-1.
- [ ] Will `--force` ever actually need to be exercised before 7/29, or does Tyler's real editing pattern never collide a checklist toggle with an open-comment paragraph? — non-blocking; informs whether Epic 1.2's flag sees real use — owner: Tyler, resolved empirically each sync cycle.
- [ ] **Hard, tool-enforced isolation of checklist-only pushes** (rejecting/splitting a push that mixes checklist-marker changes with unrelated schedule/housing edits, rather than only nudging via a render-time note) is out of scope this cycle — it would require classifying every paragraph by "checklist vs. not" and a new CLI confirmation/split mode, which does not fit the Small appetite alongside everything else in Phase 1. If mixed pushes turn out to be common in practice during the real 3+ live-doc cycles (see Story 3.1.1's cycle log), revisit as a follow-up cycle rather than expanding this one — non-blocking, owner: Tyler.

---

## Dependency Visualization

```
Phase 0: Safety Spike (blocks Phase 1 Epic 1.2 comment work and all of Phase 2)
  Task 0.1.1a: Duplicate live doc → ScratchDoc, add markgate.yaml mapping
  Task 0.1.2a: Full-document ChecklistParagraphSurvey (every bullet paragraph, not one sample) ──┐
  Task 0.1.2b: Record per-paragraph findings + mixed-doc fallback in ADR-001                       │
                                                                                                     ▼
Phase 1: Push Safety Net                                                                  Phase 2: Checklist
  Epic 1.1: RevisionGuard                                                                  Verification
    Task 1.1.1a: client.py batch_update(required_revision_id)                                Epic 2.1: Regression tests
    Task 1.1.2a: backend.py push() threads revisionId                                          (locks LiteralTextScheme)
    Task 1.1.2b: friendly PushResult(status="conflict") message on stale revision            Epic 2.2: Live scratch-doc
                                                                                                 round-trip verification
  Epic 1.2: PushPlan / PushPreview (needs Phase 0 finding)                                       (needs Phase 1 complete)
    Task 1.2.1a: docs_request_builder.py diff_summary()  ← copies current_is_native_checkbox
    Task 1.2.2a: client.py list_comments()
    Task 1.2.2d: docs_structure_parser.py is_native_checkbox resolution (feeds GlyphShapeCheck) ─┐
    Task 1.2.2b: push_preview.py find_high_risk_paragraphs(entries, comments) — comment + glyph ─┤
    Task 1.2.3a: backend.py _build_push_plan() (single get_document() fetch)                     │
    Task 1.2.3b: backend.py preview_push() — cosmetic only, --dry-run rendering                  │
    Task 1.2.3c: backend.py push() — own PushPlan fetch, gate, CommentCountBackstop ◄─────────────┘
    Task 1.2.4a: cli/main.py --dry-run renders preview_push()
    Task 1.2.4b: cli/main.py --force threaded through orchestrate_push → push(force=)
    Task 1.2.5a: cli/main.py ScratchVerificationMarker prompt before first live-doc push
                 (checks the marker file Task 2.2.1c writes — see Epic 2.2 below)
                                                                            │
Phase 3: Workflow deliverables (needs Phase 1 + Phase 2 complete)          │
  Epic 3.1: Workflow runbook (incl. Schedule/Housing digest example,       │
            real-cycle log) ◄────────────────────────────────────────────┘
  Epic 3.2: Feature-gap report

Phase 4: Docs (needs Phase 1–3 complete)
  Epic 4.1: README Known Limitations update
```

---

## Phase 0: Safety Spike — Verify Assumptions Against a Scratch Doc

### Epic 0.1: Scratch doc setup and checklist-shape verification

**Goal**: Establish a safe test target and resolve the single biggest unknown (does the live doc use literal-text or native checkboxes?) before writing any push-path code.

---

#### Story 0.1.1: Create a scratch copy of the wedding doc for safe testing
**As** Tyler, **I want** a Drive-copied duplicate of the live planning doc, **so that** every push-path change in this plan can be verified without risking Nora's/Bekah's real content.

**Acceptance Criteria**:
- A new Google Doc exists that is a full copy of doc id `1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`, including at least one checklist line and one comment (copy preserves comments; if Drive's `files.copy` doesn't preserve comments, manually add one throwaway comment to the scratch doc for Story 1.2.2/2.2.1 testing).
  - *Given* the live doc's id, *When* Tyler runs `files().copy(fileId="1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE", body={"name": "wedding-planning-SCRATCH"})` via a one-off Python REPL call using the already-configured service-account credentials, *Then* a new doc id is returned and opening it in the browser shows the same checklist lines (e.g. `- [x] Whatsapp group`, `- [ ] Splitwise`) as the live doc.
- The scratch doc id is recorded only in Tyler's local, gitignored `markgate.yaml` (never committed) as a second mapping, e.g. `local: wedding-scratch.md`, `direction: both`.

**Files**: `markgate.yaml` (local, gitignored — not a repo file), `markgate.yaml.example` (add a commented example mapping showing the scratch-doc pattern).

##### Task 0.1.1a: Duplicate the live doc and register a scratch mapping (~5 min)
- Run a one-off Python snippet (not committed) using `GoogleDocsClient`'s existing `drive_service.files().copy(fileId=..., body={"name": "wedding-planning-SCRATCH"}).execute()` to create the scratch doc.
- Add the returned doc id as a new mapping entry to Tyler's local `markgate.yaml` (`local: wedding-scratch.md`, `backend: google_docs`, `direction: both`).
- Add a commented-out example mapping block to `markgate.yaml.example` illustrating the "scratch doc for push-path testing" pattern (comment only, no real doc id).
- Files: `markgate.yaml.example`

---

#### Story 0.1.2: Determine whether the live doc's checklist items are literal bracket text, native checkbox glyphs, or a mix — checked paragraph-by-paragraph across the whole document
**As** the implementer, **I want** to know the actual JSON/HTML shape of **every** checklist line in this doc, **so that** Phase 2's scope (literal-text-only vs. also needing native-glyph pull support, vs. handling a mixed doc) is a verified decision made from full coverage, not an assumption extrapolated from one sampled line.

A multi-author, multi-month Google Doc (Nora, Bekah, Tyler each typing/pasting checklist lines over months) is plausibly a **mix** of literal `[ ]`/`[x]` text and native `BULLET_CHECKBOX` glyphs — Google Docs' editor auto-converts typed `"[ ] "` into a checkbox glyph in some contexts (autocomplete/smart-bullets) but not others, inconsistently, depending on exactly how each line was typed or pasted. A single-line sample cannot rule this out (per adversarial-review.md's BLOCKER finding) — it can land on finding (b) for the sampled line while other real checklist lines in the same doc are actually finding (a), which per ADR-001's "Consequences" means those specific lines silently render as unmarked plain bullets on pull (invisible to Claude's `OwnerDigest`), and a Tyler edit "checking them off" by prepending `[x]` would layer a text marker on top of an already-present, unrelated checkbox glyph on push.

**Acceptance Criteria**:
- A throwaway inspection walks **every `bullet`-bearing paragraph** in the `ScratchDoc`'s `documents.get()` body (not a single sampled line) and records, per paragraph: the paragraph's text prefix, whether `bullet.listId` resolves (via `document.lists[listId].listProperties.nestingLevels[n].glyphType`) to `GLYPH_TYPE_UNSPECIFIED` (native checkbox — per `research/stack.md` §2), and whether the paragraph's plain `textRun.content` already literally contains `"[x] "`/`"[ ] "`.
  - *Given* the `ScratchDoc` id from Story 0.1.1, *When* Tyler runs the survey script against `client.get_document(scratch_doc_id)` and iterates every structural element with a `bullet` object, *Then* the output is a table/list of `(paragraph_text_prefix, resolved_glyph_shape, literal_bracket_present: bool)` for every such paragraph — not a single finding for the whole document.
  - *Given* that per-paragraph table, *When* Tyler reviews it, *Then* the overall finding is one of: (a) **all** surveyed paragraphs are native-glyph, (b) **all** surveyed paragraphs are literal-text, or (c) **mixed** — some paragraphs are native-glyph and others are literal-text within the same document.
- The same full-document check is run against the HTML export (`client.get_doc_content(scratch_doc_id)`) to see what `markdownify` currently produces for each checklist line, without any code changes.
- Every per-paragraph finding, plus the exact JSON snippet and HTML snippet for at least one representative example of each distinct shape found, is written into ADR-001 (Story 0.1.2's Task below). If finding (c) (mixed), ADR-001 records which specific paragraphs (by text prefix) are native-glyph, since those are the ones that will be invisible to the literal-text scheme.

**Files**: none (throwaway script, not committed) — output feeds ADR-001.

##### Task 0.1.2a: Run and record the full-document checklist-shape survey (~10 min — widened from a single-line sample because a multi-author doc is plausibly mixed; see adversarial-review.md Blocker)
- Using `GoogleDocsClient` methods already present (`get_document`, `get_doc_content`), fetch both representations of the scratch doc.
- Walk **every** structural element in `documents.get()`'s body that has a `bullet` object (not just the one containing `"Whatsapp group"`) — for each, record its text prefix, whether `bullet.listId`'s resolved glyph is checkbox-shaped, and whether the literal text already contains `[x]`/`[ ]`.
- Cross-check the same set of lines against the HTML export.
- Produce a per-paragraph findings table (paragraph text → shape found) — this is the `ChecklistParagraphSurvey` referenced in the Domain Glossary.
- No repo files change; findings feed directly into Task 0.1.2b.

##### Task 0.1.2b: Record the per-paragraph findings and decision fork (including the mixed-doc case) in ADR-001 (~5 min)
- Write `project_plans/wedding-planning-workflow/decisions/ADR-001-checklist-state-as-literal-text.md` (see ADR Stubs section below for full content) including the full per-paragraph findings table from Task 0.1.2a under "Verification evidence" — not just one snippet.
- If finding (b) (all literal text, expected outcome): Phase 2 proceeds exactly as scoped below — no pull-side code changes.
- If finding (a) (all native glyphs): add an explicit note to ADR-001 that pull-side native-checkbox-to-literal-text conversion is **out of scope this cycle** (would require the unverified HTML-preprocessing pass `research/architecture.md` §2 describes) and add the corresponding entry to the feature-gap report (Story 3.2.1) — collaborators using native checkboxes continue toggling them by hand; docspan's pull will render them as plain, unmarked bullets until a future cycle.
- If finding (c) (**mixed** — the realistic case for a multi-author doc): ADR-001 states explicitly that the literal-text scheme (ADR-001's decision) is treated as the default representation, but **any paragraph the survey found to already be a native glyph is flagged high-risk / requires manual handling**, not silently assumed to behave like the rest of the doc. Concretely: those specific paragraphs (identified by text prefix in the survey table) are (i) listed by name in the feature-gap report as "known native-checkbox lines, not tracked by docspan," and (ii) pull emits a `MixedChecklistWarning` (`WARN`-level log line, plus a trailing marker comment in the pulled markdown) whenever it encounters a bullet paragraph whose resolved glyph is checkbox-shaped, so the gap is visible on every pull rather than only documented once and forgotten. Implementing the `MixedChecklistWarning` log/marker is a small, in-scope addition to `docs_structure_parser.py`/`converter.py` if finding (c) occurs; if it turns out to need more than a small warning (e.g. actual conversion), that is out of scope — see Story 3.2.1.
- Files: `project_plans/wedding-planning-workflow/decisions/ADR-001-checklist-state-as-literal-text.md`, and — only if finding (c) occurs — `src/docspan/backends/google_docs/docs_structure_parser.py` (emit the `WARN` log + marker comment for checkbox-shaped bullets).

---

## Phase 1: Push Safety Net — Revision Guard + Real Dry-Run

### Epic 1.1: Thread `requiredRevisionId` through `batch_update`

**Goal**: Close the machine-timescale race window between `get_document()` and `batch_update()` identified in `research/pitfalls.md` §2, using the Docs API's own `writeControl` mechanism — no new infrastructure.

---

#### Story 1.1.1: `GoogleDocsClient.batch_update` accepts and applies a revision guard
**As** the Google Docs backend, **I want** `batch_update()` to optionally require a specific `revisionId`, **so that** a push fails loudly (HTTP 400) instead of silently overwriting a concurrent collaborator edit.

**Acceptance Criteria**:
- `batch_update(doc_id, requests, required_revision_id=None)` — when `required_revision_id` is not `None`, the request body includes `"writeControl": {"requiredRevisionId": required_revision_id}`.
  - *Given* `doc_id="1T0Om..."`, `requests=[{"insertText": {...}}]`, `required_revision_id="ALm37..."`, *When* `batch_update()` is called, *Then* the dict passed to `documents().batchUpdate(documentId=doc_id, body=...)` has `body["writeControl"]["requiredRevisionId"] == "ALm37..."`.
- When `required_revision_id` is `None` (default), the request body has no `writeControl` key — behavior is unchanged from today (backward compatible for any other caller).
- Existing retry/backoff behavior (`_with_backoff`) is unchanged and still wraps the call.

**Files**: `src/docspan/backends/google_docs/client.py`

##### Task 1.1.1a: Add `required_revision_id` param to `batch_update()` (~4 min)
- Edit `batch_update()` (currently `client.py:133-151`): add `required_revision_id: Optional[str] = None` parameter.
- Build `body = {"requests": requests}`; if `required_revision_id` is not `None`, set `body["writeControl"] = {"requiredRevisionId": required_revision_id}`.
- Pass `body=body` into the existing `documents().batchUpdate(documentId=doc_id, body=body).execute()` call inside the existing `_with_backoff` lambda.
- Files: `src/docspan/backends/google_docs/client.py`

---

#### Story 1.1.2: `GoogleDocsBackend.push()` passes the fetched `revisionId` into `batch_update`
**As** Tyler, **I want** every real push to be guarded by the revision it was computed against, **so that** a push never silently applies against a doc state different from the one the diff was built from.

**Acceptance Criteria**:
- `push()` (currently `backend.py:49-74`) passes `doc["revisionId"]` (already available from the existing `client.get_document(doc_id)` call at line 57) into `self._client.batch_update(doc_id, requests, required_revision_id=doc["revisionId"])`.
  - *Given* a doc whose `revisionId` is `"ALm37abc"` at the time `get_document()` is called inside `push()`, *When* `push()` proceeds to call `batch_update()`, *Then* the call is `batch_update(doc_id, requests, required_revision_id="ALm37abc")`.
- If `batch_update` raises due to a stale `writeControl.requiredRevisionId`, `push()` **must not** let the raw `googleapiclient.errors.HttpError` propagate into `PushResult.message` — that is exactly the failure mode adversarial-review.md's Concern calls out ("Tyler would see a raw `HttpError 400 ...` rather than 'someone edited this doc since your last pull'"). Instead, `push()` catches this specific case and returns `PushResult(status="conflict", message="The doc changed since your last pull — run \`docspan pull\` again")`. `PushResult.status`'s `Literal` already includes `"conflict"` (`src/docspan/backends/base.py`), so no new status value is needed here.
  - *Given* `batch_update()` raises `HttpError` with `resp.status == 400` and a body mentioning `requiredRevisionId`, *When* `push()` catches it, *Then* it returns `PushResult(status="conflict", doc_id=doc_id, message="The doc changed since your last pull — run \`docspan pull\` again")`, and the CLI (Story 1.2.4) renders that message plainly instead of a stack trace or raw API error text.
  - Any other exception (network error, auth failure, etc.) still falls through to the existing broader `except Exception as exc: return PushResult(status="error", ...)` — only the stale-revision case gets its own friendlier branch.

**Files**: `src/docspan/backends/google_docs/backend.py`

##### Task 1.1.2a: Thread `doc["revisionId"]` into the `batch_update` call in `push()` (~3 min)
- Edit `push()` in `backend.py` (the `self._client.batch_update(doc_id, requests)` call at line 70): change to `self._client.batch_update(doc_id, requests, required_revision_id=doc["revisionId"])`.
- Confirm `doc` (from `self._client.get_document(doc_id)` at line 57) is still in scope at that point — it is, no restructuring needed.
- Files: `src/docspan/backends/google_docs/backend.py`

##### Task 1.1.2b: Catch a stale-revision conflict and return a clear message, not a raw exception (~4 min)
- In `push()`'s `except` handling, add a narrower branch before the existing catch-all: `except HttpError as exc: if exc.resp.status == 400 and "requiredRevisionId" in str(exc): return PushResult(status="conflict", doc_id=doc_id, message="The doc changed since your last pull — run \`docspan pull\` again")` — re-raise or fall through to the generic handler for any other `HttpError`.
- Import `googleapiclient.errors.HttpError` (already a transitive dependency via `google-api-python-client`, used elsewhere in `client.py`'s backoff handling).
- Add a unit test (`tests/test_google_docs_backend.py` or wherever `push()` is already tested) mocking `batch_update` to raise an `HttpError` shaped like a stale-revision failure, asserting `PushResult.status == "conflict"` and the friendly message text — not the raw exception string.
- Note for implementers: Story 1.2.3 below further refactors `push()`'s body (extracting `_build_push_plan()` and adding the comment-risk gate) — that refactor must preserve this except-branch, not remove it.
- Files: `src/docspan/backends/google_docs/backend.py`, test file covering `GoogleDocsBackend.push()`

---

### Epic 1.2: Build a real structural diff preview (dry-run engine + comment-risk and native-checkbox-glyph-risk flags) — and enforce the risk gate inside `push()` itself

**Goal**: Replace the `--dry-run` no-op stub with a real, human-readable preview of what a push would do (`preview_push()` — cosmetic, read-only), **and** make `GoogleDocsBackend.push()` itself refuse (fail-closed, unless `--force`) to write through a paragraph carrying an open collaborator comment **or a native-checkbox glyph found live at push time (`GlyphShapeCheck`)**, using a `PushPlan` it builds from its own single `get_document()` fetch immediately before calling `batch_update()`. This closes the TOCTOU gap architecture-review.md flagged as a BLOCKER: the enforcement decision must not be split across a CLI-layer preview fetch and a separate backend write fetch. Depends on Phase 0's confirmation that comments can be read (Unresolved Question 2). This epic also closes pre-mortem.md #1: `MixedChecklistWarning` (Phase 0) was pull-time-only disclosure; `GlyphShapeCheck` is the push-time enforcement counterpart, folded into the same `find_high_risk_paragraphs()` function and evaluated against the same single fetch as the comment-risk check.

---

#### Story 1.2.1: `DocsRequestBuilder` exposes a diff summary separate from raw batchUpdate requests
**As** the preview engine, **I want** a lightweight, human-oriented classification of each paragraph's change (add/remove/change/unchanged), **so that** the CLI can render a readable diff without parsing raw `insertText`/`deleteContentRange` request dicts.

**Acceptance Criteria**:
- A new `DiffEntry` dataclass (`kind: Literal["add","remove","change","unchanged"]`, `current_text: Optional[str]`, `target_text: Optional[str]`, `style: str`, `current_is_native_checkbox: bool = False`) is added.
- `DocsRequestBuilder.diff_summary(current, target) -> tuple[list[DiffEntry], int]` returns `(entries, unchanged_count)` where `entries` contains only non-`"unchanged"` rows (per `research/ux.md`'s "silence about unchanged sections" principle) and `unchanged_count` is a plain int for the summary line.
  - *Given* `current = [DocsParagraphNode(text="[ ] Splitwise", is_list_item=True, ...)]` and `target = [DocsParagraphNode(text="[x] Splitwise", is_list_item=True, ...)]`, *When* `diff_summary(current, target)` is called, *Then* it returns one `DiffEntry(kind="change", current_text="[ ] Splitwise", target_text="[x] Splitwise", style="NORMAL_TEXT")` and `unchanged_count == 0`.
  - *Given* `current` and `target` both containing an identical `DocsParagraphNode(text="Housing: Bekah has the lake house", ...)` plus one paragraph that differs, *When* `diff_summary` runs, *Then* `entries` contains exactly one row (the differing paragraph) and `unchanged_count == 1`.
  - *Given* `current = [DocsParagraphNode(text="[ ] Whatsapp group", is_list_item=True, is_native_checkbox=True, ...)]` and `target = [DocsParagraphNode(text="[x] Whatsapp group", is_list_item=True, is_native_checkbox=False, ...)]`, *When* `diff_summary(current, target)` is called, *Then* it returns one `DiffEntry(kind="change", current_text="[ ] Whatsapp group", target_text="[x] Whatsapp group", style="NORMAL_TEXT", current_is_native_checkbox=True)` — the field is copied from the **current**-side node only (the side about to be deleted/replaced); an `"add"` entry (no current node) always has `current_is_native_checkbox=False`.
- `diff_summary` reuses the same `_text_key`/`difflib.SequenceMatcher` machinery `build()` already uses (no separate diff algorithm) — implemented as a second pass over `matcher.get_opcodes()`, not a full reimplementation.

**Files**: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 1.2.1a: Add `DiffEntry` dataclass and `diff_summary()` method (~5 min)
- Add `DiffEntry` dataclass near the top of `docs_request_builder.py` (after the `_utf16_len` helper).
- Add `DocsRequestBuilder.diff_summary(self, current, target) -> tuple[List[DiffEntry], int]`: compute `current_keys`/`target_keys` and `matcher` exactly as `build()` does (lines 40-44), then walk `matcher.get_opcodes()`: `"equal"` → increment `unchanged_count` for each paired node; `"delete"` → one `DiffEntry(kind="remove", current_text=n.text, target_text=None, style=n.style, current_is_native_checkbox=n.is_native_checkbox)` per node; `"insert"` → one `DiffEntry(kind="add", current_text=None, target_text=n.text, style=n.style)` per node (no current node, so `current_is_native_checkbox` stays at its `False` default); `"replace"` → one `DiffEntry(kind="change", ..., current_is_native_checkbox=current_node.is_native_checkbox)` per zipped current/target pair, reading the flag from the current-side node of each pair (zip shorter of the two ranges; treat any length mismatch as extra `add`/`remove` entries).
- Files: `src/docspan/backends/google_docs/docs_request_builder.py`

##### Task 1.2.1b: Unit tests for `diff_summary()` (~5 min)
- Add `test_diff_summary_reports_unchanged_count_and_skips_equal_rows`, `test_diff_summary_classifies_checklist_toggle_as_change` (using the `- [ ] Splitwise` → `- [x] Splitwise` example above), `test_diff_summary_classifies_new_paragraph_as_add`, `test_diff_summary_classifies_removed_paragraph_as_remove` to `tests/test_docs_request_builder.py`, following the existing `_para()` helper pattern in that file.
- Files: `tests/test_docs_request_builder.py`

---

#### Story 1.2.2: Read-only comment cross-reference and live glyph-shape check (`CommentSnapshot` + `GlyphShapeCheck` → `HighRiskParagraph`)
**As** Tyler, **I want** the preview and the real push gate to flag any paragraph it's about to delete/replace that either overlaps an open collaborator comment or is a native checkbox glyph, **so that** I never push a comment-destroying change, or silently layer a literal marker onto an already-native checkbox, without an explicit warning (closes pre-mortem.md #1).

**Acceptance Criteria**:
- `GoogleDocsClient.list_comments(doc_id) -> list[dict]` calls `drive_service.comments().list(fileId=doc_id, fields="comments(id,content,quotedFileContent,resolved,author(displayName))", includeDeleted=False).execute()` and returns only comments where `resolved` is falsy.
  - *Given* the scratch doc has one open comment with `quotedFileContent.value == "inner"` (Nora Sullivan's comment, anchored inside "gathering for dinner", per `requirements.md`/`research/features.md`) and one resolved comment, *When* `list_comments(scratch_doc_id)` is called, *Then* it returns a list of length 1 containing the open comment's dict, with the resolved comment excluded.
- A new `find_high_risk_paragraphs(entries: List[DiffEntry], comments) -> list[HighRiskParagraph]` (in a new `push_preview.py` module) flags any `DiffEntry` of kind `"remove"`/`"change"` whose `current_text` contains a comment's `quotedFileContent.value` as a substring. **It consumes the `DiffEntry` list already computed by `diff_summary()` (Story 1.2.1) — it does not independently re-run `difflib.SequenceMatcher` or reach into `DocsRequestBuilder`'s private `_text_key`.** (Per architecture-review.md's Concern: recomputing the diff a second time duplicates logic that must stay behaviorally identical in two places, and doubles the diff cost inside every `PushPlan`.)
  - *Given* `entries = [DiffEntry(kind="change", current_text="Casual gathering for dinner at 6:30pm Friday", target_text="Casual dinner at 6:30pm Friday", style="NORMAL_TEXT")]` (Tyler reworded the paragraph) and `comments = [{"quotedFileContent": {"value": "inner"}, "author": {"displayName": "Nora Sullivan"}}]`, *When* `find_high_risk_paragraphs(entries, comments)` runs, *Then* it returns `[HighRiskParagraph(paragraph_text="Casual gathering for dinner at 6:30pm Friday", reasons=["comment"], comment_quoted_text="inner", comment_author="Nora Sullivan")]` — because the entry's `kind == "change"` and `"inner"` is a substring of its `current_text` (matching within "d**inner**", which is a known, documented limitation of the substring heuristic per ADR-002 — the paragraph is still correctly the one carrying the comment, so the flag is correct even though the substring match itself is coincidental).
  - *Given* the same `comments` list but `entries` containing no `"remove"`/`"change"` entry for the "Casual gathering for dinner" paragraph (it was unchanged, so `diff_summary()` never emitted an entry for it) and one `"change"` entry for a *different*, unrelated paragraph, *When* `find_high_risk_paragraphs` runs, *Then* it returns `[]` — an unrelated edit never gets flagged.
- `find_high_risk_paragraphs(entries: List[DiffEntry], comments) -> list[HighRiskParagraph]` **also** flags any `DiffEntry` of kind `"remove"`/`"change"` whose `current_is_native_checkbox` is `True` (populated by Story 1.2.1's `diff_summary()` from the current-side `DocsParagraphNode.is_native_checkbox`, itself resolved live by `DocsStructureParser` — Task 1.2.2d) — independent of whether that paragraph also has an open comment. This is the `GlyphShapeCheck`; it uses the exact glyph-resolution method as Phase 0's `ChecklistParagraphSurvey` (`bullet.listId` → `document.lists[listId].listProperties.nestingLevels[n].glyphType == GLYPH_TYPE_UNSPECIFIED`), but re-run live against `push()`'s own fetch rather than looked up from ADR-001's static survey table.
  - *Given* `entries = [DiffEntry(kind="change", current_text="[ ] Whatsapp group", target_text="[x] Whatsapp group", style="NORMAL_TEXT", current_is_native_checkbox=True)]` (Tyler is trying to check off a paragraph the live fetch shows is a native checkbox glyph) and `comments = []`, *When* `find_high_risk_paragraphs(entries, comments)` runs, *Then* it returns `[HighRiskParagraph(paragraph_text="[ ] Whatsapp group", reasons=["native_glyph"], comment_quoted_text=None, comment_author=None)]` — flagged even though no comment is involved, and even though the paragraph text already contains literal `[ ]` (the two representations can coexist on one paragraph, which is exactly the corruption risk pre-mortem.md #1 describes).
  - *Given* the same `entries` but `current_is_native_checkbox=False` (an ordinary literal-text checklist paragraph), *When* `find_high_risk_paragraphs` runs with no matching comment, *Then* it returns `[]` — an ordinary literal-text checklist toggle is never flagged just for being a checklist line.

**Files**: `src/docspan/backends/google_docs/client.py`, `src/docspan/backends/google_docs/push_preview.py` (new), `src/docspan/backends/google_docs/docs_structure_parser.py` (adds `is_native_checkbox` — Task 1.2.2d)

##### Task 1.2.2a: Add `list_comments()` to `GoogleDocsClient` (~5 min)
- Before writing this method, resolve Unresolved Question 3 by actually calling `list_comments()` against the `ScratchDoc` (not just confirming `drive_service.comments()` exists on the built service object) — this verifies both method existence *and* that the configured credentials' Drive scope actually permits reading comments.
- Add `list_comments(self, doc_id: str) -> list[dict]` to `client.py`, wrapped in the existing `_with_backoff` helper (same pattern as `get_document`/`batch_update`), filtering out `resolved=True` comments before returning.
- Files: `src/docspan/backends/google_docs/client.py`

##### Task 1.2.2b: Create `push_preview.py` with `HighRiskParagraph` and `find_high_risk_paragraphs()` — comment check and glyph-shape check (~7 min, widened from ~5 min to fold in `GlyphShapeCheck` per pre-mortem.md #1)
- New file `src/docspan/backends/google_docs/push_preview.py`.
- Define `HighRiskParagraph` dataclass: `paragraph_text: str`, `reasons: List[Literal["comment", "native_glyph"]]`, `comment_quoted_text: Optional[str] = None`, `comment_author: Optional[str] = None`.
- Implement `find_high_risk_paragraphs(entries: List[DiffEntry], comments) -> List[HighRiskParagraph]`: filter `entries` for `kind in ("remove", "change")`; for each such entry, collect a `reasons` list: append `"comment"` if any comment's `quotedFileContent.value` (skip empty/missing) is a substring of `entry.current_text`; append `"native_glyph"` if `entry.current_is_native_checkbox` is `True`. If `reasons` is non-empty, emit one `HighRiskParagraph` for that entry with both reasons combined (not two separate entries for the same paragraph), setting `comment_quoted_text`/`comment_author` only when `"comment"` is among `reasons`. No `SequenceMatcher`/private-method access here, and no second document fetch — both checks read only from the already-built `entries`/`comments`.
- Files: `src/docspan/backends/google_docs/push_preview.py`

##### Task 1.2.2c: Unit tests for `list_comments()` and `find_high_risk_paragraphs()` — comment and glyph-shape cases (~7 min, widened to cover `GlyphShapeCheck`)
- New file `tests/test_push_preview.py` (fixed location — not left ambiguous per adversarial-review.md's Minor note). Add the Given-When-Then cases from the Story above as `test_find_high_risk_paragraphs_flags_changed_paragraph_with_open_comment`, `test_find_high_risk_paragraphs_ignores_unchanged_paragraphs`, `test_find_high_risk_paragraphs_flags_native_checkbox_glyph_paragraph_even_without_comment`, `test_find_high_risk_paragraphs_does_not_flag_ordinary_literal_checklist_paragraph`, and `test_find_high_risk_paragraphs_combines_both_reasons_when_paragraph_has_open_comment_and_is_native_glyph`, all operating on plain `DiffEntry`/dict inputs (no mocking needed since the function takes plain data in). Add a separate `list_comments()` test mocking the Drive service at the `GoogleDocsClient` level (following `tests/test_cli.py`'s `MagicMock`/`patch` conventions) — this is a firm commitment, not conditional.
- Files: `tests/test_push_preview.py` (new)

##### Task 1.2.2d: Resolve `is_native_checkbox` in `DocsStructureParser` (~6 min)
- Edit `DocsStructureParser.parse()` (`docs_structure_parser.py:32`) to also pass the document's `lists` dict (`doc["lists"]` — sibling of `body`/`tabs`, sourced the same way `body` is resolved for tabs-vs-legacy documents) down into `_parse_paragraph()`.
- In `_parse_paragraph()` (`docs_structure_parser.py:68`), after computing `bullet = paragraph.get("bullet")`: if `bullet` is present, resolve `lists.get(bullet["listId"], {}).get("listProperties", {}).get("nestingLevels", [])[nesting_level].get("glyphType")` (guarding index/key errors with `.get()`/bounds checks, returning `None` on any missing piece) and set `is_native_checkbox = (glyph_type == "GLYPH_TYPE_UNSPECIFIED")` — this is exactly the lookup Task 0.1.2a's throwaway survey script already performs by hand; this task makes it a permanent, reusable field on every parsed paragraph instead of a one-off script output.
- Add `is_native_checkbox: bool = False` to the `DocsParagraphNode` dataclass (`docs_structure_parser.py:18`). Not part of any diff key — `DocsRequestBuilder.build()`'s existing `_text_key`/`SequenceMatcher` machinery is untouched by this field.
- Add `test_parse_paragraph_sets_is_native_checkbox_true_for_checkbox_glyph_bullet` and `test_parse_paragraph_sets_is_native_checkbox_false_for_ordinary_bullet` to `tests/test_docs_structure_parser.py`, using a `lists` fixture shaped like the real `documents.get()` response (`{"kix.abc": {"listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}}}` for the checkbox case, `{"glyphType": "DECIMAL"}`-or-similar for the ordinary case).
- Files: `src/docspan/backends/google_docs/docs_structure_parser.py`, `tests/test_docs_structure_parser.py`

---

#### Story 1.2.3: `GoogleDocsBackend` builds a single-fetch `PushPlan`; `push()` enforces the comment-risk **and native-glyph-risk** gate from its own fetch, `preview_push()` renders a cosmetic-only copy for `--dry-run`
**As** Tyler, **I want** the comment-risk **and native-glyph-risk** safety gate to be evaluated inside `push()` itself, against data `push()` fetched itself immediately before writing, **so that** no real push can ever go through against a document snapshot that was never risk-checked, and no checklist toggle can silently layer a literal marker onto an already-native checkbox glyph (pre-mortem.md #1) — closing the TOCTOU gap architecture-review.md flagged as a BLOCKER (a CLI-layer `preview_push()` call and `push()`'s own fetch are two independent reads of the doc; a comment added between them was invisible to the old design's check).

This story deliberately does **not** have `push()` call `preview_push()` (or vice versa) to share a single fetch across the CLI and the backend — that was the exact structure that created the TOCTOU gap. Instead, both `push()` and `preview_push()` independently call a shared internal builder (`_build_push_plan()`), and each does **its own** `get_document()`/`list_comments()` calls. `preview_push()`'s fetch is used only to render a dry-run summary — its staleness is cosmetic. `push()`'s fetch is the one and only fetch whose diff and comment snapshot the safety gate is evaluated against, immediately before `batch_update()` is called.

**Acceptance Criteria**:
- `GoogleDocsBackend._build_push_plan(local_path, doc_id) -> PushPlan` performs exactly one `get_document()` call and exactly one `list_comments()` call, then computes `current_nodes`, `target_nodes`, `requests` (via `DocsRequestBuilder().build(...)`), `entries`/`unchanged_count` (via `DocsRequestBuilder().diff_summary(...)`), and `high_risk` (via `find_high_risk_paragraphs(entries, comments)`) — all from that single fetch — and returns them bundled in a `PushPlan` dataclass. It never calls `batch_update`.
  - *Given* a local markdown file with `- [ ] Splitwise` changed to `- [x] Splitwise` and no other edits, and a scratch doc with no open comments on that paragraph, *When* `_build_push_plan(local_path, scratch_doc_id)` is called, *Then* it returns a `PushPlan` with one `DiffEntry(kind="change", ...)` in `.entries`, `.high_risk == []`, `.requests` non-empty, and exactly one call each to the mocked `get_document`/`list_comments` (verifiable via mock call-count assertions).
- `GoogleDocsBackend.preview_push(local_path, doc_id) -> PushPreview` calls `_build_push_plan()` (its **own**, independent call — a fresh fetch, not one passed in from elsewhere) and maps its fields into a `PushPreview(entries=..., unchanged_count=..., high_risk=..., request_count=len(requests))`. It never calls `self._client.batch_update`. Its result is used **only** for `--dry-run` rendering (Story 1.2.4) — it is never passed into or consulted by `push()`.
- `GoogleDocsBackend.push(local_path, doc_id, force: bool = False, **kwargs) -> PushResult` calls `_build_push_plan()` (its **own**, independent call — this is the fetch the write is actually gated on):
  1. If `plan.requests` is empty, return `PushResult(status="skipped", ...)` as today.
  2. If `plan.high_risk` is non-empty and `force` is not `True`, return `PushResult(status="blocked", doc_id=doc_id, message=render_high_risk(plan.high_risk))` **before ever calling `batch_update()`** — `push_preview.py` gains a `render_high_risk(high_risk: List[HighRiskParagraph]) -> str` helper shared by this message and by `PushPreview.render()`, so the warning text is identical whether seen via `--dry-run` or a blocked real push.
  3. Otherwise, call `self._client.batch_update(doc_id, plan.requests, required_revision_id=plan.doc["revisionId"])`, applying Story 1.1.2's stale-revision `"conflict"` handling and the generic `"error"` fallback.
  4. On success, apply the `CommentCountBackstop` (Task 1.2.3c): call `list_comments()` again and compare its length to `len(plan.comments)`. If the open-comment count did **not** drop, return `PushResult(status="ok", ...)` as before. If it **did** drop, return `PushResult(status="warning", doc_id=doc_id, message="⚠ open comment count dropped (N→M) — a comment may have been lost even though it wasn't flagged")` — **never** `status="ok"` with the warning merely appended to the message. Leaving `status="ok"` here would let the CLI's existing icon/exit-code logic (Story 1.2.4) render this as a green `✓`/exit-0 success even though the backstop just detected a comment was lost — the exact "silent failure disguised as success" pattern this plan's own `"blocked"`/`"conflict"` statuses already exist to avoid.
  - *Given* a scratch-doc scenario where `plan.high_risk` is non-empty and `force=False`, *When* `push(local_path, scratch_doc_id)` is called, *Then* it returns `PushResult(status="blocked", ...)` and the mocked `batch_update` has `assert_not_called()` — **and** `get_document`/`list_comments` were each called exactly once (by `push()`'s own `_build_push_plan()` call), proving the decision was made from the same fetch the write would have used, not a separate CLI-supplied one.
  - *Given* the same scenario but `force=True`, *When* `push(...)` is called, *Then* it proceeds to call `batch_update(doc_id, plan.requests, required_revision_id=plan.doc["revisionId"])` using the requests/revisionId from that same single fetch.
  - *Given* `plan.comments` has length 2 before `batch_update()`, and after a successful `batch_update()` a fresh `list_comments(doc_id)` call returns length 1 (one open comment lost during the write), *When* `push(...)` reaches the `CommentCountBackstop` check, *Then* it returns `PushResult(status="warning", doc_id=doc_id, message="⚠ open comment count dropped (2→1) — a comment may have been lost even though it wasn't flagged")`, **not** `PushResult(status="ok", ...)` — and the CLI (Story 1.2.4) renders this with a yellow `⚠`/non-green icon and a nonzero exit code, never the green `✓`/exit 0 it would show for a clean `"ok"` push.
- `PushResult.status`'s `Literal` (`src/docspan/backends/base.py`) gains `"blocked"` and `"warning"` alongside the existing `"ok" | "conflict" | "error" | "skipped"` — `"blocked"` for a pre-`batch_update()` refusal (`HighRiskParagraph` gate without `--force`), `"warning"` for a post-`batch_update()` success that `CommentCountBackstop` flagged as having dropped the open-comment count. Neither is ever collapsed back into `"ok"`.
- `PushPreview.render() -> str` produces the human-readable multi-line summary described in Story 1.2.4's acceptance criteria, reusing `render_high_risk()` for its high-risk lines, and — per the "isolate checklist-only pushes" nudge (Risk Control section) — prints one extra note line when `entries` contains both at least one checklist-marker change (`current_text`/`target_text` starting with `[ ]`/`[x]`) and at least one non-checklist change: `"ⓘ This push mixes N checklist toggle(s) with M other edit(s) — consider pushing checklist-only changes separately (see workflow runbook)."` This is a nudge only, not enforcement (see Unresolved Questions).

**Files**: `src/docspan/backends/google_docs/backend.py`, `src/docspan/backends/google_docs/push_preview.py`, `src/docspan/backends/base.py`

##### Task 1.2.3a: Extract `_build_push_plan()` and `PushPlan` from `push()` (~6 min)
- Add `PushPlan` dataclass to `push_preview.py`: `current_nodes`, `target_nodes`, `requests: list[dict]`, `doc: dict`, `entries: List[DiffEntry]`, `unchanged_count: int`, `comments: list[dict]`, `high_risk: List[HighRiskParagraph]`.
- In `backend.py`, extract lines 54-66 of the current `push()` body (content read, `MarkdownToParagraphParser().parse`, `get_document`, `DocsStructureParser().parse`, `doc_end_index` computation) plus the new `diff_summary`/`list_comments`/`find_high_risk_paragraphs` calls into `_build_push_plan(self, local_path, doc_id) -> PushPlan`.
- Files: `src/docspan/backends/google_docs/backend.py`, `src/docspan/backends/google_docs/push_preview.py`

##### Task 1.2.3b: Add `PushPreview` dataclass, `render_high_risk()`, and `preview_push()` — cosmetic, `--dry-run`-only (~6 min)
- Add `PushPreview` dataclass to `push_preview.py` (`entries: List[DiffEntry]`, `unchanged_count: int`, `high_risk: List[HighRiskParagraph]`, `request_count: int`) with a `render() -> str` method producing lines like:
  ```
  Preview: 1 change, 0 additions, 0 removals, 12 unchanged
    ~ [ ] Splitwise → [x] Splitwise
  ⚠ COMMENT AT RISK: paragraph "Casual gathering for dinner..." has an open comment
    from Nora Sullivan ("inner") and would be changed. Resolve manually in Google
    Docs, or re-run with --force to proceed anyway.
  ```
- Add a standalone `render_high_risk(high_risk: List[HighRiskParagraph]) -> str` function producing the `⚠ COMMENT AT RISK` block(s) above; `PushPreview.render()` and `push()`'s blocked-path message (Task 1.2.3c) both call it, so the text is identical in both places.
- `render_high_risk()` renders a distinct message block per reason present on each `HighRiskParagraph` — e.g. for `reasons=["native_glyph"]`:
  ```
  ⚠ NATIVE CHECKBOX GLYPH: paragraph "[ ] Whatsapp group" is a native Google Docs
    checkbox (checked/unchecked state not readable via the API) — editing it here
    would layer literal [x]/[ ] text on top of the existing glyph. Toggle this
    line by hand in Google Docs UI instead, or re-run with --force to proceed
    anyway.
  ```
  and for a paragraph with both `"comment"` and `"native_glyph"` in `reasons`, both blocks are printed one after another for that paragraph — never merged into one message that could obscure either reason.
- Add the checklist/non-checklist mixed-push note line to `PushPreview.render()` as described in the Story's acceptance criteria.
- Add `GoogleDocsBackend.preview_push(self, local_path: str, doc_id: str) -> PushPreview` to `backend.py`: calls `_build_push_plan()` (its own fetch) and maps the result into `PushPreview`. Never calls `batch_update`. Docstring explicitly states this method is for `--dry-run` rendering only and must never be used to gate a real write.
- Files: `src/docspan/backends/google_docs/backend.py`, `src/docspan/backends/google_docs/push_preview.py`

##### Task 1.2.3c: Rewrite `push()` to gate on its own `PushPlan` and apply the `CommentCountBackstop` (~8 min)
- Rewrite `push()` in `backend.py` to: call `_build_push_plan()` (its own fetch); return `"skipped"` if no requests; return `"blocked"` (using `render_high_risk`) if `plan.high_risk and not force`; otherwise call `batch_update(doc_id, plan.requests, required_revision_id=plan.doc["revisionId"])` inside the try/except from Story 1.1.2 (preserving the `"conflict"` branch added there); on success, call `self._client.list_comments(doc_id)` again and compare its length to `len(plan.comments)`. If the count is unchanged or higher, return `PushResult(status="ok", doc_id=doc_id, message=...)` as before. If it **dropped**, return `PushResult(status="warning", doc_id=doc_id, message="⚠ open comment count dropped (N→M) — a comment may have been lost even though it wasn't flagged")` instead of `"ok"`. The `CommentCountBackstop` must escalate `PushResult.status`, not just append a line to a result that still reads `"ok"` — a `status="ok"` result renders as a green `✓`/exit-0 success in the CLI (Story 1.2.4) regardless of what the message text says, which is exactly the "silent failure disguised as success" pattern the plan's own `"blocked"`/`"conflict"` statuses already exist to avoid.
- Add `force: bool = False` to `push()`'s signature (in addition to `**kwargs`, so `Backend.push(self, local_path, doc_id, **kwargs)`'s ABC signature is still satisfied — `force` is read from `kwargs.get("force", False)` or added as an explicit keyword-only parameter, whichever keeps the ABC's `**kwargs` contract intact).
- Add `"blocked"` and `"warning"` to `PushResult.status`'s `Literal` in `src/docspan/backends/base.py`.
- Files: `src/docspan/backends/google_docs/backend.py`, `src/docspan/backends/base.py`

##### Task 1.2.3d: Unit tests proving `push()` gates on its own fetch, and `preview_push()` never writes (~6 min)
- Test 1: mock `GoogleDocsClient` (`get_document`, `list_comments`) so the doc is high-risk; call `preview_push()`; assert it returns a populated `PushPreview` and `batch_update` (mocked) has `assert_not_called()`.
- Test 2 (the blocker-fix regression test): same high-risk mock setup; call `push(local_path, doc_id, force=False)`; assert `PushResult.status == "blocked"`, `batch_update.assert_not_called()`, **and** `get_document.call_count == 1` / `list_comments.call_count == 1` — proving `push()` decided from exactly one fetch it performed itself, not zero (a stale externally-supplied preview) or two (the pre-fix duplicate-fetch design).
- Test 3: same setup with `force=True`; assert `batch_update` is called once with `required_revision_id` matching the mocked `doc["revisionId"]`.
- Test 4: `CommentCountBackstop`, drop detected — mock `list_comments` to return 3 comments before `batch_update` and 2 after; assert `PushResult.status == "warning"` (**not** `"ok"`) **and** `PushResult.message` contains the `⚠ open comment count dropped (3→2)` line. This is the regression test for the status-escalation fix: a version that only appends the message and leaves `status="ok"` must fail this test.
- Test 5: `CommentCountBackstop`, no drop — mock `list_comments` to return the same count before and after `batch_update`; assert `PushResult.status == "ok"` and `.message` has no drop-warning line.
- Files: `tests/test_google_docs_backend.py` (new, if one doesn't already test `backend.py` directly — check first; extend whichever file already covers `GoogleDocsBackend`)

---

#### Story 1.2.4: CLI renders the `--dry-run` preview and threads `--force` through to `push()`'s own gate
**As** Tyler, **I want** `docspan push --dry-run` to show me a real diff, and a real (non-dry-run) push to refuse to proceed through a flagged high risk (an open comment or a native-checkbox glyph) unless I pass `--force`, **so that** the risk control requirements.md promises actually exists — enforced by the backend, not the CLI.

Per Story 1.2.3, the CLI is **not** the enforcement point anymore: it no longer calls `preview_push()` to decide whether to skip `orchestrate_push()`. That CLI-layer decision was exactly the TOCTOU gap architecture-review.md flagged as a BLOCKER (a preview fetch and a separate write fetch, split across two layers). Instead, the CLI's only two jobs are: (1) render `preview_push()`'s output for `--dry-run` (cosmetic, read-only, unchanged in spirit from before), and (2) thread the `--force` flag through to `push()`, which makes and enforces the block/proceed decision itself, from its own single fetch.

**Acceptance Criteria**:
- `docspan push --dry-run` (currently `cli/main.py:107-111`, a no-op stub) now calls `backend.preview_push(mapping.local, mapping.remote_id)` (when the backend has a `preview_push` attribute — `hasattr` check to stay Confluence-safe per the Pattern Decisions ISP note) and prints `preview.render()`. This call never writes and never gates anything — it is purely informational.
  - *Given* a mapping for `wedding-scratch.md` → the scratch doc, and a local edit changing `- [ ] Splitwise` to `- [x] Splitwise`, *When* Tyler runs `docspan push --dry-run wedding-scratch.md`, *Then* the console output includes `~ [ ] Splitwise → [x] Splitwise` and no network write occurs (`batchUpdate` never called).
- A new `--force` flag is added to the `push` command and threaded, unchanged, through `orchestrate_push(mapping, backend, state, state_dir, state_path, force=force)` into `backend.push(mapping.local, mapping.remote_id, force=force)` (`src/docspan/core/orchestrator.py`'s `orchestrate_push` gains a `force: bool = False` parameter it passes straight through). The CLI itself makes no high-risk decision — it only relays `result.status`.
  - *Given* the same scratch-doc mapping and edit, but this time with an open comment (`quotedFileContent.value == "inner"`) on the "gathering for dinner" paragraph that the diff also touches, *When* Tyler runs `docspan push wedding-scratch.md` (no `--force`), *Then* `orchestrate_push` returns a `PushResult(status="blocked", message=...)` computed entirely inside `push()`, the CLI prints that message with a `✗` line, and exits with code 1 — `batch_update` was never called.
  - *Given* the same scenario, *When* Tyler runs `docspan push --force wedding-scratch.md`, *Then* `force=True` reaches `push()`, which proceeds to call `batch_update` (the underlying push still succeeds or fails on its own merits — `--force` only bypasses the *risk-flag* block, not the `RevisionGuard`).
- The CLI's result-handling block (`icon = "✓" if result.status in ("ok", "skipped") else "✗"` etc.) is extended so `"blocked"` and `"conflict"` are treated like `"error"` for icon/color/exit-code purposes (`✗`, red, `had_error = True`) — no new branch structure needed, just add `"blocked"` and `"conflict"` to the existing "not ok/skipped" red-icon path (`"conflict"` already worked this way before this plan; `"blocked"` is new). `"warning"` (Task 1.2.3c's `CommentCountBackstop` escalation) gets its **own**, third branch, distinct from both: `icon = "⚠"`, `style = "yellow"`, and it still sets `had_error = True` so the process exits nonzero — a real push must not silently exit 0 after losing a comment — but it is never rendered with the same green `✓` as a clean `"ok"`/`"skipped"`, and is visually distinct from the red `✗` used for `"blocked"`/`"conflict"`/`"error"` so Tyler doesn't mistake "the write succeeded but a comment may be gone, go check" for "the write didn't happen at all."
  - *Given* a mocked `backend.push()` returning `PushResult(status="warning", message="⚠ open comment count dropped (2→1)")`, *When* the CLI renders the result line, *Then* it prints a yellow `⚠` (not a green `✓` and not a red `✗`) and the command's overall exit code is nonzero — confirming the `CommentCountBackstop`'s finding is never displayed as, nor counted as, a clean success.
- When `preview_push` is unavailable on the backend (e.g. Confluence), the CLI falls back to today's one-line stub message for `--dry-run`; for a real push, Confluence's `push()` simply has no `force` concept to honor and no high-risk gate (documented as a feature gap in Story 3.2.1 — Confluence comment risk-flagging is out of scope this cycle).

**Files**: `src/docspan/cli/main.py`, `src/docspan/core/orchestrator.py`

##### Task 1.2.4a: Implement real `--dry-run` rendering in `push()` (~5 min)
- Replace the stub block at `cli/main.py:107-111` with: if `dry_run` and `hasattr(backend, "preview_push")`, build the backend once (move `backend = _get_backend(...)` above the `dry_run` check), call `preview = backend.preview_push(mapping.local, mapping.remote_id)`, print `preview.render()`, `continue`. If `preview_push` is unavailable, keep today's stub line.
- Files: `src/docspan/cli/main.py`

##### Task 1.2.4b: Add `--force`, thread it through `orchestrate_push`, and remove the old CLI-layer preview gate (~6 min)
- Add `force: bool = typer.Option(False, "--force", help="Proceed with a push even if push() flags a comment-risk paragraph")` to the `push` command signature.
- Add `force: bool = False` to `orchestrate_push()`'s signature in `orchestrator.py`; change its `result = backend.push(mapping.local, mapping.remote_id)` call to `result = backend.push(mapping.local, mapping.remote_id, force=force)`.
- In `cli/main.py`, change the real-push call site to `orchestrate_push(mapping, backend, state, state_dir, state_path, force=force)`. **Do not** add any `preview_push()` call or `high_risk` check in the CLI's real-push path — that logic now lives entirely inside `push()` (Story 1.2.3). Extend the existing icon/color/`had_error` logic to treat `"blocked"` the same as `"error"`, and add a distinct `"warning"` branch (`icon = "⚠"`, `style = "yellow"`, `had_error = True`) so `CommentCountBackstop`'s post-push comment-loss detection (Task 1.2.3c) is never rendered as the same green `✓`/exit-0 as a clean `"ok"` push.
- Files: `src/docspan/cli/main.py`, `src/docspan/core/orchestrator.py`

##### Task 1.2.4c: CLI tests for dry-run rendering and `--force` threading (~5 min)
- Extend `FakeBackend` in `tests/test_cli.py` with an optional `preview_push` method (only on a `FakeBackendWithPreview` subclass, so the "no `preview_push`" fallback path is also tested against plain `FakeBackend`) and make `FakeBackend.push()` accept a `force` kwarg and return `status="blocked"` or `status="ok"` based on it, so the CLI test can assert threading without needing the real `GoogleDocsBackend`.
- Add tests: `test_dry_run_renders_preview_when_backend_supports_it`, `test_dry_run_falls_back_to_stub_when_backend_has_no_preview`, `test_push_reports_blocked_status_as_error_without_force`, `test_push_force_flag_reaches_backend_push_call` (assert the `FakeBackend.push()` mock was called with `force=True`), `test_push_reports_warning_status_with_yellow_icon_and_nonzero_exit` (mock `FakeBackend.push()` to return `PushResult(status="warning", message="⚠ open comment count dropped (2→1)")`; assert the rendered icon is `⚠`, not `✓`, and the process exits nonzero — proving `CommentCountBackstop`'s escalated status is never rendered/exited like a clean `"ok"`).
- Files: `tests/test_cli.py`

---

#### Story 1.2.5: CLI prompts for confirmation before the first push against the live wedding doc if scratch verification hasn't been recorded
**As** Tyler, **I want** a loud, one-time confirmation prompt before docspan's first-ever push against the live wedding doc (`1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE`) if I haven't recorded that Story 2.2.1's scratch-doc verification passed, **so that** I can't accidentally run a real push against the live doc purely from memory/habit under wedding-week time pressure, closing pre-mortem.md #3.

This is a CLI-layer, non-blocking nudge — not a `push()`-level enforcement gate like `HighRiskParagraph`/`--force`. It never touches `GoogleDocsBackend.push()`'s signature or its own `PushPlan` fetch; it is a confirmation the CLI asks *before* even calling `orchestrate_push()`, and only when the target `doc_id` is specifically the live wedding doc id (never for `wedding-scratch.md` or any other mapping).

**Acceptance Criteria**:
- Before calling `orchestrate_push()` for a real (non-`--dry-run`) push whose resolved `mapping.remote_id == "1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE"`, the CLI checks whether `ScratchVerificationMarker` (`<state_dir>/scratch-verified.marker`) exists.
  - *Given* the marker file does not exist and Tyler runs `docspan push wedding.md` (mapped to the live doc id), *When* the CLI reaches the real-push path, *Then* it prints `"⚠ Scratch-doc verification not recorded — proceed against live doc? [y/N]"` and reads a confirmation from stdin **before** calling `orchestrate_push()`.
  - *Given* Tyler answers anything other than `y`/`Y`, *When* the prompt is answered, *Then* the CLI aborts that push (prints a one-line "Push cancelled." message, exit code 1) without calling `orchestrate_push()` — `batch_update` is never reached.
  - *Given* Tyler answers `y`, *When* the prompt is confirmed, *Then* the CLI creates the marker file (so future live pushes skip the prompt) and proceeds to call `orchestrate_push()` exactly as it would have without this story.
- If the marker file already exists, the CLI skips the prompt entirely and proceeds directly to `orchestrate_push()` — no behavior change from before this story.
- The prompt never appears for `--dry-run` (`preview_push()` is read-only and already safe) or for any mapping whose `remote_id` is not the live wedding doc id (e.g. `wedding-scratch.md` pushes never prompt) — this is a targeted tripwire for the one highest-consequence doc_id, not a general confirmation-on-every-push nuisance.
- This check is purely additive to Story 1.2.4's flow — it does not change `push()`'s signature, `PushResult.status` values, or the `high_risk`/`--force` gate in any way; a `y` at this prompt still leaves the `HighRiskParagraph` block (comment or native-glyph) fully in force underneath it.

**Files**: `src/docspan/cli/main.py`

##### Task 1.2.5a: Add the `ScratchVerificationMarker` check and confirmation prompt to the CLI push path (~5 min)
- In `cli/main.py`'s real-push branch (immediately before the `orchestrate_push(...)` call added in Task 1.2.4b), add: if `mapping.remote_id == "1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE"` and the marker file (`state_dir / "scratch-verified.marker"`) doesn't exist, call `typer.confirm("⚠ Scratch-doc verification not recorded — proceed against live doc?", default=False)`; on `False`, print `"Push cancelled."` and `continue`/skip this mapping without calling `orchestrate_push()`; on `True`, create the marker file (`state_dir.mkdir(parents=True, exist_ok=True); (state_dir / "scratch-verified.marker").write_text(...)`) and fall through to the existing `orchestrate_push()` call.
- Hardcode the live doc id as a module-level constant (`LIVE_WEDDING_DOC_ID = "1T0Omd6G2KU6QZ3Te-8C65uIVuq3dG46I_D_FS5RBbDE"`) rather than inlining the literal twice, matching the plan's existing style of citing this id explicitly elsewhere (Domain Glossary `ScratchDoc` entry).
- Files: `src/docspan/cli/main.py`

##### Task 1.2.5b: CLI test for the confirmation prompt (~4 min)
- Add `test_live_doc_push_prompts_when_marker_missing_and_aborts_on_no`, `test_live_doc_push_prompts_and_proceeds_and_writes_marker_on_yes`, `test_live_doc_push_skips_prompt_when_marker_present`, `test_scratch_doc_push_never_prompts` to `tests/test_cli.py`, using `typer.testing.CliRunner`'s `input=` parameter (already available via the `typer` dependency) to simulate stdin, and a `tmp_path`-based `state_dir` fixture so the marker file check is isolated per test.
- Files: `tests/test_cli.py`

---

## Phase 2: Checklist Round-Trip — Verify and Lock In the Literal-Text Scheme

### Epic 2.1: Regression tests locking `LiteralTextScheme` behavior

**Goal**: Requirements.md's in-scope item 1 ("verify/fix checklist round-trip") is satisfied by confirming — with tests and live verification — that the existing pull/push code already round-trips literal-text checklist markers correctly (per ADR-001's finding), and locking that behavior in so it can't silently regress. No production code changes are expected in this epic if Phase 0's finding is (b) (literal text already in use); if finding (a), see Task 0.1.2b's fallback.

---

#### Story 2.1.1: `DocsStructureParser` passes literal checklist markers through unmangled
**As** the push diff engine, **I want** confirmation that a paragraph whose Docs text literally reads `"[x] Whatsapp group"` parses into `DocsParagraphNode(text="[x] Whatsapp group", is_list_item=True, ...)` unchanged, **so that** checked-state is preserved as opaque text with no special-casing needed.

**Acceptance Criteria**:
- `DocsStructureParser()._parse_paragraph()` produces a node whose `.text` includes the literal `[x]`/`[ ]` marker, unmodified, for a paragraph structural element shaped like the real doc's checklist lines.
  - *Given* a Google Docs JSON structural element `{"paragraph": {"elements": [{"textRun": {"content": "[x] Whatsapp group\n"}}], "bullet": {"listId": "kix.abc", "nestingLevel": 0}}, "startIndex": 10, "endIndex": 30}`, *When* `DocsStructureParser().parse({"body": {"content": [that element]}})` is called, *Then* it returns `[DocsParagraphNode(style="NORMAL_TEXT", text="[x] Whatsapp group", is_list_item=True, nesting_level=0, start_index=10, end_index=30)]`.

**Files**: `tests/test_docs_structure_parser.py`

##### Task 2.1.1a: Add checklist-line regression test to `test_docs_structure_parser.py` (~4 min)
- Add `test_parse_paragraph_preserves_literal_checkbox_marker_in_text` using the exact fixture shape from the acceptance criterion above (mirror existing test helpers/fixtures already in this file).
- Files: `tests/test_docs_structure_parser.py`

---

#### Story 2.1.2: `MarkdownToParagraphParser` passes literal checklist markers through unmangled
**As** the push diff engine, **I want** confirmation that markdown `- [x] Whatsapp group` parses into `DocsParagraphNode(text="[x] Whatsapp group", is_list_item=True)` (not `mistune`'s stripped `task_list_item` form), **so that** the target side of the diff matches the current side's literal-text representation.

**Acceptance Criteria**:
- `MarkdownToParagraphParser().parse("- [x] Whatsapp group\n- [ ] Splitwise\n")` returns two `DocsParagraphNode`s: `text="[x] Whatsapp group"` and `text="[ ] Splitwise"`, both `is_list_item=True`, `nesting_level=0`.
  - *Given* the markdown string `"- [x] Whatsapp group\n- [ ] Splitwise\n"`, *When* `MarkdownToParagraphParser().parse(...)` runs, *Then* it returns exactly those two nodes with the literal bracket markers intact (confirms `mistune.create_markdown(renderer=None)` with **no `task_lists` plugin** — deliberately left disabled, see ADR-001 — treats `[x]`/`[ ]` as ordinary inline text, which is the desired behavior under `LiteralTextScheme`).
- A companion test confirms the plugin is deliberately *not* enabled: reading `markdown_to_paragraph_parser.py:91`, `mistune.create_markdown(renderer=None)` has no `plugins=` kwarg — a comment is added there explaining why (cross-reference ADR-001), so a future contributor doesn't "helpfully" enable it and break this test.

**Files**: `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py`, `tests/test_markdown_to_paragraph_parser.py`

##### Task 2.1.2a: Add checklist-passthrough regression tests (~4 min)
- Add `test_parse_preserves_literal_checkbox_markers_in_list_item_text` to `tests/test_markdown_to_paragraph_parser.py` using the fixture from the acceptance criterion.
- Files: `tests/test_markdown_to_paragraph_parser.py`

##### Task 2.1.2b: Document the deliberate non-use of `task_lists` in code (~2 min)
- Add a one-line comment above `md = mistune.create_markdown(renderer=None)` (line 91) in `markdown_to_paragraph_parser.py`: `# Deliberately NOT enabling plugins=["task_lists"] — checklist state is kept as literal text (ADR-001); the plugin would strip the [ ]/[x] marker into attrs.checked and lose it from .text.`
- Files: `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py`

---

#### Story 2.1.3: A checklist toggle is diffed as a paragraph `"change"`, produces one delete+insert, and preserves the bullet style
**As** the push request builder, **I want** confirmation that toggling `[ ]`→`[x]` on an otherwise-unchanged list item produces exactly the same request shape as any other single-line text edit, **so that** no new code path is silently required and existing index-shift correctness (`research/pitfalls.md` §1) is untouched.

**Acceptance Criteria**:
- `DocsRequestBuilder().build(current, target, doc_end_index)` for `current=[DocsParagraphNode(text="[ ] Splitwise", is_list_item=True, start_index=50, end_index=65)]`, `target=[DocsParagraphNode(text="[x] Splitwise", is_list_item=True)]` produces a `deleteContentRange` for `[50, 64)` (clamped as usual) followed by an `insertText` of `"[x] Splitwise\n"` and a `createParagraphBullets` request with `bulletPreset: "BULLET_DISC_CIRCLE_SQUARE"` (unchanged, per the Pattern Decision to *not* branch to `BULLET_CHECKBOX`).
  - *Given* those `current`/`target` nodes, *When* `build()` is called, *Then* `requests` contains exactly one `deleteContentRange`, one `insertText` with `text == "[x] Splitwise\n"`, and one `createParagraphBullets` with `bulletPreset == "BULLET_DISC_CIRCLE_SQUARE"` — no `BULLET_CHECKBOX` anywhere in the output.

**Files**: `tests/test_docs_request_builder.py`

##### Task 2.1.3a: Add checklist-toggle request-shape regression test (~4 min)
- Add `test_checklist_toggle_produces_replace_with_disc_bullet_not_checkbox` to `tests/test_docs_request_builder.py` using the `_para()` helper already defined there.
- Files: `tests/test_docs_request_builder.py`

---

### Epic 2.2: Live verification against the `ScratchDoc`

**Goal**: Requirements.md and `research/pitfalls.md` both insist unit tests alone are insufficient for a live, shared document — this epic is the one place in the plan where a human (Tyler) executes real CLI commands against the `ScratchDoc` and confirms the outcome by eye, closing the loop the unit tests in Epic 2.1 can't close alone.

---

#### Story 2.2.1: End-to-end checklist round-trip against the scratch doc, including a comment-risk collision
**As** Tyler, **I want** to personally run pull → toggle a checkbox → dry-run → push → pull again against the scratch doc, including one deliberate collision with an open comment, **so that** I trust this before ever running it against the live doc.

**Acceptance Criteria**:
- Running `docspan pull wedding-scratch.md`, editing one checklist line (e.g. `- [ ] Splitwise` → `- [x] Splitwise`) unrelated to any comment, running `docspan push --dry-run wedding-scratch.md` (shows the change, no `⚠`), then `docspan push wedding-scratch.md`, then `docspan pull wedding-scratch.md` again — results in the live scratch doc showing the checkbox toggled, and the re-pulled markdown showing `- [x] Splitwise`.
  - *Given* the scratch doc's current state has `- [ ] Splitwise` and no comment on that line, *When* Tyler completes the pull→edit→dry-run→push→pull cycle above, *Then* the final pulled markdown contains `- [x] Splitwise` and no error was reported at any step.
- Running the same cycle, but this time editing the paragraph containing the scratch doc's copy of "gathering for dinner" (which carries the copied-over Nora Sullivan comment anchored to "inner", or a manually-added throwaway comment with `quotedFileContent.value == "inner"` per Story 0.1.1's fallback) — `docspan push --dry-run` must show the `⚠ COMMENT AT RISK` warning, and a plain `docspan push` (no `--force`) must refuse to push and exit nonzero.
  - *Given* that setup, *When* Tyler runs `docspan push wedding-scratch.md` without `--force`, *Then* the CLI exits with code 1, prints the `⚠ COMMENT AT RISK` line naming the paragraph and "Nora Sullivan" (or the throwaway comment author), and the scratch doc's comment is still present and anchored (verified by opening the scratch doc in the browser).
- Both findings (pass or fail) are recorded as a checked/unchecked line in the workflow runbook (Story 3.1.1) under a "Verified before first live push" checklist, so this isn't re-litigated informally later.
- After both scenarios above pass, Tyler creates the `ScratchVerificationMarker` file (Task 2.2.1c) — this is the artifact Story 1.2.5's CLI confirmation prompt checks for before the first live-doc push, closing pre-mortem.md #3.

**Files**: none (manual verification step; Task 2.2.1c also creates a local `scratch-verified.marker` file, not committed — same category as `markgate.yaml`) — result recorded in `project_plans/wedding-planning-workflow/implementation/plan.md` (this file, mark the story checkbox below) and in the workflow runbook (Story 3.1.1).

##### Task 2.2.1a: Run the non-comment checklist round-trip against the scratch doc (~5 min)
- Execute the pull→edit→dry-run→push→pull cycle described above against `wedding-scratch.md`.
- Confirm the final state matches the acceptance criterion; note any discrepancy as a bug to fix before Phase 3.
- Files: none (manual)

##### Task 2.2.1b: Run the comment-collision scratch-doc test and confirm the fail-closed block (~5 min)
- Execute the comment-collision scenario described above; confirm the `⚠` warning appears, the push is blocked without `--force`, and the comment survives untouched in the scratch doc's UI.
- Then re-run with `--force` and confirm the push proceeds and the comment's survival/loss outcome is observed and recorded honestly (either outcome is useful information — if the comment is lost even with `--force`, that fact must be written into the feature-gap report, Story 3.2.1, as an explicit "comment loss confirmed, not just theorized" risk).
- Files: none (manual)

##### Task 2.2.1c: Write the `ScratchVerificationMarker` after both scratch-doc verification cycles pass (~1 min)
- After Task 2.2.1a **and** Task 2.2.1b both pass (non-comment round-trip confirmed, **and** the comment-collision fail-closed block confirmed), Tyler creates the marker file by hand — a one-line command, not application code — e.g. `mkdir -p <state_dir> && touch <state_dir>/scratch-verified.marker`, using the same `state_dir` docspan already resolves from its config (see Story 1.2.5's Task 1.2.5a, which checks for this exact path).
- If Task 2.2.1a or 2.2.1b finds a discrepancy or bug, do **not** create the marker — fix the bug first (per Task 2.2.1a's existing instruction), then re-run both cycles before touching the marker. The marker's presence is meant to mean "verified," not "attempted."
- Files: none (manual step; creates a local, gitignored `scratch-verified.marker` file, not a repo file — same category as `markgate.yaml`).

---

## Phase 3: Personal Workflow Deliverables

### Epic 3.1: Workflow runbook

**Goal**: Deliver requirements.md's Scope item 2 — a documented, repeatable pull→summarize→edit→pull→dry-run→push procedure Tyler (with Claude) follows for real wedding planning, distinct from docspan's own code.

---

#### Story 3.1.1: Write the workflow runbook
**As** Tyler, **I want** a single reference document describing the exact command sequence, the digest format Claude should produce, and the kill-switch conditions, **so that** I don't have to reconstruct the safe workflow from memory during wedding week.

**Acceptance Criteria**:
- `project_plans/wedding-planning-workflow/workflow-runbook.md` exists and includes: (1) the exact CLI sequence (`docspan pull` → Claude summarizes → Tyler edits → `docspan pull` again → `docspan push --dry-run` → review → `docspan push`); (2) the `OwnerDigest` output format from `research/ux.md` §2 (grouped by owner, diff-since-last-pull, gaps bucket for unowned/no-date items), with **two** worked examples using real doc content, not one: (a) a Tasks example (Tyler/Bekah/Ann sub-headers, `- [x] Whatsapp group`, `- [ ] Splitwise` as an unowned gap example), and (b) a Schedule **or** Housing example, since requirements.md's Scope item 2 lists Tasks, Schedule, and Housing/room assignments as separate priority items and Schedule/Housing entries aren't checklist-shaped (no owner/due-date markers) — the digest format needs to demonstrably handle that shape too, not just Tasks (adversarial-review.md Concern); (3) the "Verified before first live push" checklist populated from Story 2.2.1's results, plus a one-line caution restating ADR-002's "the dinner/inner substring match worked in testing but that was coincidental, not systematic" point directly in the runbook (not only in ADR-002) so Tyler doesn't over-trust the flag; (4) the `ManualFallbackGate` and "isolate checklist-only pushes" procedural mitigations from the Risk Control section above (explicitly noting the latter is operator discipline with only a render-time nudge, not hard enforcement), stated as concrete do/don't rules Tyler can follow without re-reading this plan; (5) a "Real cycle log" section — a plain table (date, what changed, open-comment count before/after, any surprises) that Tyler fills in for at least 3 real pull→edit→push cycles against the **live** doc before requirements.md's Success Metric 1 ("zero collaborator edits/comments lost across ≥3 real cycles") can be considered met — scratch-doc verification (Epic 2.2) is necessary but not sufficient evidence for that metric, since it's a synthetic single-editor test (adversarial-review.md Concern).
  - *Given* the runbook is complete, *When* Tyler reads it during wedding week under time pressure, *Then* he can execute a full sync cycle and interpret a `⚠ COMMENT AT RISK` warning without needing to open `docs_request_builder.py` or ask Claude to explain internals (matches `research/ux.md`'s "never require Tyler to understand batchUpdate/AST internals" principle).
  - *Given* a Schedule paragraph like `"Friday 6:30pm: rehearsal dinner at [venue]"` or a Housing paragraph like `"Housing: Bekah has the lake house"` that changed since the last pull, *When* Tyler reads the Schedule/Housing worked example in the runbook, *Then* he sees how the digest surfaces that kind of entry (grouped by day/venue for Schedule, by person/property for Housing) distinctly from how it surfaces a Tasks checklist line — confirming this in-scope requirement has its own verifiable example, not just an inferred extension of the Tasks example.
  - *Given* requirements.md's Scope item 2 also lists Food/catering as a priority slice, *When* Tyler reads the runbook, *Then* it states explicitly (one sentence, no separate worked example) that Food/catering entries are non-checklist prose identical in shape to the Schedule/Housing example above — e.g. `"Catering: Layers sandwiches for Thursday lunch"` groups the same way a Schedule line does — so the digest's coverage of that priority item is traceable without a fourth redundant example (cross-artifact consistency check, coverage gap B1).

**Files**: `project_plans/wedding-planning-workflow/workflow-runbook.md` (new)

##### Task 3.1.1a: Write the CLI sequence and digest format sections, including a Schedule/Housing example (~7 min)
- Draft the command sequence and the `OwnerDigest` worked examples into `workflow-runbook.md`: (a) the Tasks example (Bekah/Tyler/Ann sub-headers, `- [x] Whatsapp group`, `- [ ] Splitwise`), and (b) a second, distinct Schedule or Housing example (e.g. `"Friday 6:30pm: rehearsal dinner at [venue]"` grouped under a day heading, or `"Housing: Bekah has the lake house"` grouped under a person/property heading) showing the digest handles non-checklist, non-owner/due-date content.
- Add a one-sentence note stating Food/catering entries (e.g. `"Catering: Layers sandwiches for Thursday lunch"`) follow the same non-checklist shape as (b) and are covered by that example, closing coverage-gap B1 without a fourth redundant worked example.
- Files: `project_plans/wedding-planning-workflow/workflow-runbook.md`

##### Task 3.1.1b: Write the safety checklist, kill-switch section, and real-cycle log template (~6 min)
- Add the "Verified before first live push" checklist (populated from Story 2.2.1), the substring-match caution line, the `ManualFallbackGate` rule, and the "isolate checklist-only pushes" rule (marked explicitly as operator discipline + render-time nudge, not tool-enforced) to `workflow-runbook.md`.
- Add the "Real cycle log" table template (columns: date, what changed, open-comment count before/after, surprises) with instructions to fill in a row after each of the first 3 real live-doc sync cycles — this is the concrete "how we know Success Metric 1 succeeded" step (adversarial-review.md Concern), not code, just a runbook table Tyler fills in by hand.
- Files: `project_plans/wedding-planning-workflow/workflow-runbook.md`

---

### Epic 3.2: Feature-gap report

**Goal**: Deliver requirements.md's Scope item 3 — an explicit, written list of everything this doc exposes that stays out of scope this cycle, so gaps are documented, not silently dropped.

---

#### Story 3.2.1: Write the feature-gap report
**As** Tyler, **I want** a single document listing every known docspan limitation this real doc exposes, **so that** I know exactly what to watch for or do manually, rather than discovering a gap mid-sync during wedding week.

**Acceptance Criteria**:
- `project_plans/wedding-planning-workflow/feature-gap-report.md` lists, at minimum: (1) tables/`sectionBreak`/`tableOfContents` silently skipped on both pull and push (`docs_structure_parser.py:64`) — the doc's TOC and any table are invisible to Claude's summary; (2) no Google Sheets backend — grocery/packing list Sheets never sync; (3) no image support on push; (4) links and bold/italic/monospace formatting are dropped on any paragraph that gets a `replace`/`insert` opcode (confirmed dead code: `_make_text_style_requests` exists in `docs_request_builder.py:174-210` but is never called from `_make_insert_requests`) — any edited paragraph containing a link to a per-day sub-doc loses that link on push; (5) native `BULLET_CHECKBOX` checked/unchecked state is confirmed unreadable via `documents.get()` — if Phase 0's full-document survey (Task 0.1.2a) found native checkboxes in use anywhere in the doc (finding (a) or the specific paragraphs from a mixed finding (c)), they are named individually here and not converted to literal text this cycle (per Task 0.1.2b's fallback); those specific paragraphs also surface a `MixedChecklistWarning` on every pull, not just here; as of this pre-mortem repair pass, editing one of those specific paragraphs is also blocked at push time (`GlyphShapeCheck`, folded into `find_high_risk_paragraphs()` — Story 1.2.2/1.2.3) unless `--force` is passed, so the gap is now guarded on write, not only disclosed on pull; (6) comment-risk detection is a substring heuristic against `quotedFileContent.value`, not a full anchor-range decode — it can theoretically miss a comment whose quoted text doesn't appear verbatim in the paragraph text, or (per Story 2.2.1b's finding) it may only *warn*, not *prevent*, comment loss even with the warning heeded, if `--force` is used; (7) three-way merge (`merge3`) is line-based and conflates checklist text-edits with check-state toggles on the same line (`research/pitfalls.md` §3) — expect occasional conflict markers requiring `docspan conflicts resolve`; (8) a residual TOCTOU blind spot: `push()`'s comment-risk gate and its guarded write both derive from the *same* single fetch (closing the CLI/backend split TOCTOU architecture-review.md flagged as a BLOCKER), but a comment added by a collaborator in the few-hundred-millisecond window between that fetch and `batch_update()` actually landing is still invisible to the check, because Drive comments are metadata outside the Doc's `revisionId` and `writeControl` only guards the document body — the window is now milliseconds within one CLI invocation (not an entire CLI-call's worth of time as before the fix), and the `CommentCountBackstop` (item 9) catches it after the fact even though it can't prevent it; (9) the `CommentCountBackstop`'s exact open-comment-count comparison is a backstop *detector*, not a preventer — if it fires, the comment is already gone; `push()` escalates `PushResult.status` from `"ok"` to `"warning"` when this fires (it is never left as a false `"ok"` with only the message text changed), and Tyler must treat that `"warning"` status / `⚠ open comment count dropped` line as "go check the doc now," not as a push that auto-corrected itself; (10) "isolate checklist-only pushes near comments" is documented operator discipline with only a render-time nudge (`PushPreview`'s mixed-push note) — docspan does not reject or split a push that mixes checklist and non-checklist edits.
  - *Given* the report is written, *When* Tyler hits any of these situations live (e.g. wants to sync the grocery Sheet, or notices a link vanished after a push), *Then* he can look it up in this one file and immediately know it's a known, already-decided-against gap rather than a new bug to debug under time pressure.
- Item 4 (link/formatting loss) is cross-referenced against the workflow runbook's `ManualFallbackGate` rule: any paragraph the runbook or Claude's summary flags as containing a markdown link should be treated as `HighRiskParagraph`-equivalent by Tyler's own judgment even though the automated `find_high_risk_paragraphs` (Story 1.2.2) only checks for comments, not links — this is stated explicitly since it's not code-enforced this cycle.

**Files**: `project_plans/wedding-planning-workflow/feature-gap-report.md` (new)

##### Task 3.2.1a: Write the feature-gap report (~7 min)
- Draft all ten items above into `feature-gap-report.md`, each with the exact file:line reference already established by `research/*.md` (cited above) so a future implementer can find the code immediately. Item 5's mixed-checklist paragraphs are drawn directly from ADR-001's per-paragraph survey table (Task 0.1.2a) — name them, don't re-summarize.
- Files: `project_plans/wedding-planning-workflow/feature-gap-report.md`

---

## Phase 4: Documentation

### Epic 4.1: Update README Known Limitations

**Goal**: Keep `README.md`'s public-facing limitations list accurate now that dry-run, the revision guard, and comment-risk flagging exist — matches the repo's existing documentation discipline (the `Known Limitations` section was already written for v0.1.0).

---

#### Story 4.1.1: Update `README.md`'s Known Limitations section
**As** any docspan user (not just Tyler), **I want** the README to reflect what actually changed, **so that** the documented limitations stay trustworthy.

**Acceptance Criteria**:
- The existing line "Google Docs: comments on edited paragraphs are lost on push (paragraph-level structural diff; comments on unchanged paragraphs are preserved)" (`README.md:211`) is amended to note that `docspan push --dry-run` and a default fail-closed `--force`-gated block now warn before this happens (still not prevented, per Story 2.2.1b's honest finding — do not overclaim it's fixed).
  - *Given* the amended README, *When* a new user reads the Known Limitations section, *Then* they understand comments can still be lost on push, but they will be warned first and must pass `--force` to proceed — not that the underlying loss is fixed.
- A new bullet is added: "Checklist state (`- [ ]`/`- [x]`) round-trips as literal text — Google Docs' native checkbox glyph is intentionally not used because its checked/unchecked state cannot be read back via the API (see ADR-001)."
- A new bullet is added: "`push --dry-run` now shows a real structural diff and flags paragraphs with open comments at risk; `push` blocks by default on a flagged paragraph unless `--force` is passed."
- A new bullet is added: "If a push succeeds but a post-push check finds the open-comment count dropped (a comment was likely lost during the write even though nothing was flagged beforehand), docspan reports this as a `⚠` warning — never as a plain green success — so it's never mistaken for a clean push."

**Files**: `README.md`

##### Task 4.1.1a: Amend the Known Limitations bullets (~4 min)
- Edit the four bullets in `README.md`'s `## Known Limitations` section (lines ~206-215) as described above.
- Files: `README.md`

---

## ADR Stubs

### ADR-001: Represent checklist state as literal `[ ]`/`[x]` text, not native `BULLET_CHECKBOX` glyphs
Written to `project_plans/wedding-planning-workflow/decisions/ADR-001-checklist-state-as-literal-text.md` — see that file for full content (produced by Task 0.1.2b, finalized alongside this plan).

### ADR-002: Comment-risk flagging via a read-only `quotedFileContent` substring match, not a full anchor-range decode
Written to `project_plans/wedding-planning-workflow/decisions/ADR-002-comment-risk-flagging-not-anchor-preservation.md`.

---

## Sequencing Summary

1. **Phase 0** must complete first — it resolves the one unknown (checklist shape, surveyed across *every* checklist paragraph, not a single sample — see Story 0.1.2) that both Phase 1's comment-risk work and all of Phase 2 depend on.
2. **Phase 1 Epic 1.1** (RevisionGuard + friendly conflict message) has no dependency on Phase 0 and can be done in parallel with Phase 0 if desired.
3. **Phase 1 Epic 1.2** (`PushPlan`/`PushPreview`, and `push()`'s own fail-closed comment-risk gate) depends on Phase 0's comment-readability finding (Unresolved Question 2) being resolved.
4. **Phase 2** depends on Phase 0's checklist-shape finding and, for Epic 2.2 (live verification), on Phase 1 being fully merged (the dry-run/`--force` gate is what Story 2.2.1 exercises).
5. **Phase 3** depends on Phase 1 and Phase 2 both being complete and verified (the runbook documents real, working behavior; the feature-gap report cites Story 2.2.1's actual findings, not projected ones).
6. **Phase 4** is last — it documents the final, verified state of everything above.
7. **Go/no-go checkpoint**: by end of day **2026-07-20** (day 3 of the ~11-day runway), Phase 0 and Phase 1 (Epics 1.1 and 1.2) must be fully complete or the plan falls back to Approach C — see the Risk Control section above for the exact, dated trigger (pre-mortem.md #2). This checkpoint sits between steps 3 and 4 above chronologically, not after them — it is a hard stop-and-reassess point, not a retrospective note.

**Estimated total**: 5 Phases (0–4) · 8 Epics · 16 Stories · 32 Tasks (widened from 28: this pre-mortem repair pass added Story 1.2.5 — the `ScratchVerificationMarker` first-live-push confirmation prompt (pre-mortem.md #3) — plus Task 1.2.2d (live `is_native_checkbox` glyph resolution in `DocsStructureParser`, feeding the new `GlyphShapeCheck` folded into `find_high_risk_paragraphs()` per pre-mortem.md #1) and Task 2.2.1c (writing the marker file). Earlier widening from the original 26 to 28: Task 0.1.2a's checklist-shape check now surveys every checklist paragraph instead of one sample, Task 1.1.2b adds the friendly conflict message, and Story 1.2.3 gained a task from splitting `_build_push_plan()`/`preview_push()`/`push()`'s gate into distinct, independently-testable steps per architecture-review.md's BLOCKER remediation. Still fits within the Small (1–2 focused day) appetite, with Phase 0 and Epic 2.2 being the only steps that require live-doc interaction rather than pure code+tests — and now with a hard day-3 checkpoint (see item 7 above) if it doesn't. Per adversarial-review.md's Minor note, this per-task time budget has no slack for live-doc flakiness or debugging; Phase 0 and Epic 2.2 remain the likely actual time sinks.
