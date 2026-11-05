# Configuration

docspan finds its config using the [resolution order](#central-config-and-xdg-storage) below. The simplest case is `markgate.yaml` in the current working directory, or the path passed with `--config`. `docspan.yaml` is also recognized: if present (and no `markgate.yaml` is), docspan reads and writes it instead.

!!! warning
    `markgate.yaml` is gitignored by default because it may contain API tokens. Use `markgate.yaml.example` as a template.

## Full Example

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json   # or oauth_client_secret_path for per-user OAuth

  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@example.com
    api_token: your-atlassian-api-token

mappings:
  - local: docs/my-doc.md
    backend: google_docs
    remote_id: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74O
    direction: both

  - local: docs/confluence-page.md
    backend: confluence
    remote_id: "123456"
    direction: push
```

---

## `backends.google_docs` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `credentials_path` | string | null | Path to a Google **service account** JSON key |
| `oauth_client_secret_path` | string | null | Path to an **OAuth client secret** JSON (Desktop app) for per-user auth |
| `token_path` | string | `$XDG_CONFIG_HOME/docspan/google_token.json` (`.../docspan/<prefix>/google_token.json` when a prefix is active) | Where the cached OAuth user token is stored and refreshed |
| `pull_comments` | bool | `true` | On pull, write a `{file}.comments.md` sidecar of the doc's comments |

Auth resolution order: `credentials_path` → `ACCOUNT_A_CREDENTIALS[_PATH]` env → per-user OAuth (`oauth_client_secret_path`, or an already-cached `token_path`).

**Environment variable alternatives** (service account):

| Variable | Description |
|---|---|
| `ACCOUNT_A_CREDENTIALS_PATH` | Path to service account JSON file |
| `ACCOUNT_A_CREDENTIALS` | Inline service account JSON string |

---

## `backends.confluence` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | null | Confluence base URL, e.g. `https://yourorg.atlassian.net` |
| `username` | string | null | Atlassian account email address |
| `api_token` | string | null | API token from id.atlassian.com |

**Environment variable alternatives**:

| Variable | Description |
|---|---|
| `CONFLUENCE_BASE_URL` | Confluence base URL |
| `ATLASSIAN_USER_NAME` | Atlassian account email |
| `CONFLUENCE_API_TOKEN` | Atlassian API token |

---

## `mappings[]` fields

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `local` | string | — | yes | Relative path to the local markdown file (a directory when `sectioned`) |
| `backend` | string | — | yes | `"google_docs"` or `"confluence"` |
| `remote_id` | string | `null` | no | Google Doc ID or Confluence page ID. Leave unset and use `docspan map` to create the remote doc. |
| `direction` | enum | `"both"` | no | `"push"`, `"pull"`, or `"both"` |
| `tab_id` | string | `null` | no | Google Docs tab id (e.g. `"t.moqlkhpwn82e"`) on a multi-tab doc. Ignored by Confluence. If unset on a multi-tab doc, push/pull still succeed but warn, naming the tabs found. |
| `pageless` | bool | `null` | no | Google Docs: `true` sets `PAGELESS`, `false` sets `PAGES` on every push; `null` leaves the doc's mode untouched. Ignored by Confluence. |
| `pull_strategy` | enum | `"auto"` | no | `"auto"` or `"structural"`. `structural` pulls through the structural API instead of Drive's HTML export, which can target any tab. |
| `sectioned` | bool | `false` | no | Google Docs only: `local` is a directory of `NN-slug.md` section files plus `_manifest.yaml`. Requires `split_level`. |
| `split_level` | string | `null` | no | Heading style a sectioned pull splits on, e.g. `HEADING_1`. |

For a multi-tab doc, `docspan map FILE --backend google_docs --new-tab-in EXISTING_FILE` creates a new tab and fills in `tab_id`. To convert an existing mapping to sectioned, use `docspan migrate-sectioned FILE`.

---

## Central config and XDG storage

By default docspan stores its config, sync state, and credentials under the [XDG base directories](https://specifications.freedesktop.org/basedir-spec/latest/). A central config registers multiple projects by *prefix* so you can run docspan from anywhere.

```
$XDG_CONFIG_HOME/docspan/config.yaml     # central config (project registry)
$XDG_CONFIG_HOME/docspan/<prefix>/…      # cached OAuth token
$XDG_STATE_HOME/docspan/<prefix>/…       # sync state + base store, per project
```

```yaml
# ~/.config/docspan/config.yaml
default_prefix: design-docs
projects:
  design-docs:
    markgate: ~/Documents/design-docs/markgate.yaml
```

```bash
docspan config add design-docs ~/Documents/design-docs/markgate.yaml
docspan config show
docspan push --prefix design-docs
docspan migrate-xdg --prefix design-docs    # move legacy in-repo state to XDG + register
```

**Prefix resolution order:** `--config PATH` (legacy — storage stays beside the file) → `--prefix` → `DOCSPAN_PREFIX` → cwd inside a registered project → `default_prefix`. If nothing matches, docspan falls back to a local `./markgate.yaml` with beside-the-file storage.

---

## State Files

After the first sync docspan writes these files. `.markgate-state.json` and `.markgate-base/` live beside the config in legacy mode (`--config`, or the local `./markgate.yaml` fallback) and under `$XDG_STATE_HOME/docspan/<prefix>/` when a central-config prefix is active. The other files always sit beside the markdown file.

| File | Description |
|---|---|
| `.markgate-state.json` | Sync state tracking: content hashes, remote versions, backend per mapping |
| `.markgate-base/` | Content-addressed store of merge bases used for three-way merge |
| `{file}.orig` | Backup of the local file before a merge; deleted after conflict resolution |
| `{file}.comments.md` | Comment sidecar (Google Docs and Confluence); written during pull when comments exist |
| `{file}.mermaid-cache.yaml` | Google Docs: maps each pushed mermaid fence's rendered-PNG hash back to its source, so another machine can restore the fence on pull. Written on push when the file has mermaid fences. |

**Commit `{file}.mermaid-cache.yaml`, and in legacy mode also `.markgate-state.json` and `.markgate-base/`.** In legacy mode the state files must be shared for three-way merge to work across machines and teammates; under a central-config prefix they are per-machine. `{file}.orig` and `{file}.comments.md` are transient and safe to gitignore.
