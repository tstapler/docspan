# Stack Research — gdocs-sectioned-sync

## Config schema (markgate.yaml)

`src/docspan/config.py` is the single loader/model for markgate.yaml. Pattern to follow:

- Plain `pydantic.BaseModel` subclasses, `Optional[X] = None` for opt-in fields, `Literal[...]` for enums (see `Mapping.direction: Literal["push", "pull", "both"]`).
- `GoogleDocsConfig` (backend-wide) already has a precedent for an opt-in behavior toggle: `pull_comments: bool = True`.
- `Mapping` (per-mapping, `config.py:78`) is the natural home for a new opt-in field — it already carries mapping-scoped Google Docs behavior (`tab_id: Optional[str] = None`, with a comment explaining "None preserves pre-tabs behavior"). A `sectioned: Optional[SectionedConfig] = None` (or flat `split_level: Optional[int] = None`) fits this exact pattern and keeps existing mappings unaffected by construction (default `None` → old code path).
- `save_config()` round-trips through `ruamel.yaml` (`_ryaml`) to preserve comments/formatting in the file, merging the pydantic `model_dump(exclude_none=True)` onto the existing `CommentedMap`. Any new field must default such that `exclude_none=True` omits it for untouched mappings — already true for `Optional[...] = None` fields.
- `load_config()` does a plain `yaml.safe_load` + `MarkgateConfig(**raw)`; no extra plumbing needed for a new nested model.

## Manifest format — prior art and fit

No such "N files ↔ 1 external doc" model exists anywhere in docspan today (confirmed by search — see "Directory-of-files precedent" below: none).

Candidates surveyed against docspan's existing conventions:

1. **YAML sidecar manifest** (e.g. `<doc-dir>/.sections.yaml`) — best fit. Docspan already has a working pattern for exactly this shape of problem: the comments sidecar (`{file}.comments.md`, `src/docspan/backends/google_docs/comments.py`) is a per-doc sidecar file separate from the content, written by pull and parsed back on `docspan comments respond`. A sections manifest is the same shape: `remote_id/tab_id` (doc identity) already lives in `markgate.yaml`; a sidecar YAML per sectioned mapping would record ordered list of `{heading_id, slug, filename}` entries. This keeps content files as pure markdown (agents/humans read them with no marker syntax to strip) and matches the project's existing round-trip-preserving YAML tooling (`ruamel.yaml`) already a dependency.
2. **Markdown front matter per file** — used by Hugo/Jekyll/Sphinx-with-frontmatter for page metadata, but docspan's `MarkdownToParagraphParser`/`nodes_to_markdown.py` pipeline has no front-matter concept today; introducing one means every per-section file needs a parser layer stripped before feeding the existing structural pipeline. Rejected as extra surface area duplicating what a sidecar gives for free.
3. **HTML-comment marker embedded in each file** (`<!-- section-id: h.abc123 -->`) — lowest new-machinery cost, but the requirements' "Comments sidecar" concept and the `heading_anchors.py` module already reserve HTML-comment-in-markdown for a different purpose (comment ids: `<!-- id:{comment_id} -->` in `comments.py:16`) and for internal anchor targets. Overloading the same syntax space for section identity risks collision/confusion; also a stray edit to the comment breaks identity silently with no separate file to diff against. Rejected as primary mechanism, though could be a defense-in-depth cross-check.

**Recommendation:** YAML sidecar manifest, one per sectioned mapping (parallel to the existing `.comments.md` sidecar), keyed by Google Docs' own `headingId` (see next section) with slug as a human-readable fallback/display field — mirrors how Sphinx/MyST multi-file books use a `toctree`/`_toc.yml` external to the content pages for identity+order, and how static site generators keep ordering data (front matter `weight`/directory-prefix) separate from prose. Git-based multi-file merge tools (e.g. Sphinx's own doc splitting, or `git mv` detection) rely on content-similarity heuristics for rename detection — worth reusing as a *fallback* (fuzzy match on heading text) when `heading_id` doesn't resolve (e.g. section was fully deleted and a similarly-named one added), but the primary key should be the stable id docspan already extracts.

## Stable identity: reuse `heading_id`, don't reinvent it

The critical existing asset: **`DocsParagraphNode.heading_id`** (`src/docspan/backends/google_docs/docs_structure_parser.py:183`) is Google Docs' own persistent id for a heading paragraph (`paragraphStyle.headingId`), already parsed on every pull. `heading_anchors.py` already builds exactly the map this feature needs:

