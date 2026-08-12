# Confluence Backend

## How it works

The Confluence backend authenticates via an Atlassian API token using HTTP Basic auth. Push converts local markdown to Atlassian Document Format (ADF) and replaces the full page body via the Confluence REST API. Pull fetches the page's Storage Format (HTML-like) and converts it to markdown using `markdownify`. If the page has inline or footer comments, they are written to a `{file}.comments.md` sidecar file.

## Auth Setup

Run `docspan auth setup confluence` to enter credentials interactively:

```
Confluence auth setup
========================================
Confluence base URL (e.g. https://yourorg.atlassian.net): https://yourorg.atlassian.net
Atlassian username (email): you@example.com
API token (from id.atlassian.com/manage-profile/security/api-tokens): ••••••••

Add to markgate.yaml:

backends:
  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@example.com
    api_token: <your-token>

Done. Test with: docspan status
```

## API Token Setup

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a descriptive label (e.g. `docspan`) and copy the generated token

## `markgate.yaml` Example

```yaml
backends:
  confluence:
    base_url: https://yourorg.atlassian.net
    username: you@example.com
    api_token: your-api-token   # or use env CONFLUENCE_API_TOKEN

mappings:
  - local: docs/confluence-page.md
    backend: confluence
    remote_id: "123456"
    direction: both
```

## Environment Variables

Instead of storing credentials in `markgate.yaml`:

```bash
export CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
export ATLASSIAN_USER_NAME=you@example.com
export CONFLUENCE_API_TOKEN=your-token
```

## Limitations

!!! warning
    - **API token required**: OAuth flow is not supported. You must generate an API token at id.atlassian.com.
    - **Comment sidecar is informational only**: Comments pulled from Confluence are written to `{file}.comments.md` but cannot be pushed back via docspan.
    - **Push replaces page content**: The full page body is replaced on every push. Inline comment positions in Confluence may shift after a push.
    - **Complex macros not preserved faithfully**: Confluence macros (status, panels, expand, etc.) are converted to approximate markdown equivalents on pull and may not round-trip cleanly on push.
    - **Mermaid diagrams are not rendered**: a ` ```mermaid ` fence is pushed as a plain ADF code block (language `mermaid`), i.e. the raw diagram source as visible text — not an image, and not Confluence's native mermaid macro. `render_mermaid_diagrams` in `PublishConfig` currently has no effect on this: the code path that would rasterize a diagram (`MermaidParser.render_diagram()` in `markdown/extensions/mermaid.py`) is a stub that never runs — it's dead code, unreachable from the real parse pipeline (`markdown/parser.py` builds `MermaidNode` directly). Fixing this requires a real renderer (e.g. `mermaid-cli` or a hosted rendering service) wired to upload the result as a Confluence attachment before ADF conversion; tracked as follow-up work, not yet implemented.
