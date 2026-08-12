# Google Docs Backend

## How it works

The Google Docs backend authenticates either via a Google service account JSON key or via per-user OAuth (an `InstalledAppFlow` that acts as you, similar to `gws`) — whichever `markgate.yaml` configures (`credentials_path` for the service account, `oauth_client_secret_path` for OAuth). Push uses a paragraph-level structural diff that computes the minimal set of `batchUpdate` requests needed to transform the current document into the target content. This approach preserves comments attached to paragraphs that have not changed. Pull exports the Google Doc as HTML and converts it to markdown.

## Auth Setup

Run `docspan auth setup google_docs` to see setup instructions.

```
Google Docs Auth Setup
========================================
Run this in an interactive terminal for a guided setup, or configure manually:

  Per-user OAuth (recommended — acts as you, like gws):
    1. Create an OAuth client (Desktop app); download client_secret.json
    2. docspan auth setup google_docs --oauth --client-secret /path/to/client_secret.json
       (or set backends.google_docs.oauth_client_secret_path in markgate.yaml)

  Service account (automation):
    1. Create a service account + JSON key; enable the Docs & Drive APIs
    2. Share your docs with the service-account email
    3. Set credentials_path in markgate.yaml (or ACCOUNT_A_CREDENTIALS_PATH env)
```

Service account credentials can also be provided inline via `ACCOUNT_A_CREDENTIALS` (the JSON itself, not a path) instead of `ACCOUNT_A_CREDENTIALS_PATH`.

## Required Scopes

docspan requests different scopes depending on the operation:

- **Push** (`PUSH_SCOPES`) needs write access, since it can create/update Docs content and comments:
  - `https://www.googleapis.com/auth/documents` — read and write Google Docs
  - `https://www.googleapis.com/auth/drive` — read/write Drive (comment reads/writes, file metadata; not just export)
  - `https://www.googleapis.com/auth/spreadsheets.readonly` — read Sheets embedded/linked in a Doc
- **Pull** (`PULL_SCOPES`) is read-only:
  - `https://www.googleapis.com/auth/documents.readonly`
  - `https://www.googleapis.com/auth/drive.readonly`
  - `https://www.googleapis.com/auth/spreadsheets.readonly`

No scope beyond the full `drive` scope above is added for comment operations — comment reads/writes reuse the same `PUSH_SCOPES` grant.

## `markgate.yaml` Example

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json
    # token_path: .markgate/google_token.json  # default, rarely changed

mappings:
  - local: docs/design-doc.md
    backend: google_docs
    remote_id: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74O
    direction: both
```

## Limitations

!!! warning
    - **Comments destroyed on push for edited paragraphs**: The structural diff preserves comments on unchanged paragraphs, but any paragraph that is deleted and reinserted loses its comments. This is a known v0.1.0 limitation.
    - **No image push support**: Local image files cannot be pushed. Images require publicly accessible URLs and additional Drive upload scope.
    - **Table cells hold one paragraph**: a markdown table cell is pushed as a single
      paragraph, and inline formatting inside it (bold, monospace, links, internal
      `#anchor` references) is applied on the second pass. Two limits follow: a cell
      whose content spans more than one paragraph in the Doc cannot be styled, and a
      table created by the current push gets its cell styling on the *next* push —
      docspan reports both rather than failing silently.
    - **Rate limiting**: The Google Docs API allows 300 requests per minute per project. Large documents with many changed paragraphs may trigger rate limit errors.
