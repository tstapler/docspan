"""Migrate a single-file Google Docs mapping to a sectioned mapping.

See `project_plans/gdocs-sectioned-migrate/implementation/plan.md` for the
Domain Glossary, Architecture Summary, and Pattern Decisions this module
implements. This file currently covers Epic 1 (the git plumbing helper
`_run_git` and the dirty-tree pre-flight gate `_check_clean_tree`), Epic 2
(the live/local zip: `_split_live`, `_split_local`, `_zip_sections`, and the
single-preamble-only guard), Epic 3 (staging the section files, atomically
swapping them into place, and committing: `_stage_sections`, `_commit_swap`,
and the dry-run `MigrationPreview`), and Epic 4 (state/config update and
rollback: `_record_migrated_state`, `_apply_config_update`, `_rollback`,
`_acquire_migration_lock`/`_release_migration_lock`).

**Epic 4 snapshot-ownership note** (a deliberate, necessary refinement of
plan.md's Task 4.1/4.2 signatures): `_record_migrated_state` and
`_apply_config_update` each capture their own *internal* prior-snapshot for
self-healing against a failure that happens strictly inside their own body
(e.g. the third of five per-section `_record_state` calls raises) --  they
restore their own partial in-memory mutation and re-raise, so a failure
inside either function alone never needs `_rollback` to touch that
function's state at all. That internal snapshot never leaves the function
(consistent with their `-> None` / `-> Mapping` return types). The *cross-step*
snapshots `_rollback` accepts (`prior_state_snapshot`/`prior_config_snapshot`)
are a separate, coarser pair the pipeline captures itself immediately before
calling `_record_migrated_state`/`_apply_config_update` at all -- needed for
the case where `_record_migrated_state` fully succeeds (including its
terminal `state.save()`) and `_apply_config_update` fails afterwards, which
neither function's own internal snapshot can cover since by then there is no
in-flight call to unwind. Likewise, `new_local_dir`/`created_paths` are
threaded in explicitly rather than re-derived, since the directory-naming
convention itself belongs to the not-yet-built pipeline (Epic 5), and the
created-paths list must be shared/mutated across both functions and
`_rollback`.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import pathlib
import shutil
import subprocess
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from rich.markup import escape
from rich.table import Table

from docspan.backends.google_docs.docs_structure_parser import DocsStructureParser
from docspan.backends.google_docs.manifest import (
    MANIFEST_FILENAME,
    PREAMBLE_HEADING_ID,
    ManifestStore,
    SectionManifestEntry,
)
from docspan.backends.google_docs.markdown_to_paragraph_parser import (
    MarkdownToParagraphParser,
)
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.projection import project
from docspan.backends.google_docs.section_splitter import Section, split_nodes
from docspan.config import (
    _SECTIONED_UNSUPPORTED_BACKENDS,
    Mapping,
    MarkgateConfig,
    config_mtime,
    save_config,
)
from docspan.core.atomic_dir import atomic_replace_dir
from docspan.core.orchestrator import record_state
from docspan.core.state import MappingState, SyncState

if TYPE_CHECKING:
    from docspan.backends.google_docs.backend import GoogleDocsBackend

# Timeout is sized per call site, not a single global constant (plan.md
# Task 1.1 / pre-mortem P1 #1): plumbing calls (`rev-parse`, `status
# --porcelain`) are cheap and bounded, so a short timeout is appropriate;
# the write calls in `_commit_swap` (`add`/`rm --cached`/`commit`, Task 3.2)
# scale with the number of migrated sections, so they get a longer budget.
_GIT_PLUMBING_TIMEOUT_S = 10
_GIT_WRITE_TIMEOUT_S = 60


class MigrationError(Exception):
    """Raised for migration pre-flight/execution failures, matching `ManifestError`'s convention."""


