# Architecture Research: checklist round-trip, comment-anchor preservation, personal workflow

Research Agent 3 — SDD Phase 2, project `wedding-planning-workflow`.

## 1. Actual data flow (read from code, not assumed)

**The pull path and the push path are architecturally asymmetric — they do not share a
parser.** This is the single most important finding and it changes where checklist work
must land.

### Pull: Docs → Markdown (HTML-export based, never touches Docs-API JSON)

`GoogleDocsBackend.pull()` (`src/docspan/backends/google_docs/backend.py:76-87`):

```
client.get_doc_content(doc_id)          # Drive API files.export_media(mimeType='text/html')
  → client.py:153-174
DocumentConverter.html_to_markdown(html) # converter.py:26-83
  ├─ _reconstruct_nested_lists(html)     # converter.py:86-395, regex/HTMLParser heuristic
  │    keyed on Google's `lst-kix_*-N` class names + `margin-left` pt values
  └─ markdownify.markdownify(html, ...)  # converter.py:57-63
write markdown to local_path
```

`DocsStructureParser` and `DocsParagraphNode` are **not used anywhere in the pull path**.
Pull never calls `documents().get()` — it only calls Drive's HTML export. So none of the
Docs-API structural fields (`paragraph.bullet`, `document.lists[listId]`, `glyphType`,
`start_index`/`end_index`) are available during pull; everything pull knows comes from
whatever `markdownify` + the nested-list regex heuristic can infer from the exported HTML.

### Push: Markdown → Docs (structural diff over Docs-API JSON)

`GoogleDocsBackend.push()` (`backend.py:49-74`):

```
target_nodes = MarkdownToParagraphParser().parse(md_content)     # markdown_to_paragraph_parser.py:78-145
                                                                    (mistune AST → List[DocsParagraphNode])
doc = client.get_document(doc_id)                                 # client.py:119-131, documents().get()
current_nodes = DocsStructureParser().parse(doc)                  # docs_structure_parser.py:32-66
requests = DocsRequestBuilder().build(current_nodes, target_nodes, doc_end_index)
                                                                    # docs_request_builder.py:22-87
client.batch_update(doc_id, requests)                              # client.py:133-151, documents().batchUpdate()
```

Key structures:

- `TextSpan` (`docs_structure_parser.py:8-14`): `text, bold, italic, link, monospace`.
- `DocsParagraphNode` (`docs_structure_parser.py:17-26`): `style, text, is_list_item,
  nesting_level, start_index, end_index, spans`. **No field for checklist/checked state,
  `listId`, or `glyphType` exists today.**
- `DocsRequestBuilder._text_key()` (`docs_request_builder.py:18-20`): the diff key is
  literally `(node.style, node.text, node.is_list_item)` — this is what `difflib.SequenceMatcher`
  compares (`docs_request_builder.py:40-44`) to decide equal/replace/insert/delete per
  paragraph.
- Request emission: `_make_insert_requests` (`docs_request_builder.py:115-155`) emits
  `insertText` + `updateParagraphStyle` + (if `is_list_item`) `createParagraphBullets`
  hardcoded to `"bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"` (line 152) — **every list
  item pushed today gets a plain disc/circle/square bullet; there is no branch for any
  other preset.**
- `_make_style_update_requests` (`docs_request_builder.py:157-172`) only fires
  `updateParagraphStyle` when `style` differs on an "equal" (text-matched) opcode — it
  never touches text/character ranges.

`DocsStructureParser.parse()` (`docs_structure_parser.py:32-66`) silently skips any
structural element that isn't `paragraph` — `table`, `sectionBreak`, `tableOfContents` are
dropped with a comment, not an error (line 64). Confirmed by direct read, matching the
Feasibility Risk already identified in `requirements.md`.

### Consequence of the asymmetry

Because pull is HTML/markdownify-based and push is Docs-API-JSON/diff-based, any
checklist fix must be designed **twice** — once for what `markdownify` produces from
Google's checkbox-list HTML export (pull), and once for `DocsStructureParser` +
`DocsRequestBuilder` (push). There is no single shared code path to patch.

## 2. Where checklist support needs to be added

