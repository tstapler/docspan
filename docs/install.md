# Install

## Requirements

Python 3.9 or later.

## Install via pip

```bash
pip install docspan
```

## Install via uv

```bash
uv add docspan
```

## Install via uv tool / uvx

```bash
uv tool install docspan
```

To run it without installing, use `uvx docspan`.

---

## Google Docs Auth Setup

docspan uses Google service account credentials for Google Docs access. If credentials are not yet configured, `docspan auth setup google_docs` prints the following instructions:

```
Google Docs Auth Setup
========================================
docspan uses Google service account credentials for Google Docs access.

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

If credentials are already configured, running `docspan auth setup google_docs` tests the connection and reports success or failure.

### Required API scopes

The service account requires:

- `https://www.googleapis.com/auth/documents` — read and write Google Docs
- `https://www.googleapis.com/auth/drive.readonly` — list and read Drive files

---

## Confluence Auth Setup

Run `docspan auth setup confluence` and follow the interactive prompts:

```
Confluence auth setup
========================================
Confluence base URL (e.g. https://yourorg.atlassian.net): https://yourorg.atlassian.net
Atlassian username (email): you@example.com
API token (from id.atlassian.com/manage-profile/security/api-tokens): ••••••••
```

After entering credentials, the command prints a YAML snippet to add to `markgate.yaml`:

```yaml
backends:
  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@example.com
    api_token: <your-token>
```

### Generating an Atlassian API token

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a label (e.g. `docspan`) and copy the token

### Environment variable alternatives

Instead of storing credentials in `markgate.yaml`, you can export:

```bash
export CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
export ATLASSIAN_USER_NAME=you@example.com
export CONFLUENCE_API_TOKEN=your-token
```

---

## Note about `markgate.yaml`

The config file is gitignored by default because it may contain API tokens. Use the provided `markgate.yaml.example` as a template and add your own `markgate.yaml` locally.
