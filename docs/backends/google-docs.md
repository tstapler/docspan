# Google Docs Backend

## How it works

The Google Docs backend authenticates via a Google service account JSON key. Push uses a paragraph-level structural diff that computes the minimal set of `batchUpdate` requests needed to transform the current document into the target content. This approach preserves comments attached to paragraphs that have not changed. Pull exports the Google Doc as HTML and converts it to markdown.

## Auth Setup

Run `docspan auth setup google_docs` to see setup instructions.

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

## Required Scopes

The service account requires:

- `https://www.googleapis.com/auth/documents` — read and write Google Docs
- `https://www.googleapis.com/auth/drive.readonly` — read Drive files for export

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
