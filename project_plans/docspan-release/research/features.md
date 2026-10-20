# Features Research — docspan v0.1.0 Release

## Complete CLI Command Reference

### Top-level app

```
docspan [OPTIONS] COMMAND [ARGS]...
```
> NOTE: The typer app is currently named `"markgate"` (see `app = typer.Typer(name="markgate", ...)`). This is the `--help` display name and needs to be changed to `"docspan"` for the release.

**Global options**: None (completion disabled via `add_completion=False`)

**Subcommands**:
- `push` — Push local markdown to remote docs
- `pull` — Pull remote docs into local markdown files
- `status` — Show current mapping status
- `auth` — Manage authentication for backends
  - `auth setup` — Interactive authentication setup for a backend
- `conflicts` — Manage merge conflicts
  - `conflicts list` — List files with unresolved merge conflicts
  - `conflicts resolve` — Resolve a merge conflict in a tracked file

---

### `docspan push`

```
docspan push [FILES]... [OPTIONS]
```

**Description**: Push local markdown to remote docs.

**Arguments**:
- `FILES` (optional, multiple) — Local markdown files to push. Defaults to all mappings.

**Options**:
- `--config`, `-c` TEXT — Path to markgate.yaml
- `--dry-run` — Preview changes without writing (flag)

**Behaviour**:
- Skips mappings with `direction = "pull"` (prints `[dim]Skipping {file} (pull-only)[/dim]`)
- In dry-run: prints `[yellow]dry-run[/yellow]  {local} → [{backend}] {remote_id}`
- On success: prints `[green]✓[/green]  {local} → {url}`
- On error: prints `[red]✗[/red]  {local} → {remote_id}` plus error message; exits 1
- If no mappings found for given files: exits 1 with error

---

### `docspan pull`

```
docspan pull [FILES]... [OPTIONS]
```

**Description**: Pull remote docs into local markdown files.

**Arguments**:
- `FILES` (optional, multiple) — Local paths to pull into. Defaults to all mappings.

**Options**:
- `--config`, `-c` TEXT — Path to markgate.yaml
- `--dry-run` — Preview changes without writing (flag)

**Behaviour**:
- Skips mappings with `direction = "push"` (prints `[dim]Skipping {file} (push-only)[/dim]`)
- In dry-run: prints `[yellow]dry-run[/yellow]  [{backend}] {remote_id} → {local}`
- Outcome messages:
  - `up-to-date`: `[dim]up to date[/dim]  {local}`
  - `local-only`: `[yellow]warning[/yellow]  {local} has local changes not yet pushed. Pull skipped. Push first or use 'markgate conflicts resolve'.`
    - **BUG**: this message still says `markgate conflicts resolve` — needs updating to `docspan conflicts resolve`
  - `merged` (clean): `[yellow]merging[/yellow]  {local}` + `[green]Merged cleanly.[/green]`
  - `merged` (conflicts): prints conflict count and `Resolve with: markgate conflicts resolve {local}`
    - **BUG**: this message still says `markgate conflicts resolve` — needs updating
  - `error`: `✗  {remote_id} → {local}: {message}`
  - `first-sync` or `fast-forward`: `[green]✓[/green]  {remote_id} → {local}`

---

### `docspan status`

```
docspan status [OPTIONS]
```

**Description**: Show current mapping status.

**Options**:
- `--config`, `-c` TEXT — Path to markgate.yaml

**Output**: Rich table titled `"markgate mappings"` with columns:
- Local file (cyan)
- Backend (magenta)
- Remote ID
- Direction

**BUG**: Table title is `"markgate mappings"` — needs updating to `"docspan mappings"`

If no mappings: `[yellow]No mappings configured.[/yellow] Add entries to markgate.yaml.`

---

### `docspan auth setup`

```
docspan auth setup BACKEND [OPTIONS]
```

**Description**: Interactive authentication setup for a backend.

**Arguments**:
- `BACKEND` — Backend to authenticate: `google_docs | confluence`

**Options**:
- `--config`, `-c` TEXT — Path to markgate.yaml

---

### `docspan conflicts list`

```
docspan conflicts list [OPTIONS]
```

**Description**: List files with unresolved merge conflicts.

**Options**:
- `--config`, `-c` TEXT — Path to markgate.yaml

