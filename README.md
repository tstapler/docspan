# docspan

Push and pull markdown to Google Docs and Confluence from a single CLI.

```
pip install docspan
docspan auth setup google_docs
docspan push docs/design-doc.md
```

---

## Install

```bash
pipx install docspan
# or
pip install docspan
# or
uv tool install docspan
```

## Quick start

**1. Copy the example config:**
```bash
cp docspan.yaml.example docspan.yaml
```

**2. Set up auth for your backend(s):**
```bash
docspan auth setup google_docs
docspan auth setup confluence
```

**3. Add mappings to `docspan.yaml`:**
```yaml
mappings:
  - local: docs/design-doc.md
    backend: google_docs
    remote_id: <your-google-doc-id>
    direction: both
```

**4. Push or pull:**
```bash
docspan push                     # push all mappings
docspan push docs/design-doc.md  # push one file
docspan pull                     # pull all mappings
docspan status                   # show mapping table
```

---

## Commands

| Command | Description |
|---|---|
| `docspan push [file]` | Convert local markdown and update the remote doc |
| `docspan pull [file]` | Fetch remote doc and write as local markdown |
| `docspan status` | Show all configured mappings |
| `docspan auth setup <backend>` | Interactive auth wizard |

**Global flags:** `--config path/to/docspan.yaml`, `--dry-run`

---

## Backends

### Google Docs
Requires a Google Cloud project with the Drive and Docs APIs enabled, and a service account with editor access to your docs.
Run `docspan auth setup google_docs` for step-by-step setup instructions.

### Confluence
Requires an Atlassian API token.
Set via env vars or `docspan auth setup confluence`:

```bash
export CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
export ATLASSIAN_USER_NAME=you@yourorg.com
export CONFLUENCE_API_TOKEN=your-token
```

---

## Config reference

```yaml
backends:
  google_docs:
    credentials_path: ~/.docspan/google_credentials.json
    token_path: .docspan/google_token.json
  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@yourorg.com
    # api_token: prefer env var CONFLUENCE_API_TOKEN

mappings:
  - local: docs/design-doc.md       # relative path to local file
    backend: google_docs            # "google_docs" or "confluence"
    remote_id: <doc-id>             # Google Doc ID or Confluence page ID
    direction: both                 # "push", "pull", or "both"
```

---

## Adding a new backend

1. Create `src/docspan/backends/<name>/backend.py`
2. Subclass `docspan.backends.base.Backend` and implement `push()`, `pull()`, `auth_setup()`, `validate_config()`
3. Register in `src/docspan/backends/__init__.py`

See `src/docspan/backends/google_docs/backend.py` for a reference implementation.

---

## Development

```bash
git clone https://github.com/tstapler/docspan
cd docspan
uv sync --extra dev
uv run pytest
uv run docspan --help
```

---

## Origins

`docspan` is built on two foundations:
- [`markdown-confluence`](https://github.com/tstapler/markdown-confluence) — Confluence publish pipeline
- [`google-docs-obsidian-sync`](https://github.com/zxc3309/google-docs-obsidian-sync) — Google Docs sync engine (fork)

---

## License

MIT
