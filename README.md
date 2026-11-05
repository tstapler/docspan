# docspan

[![PyPI](https://img.shields.io/pypi/v/docspan)](https://pypi.org/project/docspan/)
[![Python](https://img.shields.io/pypi/pyversions/docspan)](https://pypi.org/project/docspan/)
[![CI](https://github.com/tstapler/docspan/actions/workflows/ci.yml/badge.svg)](https://github.com/tstapler/docspan/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Keep markdown files in git and in sync with Google Docs and Confluence.** Write in your editor, review in git, and let teammates who live in Docs or Confluence keep commenting there.

- **Two-way sync.** `push`, `pull`, or `sync` (pull, then push) per mapping.
- **Three-way merge.** When both sides changed, docspan merges and writes conflict markers you resolve with `docspan conflicts resolve`.
- **Comment-safe pushes (Google Docs).** A paragraph-level structural diff sends minimal `batchUpdate` requests, so comments on unchanged paragraphs survive. `push` warns before it would destroy a comment and blocks unless you pass `--force`.
- **Rich content.** Images, tables, blockquotes, fenced code, and mermaid diagrams (rendered to PNG) on Google Docs; multi-tab docs and sectioned (one file per heading) mappings too.
- **Comment sidecars.** Pull writes `{file}.comments.md` for both backends; on Google Docs only, reply to or resolve comments from it with `docspan comments respond`.

| Backend | Push | Pull | Auth |
|---|---|---|---|
| Google Docs | yes | yes | Service account or per-user OAuth |
| Confluence | yes | yes | Atlassian API token |

## Install

Requires Python 3.9+.

```bash
pip install docspan        # or: uv tool install docspan
```

Mermaid rendering on Google Docs needs [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (`npm install -g @mermaid-js/mermaid-cli`). See [Install](docs/install.md#optional-mermaid-diagram-rendering-google-docs-only) for setup and troubleshooting.

## Quick start

**1. Create `markgate.yaml`** (`docspan.yaml` is also recognized). This example uses a service account; for per-user OAuth (recommended for personal use) set `oauth_client_secret_path` instead — see [Install](docs/install.md#google-docs-auth-setup):

```yaml
backends:
  google_docs:
    credentials_path: /path/to/service-account.json

mappings:
  - local: docs/design-doc.md
    backend: google_docs
    remote_id: YOUR_GOOGLE_DOC_ID
    direction: both
```

No doc yet? `docspan map docs/design-doc.md --backend google_docs` creates one and adds the mapping for you.

**2. Authenticate:**

```bash
docspan auth setup google_docs   # or: confluence
```

**3. Sync:**

```bash
docspan status                   # show mappings
docspan push --dry-run           # preview, including comments at risk
docspan push                     # push all mappings
docspan pull                     # pull all mappings, three-way merging
```

**4. Resolve conflicts, if any:**

```bash
docspan conflicts list
docspan conflicts resolve docs/design-doc.md --accept remote   # or local, merged
```

## Commands

| Command | What it does |
|---|---|
| `docspan push [FILES]...` | Push local markdown to remote docs (`--dry-run`, `--force`; `--fail-on-comment-loss` exits non-zero when a blockquote restyle rewrites a commented paragraph) |
| `docspan pull [FILES]...` | Pull remote docs into local markdown, with three-way merge |
| `docspan sync [FILES]...` | Pull, then push, each mapping |
| `docspan status` | Show configured mappings |
| `docspan map FILE --backend BACKEND` | Create a remote doc or page and map it to a file |
| `docspan migrate-sectioned FILE` | Split a single-file mapping into a sectioned one |
| `docspan style-guide` | Print authoring guidance for a backend |
| `docspan auth setup BACKEND` | Interactive auth setup |
| `docspan comments respond FILE` | Post `Reply:`/`Resolve:` directives from a `.comments.md` sidecar |
| `docspan conflicts list` / `resolve` | List and resolve merge conflicts |
| `docspan config show` / `add` | View or register projects in the central config |
| `docspan migrate-xdg` | Move legacy in-repo state to XDG directories |

Every option is documented in the [command reference](docs/commands.md), generated from the CLI source. Run `pip install docspan[docs] && mkdocs serve` to browse the docs site locally.

## Configuration

A mapping ties one local file to one remote doc:

| Field | Default | Description |
|---|---|---|
| `local` | required | Relative path to the markdown file (a directory when `sectioned`) |
| `backend` | required | `google_docs` or `confluence` |
| `remote_id` | | Google Doc ID or Confluence page ID |
| `direction` | `both` | `push`, `pull`, or `both` |
| `tab_id` | | Google Docs tab to target on a multi-tab doc |
| `pageless` | | Google Docs: force pageless (`true`) or paged (`false`) on each push |
| `sectioned`, `split_level` | `false` | Google Docs: sync as one file per heading section |

Credentials can also come from environment variables (`ACCOUNT_A_CREDENTIALS_PATH`, `CONFLUENCE_API_TOKEN`, and others). The [configuration reference](docs/configuration.md) lists every field and variable. `markgate.yaml` can contain secrets and is gitignored by default; commit `markgate.yaml.example` instead.

### Central config

Register several projects once and run docspan from anywhere. Config, OAuth tokens, and sync state live under the [XDG directories](https://specifications.freedesktop.org/basedir-spec/latest/):

```bash
docspan config add design-docs ~/Documents/design-docs/markgate.yaml
docspan push --prefix design-docs
```

Resolution order: `--config` → `--prefix` → `DOCSPAN_PREFIX` → cwd inside a registered project → `default_prefix` → local `./markgate.yaml`.

### State files

In legacy mode (`--config`, or the local `./markgate.yaml`), docspan writes `.markgate-state.json` and `.markgate-base/` (merge bases) beside the config. **Commit them**: three-way merge across machines and teammates depends on them. With a central-config prefix they live under `$XDG_STATE_HOME/docspan/<prefix>/` and are per-machine. Either way, commit `{file}.mermaid-cache.yaml` (written for files with mermaid fences); `{file}.orig` (pre-merge backup) and `{file}.comments.md` are transient and safe to gitignore.

## Known limitations

- **Edited paragraphs lose their Google Docs comments on push.** Unchanged paragraphs keep theirs. `push --dry-run` lists every at-risk comment; a real push blocks unless `--force`. Re-anchoring was investigated and rejected ([ADR-003](project_plans/wedding-planning-workflow/decisions/ADR-003-no-comment-anchor-migration.md)).
- **Google Docs images** must sit alone on their own line. SVG, missing, and oversized (>50 MB) images produce warnings.
- **Confluence:** no image push, no OAuth (API token only), and comments are pull-only.
- **Checklists** round-trip as literal `- [ ]` / `- [x]` text, because Google's checkbox state can't be read back via the API.
- **Google Docs OAuth** needs your own GCP project and stays in Google's "Testing" status (100 test users, "unverified app" warning). This avoids a paid annual security assessment.

More detail, including mermaid, table-cell, blockquote, and multi-tab behavior, is in the backend notes: [Google Docs](docs/backends/google-docs.md), [Confluence](docs/backends/confluence.md).

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md); report vulnerabilities per [SECURITY.md](SECURITY.md). Release history is in the [changelog](CHANGELOG.md).

## License

[MIT](LICENSE)
