# docspan

Push and pull markdown to Google Docs and Confluence from a single CLI. docspan provides bidirectional sync with three-way merge conflict detection, structural diff push that preserves comments on unchanged paragraphs, and a simple YAML-based configuration file (`markgate.yaml`).

## What it does

Push local markdown files to Google Docs or Confluence. Pull remote documents back into local files. When both sides have changed, docspan performs a three-way merge and writes conflict markers so you can resolve them with `docspan conflicts resolve`.

The push to Google Docs uses a paragraph-level structural diff that emits minimal `batchUpdate` requests, preserving comments on paragraphs that have not changed.

## Quickstart

**1. Install:**

```bash
pip install docspan
```

**2. Create `markgate.yaml`:**

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

**3. Set up authentication:**

```bash
docspan auth setup google_docs
```

**4. Push and pull:**

```bash
docspan push
docspan pull
docspan status
```

See the [Install](install.md) page for full auth setup instructions and the [Commands](commands.md) page for the complete CLI reference.

## Backends

| Backend | Auth Method | Push | Pull |
|---|---|---|---|
| Google Docs | Service account JSON key | yes | yes |
| Confluence | Atlassian API token | yes | yes |

## Known Limitations

!!! warning "Known limitations in v0.1.0"
    - Google Docs: comments on edited paragraphs are lost on push (paragraph-level structural diff; comments on unchanged paragraphs are preserved)
    - Google Docs: images push and pull (local files upload to Drive; `https://` URLs are referenced directly)
    - Push: no image support for Confluence — local images cannot be pushed
    - Push: no table support — markdown tables are not rendered in Google Docs
    - Confluence: requires an Atlassian API token; no OAuth flow
    - Confluence: the comment sidecar (`{file}.comments.md`) is informational only; comments cannot be pushed back
