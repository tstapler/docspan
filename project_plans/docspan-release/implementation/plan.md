# Implementation Plan: docspan v0.1.0 Release

**Date**: 2026-06-07
**Status**: Draft

---

## Dependency Graph

```
Epic 1: CLI String Rename
    │
    ├──► Epic 2: README.md          ─┐
    ├──► Epic 3: CHANGELOG.md        ├──► Epic 5: MkDocs Site ──► Epic 6: PyPI Release
    └──► Epic 4: CONTRIBUTING.md    ─┘
```

- Epic 1 must be completed first — it defines the correct CLI name that all docs reference.
- Epics 2, 3, and 4 can be worked in parallel once Epic 1 is done.
- Epic 5 assembles content from Epics 2–4 into a hosted site and must run after all three.
- Epic 6 is the final gate — tag, build, publish, release.

---

## Epic 1: CLI String Rename

**Goal**: Replace all 11 user-visible "markgate" strings with "docspan" in the source while keeping config/state filenames unchanged.

**Prerequisite**: None  
**Blocks**: All Epics (2–6)

---

### Story 1.1: Rename CLI display name and user-facing messages

**Objective**: Update every user-visible "markgate" string in `cli/main.py` to "docspan".

**Files to touch** (3):
- `src/docspan/cli/main.py`
- `src/docspan/__init__.py`
- `src/docspan/backends/base.py`

**Changes**:

| File | Location | Change |
|---|---|---|
| `cli/main.py` line 1 | module docstring | `"""markgate CLI…"""` → `"""docspan CLI…"""` |
| `cli/main.py` line 31 | `typer.Typer(name=…)` | `name="markgate"` → `name="docspan"` |
| `cli/main.py` line 176 | pull local-only message | `'markgate conflicts resolve'` → `'docspan conflicts resolve'` |
| `cli/main.py` line 183 | pull merge-conflict message | `markgate conflicts resolve` → `docspan conflicts resolve` |
| `cli/main.py` line 219 | status table title | `"markgate mappings"` → `"docspan mappings"` |
| `__init__.py` line 1 | module docstring | `"""markgate —…"""` → `"""docspan —…"""` |
| `backends/base.py` line 51-52 | class docstring | `src/markgate/backends/__init__.py` → `src/docspan/backends/__init__.py` |

**Strings to keep unchanged** (config/state backward-compat):
- `markgate.yaml` — config filename, appears in help text and error messages
- `.markgate-state.json` — state file constant
- `.markgate-base` — base store directory
- `.markgate/google_token.json` — token path
- All help text of the form `"Path to markgate.yaml"` — correct, file is still named that

**Acceptance criteria**:
- `docspan --help` shows `Usage: docspan [OPTIONS] COMMAND [ARGS]...`
- `docspan status` table shows "docspan mappings" title
- `grep -r "name=\"markgate\"" src/` returns no matches
- All 134 tests pass: `pytest`

---

### Story 1.2: Rename user-visible strings in backend files

**Objective**: Update all "markgate" CLI references in backend error messages and auth output.

**Files to touch** (2):
- `src/docspan/backends/confluence/backend.py`
- `src/docspan/backends/google_docs/backend.py`

**Changes**:

| File | Location | Change |
|---|---|---|
| `confluence/backend.py` line 53 | `_ensure_client()` RuntimeError | `"Run: markgate auth setup confluence"` → `"Run: docspan auth setup confluence"` |
| `confluence/backend.py` line 212 | `auth_setup()` output | `"Done. Test with: markgate status"` → `"Done. Test with: docspan status"` |
| `confluence/backend.py` line 229 | `validate_config()` ValueError | `"Run: markgate auth setup confluence"` → `"Run: docspan auth setup confluence"` |
| `google_docs/backend.py` line 46 | `_ensure_client()` RuntimeError | `"Run: markgate auth setup google_docs"` → `"Run: docspan auth setup google_docs"` |
| `google_docs/backend.py` line 113 | `auth_setup()` output | `"Markgate uses Google…"` → `"docspan uses Google…"` |
| `google_docs/backend.py` line 140 | `validate_config()` ValueError | `"Run: markgate auth setup google_docs"` → `"Run: docspan auth setup google_docs"` |

**Strings to keep unchanged** in backend files:
- `confluence/backend.py` line 206: `"Add to markgate.yaml:\n\n..."` — intentionally correct; config file is still named markgate.yaml

