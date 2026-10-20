# Research: Technology Stack — wedding-planning-workflow

**Date**: 2026-07-18
**Scope**: Google Docs API checklist/checkbox representation; docspan's current handling (or non-handling) of it; pinned library versions.

## 1. Current docspan code — confirmed gaps (code search)

Repo note: `/home/tstapler/Programming/markgate` and `/home/tstapler/Programming/docspan` are the same git working tree (`docspan` resolves to the `markgate` path via symlink; both point at `origin = github.com/tstapler/docspan`). All file paths below are relative to that root.

### Pull path — `src/docspan/backends/google_docs/docs_structure_parser.py`
- `_parse_paragraph()` (lines 68–119) reads `paragraph.get("bullet")` and only extracts `nestingLevel` (line 110: `bullet.get("nestingLevel", 0)`).
- `DocsParagraphNode` (lines 17–26) has only `is_list_item: bool` and `nesting_level: int` — no field for glyph type, `listId`, or checked state.
- The Docs API `Bullet` object actually contains `listId`, `nestingLevel`, and `textStyle` — **docspan never reads `listId`**, so it has no way to look up the list's glyph/preset (see §2) even if it wanted to.
- `parse()` (line 59-64) silently skips any content element that isn't `"paragraph"` — `table`, `sectionBreak`, `tableOfContents` are dropped with a comment confirming this is intentional today.

### Push path — `src/docspan/backends/google_docs/docs_request_builder.py`
- `_make_insert_requests()` (lines 115–155): whenever `node.is_list_item` is true, it unconditionally emits `createParagraphBullets` with `"bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"` (hardcoded, line 152). There is no branch for a checklist preset, and no per-node signal that would let one exist.

### Markdown parsing — `src/docspan/backends/google_docs/markdown_to_paragraph_parser.py`
- Line 91: `mistune.create_markdown(renderer=None)` — **no `plugins=["task_lists"]` passed**. Mistune's task-list detection (which turns `- [ ] foo` / `- [x] foo` into a `task_list_item` token with `attrs.checked`) is opt-in and is not enabled here.
- Practical effect: markdown `- [ ] Buy flowers` parses as an ordinary `list_item` whose text literally starts with the four characters `[ ] ` (see `_extract_text_from_token`, lines 9–21, and `_walk_list_items`, lines 24–61 — neither special-cases a leading `[ ]`/`[x]`). Pushing this would create a normal bulleted Docs paragraph reading "[ ] Buy flowers", not a checkbox.

### HTML/Markdown converter — `src/docspan/backends/google_docs/converter.py`
- `html_to_markdown()` (lines 25–75+) uses `markdownify` with `bullets="-"` — a separate, HTML-based conversion path (distinct from the JSON `docs_structure_parser`). It has no checkbox-glyph handling either; it would render any bullet (including a Docs checklist glyph) as a plain `- ` markdown bullet.

**Net: there is zero checkbox/checklist support anywhere in the pull, push, or markdown-parse paths today** — this matches and further specifies the Feasibility Risk already flagged in requirements.md.

## 2. Google Docs API — how checklists are represented (official docs + community)

- `paragraph.bullet` (the `Bullet` object) has exactly three fields: `listId` (string), `nestingLevel` (int), `textStyle`. **No glyph/checked info lives directly on the paragraph.** The glyph for a given `listId` + `nestingLevel` is defined in the document's top-level `lists` map: `document.lists[listId].listProperties.nestingLevels[nestingLevel]`, which carries a `GlyphType` (or `glyphSymbol`/`glyphFormat` for numbered lists).
- **Creating a checklist**: `createParagraphBullets` (a `documents.batchUpdate` request) accepts a `bulletPreset` enum, and that enum does include a checkbox-specific value: **`BULLET_CHECKBOX`** — "A bulleted list with `CHECKBOX` bullet glyphs for all list nesting levels." This is the request docspan's `docs_request_builder.py` would need to emit conditionally (instead of always hardcoding `BULLET_DISC_CIRCLE_SQUARE`) to create/convert a paragraph into a checklist item.
- **There is no dedicated "checklist toggle" request type.** Toggling an item's checked state in the Docs UI is not exposed as a documented, distinct `batchUpdate` request (no `checked` field on `Bullet`, `ListProperties`, or `NestingLevel` in the REST reference).
- **Critical, confirmed limitation — reading checked/unchecked state back is not supported by the API.** Multiple independent sources (Latenode community thread, a tanaikech Apps-Script gist that explicitly tested this) report:
  - When a checkbox glyph is used, `documents.get` returns `glyphType: GLYPH_TYPE_UNSPECIFIED` (Apps Script `DocumentApp` returns `null` for the equivalent) **regardless of checked/unchecked state**.
  - The JSON returned by `documents.get` is reported **identical** whether a checkbox is checked or unchecked — the only observed difference across a check/uncheck action was the document's `revisionId`, which carries no state information.
  - This limitation is symmetric between the REST Docs API and the Apps Script `DocumentApp` service — it isn't an SDK gap, it's the underlying document model not exposing checked state via either API.
  - Community-suggested workarounds are all text-based hacks (e.g. appending a marker character like `✓` next to checked items, or using comments) — none are a supported API feature.
  - UI-observed behavior (not an API contract): checking a box in the Docs UI applies strikethrough text styling to the item as a side effect, which is visible via `textStyle.strikethrough` in `documents.get` — this is the only indirect, unofficial signal that could plausibly correlate with "checked," but it is a UI convention, not a documented or guaranteed API field, and a user could apply/remove strikethrough manually without checking the box (or vice versa via the "checklist without strikethrough" UI option that now exists — see below).
