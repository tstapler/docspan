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

Every credential path (`GoogleAuthenticator`, `OAuthAuthenticator`) requests the same read/write scopes (`PUSH_SCOPES`, aliased as `SCOPES`/`DEFAULT_SCOPES`), whether the operation is push or pull:

- `https://www.googleapis.com/auth/documents` — read and write Google Docs
- `https://www.googleapis.com/auth/drive` — read/write Drive (comment reads/writes, file metadata; not just export)
- `https://www.googleapis.com/auth/spreadsheets.readonly` — read Sheets embedded/linked in a Doc

`auth.py` also defines a narrower read-only `PULL_SCOPES`, but nothing in the codebase wires it up today — pull requests the same full grant as push, not a readonly subset. Comment reads/writes reuse this same grant too; no separate scope is added for them.

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
    - **Images push and pull**: `![alt](./local.png)` uploads the local file to Drive and references it by URI; an `https://` URL is referenced directly, bypassing upload. Only a standalone image on its own line is supported — one mixed into a paragraph alongside running text is left as plain text. Missing files, files over 50MB, and unsupported formats (SVG) are reported as push warnings rather than blocking the write or crashing.
    - **Pulled image URIs can go stale**: a pulled `![alt](src)` link is Google's `contentUri` for that embedded object, which Google's API docs say may change over time even when the image itself is unchanged. The push structural diff keys image identity on `alt`/width/height, not this URI, so a rotated `contentUri` alone will not cause the paragraph to be deleted and reinserted (which would destroy any comment anchored to it) — but the stale URI persisted in your markdown file can still surface as a one-sided edit in `docspan conflicts resolve`'s three-way diff.
    - **Table cells hold one paragraph**: a markdown table cell is pushed as a single
      paragraph, and inline formatting inside it (bold, monospace, links, internal
      `#anchor` references) is applied on the second pass. Two limits follow: a cell
      whose content spans more than one paragraph in the Doc cannot be styled, and a
      table created by the current push gets its cell styling on the *next* push —
      docspan reports both rather than failing silently.
    - **Rate limiting**: The Google Docs API allows 300 requests per minute per project. Large documents with many changed paragraphs may trigger rate limit errors.
