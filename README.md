# markgate

Push and pull markdown to Google Docs and Confluence from a single CLI.

```
pip install markgate
markgate auth setup google_docs
markgate push docs/design-doc.md
```

---

## Install

```bash
pipx install markgate
# or
pip install markgate
# or
uv tool install markgate
```

## Quick start

**1. Copy the example config:**
```bash
cp markgate.yaml.example markgate.yaml
```

**2. Set up auth for your backend(s):**
```bash
markgate auth setup google_docs
markgate auth setup confluence
```

**3. Add mappings to `markgate.yaml`:**
```yaml
mappings:
  - local: docs/design-doc.md
    backend: google_docs
    remote_id: <your-google-doc-id>
    direction: both
```

**4. Push or pull:**
```bash
markgate push                     # push all mappings
markgate push docs/design-doc.md  # push one file
markgate pull                     # pull all mappings
markgate status                   # show mapping table
```

---

## Commands

| Command | Description |
|---|---|
| `markgate push [file]` | Convert local markdown and update the remote doc |
| `markgate pull [file]` | Fetch remote doc and write as local markdown |
| `markgate status` | Show all configured mappings |
| `markgate auth setup <backend>` | Interactive auth wizard |

**Global flags:** `--config path/to/markgate.yaml`, `--dry-run`

---

## Backends

### Google Docs
Requires a Google Cloud project with the Drive and Docs APIs enabled.
Run `markgate auth setup google_docs` — it opens a browser OAuth flow and saves a token locally.

### Confluence
Requires an Atlassian API token.
Set via env vars or `markgate auth setup confluence`:

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
    credentials_path: ~/.markgate/google_credentials.json
    token_path: .markgate/google_token.json
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

1. Create `src/markgate/backends/<name>/backend.py`
2. Subclass `markgate.backends.base.Backend` and implement `push()`, `pull()`, `auth_setup()`, `validate_config()`
3. Register in `src/markgate/backends/__init__.py`

See `src/markgate/backends/google_docs/backend.py` for a reference implementation.

---

## Development

```bash
git clone https://github.com/tstapler/markgate
cd markgate
uv sync --extra dev
uv run pytest
uv run markgate --help
```

---

## Origins

`markgate` is built on two foundations:
- [`markdown-confluence`](https://github.com/tstapler/markdown-confluence) — battle-tested Confluence publish pipeline
- [`google-docs-obsidian-sync`](https://github.com/zxc3309/google-docs-obsidian-sync) — Google Docs sync engine (fork)

---

## License

MIT