- Google Docs UI (as of 2026) offers a "checklist without strikethrough" formatting option (Format → Bullets & numbering → Checklist), which means even the strikethrough heuristic is not reliable for all checklists in the wild.

**Implication for requirements.md Success Metric #3** ("Checklist state (`- [x]`/`- [ ]`) round-trips correctly through pull and push"): this is very likely **infeasible to verify by reading state back from the Docs API** in the general case. docspan could still *write* checked/unchecked-looking checklists (via `BULLET_CHECKBOX` + optionally applying/removing strikethrough), and could track "did I intend this checked" locally in the markdown round-trip (docspan owns both the pull and push side of a sync cycle), but it cannot ask the live Google Doc "is this box currently checked" if a collaborator toggled it directly in the Docs UI between syncs — the API will not tell you. This should be escalated as a planning-phase decision point / feasibility risk update, not treated as a straightforward bug fix.

## 3. Pinned library versions (`pyproject.toml` / `uv.lock`)

From `pyproject.toml` (lines 36–39):
```
google-auth>=2.23.0
google-auth-oauthlib>=1.1.0
google-auth-httplib2>=0.1.1
google-api-python-client>=2.108.0
```
From `uv.lock` (resolved versions actually installed):
- `google-api-python-client` == **2.197.0**
- `google-auth` == **2.53.0** (Python ≥3.10) / 2.50.0 (Python <3.10)
- `google-auth-httplib2` == **0.4.0** (Python ≥3.10) / 0.3.1 (Python <3.10)
- `mistune` == **3.2.1** (constraint `mistune>=3.0`, pyproject.toml line 48)

**Version constraint has no bearing on checklist support.** `google-api-python-client` is a generic, discovery-document-driven client — it doesn't hardcode per-API request/response shapes, so any reasonably recent version (2.108.0+ easily includes it) already exposes `BULLET_CHECKBOX` and the full `Bullet`/`CreateParagraphBulletsRequest` schema; the checklist read-state gap above is a Docs API/document-model limitation, not a client library version issue, and pinning a newer `google-api-python-client` will not fix it. Similarly, `mistune` 3.2.1 already ships the `task_lists` plugin — it's simply not enabled in `markdown_to_paragraph_parser.py` (§1). Enabling it is a one-line change (`plugins=["task_lists"]`) plus handling the resulting `task_list_item`/`attrs.checked` token shape, which today's `_walk_list_items` does not do.

## Summary of what a fix needs to touch

1. `markdown_to_paragraph_parser.py`: enable `mistune`'s `task_lists` plugin; add `is_checklist_item`/`checked` fields to `DocsParagraphNode`; branch `_walk_list_items` on `task_list_item` tokens.
2. `docs_structure_parser.py`: read `bullet.listId` and cross-reference `document.lists[listId].listProperties.nestingLevels[n].glyphType` to detect `GLYPH_TYPE_UNSPECIFIED`/checkbox-shaped lists on pull (best-effort — see the read-state caveat in §2, this cannot reliably recover *checked* vs *unchecked*, only "this is some kind of checklist").
3. `docs_request_builder.py`: branch `createParagraphBullets` between `BULLET_DISC_CIRCLE_SQUARE` and `BULLET_CHECKBOX` based on the new node field.
4. Accept as a documented, permanent limitation (not a bug to "fix"): docspan cannot detect a collaborator's live check/uncheck action on pull. The safest achievable behavior is "docspan writes checklist glyphs and preserves whatever check-state marker it last wrote, but does not attempt to detect manual UI toggles by other editors between syncs" — this belongs in the feature-gap report (Scope item 3 in requirements.md) if the plan phase doesn't fully close it.

## Sources
- [REST Resource: documents (Bullet, CreateParagraphBulletsRequest, BulletGlyphPreset) — Google for Developers](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents)
- [Requests | Google Docs — Google for Developers](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request)
- [Work with lists | Google Docs — Google for Developers](https://developers.google.com/workspace/docs/api/how-tos/lists)
- [Is there a way to detect checkbox status in Google Docs API? — Latenode Community](https://community.latenode.com/t/is-there-a-way-to-detect-checkbox-status-in-google-docs-api/7820)
- [Retrieving Glyph Value from List Items of Google Document using Google Apps Script — tanaikech gist](https://gist.github.com/tanaikech/5f186b006c4803790318a75e65900c36)
- [Checklists in Google Docs with and without Strikethrough — Control Alt Achieve](https://www.controlaltachieve.com/2022/01/docs-checklists.html)
- [Mistune Upgrade Guide / task_lists plugin — mistune.lepture.com](https://mistune.lepture.com/en/latest/upgrade.html)
- [mistune source, `src/mistune/plugins/task_lists.py` — GitHub](https://github.com/lepture/mistune/blob/main/src/mistune/plugins/task_lists.py)
