# Stack research: gdocs-native-blockquotes

## 1. Google Docs API v1 `ParagraphStyle` schema

Source: https://developers.google.com/docs/api/reference/rest/v1/documents (`ParagraphStyle`, `ParagraphBorder`, `Dimension`, `DashStyle`) and https://developers.google.com/workspace/docs/api/how-tos/format-text (worked example).

### `indentStart`
- Type: `Dimension` — `{ magnitude: number, unit: Unit }`.
- `Unit` enum: `UNIT_UNSPECIFIED`, `PT` (1/72 inch). `PT` is the only real unit — same enum used by `spaceAbove`/`spaceBelow`/`indentFirstLine`/`indentEnd`, all of which this codebase does not currently touch.
- No documented min/max magnitude; Docs UI ordinarily works in increments of 0.5in (36pt) per indent level, but the API itself imposes no explicit range — treat "arbitrary depth" as bounded only by what still renders sanely in the Docs UI.

### `borderLeft` / `borderBetween` / `borderTop` / `borderBottom` (`ParagraphBorder`)
Fields (all nullable/optional in the generated client, per the Java model docs — none marked required):
- `color`: `OptionalColor` (`{ color: { rgbColor: {red, green, blue} } }`, floats 0–1)
- `width`: `Dimension` (`PT`)
- `padding`: `Dimension` (`PT`)
- `dashStyle`: `DashStyle` enum — `DASH_STYLE_UNSPECIFIED` (docs list this as unsupported/default fallback), `SOLID`, `DOT`, `DASH` (values shared with the Slides/Sheets line-dash type per ECMA-376 `ST_PresetLineDashVal`).

**Confirmed quirk (matches requirements.md's stated constraint)**: official docs state plainly, per-field, for every border field: *"Paragraph borders cannot be partially updated. When changing a paragraph border, the new border must be specified in its entirety."* Any `updateParagraphStyle` write that touches a border field must resend `color`+`width`+`padding`+`dashStyle` together, every time — a restyle-only depth change must still resend the whole `ParagraphBorder`, not a delta. This directly confirms the Rabbit Hole item in requirements.md about restyle paths.

**New quirk discovered (not yet in requirements.md — important for the border-coalescing rabbit hole)**: the four border fields have materially different rendering semantics, not just different get/set mechanics:
- `borderBetween`: "rendered when the adjacent paragraph has the same border and indent properties" — i.e., Docs only draws a border *between* two paragraphs when their border+indent styles match; mismatched neighbors suppress it.
- `borderTop`/`borderBottom`: rendered only when the adjacent paragraph (above/below respectively) has *different* border and indent properties from this one.
- `borderLeft`/`borderRight`: no such adjacency-dependent suppression documented — they render unconditionally once set, independent of neighboring paragraphs.

This means **`borderLeft` alone is the safe choice for a docspan-owned marker** — it isn't subject to the "coalesce with neighbor" ambiguity the requirements flagged as a rabbit hole. Using `borderTop`/`borderBottom`/`borderBetween` instead would require the marker's visual behavior to depend on what's adjacent, which is exactly the kind of variability the requirements' rabbit-hole note worried about. Still needs the requirements' own empirical spike against a live Doc to confirm multi-paragraph blockquote runs (consecutive quote paragraphs) with identical `borderLeft`+`indentStart` don't get visually merged into one continuous strip in a way that breaks per-paragraph marker *detection* on pull — detection reads each paragraph's own `paragraphStyle.borderLeft`/`indentStart` values via `documents.get`, which are independent of what's rendered visually, so detection itself should be unaffected by any visual merging; this is a rendering-only concern, not a data-model one.

### `namedStyleType` interaction note
The `ParagraphStyle` docs state `namedStyleType` "is applied before the other properties are updated" within the same `updateParagraphStyle` call, since it can reset/affect other fields in the style object. Existing code sets `namedStyleType` and nothing else — new code adding `indentStart`+`borderLeft` to the *same* request must list all three in the `fields` mask (`"namedStyleType,indentStart,borderLeft"`) since `namedStyleType`'s reset-then-apply-others ordering is per-request, and Docs applies fields in the order given by the request's own internal spec — no evidence this ordering breaks the border/indent values as long as they're in the same `fields` mask string (comma-separated, per Google's FieldMask convention used elsewhere already, e.g. `"namedStyleType"` singular today).

## 2. Existing `updateParagraphStyle` call sites in this codebase

File: `src/docspan/backends/google_docs/docs_request_builder.py`. Confirmed 4 call sites, all `"fields": "namedStyleType"` only, `"paragraphStyle": {"namedStyleType": ...}`:

