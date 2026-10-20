# Architecture Research: gdocs-sectioned-sync

## Prior research read

`project_plans/markgate-sync/research/architecture.md` covers sync-state JSON files, three-way
line-based merge (`merge3`), and `get_remote_version()` versioning (revisionId vs Confluence
version number). It does **not** cover node-tree structure, mapping representation, or
multi-file-to-one-document sync — that document is about *conflict detection between one local
file and one remote doc*, not about splitting a doc into N files. It's tangentially relevant only
in one place: the manifest this feature needs (§3 below) is a similar "identity + order" bookkeeping
problem to `MappingState`, and the same atomic-write-via-tempfile pattern
(`config.py:126-167`'s `save_config`, using `tempfile.mkstemp` + `os.replace`) is the right template
for writing the sectioned manifest. Otherwise this is new territory, as requirements.md's
"Feasibility Risks" section already flags.

## 1. Push/pull structural mechanics — can push accept N node-trees instead of 1?

Read `src/docspan/backends/google_docs/backend.py` in full for `pull()` (852-960) and
`push()`/`_build_push_plan()` (172-299, 377-...).

**Pull has two genuinely different code paths, and sectioned mode can only use one of them:**

- **Default path** (`tab_id is None`, lines 910-950): fetches via `self._client.get_doc_content(doc_id)`
  — Drive's HTML export — then `DocumentConverter().html_to_markdown()`. This cannot be scoped to a
  heading range; it always returns the whole document as one HTML blob. `DocsStructureParser` is only
  used here afterward, throwaway, to build a heading-id→slug map for anchor upgrading (line 923) — its
  parsed nodes are never what gets written to disk.
- **Structural path** (`tab_id is not None`, lines 874-908): fetches via `get_document()` +
  `resolve_document_tab()` + `DocsStructureParser().parse()`, projects with `project()`, and renders
  with `render_nodes_to_markdown()`. The parsed node list **is** the file that gets written.

This confirms requirements.md's feasibility risk directly: sectioned pull must always go through the
structural path, because only there does docspan hold an addressable, in-memory node sequence it can
slice by heading before rendering. The `tab_id` parameter itself is irrelevant to sectioning — what
matters is reusing the "structural fetch → parse → project → render" pipeline this branch already
runs, just adding a slicing step between `project()` and `render_nodes_to_markdown()`.

**Push is diff-based against one document snapshot, not a tree replace — this is the higher-risk half.**

`_build_push_plan()` (172-299):
1. Reads `local_path` as one file, parses it whole via `MarkdownToParagraphParser().parse(content)` → `target_nodes` (one flat list).
2. Fetches the live doc once (`get_document`), parses it whole via `DocsStructureParser().parse(doc)` → `current_nodes` (one flat list).
3. Projects both sides through `project()` (backend.py:243, 259) so the diff only sees what markdown can represent.
4. `DocsRequestBuilder().build(current_nodes, target_nodes, doc_end_index, ...)` computes a **positional opcode diff** (see `_opcodes` — an edit-script/SequenceMatcher-style diff, not a full-document rewrite) between the two flat node lists and emits targeted `batchUpdate` requests (insert/delete/replace ranges) gated by `required_revision_id`.
5. A second pass (`build_second_pass_requests`, lines 473-572) re-aligns by content to add inline styling/links/tables that need real post-insert indices.

**Key finding**: `current_nodes` and `target_nodes` are each a single flat `List[Node]` for the *whole
document*. `DocsRequestBuilder.build()` has no concept of "section" — it diffs two full-document node
sequences directly against each other using index-anchored requests keyed to the live document's
current indices (via `doc_end_index`, `required_revision_id`).

**Can it be adapted to accept a sequence of node-trees, or does sectioned push need a different code
path?** It can be adapted *without* a second diff engine, but not by literally passing N node-trees
into `build()` separately (each call would compute index ranges against a stale/already-mutated
document view once request 1 lands). The reuse point is upstream of `build()`:

- Sectioned push's job is to **reassemble N per-section node lists into one flat `target_nodes` list**
  (concatenate them, in manifest order) and then hand that single flat list to the *exact same*
  `_build_push_plan()` → `DocsRequestBuilder.build()` → `batch_update` machinery push() already uses.
  From `DocsRequestBuilder`'s point of view, a sectioned push is indistinguishable from a monolithic
  push of a longer document — the diff, the two-pass batchUpdate, the `required_revision_id` gating,
  and the high-risk/comment-backstop checks (422-433, 610-672) all keep working unmodified.
- The genuinely new code is (a) reading N files instead of 1 and concatenating them into one markdown
  string (or one node list) before `MarkdownToParagraphParser().parse()`, and (b) using the manifest
  (see §3) to detect section insert/delete/reorder *before* concatenation, so the flat diff downstream
  sees the reordering as ordinary node moves rather than needing new diff semantics.
- Conclusion: push does **not** need a second structural pipeline. It needs a thin
  directory-to-single-markdown-string assembly layer sitting in front of `_build_push_plan`, plus the
  manifest for section identity. This satisfies the constraint "reuse the existing structural
  parser/converter... rather than building a second pipeline."

## 2. Node tree shape — is there a natural per-heading grouping to split on?

Read `docs_structure_parser.py` (dataclasses at lines 133-260) and `nodes_to_markdown.py`.

**The parsed structure is a flat list, not a tree.** `DocsStructureParser().parse(doc)` returns
`List[Union[DocsParagraphNode, DocsTableNode, DocsImageNode]]` in document order. `DocsParagraphNode`
carries `style: str` (e.g. `"HEADING_1"`, `"HEADING_2"`, `"NORMAL_TEXT"`) as a plain string field
(line 150) — there is no parent/child nesting, no existing "section" or "outline" abstraction
anywhere in this module or `nodes_to_markdown.py`. `nodes_to_markdown.py:270`
(`_dispatch_key`) already does `node.style.startswith("HEADING_")` to special-case heading rendering,
which is the same primitive a splitter would use.

**A splitter is straightforward to add but doesn't preexist**: walk the flat node list once, and start
a new group every time a `DocsParagraphNode` with `style == f"HEADING_{split_level}"` (or ≤ split_level,
per config) is encountered; everything before the first split-level heading is a "preamble" group (front
matter that doesn't belong to any section, or is section 0). Nodes between two heading boundaries —
including tables and images — pass through untouched, since neither `DocsTableNode` nor
`DocsImageNode` carries a style/heading field to worry about.

**Configurable split level** (markgate.yaml) maps directly to a string match against `style`
(`HEADING_1`..`HEADING_6`), so no parser change is needed — only a new module (e.g.
`section_splitter.py`) consuming `DocsStructureParser`'s already-projected output and producing
`List[List[Node]]`, each rendered independently via the existing `render_nodes_to_markdown()` (521),
which takes a flat node list and has no dependency on document-wide state beyond one call to
`_group_code_runs` for fenced-code detection — confirmed safe to call per-section rather than once
per document, since that grouping is a local pass over the given list, not global doc state.

**Rejoining for push** is the same operation in reverse: read each section file, parse independently
via `MarkdownToParagraphParser().parse()`, concatenate the resulting node lists in manifest order
into one `target_nodes` list, then proceed exactly as monolithic push does (§1).

## 3. Mapping representation and CLI 1:1 assumption

Read `src/docspan/config.py` (`Mapping`, lines 78-88) and grepped `src/docspan/cli/main.py`.

```python
class Mapping(BaseModel):
    local: str       # relative path to local markdown file
    backend: str
    remote_id: Optional[str] = None
    direction: Literal["push", "pull", "both"] = "both"
    tab_id: Optional[str] = None
```

`Mapping.local` is a single file path, used as a literal string identity throughout the CLI:
- `push()`/`pull()` in `cli/main.py` select mappings to act on via `mapping.local in files` (line 223,
  372) and `mapping.local == file` (line 466) — exact string/path equality against CLI-provided file
  arguments.
- `mapping.local` is passed straight through as `local_path` into `backend.push(mapping.local,
  mapping.remote_id, tab_id=mapping.tab_id)` (line 258) and the equivalent `backend.pull(...)` call —
  both of which (per §1) read/write exactly one file at that path via `pathlib.Path(local_path)`.

**What has to change**: `Mapping` needs a new field, e.g. `sectioned: bool = False` plus
`split_level: Optional[str] = None` (or reuse `tab_id`-style config nesting under a `sections:` block),
and when `sectioned=True`, `local` names a **directory**, not a file. This is a config-model change
only — `Mapping` doesn't need `local` to become a union type if the interpretation of that one string
field is branched on `sectioned`.

The CLI's file-matching logic (`mapping.local in files`) needs a parallel branch for sectioned
mappings: matching should be "does this argument path fall under `mapping.local` directory" rather
than exact equality, since a user will likely invoke `docspan push docs/big-doc/03-installation.md`
(one section) or `docspan push docs/big-doc/` (the whole mapping) — both need to resolve to the
same `Mapping`. This is new CLI logic, not a small tweak; `push()`/`pull()` in `cli/main.py` should be
expected to grow a distinct sectioned branch that calls a new backend entry point (e.g.
`backend.push_sectioned(directory, doc_id, manifest_path, ...)`) rather than reusing `backend.push()`
as-is, even though that new entry point internally reuses `_build_push_plan`'s tail end (§1).

**Manifest (mechanism TBD in requirements, but the constraints imply its content)**: it must record,
per section, a stable identity token, its file name, and its order — the same "identity across
renames" problem requirements.md's Rabbit Holes flags. Candidate: manifest keyed by the Doc's own
`headingId` (already parsed as `DocsParagraphNode.heading_id`, line 188, and already the identity
anchor `heading_anchors.py` and cross-doc links use elsewhere in this codebase) rather than by
filename or heading text — filenames and heading text both change on a rename, `headingId` doesn't
(it's assigned once by Docs and persists across edits, per the existing docstring at line 183-188:
"NOT part of the diff key... assigned by Docs"). Store the manifest itself the same way `config.py`
persists `markgate.yaml` — atomic tempfile + `os.replace` (config.py:157-167) — as a per-directory
sidecar (e.g. `_manifest.json` inside the section directory) rather than inline in `markgate.yaml`,
so pull can update it independently of the user's own config edits.

## 4. Comment sidecar — keying and per-section extension

Read `comments.py` (full, 158 lines) and grepped `client.py`.

Today: one sidecar per file, `{local_path}.comments.md` (`COMMENTS_SUFFIX = ".comments.md"`,
`core/paths.py:7`), written by `_write_comment_sidecar(doc_id, local_path)`
(`backend.py:1019-1036`). It fetches **all** comments for the doc in one call
(`self._client.get_comments(doc_id)` → Drive API `comments.list`, fields requested:
`id,content,quotedFileContent,resolved,author(displayName)` — `client.py:270`) and dumps them into
one file, grouped Open/Resolved, with no positional information consulted or requested.

**Critical finding for per-section sidecars**: the comment resources fetched here carry no index or
range field — only `quotedFileContent.value`, a text snippet of what was selected when the comment
was made. Drive's Comments API does have an opaque `anchor` field that can encode a range, but
docspan doesn't request or parse it (`fields=` param above omits `anchor` entirely). Bucketing a
comment into "which section is it in" therefore cannot be done positionally with data already being
fetched — it would require either (a) adding `anchor` to the fields param and reverse-engineering its
undocumented range-encoding format, or (b) doing a **text-search of `quotedFileContent.value` against
each section's rendered text** to find which section contains the quoted snippet, falling back to
"unassigned" when no section matches (e.g. quote spans a section boundary or was on now-deleted text).
Option (b) is consistent with how this codebase already handles best-effort content alignment
elsewhere (`DocsRequestBuilder._align_for_styling` matches by content, not index, per the pass-2
comments at backend.py:500-530) — same trade-off (unaligned → reported, not guessed), and is the
pragmatic choice given no new external dependency is allowed. This is exactly the scope-balloon risk
requirements.md's Rabbit Holes calls out ("1 file today vs N sections") and should be scoped tightly:
a comment that can't be confidently attributed to one section either goes into a shared
`_unassigned.comments.md` or is duplicated with a note, not silently dropped — dropping would violate
this codebase's consistent "residue is reported, never silently dropped" convention (seen in
`projection.py`'s `Residue` type and repeated throughout `backend.py`'s push messaging, e.g. lines
582-596, 642-663).

## 5. Round-trip fixpoint — where an off-by-one/ordering bug could hide

For pull → push with no edits to be a no-op with N files instead of 1, three invariants all have to
hold simultaneously (any one breaking gives a false diff or a corrupted reassembly):

1. **Split/rejoin must be exact inverses at the node-list level.** Splitting a flat node list into N
   groups by heading boundary, then concatenating those N groups back in manifest order, must
   reproduce the original flat list byte-for-byte (same node objects/fields, same order) — including
   the boundary/"preamble" group before the first heading. An off-by-one here (e.g. the split
   including the heading paragraph in the *previous* group instead of starting the next group at it)
   silently duplicates or drops the heading node itself on rejoin.
2. **Per-file render/parse must independently pass through the SAME `project()` fixpoint monolithic
   pull/push already guarantees** (backend.py:923, 243, 259 all call `project()` before either
   rendering or diffing — this is the existing mechanism that makes non-sectioned pull→push a
   fixpoint, per the docstring at projection.py:1-20). If per-section splitting happens *before*
   `project()` runs, or only runs `project()` once over the whole doc rather than being safe to run
   per-section, residue handling (empty paragraphs, TITLE/SUBTITLE remapping) could disagree at
   section boundaries — e.g. an empty paragraph immediately before a heading being ambiguous about
   which section it "belongs" to for residue-reporting purposes. This needs an explicit rule: split
   after `project()`, not before, so each section's node list is already in "what markdown can
   represent" form before rendering.
3. **Manifest order must be the single source of truth for section order on push**, not filesystem
   directory listing order (which is not guaranteed stable/meaningful — e.g. `01-intro.md`,
   `10-appendix.md`, `2-middle.md` sorts lexicographically wrong) and not "order comments/headings
   happen to appear in the concatenated file." A reorder (drag section 3 above section 1 in the Doc)
   has to be detected by comparing the *pulled* manifest order against the *stored* manifest order
   from last sync — this is where an add/delete/reorder detection bug would hide: if reorder detection
   diffs by filename/content instead of by the `headingId`-based identity token (§3), a heading that
   was both renamed and reordered in the same edit becomes indistinguishable from delete+insert,
   exactly the ambiguity requirements.md's Rabbit Holes calls out.

A concrete regression test worth planning for: pull a multi-section doc, push with zero edits,
assert `preview_push` reports "No changes detected" — mirroring the existing no-op contract at
backend.py:574-597 (`status="skipped"`, `"No changes detected"`) — for the *sectioned* path, not just
confirming N files got written.
