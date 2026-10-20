# Configuration

docspan reads from a file named `markgate.yaml` in the current working directory (or the path passed with `--config`). This name is kept for backward compatibility and will be renamed to `docspan.yaml` in v0.2.0.

!!! warning
    `markgate.yaml` is gitignored by default because it may contain API tokens. Use `markgate.yaml.example` as a template.

## Full Example

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json   # optional; or use env vars
    token_path: .markgate/google_token.json            # default; rarely changed

  confluence:
    base_url: https://yourorg.atlassian.net            # required for confluence
    username: you@example.com                          # required for confluence
    api_token: your-atlassian-api-token                # required for confluence

mappings:
  - local: docs/my-doc.md                             # relative path to local file
    backend: google_docs                              # "google_docs" or "confluence"
    remote_id: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74O  # Google Doc ID or Confluence page ID
    direction: both                                   # "push", "pull", or "both" (default: both)

  - local: docs/confluence-page.md
    backend: confluence
    remote_id: "123456"
    direction: push
```

---

## `backends.google_docs` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `credentials_path` | string | null | Path to Google service account JSON key |
| `token_path` | string | `.markgate/google_token.json` | OAuth token storage path (rarely changed) |

**Environment variable alternatives** (checked when YAML field is absent):

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
| `local` | string | — | yes | Relative path to local markdown file |
| `backend` | string | — | yes | `"google_docs"` or `"confluence"` |
| `remote_id` | string | — | yes | Google Doc ID or Confluence page ID |
| `direction` | enum | `"both"` | no | `"push"`, `"pull"`, or `"both"` |

---

## State Files

docspan generates these files in your project directory after the first sync. Do not delete them manually while syncing is active.

| File | Description |
|---|---|
| `.markgate-state.json` | Sync state tracking: content hashes, remote versions, backend per mapping |
| `.markgate-base/` | Content-addressed store of merge bases used for three-way merge |
| `{file}.orig` | Backup of local file content before a merge; deleted after conflict resolution |
| `{file}.comments.md` | Confluence comment sidecar; written during pull when comments exist |