**Output**: Rich table titled `"Files with merge conflicts"` with columns:
- File (cyan)
- Conflict blocks (red)

If no conflicts: `No unresolved conflicts.`

---

### `docspan conflicts resolve`

```
docspan conflicts resolve FILE [OPTIONS]
```

**Description**: Resolve a merge conflict in a tracked file.

**Arguments**:
- `FILE` — Local file path to resolve

**Options**:
- `--accept` TEXT (required) — Resolution strategy: `remote | local | merged`
- `--config`, `-c` TEXT — Path to markgate.yaml

**Error messages**:
- Invalid `--accept`: `--accept must be one of: remote, local, merged`
- File not tracked: `File '{file}' is not tracked in .markgate-state.json`
  - **NOTE**: references `.markgate-state.json` — this is the state file name constant from `core/paths.py:STATE_FILENAME`

---

## Configuration Reference (markgate.yaml)

The config file is named `markgate.yaml` (intentionally kept — no breaking change).

### Full Schema

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

### Config Field Details

#### `backends.google_docs`
| Field | Type | Default | Description |
|---|---|---|---|
| `credentials_path` | string | null | Path to Google service account JSON key |
| `token_path` | string | `.markgate/google_token.json` | OAuth token storage path (rarely used directly) |

**Environment variable alternatives** (checked if YAML field absent):
- `ACCOUNT_A_CREDENTIALS_PATH` — path to service account JSON
- `ACCOUNT_A_CREDENTIALS` — inline service account JSON string

#### `backends.confluence`
| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | null | Confluence base URL, e.g. `https://yourorg.atlassian.net` |
| `username` | string | null | Atlassian account email |
| `api_token` | string | null | API token from id.atlassian.com |

**Environment variable alternatives**:
- `CONFLUENCE_BASE_URL`
- `ATLASSIAN_USER_NAME`
- `CONFLUENCE_API_TOKEN`

#### `mappings[]`
| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `local` | string | — | yes | Relative path to local markdown file |
| `backend` | string | — | yes | `"google_docs"` or `"confluence"` |
| `remote_id` | string | — | yes | Google Doc ID or Confluence page ID |
| `direction` | enum | `"both"` | no | `"push"`, `"pull"`, or `"both"` |

---

## Auth Setup Instructions (verbatim from auth_setup() output)

### Google Docs

If credentials are NOT already configured:

```
Google Docs Auth Setup
========================================
Markgate uses Google service account credentials for Google Docs access.

Setup steps:
  1. Create a service account at:
     https://console.cloud.google.com/iam-admin/serviceaccounts
  2. Enable Google Docs API and Google Drive API in your project
  3. Download the service account JSON key file
  4. Share your Google Docs with the service account email

Configure credentials via one of:
  Option A — YAML config:
    backends:
      google_docs:
        credentials_path: /path/to/service-account.json
  Option B — environment variable (path):
    export ACCOUNT_A_CREDENTIALS_PATH=/path/to/service-account.json
  Option C — environment variable (inline JSON):
    export ACCOUNT_A_CREDENTIALS='{ ... service account JSON ... }'
```

If credentials ARE already configured, it prints:
```
Google Docs credentials are already configured.
✓ Connection verified successfully.
```
(or `✗ Connection test failed: {error}`)

**NOTE**: The auth_setup() output says "Markgate uses Google service account credentials" — this needs updating to "docspan uses..." for the release.

### Confluence

Interactive prompts (collects base URL, username, API token via getpass), then prints:

```
Confluence auth setup
========================================
[interactive prompts for: base URL, username, API token]

Add to markgate.yaml:

backends:
  confluence:
    base_url: {entered_url}
    username: {entered_username}
    api_token: {entered_token}

Done. Test with: markgate status
```

**NOTE**: "Done. Test with: markgate status" needs updating to "docspan status". Also says "Add to markgate.yaml" — this is intentionally kept (config file stays named markgate.yaml, so this is correct).

---

## State Files (Generated at Runtime)

These files appear in the project directory after first sync — document them in README:

- `.markgate-state.json` — sync state tracking (intentionally named markgate, not changing)
- `.markgate-base/` — content-addressed store of merge bases
- `{local}.orig` — backup of local file before merge (deleted after conflict resolution)
- `{local}.comments.md` — Confluence comment sidecar (written during pull)
