# Validation Plan: docspan v0.1.0 Release

**Date**: 2026-06-07
**Status**: Draft

---

## Test Strategy

This is a documentation and release task, not a feature implementation. The primary validation concerns are:

1. **Content correctness** — generated markdown files have all required sections and accurate information.
2. **Build integrity** — the Python package builds cleanly and installs correctly.
3. **Regression safety** — CLI rename touches source code; existing tests must continue to pass.
4. **Publication readiness** — artifacts satisfy PyPI, GitHub, and MkDocs hosting requirements.

Validation is ordered to mirror the implementation dependency graph: CLI rename first, then docs content, then build/publish gates. The readiness gate below is a pre-execution check; the test cases are executed during and after implementation.

---

## Requirement to Verification Traceability Matrix

| # | Requirement (from requirements.md) | Verification Method | Verified By |
|---|---|---|---|
| R1 | CLI entrypoint renamed from `markgate` to `docspan` in `pyproject.toml` and `typer.Typer(name=...)` | `docspan --help` in clean venv after `pip install docspan==0.1.0`; `grep -r 'name="markgate"' src/` returns 0 matches | Story 1.1 AC + Story 6.4 smoke test |
| R2 | All user-visible "markgate" strings in CLI, backend error messages updated | `grep -rn "markgate" src/docspan/backends/ \| grep -v markgate.yaml \| grep -v markgate-state \| grep -v markgate-base \| grep -v markgate/google_token` returns 0 matches | Story 1.2 AC |
| R3 | All 134 existing pytest tests pass after rename | `pytest --tb=short` exits 0 with "134 passed" | Story 1.3 AC |
| R4 | README.md covers all 8 required sections (Install, Config, Quickstart, Command Reference, Config Reference, State Files, Known Limitations, License) | Section-by-section checklist review; `twine check dist/*` passes (README renders on PyPI) | Story 2.1 AC |
| R5 | README Known Limitations callout contains all 5 items | Manual checklist: Google Docs comments, no image push, no table push, Confluence API token only, comment sidecar informational | Story 2.1 AC |
| R6 | README uses `docspan` binary and `markgate.yaml` config name throughout | `grep -n "docspan.yaml" README.md` returns 0 matches; `grep -n "docspan --" README.md` returns results | Story 2.1 AC |
| R7 | CHANGELOG.md follows Keep a Changelog format with valid v0.1.0 entry | `## [0.1.0]` heading with date present; all 6 Added items listed; Known Limitations section present; bottom diff links use correct GitHub URLs | Story 3.1 AC |
| R8 | CONTRIBUTING.md covers prerequisites, dev setup, project structure, adding a backend, PR process | Follow dev setup instructions from scratch in a temp dir: `uv venv && uv pip install -e ".[dev]" && docspan --help`; all 4 backend-addition steps present with code examples | Story 4.1 AC |
| R9 | MkDocs site builds cleanly with Material theme, 7 nav pages | `mkdocs build --strict` exits 0 with no warnings; `site/` directory populated with HTML | Story 5.9 AC |
| R10 | MkDocs docs deployable to GitHub Pages | `https://tstapler.github.io/docspan` loads; all nav pages reachable; no broken internal links | Story 6.7 AC |
| R11 | `pyproject.toml` version set to `0.1.0`; package name `docspan`; correct classifiers | `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` exits 0; wheel filename `docspan-0.1.0-py3-none-any.whl` | Story 6.1 + 6.3 AC |
| R12 | `python -m build` (or `uv build`) produces clean wheel and sdist | `dist/docspan-0.1.0-py3-none-any.whl` and `dist/docspan-0.1.0.tar.gz` exist; `twine check dist/*` exits 0 | Story 6.3 AC |
| R13 | `pip install docspan==0.1.0` in clean venv works and `docspan --help` shows CLI | TestPyPI dry run (Story 6.4); final PyPI install smoke test (Story 6.5) | Story 6.4 + 6.5 AC |
| R14 | GitHub release v0.1.0 exists with tag, title, and release notes matching CHANGELOG | `https://github.com/tstapler/docspan/releases/tag/v0.1.0` accessible; release body contains install command, feature list, docs link | Story 6.6 AC |

**Coverage**: 14/14 requirements have a verification method (100%).

---

## Test Cases by Type

### Smoke Tests (post-install)

Run in a clean venv after `pip install docspan==0.1.0`:

| TC-S1 | `docspan --help` | Output shows `Usage: docspan [OPTIONS] COMMAND [ARGS]...` |
|---|---|---|
| TC-S2 | `docspan push --help` | Usage and options displayed without error |
| TC-S3 | `docspan pull --help` | Usage and options displayed without error |
| TC-S4 | `docspan status --help` | Usage and options displayed without error |
| TC-S5 | `docspan auth setup --help` | Usage and options displayed without error |
| TC-S6 | `docspan conflicts --help` | Subcommand list displayed without error |
| TC-S7 | `docspan conflicts list --help` | Usage and options displayed |
| TC-S8 | `docspan conflicts resolve --help` | `--accept` options (remote/local/merged) displayed |