- `heading_slug_to_id(nodes) -> Dict[str, str]` (`heading_anchors.py:197`) — slug → headingId, in document order.
- `heading_id_to_slug(nodes) -> Dict[str, str]` (`heading_anchors.py:208`) — inverse.
- `slugify()` / `slugify_all()` (`heading_anchors.py:78`, `:130`) — a from-scratch, dependency-free reimplementation of `github-slugger`'s algorithm (lowercase, Unicode-category-aware punctuation stripping, whitespace→hyphen, duplicate-suffix disambiguation `intro`, `intro-1`, `intro-2`...), validated against `tests/fixtures/github_slugger_vectors.json`.

This means: **no new slug library is needed** — `slugify_all` already produces filesystem-safe, collision-disambiguated slugs suitable for section filenames (e.g. `003-intro.md` combining an order prefix with `slugify()`'s output). The sectioned-mode manifest's identity column should be `heading_id` (Docs-assigned, stable across content edits and pure reorders) with `slug` carried alongside for the filename and for human review — exactly the same pairing `heading_anchors.py` already uses for cross-doc-link resolution. The code comment at `docs_structure_parser.py:186` ("NOT part of the diff key... assigned by Docs, treating it as identity would make every freshly written heading look like a different paragraph") is a specific, applicable warning: a *newly created* heading (via a new section file with no prior `heading_id`) has no id yet — the manifest/push path must handle "no prior id → insert" the same way current diff logic treats new headings, not confuse it with a rename.

## Slug library dependency check

Checked `pyproject.toml` dependencies and `uv.lock` (`grep -n "^name = " uv.lock | grep -iE "slugify|unidecode"` → no matches). Docspan has **no** `python-slugify`/`slugify`/`Unidecode` dependency, and per the section above, **does not need one** — `heading_anchors.slugify`/`slugify_all` already solves this in-house, is unit-tested, and matches the semantics (github-slugger-compatible) that markdown headings already use for internal anchors. Any new sectioned-mode code should import and reuse these functions directly rather than adding a dependency.

## Directory-of-files precedent in the codebase

Searched `src/docspan/backends` for existing multi-file/attachment output handling:

- **Confluence attachments** (`src/docspan/backends/confluence/services/confluence/attachment_client.py`) — handles *uploading a single local file* to a page as a Confluence attachment (one file ↔ one attachment object on the remote page), not a local multi-file split of one logical document. Not a reusable pattern for directory reassembly, but confirms attachment-style file handling elsewhere uses plain `pathlib.Path` + a client method, no special framework.
- **Google Docs images** (`image_source.py`) — resolves markdown image references (local path / in-memory / URL / mermaid-rendered) to upload payloads; again single-file-in, single-object-out, no directory concept.
- **Pull/push write path** (`backend.py:885-933`) — current pull writes exactly one file: `pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)` then `.write_text(markdown_content)`. No directory-of-outputs code exists to imitate; sectioned pull will be new code, but should follow this same mkdir-then-write idiom per section file rather than inventing a different I/O pattern.
- **Comments sidecar** (`backend.py:1034`, `comments.py`) — the closest existing "one doc → auxiliary file(s)" precedent, and per Scope, this is exactly what needs extending to "per-section" (one `.comments.md` per section file instead of one per doc) — same `format_comments_markdown()` function, just called once per section's comment subset instead of once for the whole doc.

## Summary of concrete reuse points for planning

- `src/docspan/config.py` — add `Mapping.sectioned: Optional[...]` (or similar) following the `tab_id`/`GoogleDocsConfig.pull_comments` precedent; `save_config`/`load_config` need no structural changes.
- `src/docspan/backends/google_docs/heading_anchors.py` — reuse `slugify_all`, `heading_slug_to_id`/`heading_id_to_slug` directly for both filename generation and manifest identity; do not add a slug dependency.
- `src/docspan/backends/google_docs/docs_structure_parser.py` — `DocsParagraphNode.heading_id` is the raw signal driving all of the above; note its documented caveat about not being diff-key-safe for brand-new headings.
- New manifest: a YAML sidecar (via existing `PyYAML`/`ruamel.yaml` deps, no new dependency), parallel in spirit to the existing `{file}.comments.md` sidecar, keyed by `heading_id` with `slug` and order as companion fields.
- No existing directory-of-many-files sync code to copy wholesale; closest analog is the per-doc comments sidecar, which itself needs to become per-section as part of this project (in scope per requirements' "Comment sidecar behavior extended to work per-section").
