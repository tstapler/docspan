# Stack Research — docspan v0.1.0 Release

## Current Project Metadata (pyproject.toml)

- **Package name**: `docspan` (already renamed from markgate in pyproject.toml)
- **Version**: Dynamic via `hatch-vcs` (git tags). `__version__ = "0.1.0"` is hardcoded in `src/docspan/__init__.py`.
- **Description**: "Push and pull markdown to Google Docs and Confluence from a single CLI"
- **Python requires**: `>=3.9`
- **License**: `{text = "MIT"}` — uses inline text form (not SPDX expression)
- **Build backend**: `hatchling` + `hatch-vcs`

## Scripts Entry Point

```toml
[project.scripts]
docspan = "docspan.cli.main:app"
```

The entry point is already correct — `docspan` maps to `docspan.cli.main:app`. No change needed in pyproject.toml for the CLI binary name.

## Key Dependencies

### Runtime
- `typer>=0.9.0` — CLI framework
- `rich>=13.0.0` — terminal output
- `PyYAML>=6.0` — config file parsing
- `pydantic>=2.0.0` — config models
- `google-auth>=2.23.0`, `google-auth-oauthlib>=1.1.0`, `google-auth-httplib2>=0.1.1`, `google-api-python-client>=2.108.0` — Google Docs backend
- `markdownify>=0.11.6` — HTML-to-markdown for pull
- `requests>=2.25.0`, `httpx>=0.24.0` — Confluence backend HTTP
- `python-dateutil>=2.8.2` — date utilities
- `merge3>=0.0.16` — three-way merge
- `mistune>=3.0` — markdown parsing

### Dev
- `pytest>=7.0.0`, `pytest-cov>=4.0.0`
- `ruff>=0.1.0`
- `mypy>=1.0.0`
- `types-PyYAML>=6.0.0`, `types-requests>=2.25.0`

## Classifiers (Already Present)

```toml
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Documentation",
    "Topic :: Text Processing :: Markup :: Markdown",
]
```

Missing: `Programming Language :: Python :: 3.13` (if supported) and `Topic :: Internet`. The `Development Status :: 3 - Alpha` is appropriate for v0.1.0.

## Project URLs (Already Set)

```toml
[project.urls]
Homepage = "https://github.com/tstapler/docspan"
Repository = "https://github.com/tstapler/docspan"
Issues = "https://github.com/tstapler/docspan/issues"
Changelog = "https://github.com/tstapler/docspan/releases"
```

No changes needed. PyPI will render these in the sidebar.

## Changes Needed in pyproject.toml for v0.1.0 Release

1. **Version**: Currently dynamic (`hatch-vcs`). For a clean v0.1.0 release, create a git tag `v0.1.0`. The `__version__` in `src/docspan/__init__.py` is hardcoded to `"0.1.0"` but `hatch-vcs` will override this at build time from the git tag. These must stay in sync.
2. **README**: `readme = "README.md"` — README.md must exist and be valid Markdown. PyPI renders it via `readme_renderer`.
3. **License field**: `license = {text = "MIT"}` — acceptable for PyPI but the modern SPDX form is preferred: `license = "MIT"`. Either works.
4. **No other structural changes needed** — the package name, entry point, and URLs are already correct.

## MkDocs + Material Theme

### Dependencies to Add (dev optional)
```toml
[project.optional-dependencies]
docs = [
    "mkdocs>=1.5.0",
    "mkdocs-material>=9.0.0",
]
```

### mkdocs.yml Structure
```yaml
site_name: docspan
site_description: Push and pull markdown to Google Docs and Confluence
site_url: https://tstapler.github.io/docspan
repo_url: https://github.com/tstapler/docspan
repo_name: tstapler/docspan

theme:
  name: material
  palette:
    - scheme: default
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - scheme: slate
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
  features:
    - navigation.tabs
    - navigation.sections
    - content.code.copy

nav:
  - Home: index.md
  - Getting Started: getting-started.md
  - Commands: commands.md
  - Configuration: configuration.md
  - Backends:
    - Google Docs: backends/google-docs.md
    - Confluence: backends/confluence.md
  - Contributing: contributing.md
  - Changelog: changelog.md

markdown_extensions:
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences
  - admonition
  - tables
```

### docs/ Directory Structure Required
```
docs/
├── index.md           # landing page (mirrors README)
├── getting-started.md
├── commands.md        # full CLI reference
├── configuration.md   # markgate.yaml reference
├── backends/
│   ├── google-docs.md
│   └── confluence.md
├── contributing.md
└── changelog.md
```

## PyPI Publish Workflow (uv + twine)

### Build
```bash
uv build
# produces dist/docspan-0.1.0-py3-none-any.whl and dist/docspan-0.1.0.tar.gz
```

### Check README renders
```bash
pip install twine
twine check dist/*
```

### Publish to PyPI
```bash
# Using uv (preferred):
uv publish

# Or using twine:
twine upload dist/*
```

### GitHub Actions publish workflow
```yaml
- name: Build
  run: uv build
- name: Publish to PyPI
  run: uv publish
  env:
    UV_PUBLISH_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
```

Use **Trusted Publishing** (OIDC) on PyPI to avoid storing API tokens. Configure at pypi.org/manage/project/docspan/settings/publishing/.

## GitHub Pages Deployment with mkdocs gh-deploy

```bash
# One-time deploy:
mkdocs gh-deploy --force

# This:
# 1. Builds the site to site/
# 2. Pushes the built HTML to the gh-pages branch
# 3. GitHub Pages serves from that branch
```

### GitHub Pages Settings
- Source: `gh-pages` branch, `/ (root)` directory
- The `gh-deploy` command handles this automatically

### Automation via GitHub Actions
```yaml
name: Deploy docs
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # needed for git-revision-date plugin
      - run: pip install mkdocs mkdocs-material
      - run: mkdocs gh-deploy --force
```

## Tooling Notes
- **ruff**: `line-length = 100`, `target-version = "py39"`, selects `E,F,I`, ignores `E501,E402`
- **mypy**: `python_version = "3.9"`, strict-ish settings
- **pytest**: `testpaths = ["tests"]`, files matching `test_*.py`
- **hatch-vcs**: version is derived from git tags — tagging `v0.1.0` on the release commit is the trigger