### Documentation Completeness Checklists

**README.md** (section-by-section):
- [ ] TC-D1: One-paragraph pitch present
- [ ] TC-D2: Supported Backends section (Google Docs, Confluence)
- [ ] TC-D3: Install section with `pip install docspan`
- [ ] TC-D4: Configuration section with full `markgate.yaml` example (both backends, direction field)
- [ ] TC-D5: Quickstart section with 4-step workflow using `docspan` binary
- [ ] TC-D6: Command Reference with all 6 commands (push, pull, status, auth setup, conflicts list, conflicts resolve)
- [ ] TC-D7: Configuration Reference table (all markgate.yaml fields)
- [ ] TC-D8: State Files section (.markgate-state.json, .markgate-base/, .orig, .comments.md)
- [ ] TC-D9: Known Limitations callout with all 5 items
- [ ] TC-D10: License section (MIT) and both badges (PyPI, License)
- [ ] TC-D11: No reference to `docspan.yaml` anywhere in README
- [ ] TC-D12: Auth setup text says "docspan uses..." not "Markgate uses..."
- [ ] TC-D13: Relative links to CONTRIBUTING.md and CHANGELOG.md use full GitHub URLs

**CHANGELOG.md**:
- [ ] TC-D14: `## [Unreleased]` section present
- [ ] TC-D15: `## [0.1.0] - 2026-06-07` heading present
- [ ] TC-D16: All 6 Added items listed (push, pull, status, auth setup, conflicts list/resolve, Google Docs backend, Confluence backend, three-way merge, comment sidecar, markgate.yaml format)
- [ ] TC-D17: Known Limitations section explains markgate.yaml/docspan.yaml naming decision
- [ ] TC-D18: Bottom diff links present and use correct GitHub URLs (`https://github.com/tstapler/docspan/...`)

**CONTRIBUTING.md**:
- [ ] TC-D19: Prerequisites section (Python 3.9+, uv, git)
- [ ] TC-D20: Dev setup sequence: clone → `uv venv` → install → `docspan --help`
- [ ] TC-D21: Test commands: pytest, ruff check, ruff format, mypy
- [ ] TC-D22: Project structure diagram (src/docspan/ with cli/, config.py, backends/, core/)
- [ ] TC-D23: "How to Add a New Backend" with 4 steps and Backend ABC interface
- [ ] TC-D24: PR process with CI requirements (pytest, ruff, mypy)
- [ ] TC-D25: No emojis in prose

### Build Validation

| TC-B1 | `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` | Exits 0 (pyproject.toml is valid TOML) |
|---|---|---|
| TC-B2 | `uv build` | Exits 0; `dist/docspan-0.1.0-py3-none-any.whl` and `dist/docspan-0.1.0.tar.gz` created |
| TC-B3 | `twine check dist/*` | Exits 0; "PASSED" for both artifacts; no README rendering warnings |
| TC-B4 | Inspect wheel METADATA: `python -m zipfile -p dist/docspan-0.1.0-py3-none-any.whl docspan-0.1.0.dist-info/METADATA \| grep "^Version:"` | Prints `Version: 0.1.0` (validates hatch-vcs tag pickup — see adversarial Finding 3) |
| TC-B5 | `git tag -l v0.1.0` | Tag exists before build |
| TC-B6 | `uv pip install -e ".[docs]"` | Exits 0; `mkdocs --version` shows 1.x; `mkdocs-material` 9.x installed |

### MkDocs Build

| TC-M1 | `mkdocs build --strict` | Exits 0 |
|---|---|---|
| TC-M2 | No warnings about missing pages | Zero warning lines in output |
| TC-M3 | No warnings about broken internal links | Zero link-error lines in output |
| TC-M4 | `site/` directory created with HTML for all 7 nav pages | Files exist: index.html, install/index.html, configuration/index.html, commands/index.html, backends/google-docs/index.html, backends/confluence/index.html, contributing/index.html |
| TC-M5 | `docs/google-docs-push.md` not present in `docs/` before mkdocs build | Moved to `project_plans/` per Story 5.2 note; no nav orphan warning |

### Regression Tests

| TC-R1 | `pytest --tb=short` after Epic 1 (CLI rename) | Exit code 0; "134 passed" in output |
|---|---|---|
| TC-R2 | `grep -r 'name="markgate"' src/` after Epic 1 | 0 matches |
| TC-R3 | `grep -rn "markgate" src/docspan/backends/ \| grep -v markgate.yaml \| grep -v markgate-state \| grep -v markgate-base \| grep -v markgate/google_token` | 0 matches |

---

## Readiness Gate

### Criterion 1: Requirements Coverage

All 14 requirements have a documented verification method.

**Result: PASS (14/14)** ✓

---

### Criterion 2: Plan Completeness

Reviewing the plan's task coverage:

