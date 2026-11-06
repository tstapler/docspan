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

## Optional: mermaid diagram rendering (Google Docs only)

Pushing a ` ```mermaid ` fence to Google Docs renders it to a PNG via the official [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (`mmdc`), which wraps Puppeteer/headless Chrome. It is a Node.js dependency, not declared in `pyproject.toml`. Install it globally so docspan finds a real binary instead of falling back to `npx`, which re-fetches on every render:

```bash
npm install -g @mermaid-js/mermaid-cli   # tested against 11.x; older 10.x should also work
```

If Puppeteer's Chrome cache is corrupted (a truncated download or interrupted install), renders fail with `Could not find chrome-headless-shell`. Re-fetch it with:

```bash
npx puppeteer browsers install chrome-headless-shell
```

Without `mmdc` and without network access for `npx`, a render failure is reported as a push warning, not a crash.

---

## Google Docs Auth Setup

docspan supports per-user OAuth (acts as you) and Google service accounts. Run `docspan auth setup google_docs` for setup instructions, or a guided flow in an interactive terminal:

```
Per-user OAuth (recommended — acts as you, like gws):
  1. Create an OAuth client (Desktop app); download client_secret.json
  2. docspan auth setup google_docs --oauth --client-secret /path/to/client_secret.json
     (or set backends.google_docs.oauth_client_secret_path in docspan.yaml)

Service account (automation):
  1. Create a service account + JSON key; enable the Docs & Drive APIs
  2. Share your docs with the service-account email
  3. Set credentials_path in docspan.yaml (or ACCOUNT_A_CREDENTIALS_PATH env)
```

If credentials are already configured, `docspan auth setup google_docs` tests the connection and reports success or failure. The scopes requested are listed on the [Google Docs backend](backends/google-docs.md#required-scopes) page.

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

After entering credentials, the command prints a YAML snippet to add to `docspan.yaml`:

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

Instead of storing credentials in `docspan.yaml`, you can export:

```bash
export CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
export ATLASSIAN_USER_NAME=you@example.com
export CONFLUENCE_API_TOKEN=your-token
```

---

## Note about `docspan.yaml`

The config file is gitignored by default because it may contain API tokens. Use `docspan.yaml.example` as a template and add your own copy locally.
