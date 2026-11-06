# docspan

**Keep markdown files in git and in sync with Google Docs and Confluence.** Write in your editor, review in git, and let teammates who live in Docs or Confluence keep commenting there.

- **Two-way sync.** `push`, `pull`, or `sync` (pull, then push) per mapping.
- **Three-way merge.** When both sides changed, docspan merges and writes conflict markers you resolve with `docspan conflicts resolve`.
- **Comment-safe pushes (Google Docs).** A paragraph-level structural diff sends minimal `batchUpdate` requests, so comments on unchanged paragraphs survive. `push` warns before it would destroy a comment and blocks unless you pass `--force`.
- **Rich content.** Images, tables, blockquotes, fenced code, and mermaid diagrams (rendered to PNG) on Google Docs; multi-tab docs and sectioned (one file per heading) mappings too.
- **Comment sidecars.** Pull writes `{file}.comments.md`; reply to or resolve Google Docs comments from it with `docspan comments respond`.

## Quickstart

**1. Install:**

```bash
pip install docspan
```

**2. Create `docspan.yaml`:**

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json

mappings:
  - local: docs/design-doc.md
    backend: google_docs
    remote_id: YOUR_GOOGLE_DOC_ID
    direction: both
```

No doc yet? `docspan map docs/design-doc.md --backend google_docs` creates one and adds the mapping.

**3. Set up authentication:**

```bash
docspan auth setup google_docs
```

**4. Sync:**

```bash
docspan status
docspan push --dry-run   # preview, including comments at risk
docspan push
docspan pull
```

See [Install](install.md) for auth setup, [Configuration](configuration.md) for every field, and [Commands](commands.md) for the CLI reference.

## Backends

| Backend | Auth Method | Push | Pull |
|---|---|---|---|
| Google Docs | Service account or per-user OAuth | yes | yes |
| Confluence | Atlassian API token | yes | yes |

## Known Limitations

!!! warning
    - Google Docs: comments on edited paragraphs are lost on push (comments on unchanged paragraphs are preserved). `push --dry-run` lists every at-risk comment, and a real push blocks unless `--force`.
    - Google Docs: an image must sit alone on its own line. A pulled image's link is Google's `contentUri`, which can change over time even when the image hasn't — the stale URI can show up as a one-sided edit during conflict resolution.
    - Confluence: no image push, no OAuth (API token only), and comments are pull-only.
    - Checklists round-trip as literal `- [ ]` / `- [x]` text.

See the [Google Docs](backends/google-docs.md#limitations) and [Confluence](backends/confluence.md#limitations) pages for the full lists.