**Open question from requirements.md is still open** — nothing in this repo (no test
fixture, no HTML sample, no prior ADR) records what a Google Docs native checklist
actually looks like in either the HTML export or the `documents().get()` JSON. Searched
`tests/test_docs_structure_parser.py` and `tests/test_docs_request_builder.py`
(27 test functions total, listed by `grep -n "^def test"`) — none exercise checkboxes.
**This must be verified against a real doc/API response before implementation**, exactly
as requirements.md's Open Questions section flags. The plan phase should budget a
throwaway script that calls `documents().get()` on a doc containing one native checkbox
list item and dumps the JSON, plus one that calls the HTML export on the same doc, before
committing to a design.

Given that constraint, here are the precise integration points the fix will touch,
regardless of the exact API shape once verified:

**Pull side (HTML → markdown), in `converter.py`:**
- `DocumentConverter.html_to_markdown()` (`converter.py:26-83`) and specifically
  `_clean_google_docs_artifacts` / the `markdownify(...)` call (`converter.py:57-63`) —
  `markdownify` has no built-in Google-Docs-checkbox-to-`- [ ]` mapping; Google's exported
  HTML for a checked/unchecked list item needs to be pre-processed (similar in spirit to
  the existing `_reconstruct_nested_lists` heuristic at `converter.py:86-395`) into
  `<input type="checkbox">`-style markup that `markdownify` *does* understand (it emits
  GFM `- [ ]`/`- [x]` for real `<input type=checkbox>` elements), or a new dedicated regex
  pass must directly emit `- [ ]`/`- [x]` before/instead of the generic bullet handling.
  This is the natural home for the fix; it sits next to the existing nested-list
  heuristic the Rabbit Holes section already flags as fragile.

**Push side (markdown → Docs), three coordinated changes:**
- `DocsParagraphNode` (`docs_structure_parser.py:17-26`): add `checked: Optional[bool] =
  None`.
