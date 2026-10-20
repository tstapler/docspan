# Pitfalls Research — docspan v0.1.0 Release

## Complete "markgate" String Occurrences Needing Update

### src/docspan/cli/main.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 1 | Module docstring | `"""markgate CLI — push, pull, auth, status, conflicts."""` | Update to `docspan CLI` |
| 30-35 | typer.Typer() | `name="markgate"` | Change to `name="docspan"` — this controls `--help` display name |
| 81 | push() option | `help="Path to markgate.yaml"` | Keep as-is (config file stays named markgate.yaml) |
| 95 | push() error msg | `"No mappings configured. Add entries to markgate.yaml."` | Keep as-is |
| 177 | pull() output | `"Push first or use 'markgate conflicts resolve'."` | **MUST CHANGE** to `docspan conflicts resolve` |
| 183-184 | pull() output | `f"Resolve with: markgate conflicts resolve {mapping.local}"` | **MUST CHANGE** to `docspan conflicts resolve` |
| 219 | status() | `Table(title="markgate mappings")` | **MUST CHANGE** to `"docspan mappings"` |
| 216 | status() | `"Add entries to markgate.yaml."` | Keep as-is |

### src/docspan/config.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 1 | Module docstring | `"""markgate.yaml loader and config model."""` | Keep as-is (the file IS still named markgate.yaml) |
| 12 | constant | `CONFIG_FILENAME = "markgate.yaml"` | Keep as-is — intentional, no breaking change |
| 17 | GoogleDocsConfig | `token_path: Optional[str] = ".markgate/google_token.json"` | Keep as-is |
| 44 | load_config() docstring | `"""Load markgate.yaml, falling back to env vars..."""` | Optional: update to "Load markgate.yaml (docspan config)..." |

### src/docspan/backends/confluence/backend.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 53 | _ensure_client() RuntimeError | `"Run: markgate auth setup confluence"` | **MUST CHANGE** to `docspan auth setup confluence` |
| 206 | auth_setup() output | `f"\nAdd to markgate.yaml:\n\n..."` | Keep as-is (the file IS still markgate.yaml) |
| 212 | auth_setup() output | `"Done. Test with: markgate status"` | **MUST CHANGE** to `docspan status` |
| 229 | validate_config() ValueError | `"Run: markgate auth setup confluence"` | **MUST CHANGE** to `docspan auth setup confluence` |

### src/docspan/backends/google_docs/backend.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 46 | _ensure_client() RuntimeError | `"Run: markgate auth setup google_docs"` | **MUST CHANGE** to `docspan auth setup google_docs` |
| 113 | auth_setup() output | `"Markgate uses Google service account credentials..."` | **MUST CHANGE** to `"docspan uses Google service account credentials..."` |
| 140 | validate_config() ValueError | `"Run: markgate auth setup google_docs"` | **MUST CHANGE** to `docspan auth setup google_docs` |

### src/docspan/backends/base.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 51-52 | Backend class docstring | `"# Register in src/markgate/backends/__init__.py"` | Update to `src/docspan/backends/__init__.py` |

### src/docspan/core/orchestrator.py

No "markgate" string occurrences — clean.

### src/docspan/core/paths.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 3 | STATE_FILENAME | `".markgate-state.json"` | Keep as-is — intentional backward-compat |
| 4 | BASE_STORE_DIR | `".markgate-base"` | Keep as-is — intentional backward-compat |
| 7 | GOOGLE_TOKEN_PATH | `".markgate/google_token.json"` | Keep as-is — intentional backward-compat |

### src/docspan/__init__.py

| Line | Type | Current String | Action Required |
|---|---|---|---|
| 1 | Module docstring | `"""markgate — push and pull markdown..."""` | **MUST CHANGE** to `"""docspan — push and pull markdown..."""` |

### Summary: Strings That MUST Change (User-Visible)

1. `cli/main.py:30` — `name="markgate"` → `name="docspan"`
2. `cli/main.py:177` — `'markgate conflicts resolve'` → `'docspan conflicts resolve'`
3. `cli/main.py:184` — `markgate conflicts resolve` → `docspan conflicts resolve`
4. `cli/main.py:219` — `"markgate mappings"` → `"docspan mappings"`
5. `backends/confluence/backend.py:53` — `markgate auth setup confluence` → `docspan auth setup confluence`
6. `backends/confluence/backend.py:212` — `markgate status` → `docspan status`
7. `backends/confluence/backend.py:229` — `markgate auth setup confluence` → `docspan auth setup confluence`
8. `backends/google_docs/backend.py:46` — `markgate auth setup google_docs` → `docspan auth setup google_docs`
9. `backends/google_docs/backend.py:113` — `Markgate uses` → `docspan uses`
10. `backends/google_docs/backend.py:140` — `markgate auth setup google_docs` → `docspan auth setup google_docs`
11. `__init__.py:1` — `markgate —` → `docspan —`

### Summary: Strings to Keep As-Is (Config Backward-Compat)

- `CONFIG_FILENAME = "markgate.yaml"` — config file keeps its name
- `STATE_FILENAME = ".markgate-state.json"` — state file keeps its name
- `BASE_STORE_DIR = ".markgate-base"` — base store keeps its name
- `GOOGLE_TOKEN_PATH = ".markgate/google_token.json"` — token path keeps its name
- All help text referencing `markgate.yaml` as the config file

---

## pyproject.toml Findings

The scripts entry point is already correct:
```toml
[project.scripts]
docspan = "docspan.cli.main:app"
```

No change needed here. The CLI binary is named `docspan`.