def _run_git(
    *args: str, cwd: str, timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    """Run a `git` subcommand and return its `CompletedProcess`.

    Mirrors `mermaid_renderer.py`'s subprocess pattern (list-argv,
    `capture_output=True`, `check=False`, no shell) rather than adding a
    GitPython/pygit2 dependency (research/stack.md §2, research/
    build-vs-buy.md §1). Raises `MigrationError` -- never lets
    `OSError`/`subprocess.TimeoutExpired`/a raw nonzero exit propagate --
    for: `git` not being installed/found, the call exceeding `timeout`, or
    an unexpected nonzero exit. Callers that expect a particular command to
    sometimes fail as part of normal operation (e.g. `rev-parse HEAD` on an
    unborn branch) catch this and translate it into their own
    distinguishable message rather than relying on this generic one.
    """
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise MigrationError(f"git not found while running {command}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MigrationError(
            f"git timed out after {timeout}s while running {command}"
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise MigrationError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{stderr or 'no error output'}"
        )
    return result


def _check_clean_tree(local_path: str) -> None:
    """Refuse migration unless `local_path` is clean and tracked at HEAD.

    Three ordered checks (plan.md Task 1.2 / research/pitfalls.md §3), each
    with its own distinguishable refusal message:

    1. Resolve the git repo root via `git rev-parse --show-toplevel` run
       with `cwd` set to the file's own directory -- this handles
       nested/submodule repos correctly by resolving the repo that actually
       owns the file, not whatever repo docspan itself happens to be
       invoked from. No `git`, or not inside a repo at all, refuses.
    2. `git rev-parse HEAD` in that root -- fails on an unborn branch (a
       fresh `git init` with no commits yet), which refuses distinctly from
       "dirty," since there is no HEAD to diff against at all.
    3. `git status --porcelain -- <local_path>`, scoped to the mapping's own
       pathspec (never a whole-repo status, so unrelated dirty files
       elsewhere in the same repo never trip this gate). Empty output
       passes. Output starting with `??` means untracked, which refuses
       with a message distinct from "uncommitted changes" -- "clean" is
       meaningless for a file with no HEAD-relative content to diff. Any
       other non-empty output means uncommitted changes and refuses with
       the ux.md §3 message shape (a three-line remediation snippet, since
       this is the gate most likely to surprise a first-time user).
    """
    file_dir = os.path.dirname(os.path.abspath(local_path)) or os.sep

    try:
        toplevel = _run_git(
            "rev-parse",
            "--show-toplevel",
            cwd=file_dir,
            timeout=_GIT_PLUMBING_TIMEOUT_S,
        )
    except MigrationError as exc:
        raise MigrationError(
            f"{local_path} is not inside a git repository: {exc}"
        ) from exc
    repo_root = toplevel.stdout.strip()

    try:
        _run_git(
            "rev-parse", "HEAD", cwd=repo_root, timeout=_GIT_PLUMBING_TIMEOUT_S
        )
    except MigrationError as exc:
        raise MigrationError(
            "repository has no commits yet; make an initial commit first"
        ) from exc

    status = _run_git(
        "status",
        "--porcelain",
        "--",
        local_path,
        cwd=repo_root,
        timeout=_GIT_PLUMBING_TIMEOUT_S,
    )
    output = status.stdout
    if not output:
        return

    if output.lstrip().startswith("??"):
        raise MigrationError(
            f"{local_path} is not yet tracked by git — migrate-sectioned "
            "refuses to split an untracked file, since the split must "
            "operate on a known-good, versioned state (see git status). "
            "Add and commit it first, then re-run:\n\n"
            f"  git add {local_path} && git commit -m \"...\"\n"
            f"  docspan migrate-sectioned {local_path}"
        )

    raise MigrationError(
        f"{local_path} has uncommitted changes — migrate-sectioned refuses "
        "to split a dirty working tree, since the split must operate on a "
        "known-good, versioned state (see git status). Commit or stash your "
        "changes, then re-run:\n\n"
        f"  git status {local_path}\n"
        f"  git add {local_path} && git commit -m \"...\"\n"
        f"  docspan migrate-sectioned {local_path}"
    )


def _split_live(client, doc_id: str, split_level: str) -> List[Section]:
    """Split the *live* Google Doc into `Section`s (identity only, per Task 2.1).

    `client.get_document(doc_id)` -> `DocsStructureParser().parse()` ->
    `project()` -> `split_nodes(nodes, split_level)` -- the exact same
    reusable pipeline `pull_sectioned` already runs (`backend.py`), so this
    performs no new Docs API surface, per plan.md's Pattern Decisions. Only
    `heading_id`/`title` per section end up mattering to the caller (Task
    2.3's zip) -- the section *content* here is discarded in favor of the
    local file's byte-faithful content.
    """
    doc = client.get_document(doc_id)
    nodes = DocsStructureParser().parse(doc)
    nodes, _residue = project(nodes)
    return split_nodes(nodes, split_level)


def _split_local(local_path: str, split_level: str) -> List[Section]:
    """Split the *local* markdown file into `Section`s (content only, per Task 2.2).

    `MarkdownToParagraphParser().parse(...)` -> `split_nodes(nodes,
    split_level)`. Every section's `heading_id` is falsy here by
    construction -- `MarkdownToParagraphParser` never writes `heading_id`,
    since that field only exists on nodes produced from a live Docs API
    fetch. This is expected, not a bug: Task 2.3's zip is exactly what
    supplies the missing identity from `_split_live`'s output.
    """
    content = pathlib.Path(local_path).read_text(encoding="utf-8")
    nodes = MarkdownToParagraphParser().parse(content)
    return split_nodes(nodes, split_level)


def _zip_sections(live: List[Section], local: List[Section]) -> List[Section]:
    """Zip live-doc identity onto local-file content, position-by-position.

    Per Task 2.3: this is migration's core algorithm, not a second splitting
    pipeline -- `split_nodes()` already ran once on each side (Tasks 2.1/
    2.2); this only reconciles their outputs. Aborts with a remote-diverged
    `MigrationError` unless the two section lists have the same length and
    every pair's `title` matches at its index -- anything else means the
    live document has changed since the local file was last pulled, and
    zipping by position would silently mismatch identity to the wrong
    content.

    On success, each resulting section takes its `heading_id` from the live
    side (real Docs-assigned identity) and everything else (`nodes`, i.e.
    byte-faithful content; `slug`) from the local side. The `or
    f"__migrated-{i}__"` fallback covers the case where the live section
    itself has no real `heading_id` of its own -- the only such case is the
    preamble, whose `heading_id` is the sentinel `PREAMBLE_HEADING_ID`
    (never falsy), so this fallback exists purely for defense-in-depth
    against a section with a genuinely empty `heading_id` (e.g. a fresh
    heading Docs hasn't assigned an id to yet) rather than a case this
    module expects to hit in practice.
    """
    if len(live) != len(local) or any(
        live_section.title != local_section.title
        for live_section, local_section in zip(live, local)
    ):
        raise MigrationError("remote has diverged — run 'docspan pull' first")

    return [
        dataclasses.replace(
            local_section,
            heading_id=live_section.heading_id or f"__migrated-{index}__",
        )
        for index, (live_section, local_section) in enumerate(zip(live, local))
    ]


def _deepest_heading_style_present(nodes) -> Optional[str]:
    """Return the deepest `HEADING_N` style found in `nodes`, or `None`.

    Mirrors `split_nodes`'s own rank-computation exactly (`is_heading_style`
    + numeric suffix), so the single-preamble guard below can reuse its
    message wording verbatim rather than inventing new phrasing (Task 2.4).
    """
    from docspan.backends.google_docs.heading_anchors import is_heading_style

    ranks = set()
    for node in nodes:
        style = getattr(node, "style", None)
        if not is_heading_style(style):
            continue
        try:
            ranks.add(int(style.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    if not ranks:
        return None
    return f"HEADING_{max(ranks)}"


def _guard_against_single_preamble_only(
    zipped: List[Section], local_path: str, split_level: str
) -> None:
    """Refuse migration when the zipped result is a single preamble section.

    Per Task 2.4: `split_nodes()` itself treats "no headings at
    `split_level`" as a valid single-preamble result for steady-state pull
    (a document can legitimately have no headings), but for *migration*
    specifically that would burn git history and flip `sectioned: true` for
    no benefit -- so migration refuses instead of silently succeeding
    (research/pitfalls.md §1). Surfaced pre-stage, before any files are
    written. No override flag in this pass (YAGNI per the plan's Medium
    appetite).

    Reuses `SectionSplitError`'s message-construction logic/wording (naming
    the deepest heading style actually present) when the local document does
    have headings, just not at `split_level` -- though in practice
    `split_nodes` itself already raises `SectionSplitError` before this
    guard would ever see such a case, since a non-empty
    `heading_ranks_present` that excludes `split_level` is exactly what
    triggers that exception at the `_split_local`/`_split_live` call sites.
    This guard's realistic case is therefore a document with *no* headings
    at any level, which `split_nodes` treats as a valid single-preamble
    result rather than an error.
    """
    if len(zipped) != 1 or zipped[0].heading_id != PREAMBLE_HEADING_ID:
        return

    content = pathlib.Path(local_path).read_text(encoding="utf-8")
    local_nodes = MarkdownToParagraphParser().parse(content)
    deepest = _deepest_heading_style_present(local_nodes)

    if deepest is not None:
        raise MigrationError(
            f"split_level {split_level!r} does not appear in this document; "
            f"the deepest heading style present is {deepest}"
        )

    raise MigrationError(
        f"this document has no headings at any level, so split_level "
        f"{split_level!r} produces only a single preamble section — "
        "migrate-sectioned refuses to split a document with nothing "
        "meaningful to migrate into sections"
    )


def _section_filename(index: int, slug: str, width: int) -> str:
    """Build the `NN-slug.md` filename for section `index`, matching `pull_sectioned`'s convention.

    Mirrors `backend.py`'s `pull_sectioned` (`f"{str(index).zfill(width)}-
    {section.slug}.md"`) verbatim, so sectioned mappings produced by
    migration and by ordinary sectioned pulls are byte-for-byte
    indistinguishable in their naming scheme.
    """
    return f"{str(index).zfill(width)}-{slug}.md"


def _section_boundaries(
    zipped: List[Section],
) -> List["_SectionBoundary"]:
    """Compute each section's filename/content/manifest entry without touching disk.

    Split out of `_stage_sections` per Task 3.4's note: `--dry-run` (Epic 5)
    and `MigrationPreview.from_zipped` both need this pure half, without the
    filesystem side effects (`_stage_sections`'s scratch-dir write +
    `ManifestStore.save`) neither of them wants.
    """
    width = max(2, len(str(len(zipped) - 1))) if zipped else 2
    boundaries = []
    for index, section in enumerate(zipped):
        filename = _section_filename(index, section.slug, width)
        content = render_nodes_to_markdown(section.nodes) if section.nodes else ""
        entry = SectionManifestEntry(
            heading_id=section.heading_id,
            slug=section.slug,
            filename=filename,
            title=section.title or None,
        )
        boundaries.append(_SectionBoundary(filename=filename, content=content, entry=entry))
    return boundaries


@dataclasses.dataclass
class _SectionBoundary:
    """One section's computed filename/content/manifest entry (pure, pre-filesystem)."""

    filename: str
    content: str
    entry: SectionManifestEntry


def _stage_sections(zipped: List[Section], target_dir: str) -> pathlib.Path:
    """Write `zipped`'s sections to a scratch dir and build its `_manifest.yaml` (Task 3.1).

    Each section's content is `local[i]`'s own nodes rendered via
    `render_nodes_to_markdown` -- the exact same rendering `pull_sectioned`
    already uses for ordinary sectioned pulls (`backend.py`) -- never
    re-derived from the live document's own content, which `_zip_sections`
    already discarded in favor of the local file's (see its docstring). The
    scratch dir is a sibling of `target_dir`, named
    `.{dirname}.migrate-{pid}.tmp/`, so the swap in `_commit_swap` (Task 3.2)
    is a same-filesystem rename, never a cross-filesystem copy.

    The pure compute-boundaries half (filenames, rendered content, manifest
    entries) lives in `_section_boundaries` so `--dry-run` can call that
    directly and never create this directory at all (Task 3.4's note).
    """
    target = pathlib.Path(target_dir)
    scratch_dir = target.parent / f".{target.name}.migrate-{os.getpid()}.tmp"
    scratch_dir.mkdir(parents=True, exist_ok=False)

    boundaries = _section_boundaries(zipped)
    entries: List[SectionManifestEntry] = []
    try:
        for boundary in boundaries:
            (scratch_dir / boundary.filename).write_text(boundary.content, encoding="utf-8")
            entries.append(boundary.entry)

        ManifestStore.save(str(scratch_dir / MANIFEST_FILENAME), entries)
    except Exception:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        raise
    return scratch_dir


_COMMIT_MESSAGE_TEMPLATE = (
    "migrate-sectioned: split {old_file_path} into {n} sections\n\n"
    "Tip: git's default rename detection won't show pre-split history for "
    "these files. Use:\n"
    "  git log --follow -C20% <file>\n"
    "  git blame -C20% -C20% -C20% <file>"
)


def _commit_swap(
    staging_dir: pathlib.Path, target_dir: str, old_file_path: str, repo_root: str
) -> str:
    """Atomically swap `staging_dir` into `target_dir`, delete the original file, and commit (Task 3.2).

    Captures `pre_migration_head` via `git rev-parse HEAD` *first*, before
    any other call in this function runs, so it reflects the exact state
    `_check_clean_tree` already validated, and returns it on success for
    `test_commit_swap_preserves_history_via_git_log_follow`'s direct
    assertion. The pipeline (`migrate_sectioned`) does *not* rely solely on
    this return value, though: it independently captures the same
    `pre_migration_head` itself immediately before calling this function, so
    that value is still available to thread into `_rollback` (Epic 4's `git
    reset --hard <pre_migration_head>`) even if `git commit` itself raises
    (including a `BaseException` like `KeyboardInterrupt`) after
    `atomic_replace_dir`/`add`/`rm --cached` below have already mutated the
    working tree but before this function returns.

    The swap (`atomic_replace_dir`) and the original file's removal both
    happen *before* the commit, and both land in the same commit as the new
    section files' `git add` -- one commit containing "delete original + add
    all sections" in a single parent-diff, since `git log --follow`/`git
    blame` history continuity depends on that pairing being visible to git's
    rename/copy detection (research/stack.md §1, Task 3.2's note). This
    function does not attempt any rollback of its own steps beyond what's
    already atomic (the swap itself) -- including a `KeyboardInterrupt`
    raised after `add`/`rm --cached` have staged changes but before `commit`
    returns; restoring that half-staged state is Epic 4's `_rollback`
    (Task 4.3), invoked by the caller/pipeline, not by this function.
    """
    pre_migration_head = _run_git(
        "rev-parse", "HEAD", cwd=repo_root, timeout=_GIT_PLUMBING_TIMEOUT_S
    ).stdout.strip()

    atomic_replace_dir(staging_dir, target_dir)
    os.remove(old_file_path)

    _run_git("add", target_dir, cwd=repo_root, timeout=_GIT_WRITE_TIMEOUT_S)
    _run_git("rm", "--cached", old_file_path, cwd=repo_root, timeout=_GIT_WRITE_TIMEOUT_S)

    message = _COMMIT_MESSAGE_TEMPLATE.format(
        old_file_path=old_file_path, n=len(list(pathlib.Path(target_dir).glob("*.md")))
    )
    _run_git("commit", "-m", message, cwd=repo_root, timeout=_GIT_WRITE_TIMEOUT_S)

    return pre_migration_head


@dataclasses.dataclass
class MigrationPreviewRow:
    """One section's dry-run preview: filename, title, line count, word count (Task 3.4)."""

    filename: str
    title: str
    line_count: int
    word_count: int


@dataclasses.dataclass
class MigrationPreview:
    """The full dry-run preview: one `MigrationPreviewRow` per section, plus `.render()`.

    Pure over the zipped section list -- computable via `_section_boundaries`
    without staging anything to disk -- so it feeds both
    `migrate-sectioned --dry-run` and (per plan.md's Pattern Decisions) the
    pixel-identical `pull --dry-run --to-sectioned` table.
    """

    rows: List[MigrationPreviewRow]

    @staticmethod
    def from_zipped(zipped: List[Section]) -> "MigrationPreview":
        """Build the preview's rows from `zipped`, in section order."""
        rows = [
            MigrationPreviewRow(
                filename=boundary.filename,
                title=boundary.entry.title or "",
                line_count=len(boundary.content.splitlines()),
                word_count=len(boundary.content.split()),
            )
            for boundary in _section_boundaries(zipped)
        ]
        return MigrationPreview(rows=rows)

    def render(self) -> Table:
        """Render this preview as a Rich `Table`, per ux.md §2's exact column shape.

        `Target file` is styled cyan (matches `status`/`config list`'s
        `Table(title=...)` convention); `Heading` is plain and `escape()`-wrapped
        since headings are untrusted document text that may contain literal
        `[`/`]` Rich-markup-looking characters; `Lines`/`Words` are right-aligned.
        A section with no real heading (the zero-heading/preamble case) renders
        `(no heading — preamble)` instead of an empty cell.
        """
        table = Table()
        table.add_column("Target file", style="cyan")
        table.add_column("Heading")
        table.add_column("Lines", justify="right")
        table.add_column("Words", justify="right")
        for row in self.rows:
            heading = escape(row.title) if row.title else "(no heading — preamble)"
            table.add_row(row.filename, heading, str(row.line_count), str(row.word_count))
        return table


# ─────────────────────────────────────────────────────────────────────────────
# Epic 4 — state + config update, rollback
# ─────────────────────────────────────────────────────────────────────────────


def _record_migrated_state(
    state: SyncState,
    state_path: str,
    state_dir: str,
    mapping: Mapping,
    zipped_sections: List[Section],
    remote_version: str,
    new_local_dir: str,
    created_paths: List[pathlib.Path],
) -> None:
    """Record one `SyncState` entry per migrated section, replacing the old single-file entry (Task 4.1).

    Reuses `orchestrator.record_state` verbatim -- migration must produce
    state identical to a fresh `pull_sectioned`, and passing `save=False`
    per call avoids the N intermediate disk writes `save=True` would incur.
    `state.save(state_path)` is called exactly once, after every mutation
    below (both the per-section additions and the old entry's removal) is
    already applied in memory.

    An *internal* snapshot of `state.mappings` is captured purely so a
    failure partway through this function's own loop can restore its own
    partial mutation before re-raising -- distinct from the coarser
    `prior_state_snapshot` the pipeline captures itself before calling this
    function at all (see the module docstring's snapshot-ownership note).
    `created_paths` is mutated in place as each section path is recorded
    (and again when the old path is removed), not batched after the fact.
    """
    old_local_path = mapping.local
    internal_snapshot = copy.deepcopy(state.mappings)
    assert mapping.remote_id is not None

    try:
        for boundary in _section_boundaries(zipped_sections):
            section_path = os.path.join(new_local_dir, boundary.filename)
            recorded = record_state(
                state,
                state_path,
                state_dir,
                section_path,
                mapping.remote_id,
                mapping.backend,
                boundary.content,
                remote_version,
                save=False,
            )
            if not recorded:
                raise MigrationError(
                    f"failed to record sync state for migrated section {section_path!r}"
                )
            created_paths.append(pathlib.Path(section_path))

        if old_local_path in state.mappings:
            del state.mappings[old_local_path]
            created_paths.append(pathlib.Path(old_local_path))

        state.save(state_path)
    except Exception:
        state.mappings.clear()
        state.mappings.update(internal_snapshot)
        raise


def _apply_config_update(
    config: MarkgateConfig,
    config_path: str,
    mapping: Mapping,
    split_level: str,
    new_local_dir: str,
) -> Mapping:
    """Swap `mapping` for a freshly constructed sectioned `Mapping` in `config`, then save (Task 4.2).

    `new_mapping` is built via fresh `Mapping(**{...})` construction --
    never `model_copy`/mutation -- so `_validate_sectioned_split_level`
    (and `_SECTIONED_UNSUPPORTED_BACKENDS`) run and can raise *before*
    `config.mappings` is touched or `save_config` is ever called, giving
    defense-in-depth beyond the CLI's own pre-check. `expected_mtime` is
    re-read immediately before `save_config` (not reused from an earlier
    load) per pitfalls.md §4's concurrency mitigation; a `ConfigConflictError`
    from that call is a trigger for full rollback and is re-raised as-is,
    never retried.

    Returns `new_mapping` (not `config`) so the pipeline can set it directly
    on `MigrationResult.new_mapping`. As with `_record_migrated_state`, the
    snapshot taken here is purely internal self-healing for a failure inside
    this function's own body; the coarser `prior_config_snapshot` `_rollback`
    restores from is captured by the pipeline before this function is called.
    """
    new_mapping = Mapping(
        **{
            **mapping.model_dump(),
            "sectioned": True,
            "split_level": split_level,
            "local": new_local_dir,
        }
    )

    index = next(
        (i for i, existing in enumerate(config.mappings) if existing.local == mapping.local),
        None,
    )
    if index is None:
        raise MigrationError(f"mapping for {mapping.local!r} not found in config")

    internal_snapshot = list(config.mappings)
    config.mappings[index] = new_mapping

    try:
        expected_mtime = config_mtime(config_path)
        save_config(config, config_path, expected_mtime=expected_mtime)
    except Exception:
        config.mappings[:] = internal_snapshot
        raise

    return new_mapping


def _rollback(
    repo_root: str,
    pre_migration_head: Optional[str],
    commit_landed: bool,
    created_paths: List[pathlib.Path],
    prior_state_snapshot: Optional[Dict[str, MappingState]] = None,
    prior_config_snapshot: Optional[List[Mapping]] = None,
    state: Optional[SyncState] = None,
    state_path: Optional[str] = None,
    config: Optional[MarkgateConfig] = None,
) -> None:
    """Undo a failed migration, per the pre-commit/post-commit split (Task 4.3).

    Pre-commit failure (`commit_landed=False`): if `pre_migration_head` was
    already captured (failure happened inside or after `_commit_swap` began,
    e.g. a `KeyboardInterrupt` after `add`/`rm --cached` but before `commit`
    returns), `git reset --hard <pre_migration_head>` discards any
    half-staged index/working-tree state and restores the original file.
    Otherwise (failure before `_commit_swap` ran at all) there is nothing
    git-side to unwind -- the caller is responsible for discarding the
    staging directory, and no `git reset --hard` is invoked. `created_paths`
    is expected to be empty in this case.

    Post-commit failure (`commit_landed=True`): `git reset --hard
    <pre_migration_head>` undoes the landed migration commit *and* discards
    any uncommitted working-tree changes made after it (e.g. `markgate.yaml`
    edits from `_apply_config_update`, since `_commit_swap` never `git add`s
    the config file) in one step. `SyncState` is separately restored from
    `prior_state_snapshot` and re-persisted via `state.save(state_path)` --
    required because `_record_migrated_state`'s terminal `state.save()` may
    already have flushed the new (now-wrong) state to disk before the
    failure occurred, so the in-memory restore alone would not be reflected
    on disk. `MarkgateConfig` is restored in-memory from
    `prior_config_snapshot` for consistency (e.g. if `config` is used again
    by the caller), covering anything outside `repo_root`'s git tree that
    `git reset --hard` cannot reach.

    If rollback itself fails partway (e.g. `git reset --hard` errors), this
    raises a `MigrationError` distinctly rather than downgrading to a
    warning, so the caller (Epic 5) can render ux.md §4's "rollback FAILED"
    two-line shape with its `git checkout --`-based escape hatch instead of
    the single-line "Rolling back... done." success shape.
    """
    try:
        if pre_migration_head is not None:
            _run_git(
                "reset",
                "--hard",
                pre_migration_head,
                cwd=repo_root,
                timeout=_GIT_WRITE_TIMEOUT_S,
            )

        if commit_landed:
            if state is not None and prior_state_snapshot is not None:
                state.mappings.clear()
                state.mappings.update(copy.deepcopy(prior_state_snapshot))
                if state_path is not None:
                    state.save(state_path)

            if config is not None and prior_config_snapshot is not None:
                config.mappings[:] = copy.deepcopy(prior_config_snapshot)
    except Exception as exc:
        raise MigrationError(
            "Rollback FAILED: the migration failed and the automatic "
            f"rollback itself also failed: {exc}. Your working tree may be "
            "in an inconsistent state. Compare against your last commit "
            "before continuing:\n\n"
            "  git status\n"
            f"  git diff\n"
            "  git checkout -- .   # discard partial migration"
        ) from exc


_MIGRATION_LOCK_PREFIX = ".docspan-migration-"
_MIGRATION_LOCK_SUFFIX = ".lock"


def _migration_lock_path(repo_root: "str | pathlib.Path", mapping_key: str) -> pathlib.Path:
    """Path of the sentinel lock file for `mapping_key` under `repo_root` (Task 4.5)."""
    return pathlib.Path(repo_root) / f"{_MIGRATION_LOCK_PREFIX}{mapping_key}{_MIGRATION_LOCK_SUFFIX}"


def _acquire_migration_lock(repo_root: "str | pathlib.Path", mapping_key: str) -> pathlib.Path:
    """Atomically acquire the per-mapping migration lock, or raise if already held (Task 4.5).

    `O_CREAT | O_EXCL` makes the file's creation itself the atomic
    race-check (per pre-mortem P1 #2), closing the gap where a second
    concurrent migration's own `_rollback` (`git reset --hard
    <pre_migration_head>`) could discard a commit made by an unrelated
    `pull`/`push` that landed after this migration's `pre_migration_head`.
    Raises before touching any file, git state, or config if the lock is
    already held.
    """
    lock_path = _migration_lock_path(repo_root, mapping_key)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise MigrationError(
            "migration already in progress for this mapping"
        ) from exc
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
    finally:
        os.close(fd)
    return lock_path


def _release_migration_lock(lock_path: pathlib.Path) -> None:
    """Release a lock acquired by `_acquire_migration_lock`, tolerating an already-missing file.

    Meant to be called from a `finally` after `_rollback`/success, whichever
    occurs, so the lock is released on every code path.
    """
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Epic 5 — the orchestrating pipeline + its result contract
# ─────────────────────────────────────────────────────────────────────────────


class MigrationOutcome(Enum):
    """Terminal state of one `migrate_sectioned()` call (Task 5.1)."""

    SUCCESS = "success"
    DRY_RUN = "dry_run"
    REFUSED = "refused"  # pre-flight failure, nothing attempted
    FAILED = "failed"  # attempted, rolled back
    ROLLBACK_FAILED = "rollback_failed"  # attempted, rollback itself failed


@dataclasses.dataclass
class MigrationResult:
    """What `migrate_sectioned()` returns -- the CLI renders this, never `typer.Exit`/prints itself.

    `sections` holds the single `MigrationPreview` (its own `.rows` carry the
    per-section filename/title/line/word data) for `SUCCESS`/`DRY_RUN`, and is
    empty for `REFUSED`/`FAILED`/`ROLLBACK_FAILED` when the preflight/zip never
    got far enough to build one.
    """

    outcome: MigrationOutcome
    sections: List[MigrationPreview]
    messages: List[str]
    new_mapping: Optional[Mapping] = None
    rolled_back: bool = False


def migrate_sectioned(
    mapping: Mapping,
    backend: "GoogleDocsBackend",
    config: MarkgateConfig,
    config_path: str,
    state: SyncState,
    state_dir: str,
    state_path: str,
    split_level: str,
    dry_run: bool = False,
) -> MigrationResult:
    """Migrate one single-file `mapping` to a sectioned mapping (Task 5.1).

    Orchestrates every prior epic's building block into the pipeline plan.md's
    Architecture Summary describes (lines 97-136): pre-flight checks that fail
    fast without writing anything, live/local zip, staging, an atomic commit
    swap, state + config updates (config last, per pitfalls.md §2), and a
    `_rollback` on any failure from the commit swap onward. Never calls
    `typer.Exit` or prints -- the CLI (Task 5.2) renders this result.

    `except BaseException` (not `Exception`) intentionally covers
    `KeyboardInterrupt`/`SystemExit` too: once `_commit_swap` may have started
    mutating the working tree, an interrupt must still trigger `_rollback`
    rather than leaving a half-migrated repo behind. The migration lock is
    always released in the outer `finally`, on every path.
    """
    messages: List[str] = []

    # --- Pre-flight (fail fast, no writes at all) ---
    if mapping.sectioned:
        return MigrationResult(
            outcome=MigrationOutcome.REFUSED,
            sections=[],
            messages=[f"{mapping.local} is already sectioned"],
        )
    if mapping.backend in _SECTIONED_UNSUPPORTED_BACKENDS:
        return MigrationResult(
            outcome=MigrationOutcome.REFUSED,
            sections=[],
            messages=[
                f"backend {mapping.backend!r} does not support sectioned sync"
            ],
        )

    mapping_key = os.path.splitext(os.path.basename(mapping.local))[0]
    file_dir = os.path.dirname(os.path.abspath(mapping.local)) or os.sep
    try:
        repo_root = _run_git(
            "rev-parse", "--show-toplevel", cwd=file_dir, timeout=_GIT_PLUMBING_TIMEOUT_S
        ).stdout.strip()
    except MigrationError as exc:
        return MigrationResult(outcome=MigrationOutcome.REFUSED, sections=[], messages=[str(exc)])

    try:
        lock_path = _acquire_migration_lock(repo_root, mapping_key)
    except MigrationError as exc:
        return MigrationResult(outcome=MigrationOutcome.REFUSED, sections=[], messages=[str(exc)])

    new_local_dir = os.path.splitext(mapping.local)[0]
    staging_dir: Optional[pathlib.Path] = None
    pre_migration_head: Optional[str] = None
    commit_landed = False
    created_paths: List[pathlib.Path] = []

    try:
        try:
            _check_clean_tree(mapping.local)

            client = backend.client
            live_sections = _split_live(client, mapping.remote_id, split_level)
            local_sections = _split_local(mapping.local, split_level)
            zipped = _zip_sections(live_sections, local_sections)
            _guard_against_single_preamble_only(zipped, mapping.local, split_level)
        except MigrationError as exc:
            return MigrationResult(
                outcome=MigrationOutcome.REFUSED, sections=[], messages=[str(exc)]
            )

        preview = MigrationPreview.from_zipped(zipped)

        if dry_run:
            return MigrationResult(
                outcome=MigrationOutcome.DRY_RUN,
                sections=[preview],
                messages=messages,
            )

        # From here on, a failure must roll back rather than simply report --
        # `_stage_sections` alone is still pre-commit (its scratch dir is
        # discarded on failure, no `_rollback` needed), but `_commit_swap`
        # onward touches git/state/config and needs the full undo path.
        prior_state_snapshot = copy.deepcopy(state.mappings)
        prior_config_snapshot = list(config.mappings)

        try:
            staging_dir = _stage_sections(zipped, new_local_dir)

            # Captured here, *before* `_commit_swap` runs, rather than relied
            # on solely as `_commit_swap`'s return value: if `git commit`
            # itself raises (including a `BaseException` like
            # `KeyboardInterrupt`) after `atomic_replace_dir`/`add`/`rm
            # --cached` have already mutated the working tree, `_commit_swap`
            # never returns and its own internally-computed
            # `pre_migration_head` would otherwise be lost -- leaving
            # `_rollback` below with `pre_migration_head=None` and unable to
            # `git reset --hard` back to a valid state.
            pre_migration_head = _run_git(
                "rev-parse", "HEAD", cwd=repo_root, timeout=_GIT_PLUMBING_TIMEOUT_S
            ).stdout.strip()

            _commit_swap(staging_dir, new_local_dir, mapping.local, repo_root)
            commit_landed = True
            staging_dir = None  # already swapped into place; nothing left to discard

            remote_version: str
            try:
                remote_version = backend.get_remote_version(mapping.remote_id)
            except Exception:
                remote_version = ""

            _record_migrated_state(
                state,
                state_path,
                state_dir,
                mapping,
                zipped,
                remote_version,
                new_local_dir,
                created_paths,
            )
            new_mapping = _apply_config_update(
                config, config_path, mapping, split_level, new_local_dir
            )
        except BaseException as exc:
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)
                staging_dir = None
            try:
                _rollback(
                    repo_root=repo_root,
                    pre_migration_head=pre_migration_head,
                    commit_landed=commit_landed,
                    created_paths=created_paths,
                    prior_state_snapshot=prior_state_snapshot if commit_landed else None,
                    prior_config_snapshot=prior_config_snapshot if commit_landed else None,
                    state=state,
                    state_path=state_path,
                    config=config,
                )
            except MigrationError as rollback_exc:
                return MigrationResult(
                    outcome=MigrationOutcome.ROLLBACK_FAILED,
                    sections=[preview],
                    messages=[str(exc), str(rollback_exc)],
                    rolled_back=False,
                )
            return MigrationResult(
                outcome=MigrationOutcome.FAILED,
                sections=[preview],
                messages=[str(exc)],
                rolled_back=True,
            )

        return MigrationResult(
            outcome=MigrationOutcome.SUCCESS,
            sections=[preview],
            messages=messages,
            new_mapping=new_mapping,
        )
    finally:
        _release_migration_lock(lock_path)