**Acceptance criteria**:
- `grep -rn "markgate" src/docspan/backends/ | grep -v markgate.yaml | grep -v markgate-state | grep -v markgate-base | grep -v markgate/google_token` returns no matches
- `pytest` — all 134 tests pass

---

### Story 1.3: Verify full rename with test suite

**Objective**: Run the full test suite and confirm no regressions.

**Files to touch** (0 — verification only):

**Commands**:
```bash
pytest --tb=short
```

**Acceptance criteria**:
- Exit code 0
- "134 passed" in output (no failures, no errors)
- If count differs, investigate before proceeding

---

## Epic 2: README.md

**Goal**: Write a single comprehensive README that renders correctly on GitHub and PyPI.

**Prerequisite**: Epic 1 (CLI name must be "docspan")  
**Blocks**: Epic 5 (MkDocs index.md mirrors README)

---

### Story 2.1: Write README.md

**Objective**: Create README.md at repo root covering all required sections.

**Files to touch** (1):
- `README.md` (create)

**Structure**:
```
# docspan
[PyPI badge] [License badge]

One-paragraph pitch: what docspan does.

## Supported Backends
Two-column list: Google Docs (push+pull), Confluence (push+pull).

## Install
pip install docspan

## Configuration (markgate.yaml)
Full YAML example with both backends and direction field.
Include note: "The config file is named markgate.yaml — this name is preserved for backward
compatibility and will be renamed in v0.2.0."

## Quickstart
Four steps: configure → push → pull → conflicts workflow.
Code blocks for each step using `docspan` binary.

## Command Reference
Sub-sections: push, pull, status, auth setup, conflicts list, conflicts resolve.
Each with synopsis, argument/option table, and behavior notes.
Use verbatim auth_setup() output for auth sections.

## Configuration Reference
Table of all markgate.yaml fields (backends.google_docs.*, backends.confluence.*, mappings[]).
Environment variable alternatives.

## State Files
Brief note on .markgate-state.json, .markgate-base/, {file}.orig, {file}.comments.md.

## Known Limitations
> **Note** callout (GitHub-flavored admonition using > [!NOTE]):
- Google Docs: comments on edited paragraphs are lost (paragraph-level diff)
- Push: no image support (local images cannot be pushed)
- Push: no table support (markdown tables are not rendered in Google Docs)
- Confluence: requires Atlassian API token (no OAuth flow)
- Confluence: inline comment sidecar ({file}.comments.md) is informational only

## License
MIT
```