- Epic 1 (CLI rename): 3 stories, all with explicit file-level change tables and grep-based acceptance criteria. No orphaned tasks.
- Epic 2 (README): 1 story with 5 acceptance criteria. The existing README.md conflict (adversarial Finding 1) means Story 2.1 must overwrite rather than create — but the story's acceptance criteria are sufficient to validate the output regardless of create vs. overwrite.
- Epic 3 (CHANGELOG): 1 story, 4 acceptance criteria. Complete.
- Epic 4 (CONTRIBUTING): 1 story, 4 acceptance criteria. Complete.
- Epic 5 (MkDocs): 9 stories, each with acceptance criteria. Story 5.8 leaves a minor ambiguity (snippets extension vs. duplication) but both paths produce a valid output.
- Epic 6 (Release): 7 stories covering tag, build, TestPyPI dry run, PyPI publish, GitHub release, and Pages deploy. All have acceptance criteria.

No missing acceptance criteria detected. No stories without a deliverable.

**Result: PASS** ✓

---

### Criterion 3: Adversarial Findings Addressed

The adversarial review returned verdict CONCERNS (no BLOCKED findings). All 3 findings are non-blocking. Assessment of plan coverage:

| Finding | Severity | Addressed in plan? | Notes |
|---|---|---|---|
| F1: README.md already exists with `docspan.yaml` refs; `docspan.yaml.example` not addressed | CONCERN | Partially | Story 2.1's acceptance criteria enforce the correct output (no `docspan.yaml` references). However, the plan says "create" not "overwrite," and `docspan.yaml.example` is not mentioned. This is a pre-execution clarification gap, not a test failure risk — the acceptance criteria are the definitive gate. Implementer must know to replace the existing file and remove/rename `docspan.yaml.example`. |
| F2: `.gitignore` already ignores `markgate.yaml`; plan should audit existing gitignore | CONCERN | Not addressed | Story 5.1 does not include an audit of the existing `.gitignore`. Duplicate `dist/` entries and the existing `markgate.yaml` ignore rule need to be noted in docs (configuration.md and CONTRIBUTING.md should warn that `markgate.yaml` is gitignored). The test for this: TC-D4 verifies configuration docs exist; the gitignore warning is an addendum not covered by any existing TC. |
| F3: hatch-vcs verification step in Story 6.2 gives false confidence | CONCERN | Addressed via TC-B4 | TC-B4 in this validation plan replaces the misleading `import docspan; print(__version__)` check with a post-build METADATA inspection. The plan's Story 6.2 still has the misleading verification, but TC-B4 is the gate that matters. |

None of the findings are BLOCKED severity. Finding 2 (gitignore audit) is the least addressed — the plan has no story that audits the existing `.gitignore` or adds documentation warnings about `markgate.yaml` being gitignored. This is a CONCERN that affects user experience (silent gitignore of config) but does not affect the release artifacts.

**Result: CONCERNS** (F2 not fully mitigated in plan; F1 partially mitigated; F3 mitigated via TC-B4)

---

### Criterion 4: Risk Assessment

**Top 2 Risks**

**Risk 1: hatch-vcs produces a dev version instead of 0.1.0 in the wheel**

- Scenario: The git tag `v0.1.0` is missing, malformed, or created after `uv build` runs. hatch-vcs falls back to a dev version like `0.1.0.dev5+gabcdef`, and the wheel is named incorrectly.
- Impact: PyPI upload fails (version mismatch) or produces an unusable artifact.
- Mitigation: Story 6.2 explicitly requires `git tag -l v0.1.0` to confirm tag existence before running Story 6.3. TC-B5 enforces this gate. TC-B4 validates the wheel METADATA version after build — this catches the problem before upload.

**Risk 2: `mkdocs build --strict` fails due to the existing `docs/google-docs-push.md` being orphaned from nav**

- Scenario: The internal design doc at `docs/google-docs-push.md` is not moved before running `mkdocs build`. MkDocs `--strict` mode treats files present in `docs/` but absent from `nav:` as warnings (converted to errors in strict mode).
- Impact: TC-M1 fails; Epic 5 cannot be signed off; Epic 6 is blocked.
- Mitigation: Story 5.2 explicitly notes this file must be moved to `project_plans/` before the build. TC-M5 verifies the file is absent from `docs/` before the build gate. This is already in the plan — the risk is low if the implementer reads Story 5.2's note carefully.

---

## Overall Readiness Gate Verdict

| Criterion | Result |
|---|---|
| Requirements coverage | PASS (14/14) |
| Plan completeness | PASS |
| Adversarial findings addressed | CONCERNS (F2 gitignore audit unaddressed) |
| Risk assessment | 2 risks identified, both mitigated |

**Verdict: CONCERNS**

The plan is executable and safe to begin. The CONCERNS verdict is driven solely by adversarial Finding 2 (the existing `.gitignore` silently ignores `markgate.yaml` and the plan has no story to audit this or add documentation warnings). This does not block implementation but should be addressed during Story 5.5 (configuration.md) and Story 4.1 (CONTRIBUTING.md) by adding an explicit callout that `markgate.yaml` is gitignored by default to prevent accidental API token commits.

No requirement is unverified. No BLOCKED findings exist. Implementation may proceed.