- `DocsStructureParser._parse_paragraph()` (`docs_structure_parser.py:68-120`), bullet
  handling at lines 107-110 (`bullet = paragraph.get("bullet"); is_list_item = bullet is
  not None; nesting_level = ...`): needs to resolve the bullet's `listId` against the
  document's top-level `lists` map (`doc["lists"][listId].listProperties.nestingLevels[
  nesting_level].glyphType`, per the requirements' hypothesis) to populate `checked`.
  This requires threading `doc["lists"]` into `_parse_paragraph` — currently
  `DocsStructureParser.parse()` (lines 32-66) never reads `doc["lists"]` at all, only
  `doc["tabs"]`/`doc["body"]`.
- `MarkdownToParagraphParser` (`markdown_to_paragraph_parser.py:78-145`): `_walk_list_items`
  (lines 24-61) has zero checkbox detection today — markdown text like `- [x] Foo` is
  currently swallowed as literal node text `"[x] Foo"`, `is_list_item=True`, with no
  `checked` field set. Mistune's default AST (`mistune.create_markdown(renderer=None)`,
  line 91) does not parse GFM task-list syntax without the `task_lists` plugin explicitly
  enabled — that's a one-line change (`plugins=["task_lists"]`) plus token handling for
  the resulting `task_list_item` / `checked` attrs mistune emits.
- `DocsRequestBuilder._text_key()` (`docs_request_builder.py:18-20`) must fold `checked`
  into the comparison key so a pure checkbox toggle is detected as a paragraph difference
  at all — see Section 3 for why this is exactly the dangerous part.
- `DocsRequestBuilder._make_insert_requests()` (`docs_request_builder.py:115-155`, the
  hardcoded `bulletPreset: "BULLET_DISC_CIRCLE_SQUARE"` at line 152) needs a conditional
  branch to use a checkbox preset (hypothesis: `BULLET_CHECKBOX`, needs API verification)
  when `node.checked is not None`.

## 3. Comment-anchor preservation — traced precisely

`README.md:211` already documents the failure mode plainly: *"Google Docs: comments on
edited paragraphs are lost on push (paragraph-level structural diff; comments on
unchanged paragraphs are preserved)"*. Confirmed by reading the actual diff logic:

`DocsRequestBuilder.build()` (`docs_request_builder.py:22-87`) runs
`difflib.SequenceMatcher(None, current_keys, target_keys, autojunk=False)` where each key
is `(style, text, is_list_item)` per paragraph (lines 18-20, 40-41). Per opcode:

- **`equal`** (`docs_request_builder.py:49-54`): paragraph's `(style, text, is_list_item)`
  tuple matched exactly → only `_make_style_update_requests` runs, which emits
  `updateParagraphStyle` *only if `style` differs* (lines 157-172) — **no
  `deleteContentRange`/`insertText` is ever emitted for an equal-opcode paragraph.** The
  underlying character range (and therefore any comment anchored inside it) is never
  touched. This is why unchanged paragraphs keep their comments.
- **`replace`/`delete`** (`docs_request_builder.py:56-61, 72-80`): emits
  `deleteContentRange` covering the paragraph's `[start_index, end_index)` (clamped to
  protect the terminal newline, lines 93-113), then `insertText` for the replacement.
  Google Docs' own behavior (outside docspan's control) detaches/drops comments anchored
  inside a deleted range.

**Granularity is per-whole-paragraph, not per-character-range.** The diff key is the
*entire* paragraph text as one string — there is no sub-paragraph diffing. This directly
confirms the risk called out in requirements.md's Rabbit Holes section: **once checkbox
state is folded into either `node.text` (e.g. `"- [ ] Foo"` → `"- [x] Foo"`) or into the
`_text_key` tuple as a separate `checked` field, a checklist-toggle-only edit will make
`(style, text_or_checked, is_list_item)` differ, so `SequenceMatcher` classifies that
paragraph as `replace`, not `equal` — triggering delete+insert on a paragraph that may
carry a live collaborator comment, exactly the scenario in this doc (it already has two
comments anchored mid-paragraph per requirements.md line 42).**

Concretely: naively adding `checked` support the way the rest of the request builder
works today (fold it into `_text_key`, let `replace` handle it) will **regress the
"comments survive push" property for every checklist item a comment happens to be
anchored to**. To avoid that, the push architecture needs a new opcode path: detect
"same `style`, same `text` stripped of any checkbox marker, same `is_list_item`, only
`checked` differs" as its own case, and emit a request that toggles the bullet/checkbox
without deleting the paragraph's text range — assuming the real Docs API exposes a
non-destructive way to do that (unverified — see Section 2's note on required API
verification). **If no non-destructive primitive exists, this becomes a hard
architectural constraint to document as a feature gap, not something to silently accept**
(a checked-status toggle on a commented paragraph would still drop the comment).

Additionally: `DocsStructureParser.parse()` skipping `table`/`sectionBreak`/
`tableOfContents` (`docs_structure_parser.py:59-64`) means any comment anchored inside
one of those skipped elements is entirely invisible to the diff — not protected, not
reasoned about, just absent from `current_nodes`. `doc_end_index` for delete-range
clamping is still computed correctly from `body_content[-1]["endIndex"]`
(`backend.py:64`), so index math stays safe, but content/comments inside a skipped
element are simply never considered when deciding what's "unchanged."

## 4. Personal workflow: pull → summarize → edit → pull-again → dry-run → push

### Does a second pull risk clobbering Tyler's local edits?

No — `orchestrate_pull()` (`src/docspan/core/orchestrator.py:181-234`) already implements
a safe branch structure keyed on `remote_changed` (comparing `backend.get_remote_version()`
— the Docs `revisionId`, `backend.py:89-94 `— against `state.entry.remote_version`) and
`local_changed` (comparing `sha256_of_content(local_content)` against
`state.entry.local_hash`):

| remote_changed | local_changed | action | effect on Tyler's edits |
|---|---|---|---|
| no | no | `up-to-date` | no-op |
| yes | no | `_fast_forward_pull` (`orchestrator.py:257-274`) | safe, nothing local to lose |
| no | yes | `local-only` (`orchestrator.py:227-228`) | **pull is skipped entirely**, CLI prints a warning (`cli/main.py:174-178`) — local edits never touched |
| yes | yes | `_merge_pull` (`orchestrator.py:277-332`) | three-way merge, see below |

`_merge_pull` backs up the current local content to `{local}.orig` (`ORIG_SUFFIX`,
`orchestrator.py:287-289`), pulls remote into a temp file, reads the merge base from the
content-addressed `.markgate-base/<sha256>.base` store (`get_base_content`,
`orchestrator.py:50-56`, per ADR-002), then calls `three_way_merge(base, theirs=remote,
ours=local)` (`merge.py:14-30`) — `merge3.Merge3.merge_lines()` with
git-style `<<<<<<< ours` / `=======` / `>>>>>>> theirs` markers (`merge.py:21-27`, per
ADR-001). Conflicting lines are written into the local file as literal conflict markers,
not silently resolved either way.

**Answer: yes, the existing pull already does a safe merge (or skips) against local
uncommitted edits.** A second `docspan pull` immediately before push, as the workflow
specifies, will not blindly overwrite Tyler's in-progress edits — worst case is inline
conflict markers he must resolve by hand.

### Two gaps this workflow specifically exposes

1. **`docspan push --dry-run` does not compute or display a diff.** Read
   `cli/main.py:107-111` (push) and `163-167` (pull): when `dry_run` is set, the CLI
   prints a single static line (`"dry-run {local} → [{backend}] {remote_id}"`) and
   `continue`s — it never calls `MarkdownToParagraphParser`, `DocsStructureParser`, or
   `DocsRequestBuilder` to build the actual batchUpdate request list. `--dry-run` today
   is a no-op stub, not a preview. Requirements.md's Scope (line 48) and Observability
   Requirements (line 73) both depend on "review diff (`docspan push --dry-run`)" as the
   safety gate before every push — **this needs to be built**, not just relied upon,
   before the workflow's stated risk control ("always review `docspan push --dry-run`
   output before the real push") is actually true.
2. **The three-way merge is pure markdown-line diffing, blind to Docs/checklist
   semantics.** `three_way_merge` (`merge.py:14-30`) operates on `str.splitlines()` with
   no knowledge of paragraphs, styles, or checkbox state — it's a correct, appropriate
   layer for reconciling markdown text, but it runs *before* any of the Section 2/3
   checklist-aware push logic ever sees the paragraphs. A merge conflict on a checklist
   line surfaces as ordinary `<<<<<<<`/`=======`/`>>>>>>>` markers Tyler resolves by hand
   in the markdown file; only after that manual resolution does the paragraph reach
   `DocsRequestBuilder`. This is fine as a design (matches ADR-001/002 and the
   "reuse it, don't replace it" constraint) but should be stated explicitly so the
   workflow doc doesn't assume merge conflicts are "aware" of checklist toggles.

`state.py`'s `SyncState`/`MappingState` (`state.py:11-53`, atomic save via temp-file +
`os.rename` at lines 34-38) needs no changes for this workflow — tracking
`doc_id, backend, last_synced_at, base_hash, remote_version, local_hash` per local path is
already sufficient plumbing; no new state fields are implied by checklist or comment work.

## Summary of concrete integration points

- `docspan/backends/google_docs/docs_structure_parser.py` — add `checked` field to
  `DocsParagraphNode`; extend `_parse_paragraph` to resolve `listId` → `glyphType` (needs
  live-API verification first); thread `doc["lists"]` through `parse()`.
- `docspan/backends/google_docs/converter.py` — new checkbox-HTML pre-processing pass
  (pull side), separate from and in addition to the existing `_reconstruct_nested_lists`.
- `docspan/backends/google_docs/markdown_to_paragraph_parser.py` — enable mistune's
  `task_lists` plugin; populate `checked` from `task_list_item` tokens.
- `docspan/backends/google_docs/docs_request_builder.py` — fold `checked` into diffing
  without regressing comment preservation on `equal`-classified paragraphs (needs a new
  "checked-only" opcode path, not a straight fold into `_text_key`); replace the hardcoded
  `BULLET_DISC_CIRCLE_SQUARE` preset with a conditional checkbox preset.
- `docspan/cli/main.py` — `--dry-run` for `push` needs to actually build and print the
  `DocsRequestBuilder` diff to satisfy the workflow's stated safety gate; today it's a stub.

## Risks / open items to carry into planning

1. **Google Docs checklist JSON/HTML shape is unverified** — no fixture, no prior ADR, no
   test covers it. Must be checked against the live API/export before design is finalized
   (requirements.md's own Open Questions already flag this).
2. **Comment-preservation-vs-checkbox-toggle is a real conflict in the current
   architecture**, not a hypothetical — naively implementing checklist diffing the same
   way style updates work today will make checkbox toggles delete+reinsert the paragraph,
   dropping any comment on it. This is the single highest-risk design decision for
   Section "In Scope" item 1 of requirements.md.
3. **`--dry-run` must be implemented for real**, or the workflow's "always review dry-run
   before push" risk control is currently false — it's a silent stub today.
4. Skipped structural elements (`table`, `sectionBreak`, `tableOfContents` in
   `docs_structure_parser.py:64`) mean comments/content inside them are invisible to the
   push diff entirely — flag as feature-gap, matches requirements.md's own risk note about
   the doc's TOC.
