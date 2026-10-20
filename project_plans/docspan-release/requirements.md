# Requirements: docspan v0.1.0 Release

**Date**: 2026-06-07  
**Feature**: User-facing documentation and PyPI release for docspan v0.1.0

---

## Problem Statement

docspan is a feature-complete CLI tool (134 tests passing) that pushes and pulls markdown to Google Docs and Confluence via bidirectional sync with three-way merge. The implementation is complete but:
- There is no user-facing documentation (no README, no quickstart, no command reference)
- The CLI command is still called `markgate` (pre-rename artifact)
- The package is not on PyPI
- There is no CHANGELOG, CONTRIBUTING guide, or hosted docs site

Without documentation, the tool is unusable by anyone who didn't write it. Without PyPI, distribution is manual.

---

## Goals

1. **Rename CLI entrypoint** from `markgate` to `docspan` across pyproject.toml and CLI app name
2. **Write README.md** — install, quickstart, full command reference, both backends, limitations
3. **Write CHANGELOG.md** — v0.1.0 entry with feature summary
4. **Write CONTRIBUTING.md** — dev setup, test commands, how to add a backend, PR process
5. **Set up MkDocs docs site** — structured multi-page documentation hosted on GitHub Pages
6. **Publish to PyPI** as `docspan` package at version `0.1.0`
7. **Create GitHub release** v0.1.0 with release notes

---

## Users / Stakeholders

- **Primary**: Open source community — engineers who want to sync markdown notes with Google Docs or Confluence
- **Secondary**: Contributors who want to extend docspan with new backends or fix bugs

---

## Functional Requirements

### CLI Rename
- `pyproject.toml` `[project.scripts]` entry changes from `markgate = "docspan.cli.main:main"` to `docspan = "docspan.cli.main:main"`
- `app = typer.Typer(name="markgate", ...)` in `cli/main.py` updated to `name="docspan"`
- Help text, error messages, and state file references (`markgate.yaml`, `.markgate-state.json`) updated throughout docs (code keeps existing filenames for backwards compat — only the CLI binary name changes)

### README.md
Must cover:
- What docspan does (one-paragraph pitch)
- Supported backends: Google Docs (push+pull), Confluence (push+pull)
- Install: `pip install docspan`
- `markgate.yaml` configuration example (both backends)
- Quickstart: configure → push → pull → conflicts workflow
- Full command reference: push, pull, status, auth setup, conflicts list/resolve
- Known limitations (prominent callout box):
  - Google Docs: comments lost on edited paragraphs (paragraph-level diff)
  - Push: no image support
  - Push: no table support
  - Confluence: requires Atlassian API token (no OAuth)
- License badge, PyPI badge

### CHANGELOG.md
- Standard Keep a Changelog format
- v0.1.0 entry with: Added (full feature list), Known Limitations

### CONTRIBUTING.md
Must cover:
- Prerequisites (Python 3.9+, uv)
- Dev setup: `uv sync`, running tests (`pytest`)
- Project structure overview (src/docspan layout)
- How to add a new backend (implement Backend ABC, register in backends/__init__.py)
- PR process and CI requirements

### MkDocs Site
Pages:
- index.md (overview + quickstart, mirrors README minus install details)
- install.md (detailed install + auth setup for each backend)
- configuration.md (markgate.yaml reference with all fields)
- commands.md (full CLI reference)
- backends/google-docs.md (Google Docs backend: setup, scopes, limitations)
- backends/confluence.md (Confluence backend: setup, API token, comment sidecar)
- contributing.md (mirrors CONTRIBUTING.md)

Theme: Material for MkDocs (standard OSS choice, good search, dark mode)

### PyPI Release
- `pyproject.toml` version bumped to `0.1.0`
- Package name: `docspan`
- Classifiers: Development Status :: 4 - Beta, Python 3.9+, MIT License
- `python -m build` produces clean wheel + sdist
- Publish via `twine upload` or `uv publish`

### GitHub Release
- Tag: `v0.1.0`
- Release title: `v0.1.0 — Initial release`
- Body: concise feature summary, install command, links to docs

---

## Non-Goals

- No OAuth flow for Confluence (API token only)
- No Windows CI (Linux + macOS)
- No image or table push support (documented as future work)
- No versioned docs (single docs site for latest)
- No auto-publish CI pipeline for PyPI (manual publish for v0.1.0)

---

## Constraints

- Python 3.9+ compatibility (already enforced in CI)
- Must not break existing `markgate.yaml` config files (only binary name changes)
- MkDocs config must work with `mkdocs serve` locally and `mkdocs gh-deploy`
- All new markdown files follow existing project style (no emojis in prose)

---

## Success Criteria

1. `pip install docspan` works in a clean venv and `docspan --help` shows the CLI
2. README renders correctly on GitHub with all sections present
3. `mkdocs build` completes without errors
4. CHANGELOG.md has a valid v0.1.0 entry
5. CONTRIBUTING.md has working dev setup instructions (verified by following them)
6. GitHub release v0.1.0 exists with correct tag and release notes
7. All 134 existing tests continue to pass after CLI rename

---

## Open Questions

- Should `markgate.yaml` be renamed to `docspan.yaml`? Decision: **No** — keep `markgate.yaml` for now to avoid breaking existing configs; document it as a known inconsistency to resolve in v0.2.0.
- Should `.markgate-state.json` be renamed? Decision: **No** — same reason; add note to CHANGELOG.

---

## Out of Scope (Future Work)

- v0.2.0: rename config file to `docspan.yaml`, state file to `.docspan-state.json`
- Image push support for Google Docs
- Table push support for Google Docs
- Confluence OAuth flow
- Additional backends (Notion, Obsidian Publish, etc.)