1. Line 1420–1423 — inside a comment/context block illustrating a paragraph-normalize-before-delete pattern.
2. Line 2364–2367 — same pattern, a different code path (delete/trim context).
3. Line 2508–2513 — **paragraph-insert** path: sets `namedStyleType` right after `insertText`, over the just-inserted paragraph's `paragraph_range` (`startIndex`/`endIndex`). This is the site the requirements call "~line 2508" for the push-side blockquote fields.
4. Line 2704–2709 — **restyle** path (`current_node.style != target_node.style` guard): only emits `updateParagraphStyle` when the named style actually differs. This is "~line 2704" from requirements.

All 4 sites use the identical shape: `{"updateParagraphStyle": {"range": {...}, "paragraphStyle": {...}, "fields": "..."}}`. Extending these means adding `indentStart`/`borderLeft` keys to `paragraphStyle` and extending the `fields` string (comma-joined) — the existing pattern already generalizes to a multi-field mask with no structural change needed. Note the restyle site (#4) is currently gated by `current_node.style != target_node.style`; a change that only affects `is_blockquote`/`quote_depth` (not `style`) needs its own gate added, or it will never fire the border/indent update on a depth-only edit — this is exactly the "restyle-only change" rabbit hole in requirements.md line 60.

## 3. `_node_key`/`_content_key` precedent

`src/docspan/backends/google_docs/docs_request_builder.py` — `_node_key` (~line 225) and `_content_key` (~line 328). Confirmed existing precedent the requirements point to:
- `_node_key` includes `render_prefix`-derived signal (`bool(node.render_prefix)`, ~line 301) — used to separate a code line's identity from prose so `difflib.SequenceMatcher` doesn't pair them, even though `_content_key` stays text-only so an unchanged code line can still fold back to `equal`.
- `_content_key` deliberately excludes `render_prefix` (~line 336–340) to avoid spuriously flagging an unchanged code paragraph as a rewrite.
- This is the exact analogy requirements.md cites for `is_blockquote`/`quote_depth`: put them in `_node_key` (affects pairing/identity) but not `_content_key` (doesn't force a rewrite classification for genuinely-unchanged blockquote paragraphs).

## 4. Pinned client library version

- `pyproject.toml:42` and `requirements.txt:4`: `google-api-python-client==2.108.0` (pyproject uses `>=2.108.0`).
- `google-api-python-client` is a **dynamically-generated, untyped** client — request/response bodies are plain Python `dict`s built against the discovery document, not generated dataclasses/TypedDicts. Confirmed no static `ParagraphStyle`/`ParagraphBorder`/`Dimension` Python types exist in this dependency; `indentStart`/`borderLeft` are usable today purely as dict keys, exactly like the existing `namedStyleType` dict key already is. No library upgrade is needed to use these fields — they're server-side schema, not client-generated bindings, so any client version that can send arbitrary `updateParagraphStyle` dicts (all of them) supports this.

## 5. Known pitfalls (community/GitHub/StackOverflow)

- No GitHub issue found specifically titled around "border coalescing" for Docs paragraphs; the closest documented behavior is Google's own text (section 1 above) describing `borderBetween`/`borderTop`/`borderBottom` as conditionally rendered based on adjacent-paragraph equality — this is Google's *documented design*, not a bug report, and using `borderLeft` sidesteps it entirely (see recommendation above).
- Google's docs separately document a related but distinct coalescing behavior for **table cell borders** (shared borders between adjacent cells get updated together, applied in a fixed right→left→bottom→top order, invisible merged borders skipped) — this does not apply to paragraph borders and should not be conflated with them; found only because it surfaces in the same search results and is worth explicitly ruling out as irrelevant.
- The full-resend requirement for borders is stated identically across every language client's generated docs (Java, .NET, Ruby, Node, Go), confirming it's a server-API-level constraint, not an artifact of one client library's bindings — so any future Python client version/library swap doesn't change this constraint.

## Sources
- https://developers.google.com/docs/api/reference/rest/v1/documents (ParagraphStyle, ParagraphBorder, Dimension, DashStyle sections)
- https://developers.google.com/workspace/docs/api/how-tos/format-text (worked `borderLeft` JSON example)
- https://googleapis.dev/java/google-api-services-docs/latest/com/google/api/services/docs/v1/model/ParagraphBorder.html
- `src/docspan/backends/google_docs/docs_request_builder.py` (lines 1420-1423, 2364-2367, 2508-2513, 2704-2709, 225-350)
- `pyproject.toml:42`, `requirements.txt:4`
