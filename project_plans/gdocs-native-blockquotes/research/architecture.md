# Architecture research: gdocs-native-blockquotes

Sources read in full: `docs_request_builder.py` `_node_key`/`_content_key`/`_restyles`/
`_make_style_update_requests`/paragraph-insert path (`_build_insert_requests`, ~L2384-2543);
`docs_structure_parser.py` `DocsParagraphNode` dataclass (L147-188) and `_parse_paragraph`
(L518-604); `markdown_to_paragraph_parser.py` `_walk_block_quote`/`_prefix_node_text`
(L260-399); `nodes_to_markdown.py` `_group_code_runs` and the pull renderer registry
(L260-538); `cli/lint.py`'s blockquote rule; `style_guide.py`'s Google Docs guidance.
No Event-Command-Policy table — this is a data-representation/sync problem (diff-engine
identity classification, request-shape emission, live-doc marker detection), not a
multi-actor business-rules domain.

## 1. `_node_key` / `_content_key`: where `is_blockquote`/`quote_depth` participate

Both methods live in `docs_request_builder.py` (`_node_key` L225-285, `_content_key`
L328-367) and already establish the precedent this change follows exactly: `_node_key`
is full identity (style, `is_list_item`, `nesting_level`, `_is_code_line`, `text`) — "which
live paragraph is this markdown node about?" — while `_content_key` is deliberately
text-only ("identity ignoring everything editable in place") so `_repair` can fold an
unchanged-text, restyled pairing back to `equal`.

- **`_node_key`**: add `node.is_blockquote, node.quote_depth` to the tuple built at
  L278-285. Rationale is the same one the docstring gives for `_is_code_line`
  (L251-259): without it, `SequenceMatcher` could pair a blockquote paragraph with a
  same-text, same-style, non-quote paragraph elsewhere in the document (e.g. a quote
  whose text is edited down to match a stray body paragraph), permanently misclassifying
  which one is "the quote." This directly serves constraint #49 in requirements.md.
