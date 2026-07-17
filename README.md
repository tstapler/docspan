# docspan

[![PyPI](https://img.shields.io/pypi/v/docspan)](https://pypi.org/project/docspan/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

Push and pull markdown to Google Docs and Confluence from a single CLI. docspan provides bidirectional sync with three-way merge conflict detection, structural diff push that preserves comments on unchanged paragraphs, and a simple YAML-based configuration file.

The config file is named `markgate.yaml` — this name is preserved for backward compatibility and will be renamed in v0.2.0.

---

## Supported Backends

| Backend | Push | Pull |
|---|---|---|
| Google Docs | yes | yes |
| Confluence | yes | yes |

---

## Install

```bash
pip install docspan
```

---

## Quick start

**1. Create `markgate.yaml`:**

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

**2. Set up authentication:**

```bash
docspan auth setup google_docs
# or
docspan auth setup confluence
```

**3. Push and pull:**

```bash
docspan push                     # push all mappings
docspan pull                     # pull all mappings
docspan status                   # show mapping table
```

**4. Resolve conflicts (if any):**

```bash
docspan conflicts list
docspan conflicts resolve docs/design-doc.md --accept remote
```

---

## Configuration (`markgate.yaml`)

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json  # or use env ACCOUNT_A_CREDENTIALS_PATH
  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@example.com
    api_token: your-api-token  # or env CONFLUENCE_API_TOKEN

mappings:
  - local: docs/notes.md
    backend: google_docs
    remote_id: YOUR_GOOGLE_DOC_ID
    direction: both  # push | pull | both
  - local: docs/page.md
    backend: confluence
    remote_id: YOUR_CONFLUENCE_PAGE_ID
    direction: both
```

**Note**: `markgate.yaml` is gitignored by default because it may contain API tokens. Commit a `markgate.yaml.example` template alongside it.

---

## Central config & XDG storage

By default docspan stores its config, sync state, and credentials under the [XDG base directories](https://specifications.freedesktop.org/basedir-spec/latest/), and a **central config** lets you register multiple projects by *prefix* and run docspan from anywhere.

```
$XDG_CONFIG_HOME/docspan/config.yaml     # central config (project registry)
$XDG_CONFIG_HOME/docspan/<prefix>/…      # cached OAuth token
$XDG_STATE_HOME/docspan/<prefix>/…       # sync state + base store, per project
```

Central config (`~/.config/docspan/config.yaml`):

```yaml
default_prefix: design-docs
projects:
  design-docs:
    markgate: ~/Documents/design-docs/markgate.yaml
```

Register and use projects:

```bash
docspan config add design-docs ~/Documents/design-docs/markgate.yaml   # register (prefix → markgate.yaml)
docspan config show                                                    # list projects + active resolution
docspan push --prefix design-docs                                      # or DOCSPAN_PREFIX, or default_prefix, or cwd match
docspan migrate-xdg --prefix design-docs                               # move legacy in-repo state to XDG + register
```

**Prefix resolution order:** `--config PATH` (legacy — storage stays beside the file) → `--prefix` → `DOCSPAN_PREFIX` → cwd inside a registered project → `default_prefix`. If nothing matches, docspan falls back to a local `./markgate.yaml` with beside-the-file storage (fully backward-compatible).

---

## Command Reference

### `docspan push`

```
docspan push [FILES]... [--dry-run] [--config PATH]
```

Push local markdown files to remote docs. Skips mappings with `direction = "pull"`. Accepts an optional list of local file paths to restrict which mappings are pushed.

### `docspan pull`

```
docspan pull [FILES]... [--dry-run] [--config PATH]
```

Pull remote documents into local markdown files with three-way merge. Writes conflict markers to the file if automatic merge fails.

### `docspan status`

```
docspan status [--config PATH]
```

Display all configured mappings in a table showing local file, backend, remote ID, and direction.

### `docspan auth setup`

```
docspan auth setup BACKEND [--config PATH]
```

Interactive authentication setup. `BACKEND` is one of `google_docs` or `confluence`.

For Google Docs, prints step-by-step service account setup instructions. For Confluence, prompts for base URL, username, and API token, then prints a YAML snippet to add to `markgate.yaml`.

### `docspan conflicts list`

```
docspan conflicts list [--config PATH]
```

Scan all tracked files for unresolved merge conflict markers (`<<<<<<< `). Prints a table of conflicted files and conflict block counts.

### `docspan conflicts resolve`

```
docspan conflicts resolve FILE --accept remote|local|merged [--config PATH]
```

Resolve a merge conflict in a tracked file.

| Strategy | Behavior |
|---|---|
| `remote` | Re-fetch the remote version and overwrite the local file |
| `local` | Restore the pre-merge local content from the `.orig` backup |
| `merged` | Accept the current file contents as the resolved version (conflict markers must be removed first) |

---

## Configuration Reference

### `backends.google_docs`

| Field | Type | Default | Description |
|---|---|---|---|
| `credentials_path` | string | null | Path to Google service account JSON key |
| `token_path` | string | `.markgate/google_token.json` | OAuth token storage path |

**Environment variable alternatives:**
- `ACCOUNT_A_CREDENTIALS_PATH` — path to service account JSON
- `ACCOUNT_A_CREDENTIALS` — inline service account JSON string

### `backends.confluence`

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | null | Confluence base URL, e.g. `https://yourorg.atlassian.net` |
| `username` | string | null | Atlassian account email |
| `api_token` | string | null | API token from id.atlassian.com |

**Environment variable alternatives:**
- `CONFLUENCE_BASE_URL`
- `ATLASSIAN_USER_NAME`
- `CONFLUENCE_API_TOKEN`

### `mappings[]`

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `local` | string | — | yes | Relative path to local markdown file |
| `backend` | string | — | yes | `"google_docs"` or `"confluence"` |
| `remote_id` | string | — | yes | Google Doc ID or Confluence page ID |
| `direction` | enum | `"both"` | no | `"push"`, `"pull"`, or `"both"` |

---

## State Files

docspan generates these files in your project directory after first sync:

| File | Description |
|---|---|
| `.markgate-state.json` | Sync state tracking (content hashes, remote versions) |
| `.markgate-base/` | Content-addressed store of merge bases |
| `{file}.orig` | Backup of local file before merge; deleted after conflict resolution |
| `{file}.comments.md` | Confluence comment sidecar; written during pull if comments exist |

---

## Known Limitations

> [!NOTE]
> **Known limitations in v0.1.0**
>
> - Google Docs: comments on edited paragraphs are lost on push (paragraph-level structural diff; comments on unchanged paragraphs are preserved)
> - Push: no image support — local images cannot be pushed to Google Docs or Confluence
> - Push: no table support — markdown tables are not rendered in Google Docs
> - Confluence: requires an Atlassian API token; no OAuth flow
> - Confluence: the comment sidecar (`{file}.comments.md`) is informational only; comments cannot be pushed back

---

## License

MIT. See [LICENSE](LICENSE) for details.

For contribution guidelines, see [CONTRIBUTING.md](https://github.com/tstapler/docspan/blob/main/CONTRIBUTING.md).
For the full change history, see [CHANGELOG.md](https://github.com/tstapler/docspan/blob/main/CHANGELOG.md).