**Constraints**:
- No emojis in prose
- Auth sections use verbatim text from `auth_setup()` output (as documented in features.md), updated to say "docspan uses..." not "Markgate uses..."
- All relative links to CONTRIBUTING.md and CHANGELOG.md must use full GitHub URLs (https://github.com/tstapler/docspan/blob/main/CONTRIBUTING.md) so PyPI doesn't 404
- PyPI badge: `[![PyPI](https://img.shields.io/pypi/v/docspan)](https://pypi.org/project/docspan/)`
- License badge: `[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)`

**Acceptance criteria**:
- All 8 required sections present (Install, Config, Quickstart, Command Reference, Config Reference, State Files, Known Limitations, License)
- `twine check dist/*` passes after build (README renders on PyPI)
- Known Limitations callout contains all 5 items listed above
- No section references "docspan.yaml" — it's always "markgate.yaml"
- Auth setup text matches actual `auth_setup()` output with "docspan uses…" (not "Markgate uses…")

---

## Epic 3: CHANGELOG.md

**Goal**: Create a Keep-a-Changelog-format CHANGELOG with a valid v0.1.0 entry.

**Prerequisite**: Epic 1  
**Blocks**: Epic 5

---

### Story 3.1: Write CHANGELOG.md

**Objective**: Create CHANGELOG.md at repo root following keepachangelog.com conventions.

**Files to touch** (1):
- `CHANGELOG.md` (create)

**Structure**:
```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-07

### Added
- `docspan push` — push local markdown files to Google Docs or Confluence
- `docspan pull` — pull remote documents into local markdown files with three-way merge
- `docspan status` — show current mapping status
- `docspan auth setup` — interactive authentication setup for google_docs and confluence backends
- `docspan conflicts list` — list files with unresolved merge conflicts
- `docspan conflicts resolve` — resolve merge conflicts with remote/local/merged strategy
- Google Docs backend: push and pull via Google Docs API (service account auth)
- Confluence backend: push and pull via Atlassian REST API (API token auth)
- Three-way merge for bidirectional sync conflict detection
- Confluence comment sidecar: pull writes inline comments to {file}.comments.md
- `markgate.yaml` config file format with per-mapping direction control (push/pull/both)

### Known Limitations
- Google Docs: comments on edited paragraphs are destroyed on push (paragraph-level diff)
- Push: no image support (local image files cannot be pushed to Google Docs or Confluence)
- Push: no table support (markdown tables are not rendered in Google Docs)
- Confluence: requires Atlassian API token; no OAuth flow
- Confluence: comment sidecar ({file}.comments.md) is informational only; comments cannot be pushed back
- Config file is named `markgate.yaml` (not `docspan.yaml`) and state file is `.markgate-state.json` (not `.docspan-state.json`). These will be renamed in v0.2.0.

[Unreleased]: https://github.com/tstapler/docspan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tstapler/docspan/releases/tag/v0.1.0
```

**Acceptance criteria**:
- `## [0.1.0]` heading with date is present
- All 6 Added items are listed
- Known Limitations section explains the markgate.yaml naming decision
- Bottom diff links use correct GitHub URLs

---

## Epic 4: CONTRIBUTING.md

**Goal**: Create a developer-facing CONTRIBUTING guide covering prerequisites, dev setup, project structure, adding a backend, and PR process.

**Prerequisite**: Epic 1  
**Blocks**: Epic 5

---

### Story 4.1: Write CONTRIBUTING.md

**Objective**: Create CONTRIBUTING.md at repo root.

**Files to touch** (1):
- `CONTRIBUTING.md` (create)

**Structure**:
```
# Contributing to docspan

## Prerequisites
- Python 3.9+
- uv (https://docs.astral.sh/uv/)
- git

## Dev Setup
git clone https://github.com/tstapler/docspan
cd docspan
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
docspan --help   # verify CLI works

## Running Tests
pytest                                   # all tests
pytest --cov=docspan --cov-report=term-missing  # with coverage
ruff check src/ tests/                   # lint
ruff format src/ tests/                  # format
mypy src/                                # type check

## Project Structure
(Use the structure diagram from architecture.md:
src/docspan/ with cli/, config.py, backends/, core/ sub-sections)

## How to Add a New Backend
(4 steps from architecture.md: create package, implement Backend ABC,
add config model to config.py, register in BACKENDS dict)
(Include the Backend ABC interface and PushResult/PullResult signatures)

## PR Process
1. Fork and create a feature branch from main
2. Ensure all tests pass: pytest
3. Ensure lint passes: ruff check src/ tests/
4. Add tests for new functionality
5. Open a PR against main — CI runs pytest, ruff, and mypy automatically
6. At least one review approval required before merge
```

**Acceptance criteria**:
- `uv venv && uv pip install -e ".[dev]" && docspan --help` sequence described verbatim and works when followed
- All 4 backend-addition steps present with code examples
- PR process section lists CI requirements (pytest, ruff, mypy)
- No emojis in prose

---

## Epic 5: MkDocs Site

**Goal**: Set up a Material-themed MkDocs docs site that builds cleanly and can be deployed to GitHub Pages.

**Prerequisite**: Epics 2, 3, 4 (content sources)  
**Blocks**: Epic 6 (docs URL must be valid in release notes)

---

### Story 5.1: Add MkDocs dependencies to pyproject.toml

**Objective**: Add `[docs]` optional-dependency group and `site/` to .gitignore.

**Files to touch** (2):
- `pyproject.toml`
- `.gitignore` (create or edit)

**Changes**:

In `pyproject.toml`, add after the `[project.optional-dependencies]` dev block:
```toml
[project.optional-dependencies]
docs = [
    "mkdocs>=1.5.0,<2.0",
    "mkdocs-material>=9.5.0,<10.0",
]
```

Note: Pin `mkdocs-material` to `>=9.5.0,<10.0` to prevent surprise breaking changes from a major version bump.

In `.gitignore`, add:
```
site/
dist/
*.egg-info/
```

**Acceptance criteria**:
- `uv pip install -e ".[docs]"` installs without error
- `mkdocs --version` shows 1.x
- `mkdocs-material` 9.x installed

---

### Story 5.2: Create mkdocs.yml

**Objective**: Write `mkdocs.yml` at repo root with Material theme, correct nav, and admonition extensions.

**Files to touch** (1):
- `mkdocs.yml` (create)

**Content**:
```yaml
site_name: docspan
site_description: Push and pull markdown to Google Docs and Confluence from a single CLI
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
  - Install: install.md
  - Configuration: configuration.md
  - Commands: commands.md
  - Backends:
    - Google Docs: backends/google-docs.md
    - Confluence: backends/confluence.md
  - Contributing: contributing.md

markdown_extensions:
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences
  - admonition
  - tables
  - attr_list
```

**Note on existing docs/ content**: The existing `docs/google-docs-push.md` is an internal design doc. It must be either:
- Moved to `project_plans/` before running `mkdocs build`, OR
- Listed in `nav:` as a "Design Notes" page (if worth exposing)

Recommended: move it to `project_plans/docspan-release/research/google-docs-push.md` to keep the MkDocs `docs/` directory clean.

**Acceptance criteria**:
- `mkdocs build --strict` completes with exit code 0
- All 7 nav pages are reachable
- No warnings about missing files or pages

---

### Story 5.3: Write docs/index.md

**Objective**: Landing page that mirrors the README overview and quickstart (minus install detail).

**Files to touch** (1):
- `docs/index.md` (create)

**Content outline**:
```
# docspan

One-paragraph pitch (same as README intro paragraph).

## What it does
Push markdown → Google Docs or Confluence.
Pull → merge back into local files.
Bidirectional sync with three-way merge for conflict detection.

## Quickstart
(Four-step workflow matching README quickstart section)

## Backends
Brief table: Google Docs, Confluence — auth method, push/pull support.

## Known Limitations
(Same 5 items as README, in a MkDocs admonition block:
!!! warning "Known Limitations"
    - ...
)
```

**Acceptance criteria**:
- Page renders in `mkdocs serve` without errors
- Quickstart section uses `docspan` binary (not `markgate`)
- No emojis

---

### Story 5.4: Write docs/install.md

**Objective**: Detailed install and auth setup page for each backend.

**Files to touch** (1):
- `docs/install.md` (create)

**Content outline**:
```
# Install

## Requirements
Python 3.9+

## Install via pip
pip install docspan

## Install via uv
uv add docspan

## Google Docs Auth Setup
(Verbatim steps from auth_setup() output, updated to say "docspan uses…")
(include environment variable alternatives)

## Confluence Auth Setup
(Verbatim interactive prompt flow from auth_setup() output)
(include link to id.atlassian.com for API token generation)
```

**Acceptance criteria**:
- Auth setup steps match actual `auth_setup()` output
- Both backend sections present
- Google Docs section mentions all three credential options (YAML, env path, env inline JSON)

---

### Story 5.5: Write docs/configuration.md

**Objective**: Full markgate.yaml reference with field tables and environment variable alternatives.

**Files to touch** (1):
- `docs/configuration.md` (create)

**Content outline**:
```
# Configuration

Config file: markgate.yaml (note explaining the name kept for backward compat)

## Full Example
(Full YAML example from features.md)

## backends.google_docs fields
(Table from features.md)

## backends.confluence fields
(Table from features.md)

## mappings[] fields
(Table from features.md)

## Environment Variables
(Both backend env var alternatives)

## State Files
(Brief note on generated files: .markgate-state.json, .markgate-base/, {file}.orig, {file}.comments.md)
```

**Acceptance criteria**:
- All config fields documented with type, default, and description
- All environment variable alternatives listed for both backends
- State files section explains what each file is and when it appears

---

### Story 5.6: Write docs/commands.md

**Objective**: Full CLI reference mirroring the features.md command research.

**Files to touch** (1):
- `docs/commands.md` (create)

**Content outline**:
```
# Command Reference

## docspan push
Synopsis, arguments, options, behavior notes (including dry-run, skip pull-only).

## docspan pull
Synopsis, arguments, options, behavior notes (including all 6 outcome states).

## docspan status
Synopsis, options, output description.

## docspan auth setup
Synopsis, arguments, options.

## docspan conflicts list
Synopsis, options, output description.

## docspan conflicts resolve
Synopsis, arguments (FILE), options (--accept), all three strategies.
```

**Acceptance criteria**:
- All 6 commands documented
- `--accept` values (remote/local/merged) explained for `conflicts resolve`
- No reference to "markgate" binary (only "docspan")
- References to `markgate.yaml` as the config file name are correct

---

### Story 5.7: Write docs/backends/google-docs.md and docs/backends/confluence.md

**Objective**: Backend-specific deep-dive pages covering auth, scopes, limitations, and examples.

**Files to touch** (2):
- `docs/backends/google-docs.md` (create)
- `docs/backends/confluence.md` (create)

**google-docs.md outline**:
```
# Google Docs Backend

## How it works
Service account auth. Push uses structural diff. Pull uses HTML-to-markdown.

## Auth Setup
(Same as install.md Google section — cross-reference or duplicate)

## Required Scopes
documents (read-write), drive.readonly

## markgate.yaml Example
(snippet with google_docs backend section)

## Limitations
- Comments destroyed on push
- No image push support
- No table push support
- Rate limit: 300 req/min/project
```

**confluence.md outline**:
```
# Confluence Backend

## How it works
REST API + Atlassian API token. Push replaces full page body. Pull converts Storage Format to markdown.

## Auth Setup
(Same as install.md Confluence section)

## API Token Setup
Link to id.atlassian.com/manage-profile/security/api-tokens

## markgate.yaml Example
(snippet with confluence backend section)

## Limitations
- API token required (no OAuth)
- Comment sidecar is informational only
- Complex macros not preserved faithfully
- Push replaces full page; inline comment positions may shift
```

**Acceptance criteria**:
- Both pages render without MkDocs errors
- All limitations from pitfalls.md are represented
- markgate.yaml snippets are syntactically correct YAML

---

### Story 5.8: Write docs/contributing.md

**Objective**: Docs-site version of CONTRIBUTING.md (can mirror or include-by-reference).

**Files to touch** (1):
- `docs/contributing.md` (create)

**Content**: Mirror CONTRIBUTING.md content exactly. If MkDocs supports `--8<-- "CONTRIBUTING.md"` (pymdownx.snippets), use that to avoid duplication. Otherwise duplicate the content.

**Acceptance criteria**:
- Content matches CONTRIBUTING.md
- Page renders without errors in `mkdocs serve`

---

### Story 5.9: Verify MkDocs build

**Objective**: Run `mkdocs build --strict` and confirm zero errors and zero warnings.

**Files to touch** (0 — verification only):

**Commands**:
```bash
uv pip install -e ".[docs]"
mkdocs build --strict
```

**Acceptance criteria**:
- Exit code 0
- `site/` directory created with HTML output
- No warnings about: missing pages, broken internal links, undefined nav entries

---

## Epic 6: PyPI Release

**Goal**: Tag v0.1.0, build, publish to TestPyPI, then PyPI, and create a GitHub release.

**Prerequisite**: Epics 1–5 all complete  
**Blocks**: Nothing (final step)

---

### Story 6.1: Update pyproject.toml classifiers and prepare for release

**Objective**: Verify pyproject.toml is release-ready; add `readme = "README.md"` line if missing.

**Files to touch** (1):
- `pyproject.toml`

**Checklist**:
- `readme = "README.md"` is present (it is — confirmed in pyproject.toml)
- `Development Status :: 3 - Alpha` classifier — keep as-is (correct for v0.1.0)
- `[project.scripts]` entry is `docspan = "docspan.cli.main:app"` (already correct)
- `[project.urls]` entries are set (already correct)
- `dynamic = ["version"]` and `[tool.hatch.version] source = "vcs"` are present (already correct)

**No code changes expected** — this story is a pre-flight verification.

**Acceptance criteria**:
- `pyproject.toml` passes `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` with no error
- All required fields documented in PyPA packaging guide are present

---

### Story 6.2: Create git tag v0.1.0

**Objective**: Tag the release commit. The tag must exist BEFORE running `uv build` because hatch-vcs derives the version from git tags.

**Commands**:
```bash
# Ensure all changes from Epics 1–5 are committed
git status           # must be clean
git log --oneline -5 # confirm HEAD is the release commit

# Create annotated tag
git tag -a v0.1.0 -m "v0.1.0 — Initial release"

# Verify hatch-vcs picks up the tag
python -c "import docspan; print(docspan.__version__)"
# Expected output: 0.1.0
```

**Acceptance criteria**:
- `git tag -l v0.1.0` shows the tag
- `python -c "import docspan; print(docspan.__version__)"` prints `0.1.0`
- Working tree is clean (no uncommitted changes)

---

### Story 6.3: Build wheel and sdist

**Objective**: Produce clean build artifacts in `dist/`.

**Commands**:
```bash
uv build
# Expected: dist/docspan-0.1.0-py3-none-any.whl
#           dist/docspan-0.1.0.tar.gz

# Verify README renders on PyPI
pip install twine
twine check dist/*
```

**Acceptance criteria**:
- `dist/docspan-0.1.0-py3-none-any.whl` exists
- `dist/docspan-0.1.0.tar.gz` exists
- `twine check dist/*` exits 0 with "PASSED" for both artifacts
- No warnings about README rendering

---

### Story 6.4: Publish to TestPyPI and verify install

**Objective**: Do a dry run on TestPyPI before touching PyPI production.

**Commands**:
```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token $TEST_PYPI_TOKEN

# In a fresh venv:
python -m venv /tmp/docspan-test-venv
/tmp/docspan-test-venv/bin/pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ docspan==0.1.0
/tmp/docspan-test-venv/bin/docspan --help
```

**Acceptance criteria**:
- `docspan --help` displays correctly in the test venv
- Output shows `Usage: docspan [OPTIONS] COMMAND [ARGS]...`
- No import errors

---

### Story 6.5: Publish to PyPI

**Objective**: Publish the production release.

**Commands**:
```bash
uv publish --token $PYPI_TOKEN
# or: twine upload dist/*
```

**Acceptance criteria**:
- `https://pypi.org/project/docspan/0.1.0/` is accessible
- `pip install docspan==0.1.0` in a clean venv succeeds
- `docspan --help` works after install

---

### Story 6.6: Create GitHub release

**Objective**: Create a GitHub release with tag v0.1.0, release title, and release notes.

**Commands**:
```bash
git push origin v0.1.0

gh release create v0.1.0 \
  --title "v0.1.0 — Initial release" \
  --notes "$(cat <<'EOF'
## docspan v0.1.0 — Initial release

Push and pull markdown to Google Docs and Confluence from a single CLI.

### Install

pip install docspan

### What's new
- `docspan push` / `pull` with bidirectional sync and three-way merge
- Google Docs backend (service account auth)
- Confluence backend (API token auth)
- `docspan auth setup` interactive configuration
- `docspan conflicts list/resolve` for merge conflict management

### Documentation
https://tstapler.github.io/docspan

### Known Limitations
See [CHANGELOG.md](https://github.com/tstapler/docspan/blob/main/CHANGELOG.md#010---2026-06-07) for the full list.
EOF
)"
```

**Acceptance criteria**:
- GitHub release page exists at `https://github.com/tstapler/docspan/releases/tag/v0.1.0`
- Release body contains install command, feature list, and docs link
- Tag `v0.1.0` is attached

---

### Story 6.7: Deploy MkDocs to GitHub Pages

**Objective**: Publish the docs site so the release notes URL is live.

**Commands**:
```bash
# Enable GitHub Pages in repo settings first:
# Settings → Pages → Source: Deploy from a branch → gh-pages / (root)

mkdocs gh-deploy --force
# Verify: https://tstapler.github.io/docspan
```

**Acceptance criteria**:
- `https://tstapler.github.io/docspan` loads the MkDocs site
- All nav pages are reachable
- No broken internal links

---

## Task Summary

| Epic | Stories | Tasks | Hours (est.) |
|---|---|---|---|
| 1: CLI String Rename | 3 | 11 string changes + test run | 1–2h |
| 2: README.md | 1 | ~400-line README | 2–3h |
| 3: CHANGELOG.md | 1 | v0.1.0 entry | 0.5–1h |
| 4: CONTRIBUTING.md | 1 | Dev guide | 1–2h |
| 5: MkDocs Site | 9 | 7 pages + config + deps + verify | 3–4h |
| 6: PyPI Release | 7 | Tag + build + publish + release | 1–2h |
| **Total** | **22** | — | **8.5–14h** |

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `hatch-vcs` version mismatch (tag after build) | Story 6.2 explicitly tags BEFORE Story 6.3 builds |
| MkDocs nav conflict with existing `docs/google-docs-push.md` | Story 5.2 explicitly moves it to `project_plans/` before build |
| PyPI README rendering failure | Story 6.3 runs `twine check` before upload |
| TestPyPI missing transitive deps | Use `--extra-index-url https://pypi.org/simple/` in test install |
| MkDocs Material major version break | Pin `mkdocs-material>=9.5.0,<10.0` in Story 5.1 |
| `site/` committed to main accidentally | Story 5.1 adds `site/` to `.gitignore` |
