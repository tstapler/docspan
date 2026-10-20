# Adversarial Review: docspan v0.1.0 Release Plan

**Reviewer**: Adversarial Review Subagent  
**Date**: 2026-06-07  
**Plan reviewed**: `/home/tstapler/Programming/markgate/project_plans/docspan-release/implementation/plan.md`

---

## Verdict: CONCERNS

The plan is structurally sound and has no blockers that would break existing functionality or make the release impossible. However, there are several non-blocking issues that could cause confusion, wasted effort, or incorrect documentation if not addressed before execution.

---

## Top 3 Findings

### Finding 1: README.md already exists and uses `docspan.yaml` — plan must account for this (CONCERN)

The plan's Story 2.1 says "Create README.md (create)" — but `/home/tstapler/Programming/markgate/README.md` **already exists**. The existing README contains critical inconsistencies that must be corrected, not just overwritten without review:

- The existing README references `docspan.yaml` repeatedly (not `markgate.yaml`):
  - Line 28: `cp docspan.yaml.example docspan.yaml`
  - Line 36: `` Add mappings to `docspan.yaml` ``
  - Lines 89–103: Config reference block uses `docspan.yaml` paths (`~/.docspan/google_credentials.json`, `.docspan/google_token.json`)
- The plan's acceptance criteria require: "No section references 'docspan.yaml' — it's always 'markgate.yaml'"

This is correct behavior per the requirements (the actual config file is `markgate.yaml`, not `docspan.yaml`). However, Story 2.1 says "create" when it should say "overwrite" — and the implementer must know the existing README exists and must be replaced wholesale, not patched.

Additionally, `docspan.yaml.example` exists in the repo root, which is an artifact of the earlier rename. This file should either be renamed to `markgate.yaml.example` (to match the actual config filename) or removed — the plan does not address this file at all.

**Action needed**: Clarify in Story 2.1 that README.md already exists and must be replaced. Add a note to either rename `docspan.yaml.example` → `markgate.yaml.example` or remove it, since the plan explicitly keeps config filenames as `markgate.yaml`.

---

### Finding 2: `.gitignore` already ignores `markgate.yaml` — this silently suppresses the config file (CONCERN)

The existing `.gitignore` at repo root contains:

```
# Configuration files with sensitive data
config.yaml
markgate.yaml
```

This means `markgate.yaml` is already gitignored. For users who follow the quickstart and create a `markgate.yaml`, the file will be silently excluded from git. This is probably intentional (the config contains API tokens), but:

1. The plan's Story 5.1 says to add `site/` to `.gitignore` — however, the plan does not audit the existing `.gitignore` for problematic entries. The implementer may not notice that `markgate.yaml` is already ignored.
2. The CONTRIBUTING.md and configuration docs should explicitly warn users that `markgate.yaml` is gitignored by default (to prevent accidentally committing API tokens — good — but also to prevent confusion when `git status` doesn't show the config file).
3. Story 5.1 also plans to add `dist/` and `*.egg-info/` to `.gitignore` — but `dist/` is already covered by the existing entry (it's listed in the gitignore). Adding it again creates a duplicate but doesn't break anything.

**Action needed**: Add a note in Story 5.1 to audit the existing `.gitignore` before writing. Add a warning in the configuration docs that `markgate.yaml` is gitignored by default. Prevent duplicate entries.

---

### Finding 3: `src/docspan/__init__.py` uses a hardcoded `__version__` — plan's hatch-vcs verification step may give false confidence (CONCERN)

Story 6.2 says:
> Verify hatch-vcs picks up the tag: `python -c "import docspan; print(docspan.__version__)"`

However, `src/docspan/__init__.py` contains:
```python
__version__ = "0.1.0"
```

This is a **static hardcoded string**. When running in editable install mode (`uv pip install -e .`), `import docspan` reads this hardcoded value — it does NOT invoke hatch-vcs at all. The verification step will print `0.1.0` whether or not the git tag exists, giving false confidence that hatch-vcs is working.

hatch-vcs only sets the version at **build time** (when running `uv build`). The actual wheel's `dist-info/METADATA` will contain the version derived from the git tag. The `__init__.py` hardcode is a fallback for development use.

This means:
1. The tag must still be created before `uv build` (Story 6.2 before Story 6.3) — this ordering is correct.
2. The verification command in Story 6.2 is misleading. A better check is to inspect the built artifact: `python -m zipfile -p dist/docspan-0.1.0-py3-none-any.whl docspan-0.1.0.dist-info/METADATA | grep "^Version:"` — or simply verify the wheel filename includes `0.1.0`.
3. If the tag is accidentally created as `0.1.0` (not `v0.1.0`), hatch-vcs will produce version `0.1.0` correctly; but if no tag matches, it will produce a dev version like `0.1.0.dev5+gabcdef`. The plan correctly uses `v0.1.0` (with the `v` prefix), which hatch-vcs strips to `0.1.0`. This part is fine.

**Action needed**: Replace the `import docspan; print(docspan.__version__)` verification in Story 6.2 with a post-build check against the wheel's METADATA. This is a non-blocking concern because the ordering (tag before build) is already correct — the verification is just misleading, not wrong.

---

## Additional Checks (Checklist from Reviewer Brief)

### 1. markgate.yaml filename consistency in docs

The plan is correct. Every docs story that references the config file calls it `markgate.yaml`. The acceptance criteria for Story 2.1 explicitly states: "No section references 'docspan.yaml' — it's always 'markgate.yaml'." The plan is consistent on this point.

However: the existing README.md (which the plan says to "create") uses `docspan.yaml` and `docspan.yaml.example` — see Finding 1.

### 2. hatch-vcs / git tag dependency

The dependency is correctly handled: Story 6.2 (tag) precedes Story 6.3 (build) in the ordered plan. No circular dependency here. The verification in Story 6.2 is misleading — see Finding 3 — but the ordering itself is correct.

### 3. Circular dependencies in epic ordering

No circular dependencies detected. The graph is:
```
Epic 1 → Epics 2, 3, 4 (parallel) → Epic 5 → Epic 6
```
This is a valid DAG with no cycles.

### 4. MkDocs Material theme pinning

Correctly handled. Story 5.1 specifies `mkdocs-material>=9.5.0,<10.0` with an explicit explanation of why. This is in the pyproject.toml docs optional-dependency group. The Risks table also lists this explicitly. No issues.

### 5. README binary name vs config file name

The plan correctly specifies the README uses `docspan` as the binary name and `markgate.yaml` as the config filename throughout. The acceptance criteria in Story 2.1 enforce this. The auth output text update (`docspan uses…` instead of `Markgate uses…`) is also correctly called out.

---

## Minor Observations (Informational Only)

- **Story 4.1 CONTRIBUTING.md**: The dev setup uses `uv pip install -e ".[dev]"` but the existing README uses `uv sync --extra dev`. Both work, but consistency with the existing README would reduce friction. Consider using `uv sync --extra dev` for consistency.

- **Story 5.2 (MkDocs nav count)**: The nav has 7 entries (Home, Install, Configuration, Commands, Backends/Google Docs, Backends/Confluence, Contributing) but Story 5.9's acceptance criteria says "All 7 nav pages are reachable" — this is consistent. However, Story 5.2's acceptance criteria says "All 7 nav pages are reachable" while the nav section of Story 5.2 itself only lists 6 top-level items (Backends counts as one with two sub-pages). This is fine — the count of 7 refers to leaf pages (including both backend sub-pages). No issue, just potentially confusing wording.

- **Story 6.6 CHANGELOG anchor link**: The release notes link `#010---2026-06-07` is a reasonable guess for GitHub's anchor generation from `## [0.1.0] - 2026-06-07`, but GitHub generates anchors by lowercasing, removing punctuation, and replacing spaces with `-`. The actual anchor for `## [0.1.0] - 2026-06-07` will be `#010---2026-06-07`. This looks correct, but should be verified after CHANGELOG.md is committed.

- **Story 5.8 (docs/contributing.md)**: The plan mentions `--8<-- "CONTRIBUTING.md"` (pymdownx.snippets) as an option to avoid duplication, but `pymdownx.snippets` is not listed in the `markdown_extensions` in Story 5.2's mkdocs.yml. If snippets are used, the extension must be added. If not used, the content must be duplicated. The plan leaves this ambiguous — the implementer should pick one approach.

- **Story 1.3 verification**: The plan says to check for "134 passed" but warns "If count differs, investigate before proceeding." This is the right approach but the test count may naturally change between Epic 1 and Epic 6 if new tests are added for documentation-related functionality. Since Epic 1 is the only epic that touches source code, this check is only relevant immediately after Story 1.1/1.2.

---

## Summary

| # | Finding | Severity | Blocking? |
|---|---|---|---|
| 1 | README.md already exists with `docspan.yaml` refs; `docspan.yaml.example` not addressed | CONCERN | No |
| 2 | `.gitignore` already ignores `markgate.yaml`; plan should audit existing gitignore | CONCERN | No |
| 3 | hatch-vcs version verification in Story 6.2 gives false confidence; use post-build check | CONCERN | No |

**Verdict: CONCERNS** — Plan is executable as written. The concerns reduce risk if addressed before implementation begins.