The app name inside typer (`name="markgate"`) is the only remaining rename needed.

---

## Known Limitations to Document

### Google Docs Backend

From `docs/google-docs-push.md` and code inspection:

1. **Comments destroyed on push**: Every push to Google Docs destroys all comments and suggestions on the document. The structural diff approach (Option B) was implemented but still clears content due to delete-and-reinsert approach. This is documented in `docs/google-docs-push.md` as a known v1 limitation.

2. **No image push**: Images require publicly accessible URLs. Local image files cannot be pushed. `ImageNode` in the request builder notes this requires Drive upload scope.

3. **No table push**: `TableNode` mapping is deferred. Tables in markdown are not rendered in Google Docs. Documented as "deferred until Phase 1 and 2 are stable."

4. **Rate limiting**: Docs API rate limit is 300 requests/minute/project. Large documents may need backoff.

5. **OAuth scopes**: Push requires `documents` (read-write) + `drive.readonly` scopes. The setup instructions must clearly state this.

### Confluence Backend

6. **API token required**: Confluence requires an Atlassian API token (not password). Users must generate this at `id.atlassian.com/manage-profile/security/api-tokens`. The auth_setup() prompt mentions this URL — it's correct.

7. **Comments are read-only sidecar**: On pull, Confluence inline and footer comments are written to a `{file}.comments.md` sidecar. Comments cannot be pushed back — the sidecar is informational only.

8. **Push replaces page content**: Confluence push replaces the full page body. Inline comments in Confluence that reference text positions may become misaligned after push.

9. **Storage format conversion**: Pull converts Confluence Storage Format (HTML-like) via `markdownify`. Complex Confluence macros (status, panels, expand) are not preserved faithfully.

---

## PyPI Release Checklist Pitfalls

### Classifiers
- `Development Status :: 3 - Alpha` is correct for v0.1.0 — do not change to Beta/Stable yet.
- Python version classifiers go up to 3.12 in pyproject.toml. If Python 3.13 is supported, add the classifier.
- The `License :: OSI Approved :: MIT License` classifier is present — correct.

### README Rendering
- PyPI uses `readme_renderer` to render README.md
- Run `twine check dist/*` before uploading to catch rendering issues
- Ensure README.md uses standard Markdown (no GitHub-specific extensions like `:::note`)
- Avoid relative links to local files (e.g., `[CONTRIBUTING](CONTRIBUTING.md)`) — these 404 on PyPI; use full GitHub URLs or skip them in the README body

### License Field
- Current: `license = {text = "MIT"}` — valid but old-style
- Modern PyPI prefers: `license = "MIT"` (PEP 639 SPDX form)
- Both are accepted; changing to SPDX form is optional

### Build Artifacts
- `hatch-vcs` generates version from git tags — must tag `v0.1.0` BEFORE building
- Check `src/docspan/__init__.py` has `__version__ = "0.1.0"` — it does, but hatch-vcs may override at build time
- The wheel will be named `docspan-0.1.0-py3-none-any.whl` — verify before upload
- Run `uv build` to produce `dist/` artifacts
- Never upload `.egg-info` or `dist/` directories to git (add to `.gitignore`)

### Trusted Publishing (Recommended over API tokens)
- Configure OIDC trusted publishing at pypi.org before first upload
- Avoids storing long-lived API tokens in GitHub Secrets
- Requires setting `environment: release` in the GitHub Actions workflow

### First-Time Publish
- Package name `docspan` must be claimed on PyPI — first publish reserves it
- Test with TestPyPI first: `uv publish --publish-url https://test.pypi.org/legacy/`

---

## MkDocs Pitfalls

### Navigation Structure
- If `nav:` is specified in `mkdocs.yml`, every page must be listed or it won't appear in the nav (MkDocs 1.x behavior)
- If `nav:` is omitted, MkDocs auto-generates nav from the `docs/` directory structure
- For v0.1.0 with few pages, omitting `nav:` is simpler and less error-prone

### GitHub Pages Branch
- `mkdocs gh-deploy` pushes to the `gh-pages` branch by default
- GitHub repository must have GitHub Pages enabled:
  - Settings → Pages → Source: Deploy from a branch → Branch: `gh-pages` / root
- The `gh-pages` branch will be created automatically by `gh-deploy` on first run
- If the repo is private, GitHub Pages requires a paid plan

### Material Theme Version Pinning
- Pin `mkdocs-material` to a specific minor version (e.g., `>=9.5.0,<10`) to avoid surprise breaking changes in the docs build
- Material 9.x is stable; Material 10.x (if released) may have breaking nav changes
- The `insiders` variant of Material requires sponsorship — use the public `mkdocs-material` package

### docs/ Directory Conflict
- The existing `docs/` directory contains `google-docs-push.md` (an internal design doc)
- This doc should either be:
  a. Moved to `project_plans/` or `docs/design/` and excluded from MkDocs nav
  b. Or kept in `docs/` and included in MkDocs nav as a "Design Notes" page
- If MkDocs is configured with `docs_dir: docs` (default), ALL `.md` files in `docs/` will be included unless explicitly excluded

### Site URL and Canonical Links
- Set `site_url` in `mkdocs.yml` to enable canonical link tags and sitemap
- Without it, MkDocs warns and sitemap.xml may be broken
- For GitHub Pages: `site_url: https://tstapler.github.io/docspan`

### .gitignore
- Add `site/` to `.gitignore` — MkDocs builds to `site/` locally but `gh-deploy` handles the branch separately
- The `site/` directory should never be committed to `main`
