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
    - **Mermaid diagrams push as rendered PNGs**: a fenced ` ```mermaid ` block is rendered to a raster PNG (via `mermaid-cli`/`mmdc`, shelled out to — install with `npm install -g @mermaid-js/mermaid-cli`, or it's fetched on demand through `npx`) and pushed as an inline image, since `insertInlineImage` has no native mermaid or SVG support. A render failure (missing Node.js/mermaid-cli, invalid diagram syntax, timeout) is reported as a push warning, not a crash. There is no pull-side reconstruction — a mermaid diagram round-trips back to markdown as a plain image reference, not a ` ```mermaid ` fence.
    - **Pulled image URIs can go stale**: a pulled `![alt](src)` link is Google's `contentUri` for that embedded object, which Google's API docs say may change over time even when the image itself is unchanged. The push structural diff keys image identity on `alt`/width/height, not this URI, so a rotated `contentUri` alone will not cause the paragraph to be deleted and reinserted (which would destroy any comment anchored to it) — but the stale URI persisted in your markdown file can still surface as a one-sided edit in `docspan conflicts resolve`'s three-way diff.
    - **Table cells hold one paragraph**: a markdown table cell is pushed as a single
      paragraph, and inline formatting inside it (bold, monospace, links, internal
      `#anchor` references) is applied on the second pass. Two limits follow: a cell
      whose content spans more than one paragraph in the Doc cannot be styled, and a
      table created by the current push gets its cell styling on the *next* push —
      docspan reports both rather than failing silently.
    - **Rate limiting**: The Google Docs API allows 300 requests per minute per project. Large documents with many changed paragraphs may trigger rate limit errors.
    - **Blockquotes render as an indented, left-bordered callout**: a Markdown `> ...` line is pushed as a paragraph with native `indentStart`/`borderLeft` styling (no literal `>` text), and pulling that paragraph back reconstructs the `> ` prefix from that styling, byte-for-byte, independent of the visual border. A Doc that still has a *pre-migration* blockquote (pushed by an older docspan version as literal `>` text) keeps rendering as plain text until the file containing it is pushed again for any reason — at that point every legacy blockquote in the file is deleted and reinserted with native styling in that same push, which, per the comments limitation above, destroys any comment anchored to one of those paragraphs. This is a one-time cost per file, not a recurring one. Very large files with many legacy blockquotes could in principle hit the Google Docs API's `batchUpdate` payload-size cap during that one migrating push; this has not been reproduced or quantified against a real document.

        Both `docspan push --dry-run` and a real `docspan push` print a `STYLE_UPGRADE_COUNT=<N>` line for each run, counting the legacy blockquote paragraphs about to be (or that were) rewritten to native styling — `0` when there are none. This is a plain, machine-parsable line meant for CI: grep it out of the output rather than parsing the human-readable `⚠` warnings above it. Pass `--fail-on-comment-loss` to make `push` exit non-zero when `STYLE_UPGRADE_COUNT` is greater than zero; without the flag the count is reporting-only and never affects the exit code or blocks the write.