- **`_content_key`**: do **not** add either field. `_content_key`'s whole purpose
  (L328-360) is to say "same content, restyle not rewrite" — and per `_restyles`
  (L2644-2666), a restyle is exactly what changing `indentStart`/`borderLeft` alone
  should be: cheap, in-place, `updateParagraphStyle`-only, comment-preserving. Treating
  `quote_depth` as identity-only and absent from `_content_key` means: same text +
  different quote_depth → `_node_key` still distinguishes them by node position via
  `_opcodes`' SequenceMatcher, but *if* `_node_key` places them in the same `equal`/
  paired slot (i.e. `_repair` re-tags a text-identical pair), `_content_key`'s
  text-only comparison lets `_restyles`/`_make_style_update_requests` fire instead of
  delete-and-reinsert. This matches the explicit design directive in
  requirements.md L49 ("participate in identity … but not in restyle-vs-rewrite
  classification, analogous to how `render_prefix` … is already excluded/included per
  the documented precedent").

**Consequence for `_restyles`/`_make_style_update_requests`**: these two currently
compare only `style`/`is_list_item` (L2663-2666, L2702-2722). They must be extended to
also compare `is_blockquote`/`quote_depth` and emit the new `indentStart`/`borderLeft`
fields when those differ — see §3. Without this, a text-unchanged quote-depth edit would
be silently dropped (no request emitted at all), since `_restyles` gates whether
`_make_style_update_requests` runs.

## 2. Minimal insert/restyle code path change: a shared helper

- **Insert path**: `docs_request_builder.py` `_build_insert_requests` (~L2488-2513).
  Today it emits one `updateParagraphStyle` per paragraph with
  `paragraphStyle: {namedStyleType: node.style}`, `fields: "namedStyleType"`.
- **Restyle path**: `_make_style_update_requests` (~L2702-2709), same shape, gated on
  `current_node.style != target_node.style`.

Both sites need to *conditionally* merge in `indentStart` and a full `borderLeft`
object when `node.is_blockquote`, and extend the `fields` mask accordingly. Inlining
this twice risks the two sites drifting (e.g. one remembers to resend the full
`ParagraphBorder` object per the "borders cannot be partially updated" constraint,
the other doesn't). Cleanest shape: a single helper,

```python
def _blockquote_paragraph_style_fields(node: DocsParagraphNode) -> Tuple[dict, List[str]]:
    """Return (paragraphStyle fragment, field names) for node's blockquote styling.
    Empty dict/list when node.is_blockquote is False — callers merge unconditionally.
    """
```

that both call sites merge into their existing `paragraphStyle`/`fields` dict rather
than replacing it — e.g.:

```python
style, extra_fields = self._blockquote_paragraph_style_fields(node)
paragraph_style = {"namedStyleType": node.style, **style}
fields = ",".join(["namedStyleType", *extra_fields]) if extra_fields else "namedStyleType"
```

This is additive to the existing pattern (constraint in requirements.md L34: "extend the
`fields` mask, don't replace the pattern") and satisfies the "full ParagraphBorder must
be resent on every write that touches it" constraint by construction — the helper always
returns the complete border object, never a partial one, so a restyle-only depth change
(§ Rabbit Holes in requirements.md) can't accidentally emit a partial update.

For `_restyles`'s boolean gate (L2663-2666), add
`or current_node.is_blockquote != target_node.is_blockquote or current_node.quote_depth != target_node.quote_depth`
so a depth-only edit is detected as "something to restyle" at all — otherwise
`_make_style_update_requests` is never invoked for that pairing.

## 3. The docspan-owned marker: a shared constant

Requirements (L45, L82) ask for a "distinctive, docspan-owned width/color" the pull
side can recognize, distinct from a border a human applied manually in the Docs UI, and
warn that Docs may coalesce/normalize adjacent borders (needs live-doc verification, not
yet done — flagged as an open question, not resolved by this research pass).

Cleanest approach: one module-level constant, imported by both sides, e.g. in
`docs_request_builder.py` (or a small shared module both `docs_request_builder.py` and
`docs_structure_parser.py` already import, if one exists — needs a check at planning
time; `docs_structure_parser.py` currently has no import from `docs_request_builder.py`
so introducing one either direction, or a new tiny `docs_paragraph_style.py`, is a
planning decision):

```python
# The exact color/width Docs assigns to a docspan-authored blockquote border.
# Chosen to be distinctive enough that pull can treat its presence as
# "docspan put this here" rather than "the user manually bordered this
# paragraph" — must not collide with Docs' own default border styling.
BLOCKQUOTE_BORDER_MARKER = {
    "color": {"color": {"rgbColor": {...}}},  # exact value: TBD, needs live-doc spike
    "width": {"magnitude": ..., "unit": "PT"},
    "dashStyle": "SOLID",
    "padding": {"magnitude": ..., "unit": "PT"},
}
BLOCKQUOTE_INDENT_PT_PER_LEVEL = ...  # TBD
```

Push side (`_blockquote_paragraph_style_fields`) uses it to build the `borderLeft`
value. Pull side (`docs_structure_parser.py`'s `_parse_paragraph`, ~L593-604) reads
`paragraph_style.get("borderLeft")` and compares against this same constant (color+width
at minimum; dashStyle/padding are lower-signal) to decide `is_blockquote=True` and
derives `quote_depth` from `indentStart` via the same `BLOCKQUOTE_INDENT_PT_PER_LEVEL`
divisor. A single shared constant is what prevents the two sides drifting — the
alternative (each side hardcoding its own copy of the color/width) is exactly the kind
of magic-number duplication requirements.md's "Rabbit Holes" section is worried about.
Legacy documents (literal `> ` text, no border) simply never match this constant, so
`is_blockquote` stays `False` and the existing literal-text round-trip is untouched —
satisfying the "must not force a migration on read" constraint (L48).

## 4. Full push/pull data-flow change list, in order

**Push (markdown → Docs)**
1. `markdown_to_paragraph_parser.py` `_walk_block_quote` (L363-399): stop calling
   `_prefix_node_text` (which bakes `"> "` into `.text`); instead set
   `is_blockquote=True, quote_depth=quote_depth` on each produced `DocsParagraphNode`,
   leaving `.text` as the bare content. `_prefix_node_text` (L345-360) becomes unused
   for the blockquote call sites (paragraph/list/code-block branches at L373-394) —
   likely deletable if nothing else calls it; confirm at planning time.
2. `docs_structure_parser.py` `DocsParagraphNode` dataclass (L147-188): add
   `is_blockquote: bool = False`, `quote_depth: int = 0` fields, each documented with
   the same "NOT part of the diff key" / "part of `_node_key`, not `_content_key`"
   style comment this file already uses for `is_native_checkbox`/`heading_id`.
3. `docs_request_builder.py` `_node_key` (L278-285): add the two fields to the
   identity tuple (§1).
4. `docs_request_builder.py`: new helper `_blockquote_paragraph_style_fields` (§2),
   plus a shared marker constant (§3, exact module TBD).
5. `docs_request_builder.py` `_build_insert_requests` (~L2508-2513): merge helper's
   output into the existing `updateParagraphStyle` request.
6. `docs_request_builder.py` `_restyles` (L2663-2666) and
   `_make_style_update_requests` (L2702-2722): compare/emit the two new fields (§1-2).
7. `cli/lint.py` `find_blockquote_issues`: narrow or remove now that `>` renders
   correctly on `google_docs` — requirements.md leaves "delete vs narrow" as an open
   question (kept for future backends without native support).
8. `style_guide.py` `GOOGLE_DOCS_STYLE_GUIDE` (L16-19): remove/update the "don't use
   `>`" bullet.

**Pull (Docs → markdown)**
9. `docs_structure_parser.py` `_parse_paragraph` (L518-604): read
   `paragraph_style.get("indentStart")` / `.get("borderLeft")`, compare against the
   shared marker constant, and populate `is_blockquote`/`quote_depth` on the returned
   `DocsParagraphNode` (mirrors how `render_prefix`/`is_native_checkbox` are already
   resolved live in this method, L583/L591). Legacy literal-`> `-text paragraphs have
   no matching border, so this returns `is_blockquote=False` for them unchanged.
10. `nodes_to_markdown.py`: new `_group_blockquote_runs`, modeled on `_group_code_runs`
    (L339-399) — partitions consecutive `is_blockquote` nodes (grouped further by
    contiguous equal `quote_depth`, since depth can change line-to-line only at a
    nesting boundary) into `("blockquote", quote_depth, inner_nodes)` runs. Needs to
    compose with `_group_code_runs` per the Rabbit Holes note (L63, L73): a fenced
    code block inside a quote is a blockquote-run containing pure-code-line nodes,
    so the two groupers must nest (run `_group_code_runs`-shaped detection *inside*
    a blockquote run's inner nodes, or vice versa) rather than being applied as two
    independent flat passes over the same node list. This is flagged as the highest
    open design risk in requirements.md (L73, L83) and is not resolved by this
    research pass — needs a concrete nesting design before Phase 3 planning closes.
11. `nodes_to_markdown.py`: new `BlockquoteNodeRenderer`/inline render function
    registered under a `"blockquote"` dispatch key (mirrors `HeadingNodeRenderer` etc.,
    L459-515) that reconstructs `"> " * quote_depth` prefixes per line, recursing
    into the same per-node rendering `render_nodes_to_markdown` already uses for
    prose/list/code content so nested list-in-quote and code-in-quote reuse existing
    renderers rather than duplicating them.
12. `render_nodes_to_markdown` (L521-538): wire the new grouping stage into the loop
    (today only `_group_code_runs` is consulted at L524).
13. `docs/backends/google-docs.md`: update the Limitations section to describe native
    blockquote rendering, the legacy-literal-text fallback, and the one-time
    comment-loss migration cost on first push of an already-pushed quote (Risk
    Control section of requirements.md, L79).
14. New ADR (`ADR-00N`, following `ADR-001`/`ADR-003` precedent) documenting the
    design and the migration/comment-loss tradeoff.

## 5. Open items this research does not resolve (carry into planning/Phase 3)

- Exact `borderLeft` color/width/dashStyle/padding values, and empirical
  verification that Docs does not coalesce/normalize the marker border against an
  adjacent paragraph's — requirements.md flags this as needing a live-Doc spike, not
  answered by reading code or docs (L61, L82).
- Exact nesting composition of `_group_blockquote_runs` and `_group_code_runs` for a
  code-block-inside-quote (§4 item 10) — needs `_group_code_runs`'s full
  implementation (read in this pass, L339-399) plus a concrete design sketch before
  Phase 3 can finalize the pull-side approach (requirements.md L73, L83).
- Whether quote depth + list nesting level should stack additively in
  `indentStart`/tab-based list indent, or use independent mechanisms — the two indent
  sources currently live in different systems (`indentStart` for the new blockquote
  field vs. leading-tab-derived `nesting_level` for lists, per `_restyles`'s comment
  at L2657-2662 that list nesting is deliberately *not* an attribute-driven restyle).
- Whether `_prefix_node_text` becomes fully dead code once `_walk_block_quote` stops
  calling it, or is still used elsewhere (needs a repo-wide grep at implementation
  time before deleting).
