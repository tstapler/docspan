"""Sync orchestration logic — decoupled from the CLI layer.

Each public function handles one push or pull scenario and returns a typed
outcome that the CLI can render without knowing any sync logic.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

from docspan.backends.base import Backend, PullResult, PushResult
from docspan.backends.google_docs.manifest import (
    MANIFEST_FILENAME,
    ManifestError,
    ManifestStore,
    SectionManifestEntry,
)
from docspan.core.merge import three_way_merge
from docspan.core.paths import BASE_FILE_SUFFIX, BASE_STORE_DIR, ORIG_SUFFIX, STATE_FILENAME
from docspan.core.state import MappingState, SyncState, sha256_of_content

if TYPE_CHECKING:
    from docspan.config import Mapping


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_state_path(config_path: Optional[str], prefix: Optional[str] = None) -> str:
    return os.path.join(get_state_dir(config_path, prefix), STATE_FILENAME)


def get_state_dir(config_path: Optional[str], prefix: Optional[str] = None) -> str:
    # Central-config mode: storage lives under XDG state home, namespaced by prefix.
    if prefix:
        from docspan.core.xdg import state_dir_for_prefix
        return str(state_dir_for_prefix(prefix))
    # Legacy mode: storage sits beside the markgate.yaml (or cwd).
    return _state_dir(config_path)


def _state_dir(config_path: Optional[str]) -> str:
    if config_path is not None:
        return os.path.dirname(os.path.abspath(config_path))
    return os.getcwd()


# ─────────────────────────────────────────────────────────────────────────────
# Content-addressed base store
# ─────────────────────────────────────────────────────────────────────────────

def get_base_content(state_dir: str, base_hash: str) -> str:
    """Read the merge base for a file from the content-addressed store."""
    base_path = os.path.join(state_dir, BASE_STORE_DIR, f"{base_hash}{BASE_FILE_SUFFIX}")
    if not os.path.exists(base_path):
        return ""
    with open(base_path, encoding="utf-8") as fh:
        return fh.read()


def save_base_content(state_dir: str, content: str) -> str:
    """Write content to the content-addressed base store. Returns the sha256 hex digest."""
    sha = sha256_of_content(content)
    base_dir = os.path.join(state_dir, BASE_STORE_DIR)
    os.makedirs(base_dir, exist_ok=True)
    base_path = os.path.join(base_dir, f"{sha}{BASE_FILE_SUFFIX}")
    if not os.path.exists(base_path):
        with open(base_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return sha


def _section_files(directory: str) -> list[str]:
    """List a sectioned mapping's section content files, sorted.

    Only `*.md` files are per-section content subject to the state/merge
    loop; `_manifest.yaml` and any other sidecar is not.
    """
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".md"))


def _load_manifest_entries(directory: str) -> list[SectionManifestEntry]:
    """Load `_manifest.yaml` from `directory`, or `[]` if absent/unreadable.

    A missing manifest is expected on a mapping's very first sectioned pull
    (nothing has ever been written there yet); a malformed one is treated
    the same way rather than failing the whole pull, since manifest.py's own
    `ManifestStore.load` is only a rename-detection aid here, not the
    source of truth for section content.
    """
    manifest_path = os.path.join(directory, MANIFEST_FILENAME)
    try:
        return ManifestStore.load(manifest_path)
    except ManifestError:
        return []


def _detect_section_renames(
    old_dir: str, new_dir: str
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Diff old vs. new `_manifest.yaml` by `heading_id` to find renamed sections.

    Returns `(renumbered_only, content_renamed)`, each a list of
    `(old_filename, new_filename)` pairs. A `heading_id` present in both
    manifests whose `filename` changed is a rename; it is "renumbering-only"
    when the `slug` is unchanged (only the `NN` ordinal prefix shifted,
    e.g. because a sibling section was inserted/deleted elsewhere) and
    "content-driven" otherwise (the heading text itself changed, so
    `section_splitter.split_nodes` derived a new slug — see Gap 1's
    heading_id-match logic in `section_splitter.py`).
    """
    old_entries = _load_manifest_entries(old_dir)
    new_entries = _load_manifest_entries(new_dir)
    # A heading missing a Docs-assigned heading_id lands here as `""`
    # (section_splitter.py's `heading_id or ""`). Two or more such headings
    # in the same pull would otherwise collapse onto the same `""` dict key
    # and silently clobber each other's rename-detection entry, so blank
    # ids are excluded from the identity map entirely — they're always
    # treated as new/unmatched rather than merged.
    old_by_id = {e.heading_id: e for e in old_entries if e.heading_id}
    new_by_id = {e.heading_id: e for e in new_entries if e.heading_id}

    renumbered_only: list[tuple[str, str]] = []
    content_renamed: list[tuple[str, str]] = []
    for heading_id, old_entry in old_by_id.items():
        new_entry = new_by_id.get(heading_id)
        if new_entry is None or new_entry.filename == old_entry.filename:
            continue
        pair = (old_entry.filename, new_entry.filename)
        if new_entry.slug == old_entry.slug:
            renumbered_only.append(pair)
        else:
            content_renamed.append(pair)
    return renumbered_only, content_renamed


def _rekey_renamed_sections(
    canonical_dir: str,
    physical_dir: str,
    state: SyncState,
    renames: list[tuple[str, str]],
) -> None:
    """Move (not duplicate) each renamed section's state entry and local file.

    Without this, a rename would orphan the old path's `MappingState` entry
    (leaving stale, never-cleaned-up state) while the new path is treated as
    a first-sync — discarding the section's actual merge history/local_hash
    even though its heading_id/content identity survived the rename.

    `canonical_dir` and `physical_dir` are split because
    `_orchestrate_pull_sectioned` stages every write for a pull in a scratch
    directory before atomically swapping it into place (Epic 6 Story 6.1):
    `state.mappings` must always be keyed by the real `mapping.local` path
    (`canonical_dir`) since that's what every other lookup in this module
    keys against and what survives the eventual swap, but the actual file
    being renamed on disk right now still lives in the scratch directory
    (`physical_dir`) until the swap happens.
    """
    for old_filename, new_filename in renames:
        old_canonical = os.path.join(canonical_dir, old_filename)
        new_canonical = os.path.join(canonical_dir, new_filename)
        if old_canonical in state.mappings:
            state.mappings[new_canonical] = state.mappings.pop(old_canonical)

        old_physical = os.path.join(physical_dir, old_filename)
        new_physical = os.path.join(physical_dir, new_filename)
        if os.path.exists(old_physical):
            if os.path.exists(new_physical):
                # The state entry above was already rekeyed to `new_filename`,
                # but the physical rename can't proceed without clobbering
                # whatever is already there. Leaving this unlogged would
                # silently strand `old_physical` as an untracked file that
                # can never be flagged as orphaned again (its heading_id now
                # matches the new entry) — surface it instead of losing it
                # quietly.
                logger.warning(
                    "Skipping rename of %r to %r during sectioned pull: "
                    "target already exists. The state entry has been rekeyed, "
                    "but %r was left in place untracked — resolve manually.",
                    old_physical, new_physical, old_physical,
                )
            else:
                os.rename(old_physical, new_physical)


def _detect_orphaned_sections(
    old_dir: str, new_dir: str
) -> list[SectionManifestEntry]:
    """Sections the prior manifest knew about that vanished from a fresh pull.

    heading_id-keyed so a rename (already handled by `_detect_section_renames`)
    is never mistaken for a deletion — an entry only lands here when its
    heading_id is genuinely absent from the freshly-pulled manifest, not
    just renamed/renumbered. Per plan.md's Domain Glossary ("Orphan
    section"), this must be surfaced as a conflict, never silently dropped
    or silently kept as if nothing happened.
    """
    old_entries = _load_manifest_entries(old_dir)
    # Blank heading_ids (missing Docs-assigned id) are excluded from the
    # "known ids" set for the same reason as `_detect_section_renames`:
    # they must never be treated as matching each other, so a
    # heading_id-less old entry is always reported as orphaned rather than
    # spuriously "found" via an unrelated blank-id new entry.
    new_ids = {e.heading_id for e in _load_manifest_entries(new_dir) if e.heading_id}
    return [e for e in old_entries if not e.heading_id or e.heading_id not in new_ids]


def _atomic_replace_dir(tmp_dir: str, target_dir: str) -> None:
    """Atomically replace `target_dir`'s contents with `tmp_dir`'s.

    String-path analog of `backends/google_docs/backend.py`'s
    `GoogleDocsBackend._atomic_replace_dir` (the pattern `pull_sectioned`
    already uses for its own directory swap): move any existing target
    aside to a sibling `.old.` directory via `os.replace` (same filesystem,
    so this step alone can't partially fail), then `os.replace` the staged
    directory into the target's place; on any failure, restore the
    original target from the `.old.` sibling before re-raising, so a crash
    at any point leaves `target_dir` either fully absent (first sync only)
    or fully intact — never half-written.
    """
    old_dir: Optional[str] = None
    if os.path.exists(target_dir):
        old_dir = tempfile.mkdtemp(
            dir=os.path.dirname(os.path.abspath(target_dir)),
            prefix=f".{os.path.basename(target_dir)}.old.",
            suffix=".tmp",
        )
        os.replace(target_dir, old_dir)
    try:
        os.replace(tmp_dir, target_dir)
    except Exception:
        if old_dir is not None:
            os.replace(old_dir, target_dir)
        raise
    else:
        if old_dir is not None:
            shutil.rmtree(old_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Outcome types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PushOutcome:
    local_path: str
    result: PushResult
    state_saved: bool = False


@dataclass
class PullOutcome:
    local_path: str
    # "first-sync" | "fast-forward" | "merged" | "up-to-date" | "local-only" | "error"
    action: str
    result: Optional[PullResult] = None
    has_conflicts: bool = False
    conflict_count: int = 0
    # Sectioned pulls only (plan.md Task 2.2.2 / Observability Plan): section
    # renames detected via heading_id-match against the prior manifest,
    # split into renumbering-only (same heading_id/slug, only the `NN`
    # ordinal prefix shifted because a sibling section was added/removed)
    # versus content-driven (heading_id matched but the slug/content itself
    # changed) — each entry is (old_filename, new_filename), so a user isn't
    # misled into thinking an untouched section's heading/content changed
    # when only its position did.
    renumbered_only: list = field(default_factory=list)
    content_renamed: list = field(default_factory=list)
    # Sectioned pulls only: filenames of sections the prior manifest knew
    # about that vanished from this pull's fresh manifest (see
    # `_detect_orphaned_sections`). Each is converted into an explicit
    # conflict in its own section file (never silently dropped, never
    # silently auto-deleted) — this list is purely for observability/
    # reporting on top of that.
    orphaned_sections: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# State recording
# ─────────────────────────────────────────────────────────────────────────────

def record_state(
    state: SyncState,
    state_path: str,
    state_dir: str,
    local_path: str,
    doc_id: str,
    backend_name: str,
    content: str,
    remote_version: str,
    save: bool = True,
) -> bool:
    """Persist sync state after a successful operation. Returns True on success.

    `save=False` updates `state` in memory (and writes the content-addressed
    base blob, which is idempotent and has no ordering dependency on the
    caller's own atomicity) but skips the `state.save(state_path)` disk
    write — used by `_orchestrate_pull_sectioned`'s per-section loop so the
    on-disk state file is only ever flushed once, after that function's
    directory-level atomic swap has actually succeeded. Flushing state to
    disk before the swap would let `.markgate-state.json` describe section
    content that a crash between the two writes never actually delivered
    into the real target directory.
    """
    try:
        local_hash = sha256_of_content(content)
        base_hash = save_base_content(state_dir, content)
        state.update(
            local_path,
            MappingState(
                doc_id=doc_id,
                backend=backend_name,
                last_synced_at=datetime.now(timezone.utc).isoformat(),
                base_hash=base_hash,
                remote_version=remote_version,
                local_hash=local_hash,
            ),
        )
        if save:
            state.save(state_path)
        return True
    except Exception:
        logger.warning("Failed to save sync state for %s", local_path, exc_info=True)
        return False


def _record_state(
    state: SyncState,
    state_path: str,
    state_dir: str,
    local_path: str,
    mapping: "Mapping",
    content: str,
    remote_version: str,
    save: bool = True,
) -> bool:
    assert mapping.remote_id is not None
    return record_state(
        state, state_path, state_dir, local_path,
        mapping.remote_id, mapping.backend, content, remote_version, save=save,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Push orchestration
# ─────────────────────────────────────────────────────────────────────────────

def orchestrate_push(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
    force: bool = False,
    mappings: Optional[list] = None,
    cross_doc_cache: Optional[dict] = None,
) -> PushOutcome:
    """`mappings` is the full markgate.yaml mapping list (not just this one) —
    threaded through to backend.push() as a kwarg so a Google Docs push can
    resolve relative cross-document links to other mapped files' remote URLs.
    None (the default) preserves old behavior: no cross-doc resolution.

    `cross_doc_cache`, when supplied by the caller and reused across multiple
    orchestrate_push calls in one run, lets a heading fetch for one target
    document be shared across every pushed document that links to it —
    a `push --all` run doesn't refetch the same target once per pushing
    document.
    """
    assert mapping.remote_id is not None, "orchestrate_push requires a mapping with a created remote doc/page"

    if mapping.sectioned:
        result = backend.push_sectioned(
            mapping.local, mapping.remote_id, force=force, tab_id=mapping.tab_id,
            mappings=mappings, cross_doc_cache=cross_doc_cache,
        )
    else:
        result = backend.push(
            mapping.local, mapping.remote_id, force=force, tab_id=mapping.tab_id,
            mappings=mappings, cross_doc_cache=cross_doc_cache,
        )
    outcome = PushOutcome(local_path=mapping.local, result=result)

    if result.status in ("ok", "warning") and os.path.exists(mapping.local):
        try:
            remote_version = backend.get_remote_version(mapping.remote_id)
        except Exception:
            logger.warning(
                "Could not retrieve remote version after push for %s; "
                "recording empty version — next pull will re-sync",
                mapping.remote_id,
                exc_info=True,
            )
            remote_version = ""

        if mapping.sectioned:
            # One state entry per section file, keyed by that file's own
            # path — reuses the existing (already generically-keyed) state
            # store without needing any change to SyncState itself.
            saved = True
            for filename in _section_files(mapping.local):
                section_path = os.path.join(mapping.local, filename)
                with open(section_path, encoding="utf-8") as fh:
                    content = fh.read()
                saved = _record_state(
                    state, state_path, state_dir, section_path, mapping, content, remote_version
                ) and saved
            outcome.state_saved = saved
        else:
            with open(mapping.local, encoding="utf-8") as fh:
                content = fh.read()
            outcome.state_saved = _record_state(
                state, state_path, state_dir, mapping.local, mapping, content, remote_version
            )

    return outcome


# ─────────────────────────────────────────────────────────────────────────────
# Pull orchestration
# ─────────────────────────────────────────────────────────────────────────────

def orchestrate_pull(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
) -> PullOutcome:
    assert mapping.remote_id is not None, "orchestrate_pull requires a mapping with a created remote doc/page"

    if mapping.sectioned:
        return _orchestrate_pull_sectioned(mapping, backend, state, state_dir, state_path)

    entry = state.get(mapping.local)

    local_exists = os.path.exists(mapping.local)
    if local_exists:
        with open(mapping.local, encoding="utf-8") as fh:
            local_content = fh.read()
        current_local_hash = sha256_of_content(local_content)
    else:
        local_content = ""
        current_local_hash = ""

    try:
        remote_version = backend.get_remote_version(mapping.remote_id)
    except Exception as exc:
        return PullOutcome(
            local_path=mapping.local,
            action="error",
            result=PullResult(
                status="error",
                doc_id=mapping.remote_id,
                local_path=mapping.local,
                message=str(exc),
            ),
        )

    if entry is None:
        return _first_sync_pull(mapping, backend, state, state_dir, state_path, remote_version)

    remote_changed = remote_version != entry.remote_version
    local_changed = local_exists and current_local_hash != entry.local_hash

    if not remote_changed and not local_changed:
        return PullOutcome(local_path=mapping.local, action="up-to-date")

    if remote_changed and not local_changed:
        return _fast_forward_pull(
            mapping, backend, state, state_dir, state_path, remote_version
        )

    if local_changed and not remote_changed:
        return PullOutcome(local_path=mapping.local, action="local-only")

    # Both sides changed — three-way merge
    return _merge_pull(
        mapping, backend, state, state_dir, state_path,
        local_content, remote_version, entry.base_hash,
    )


def _first_sync_pull(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
    remote_version: str,
) -> PullOutcome:
    assert mapping.remote_id is not None
    result = backend.pull(mapping.remote_id, mapping.local, tab_id=mapping.tab_id)
    outcome = PullOutcome(local_path=mapping.local, action="first-sync", result=result)
    if result.status in ("ok", "warning") and os.path.exists(mapping.local):
        with open(mapping.local, encoding="utf-8") as fh:
            new_content = fh.read()
        _record_state(
            state, state_path, state_dir, mapping.local, mapping,
            new_content, remote_version or "",
        )
    return outcome


def _fast_forward_pull(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
    remote_version: str,
) -> PullOutcome:
    assert mapping.remote_id is not None
    result = backend.pull(mapping.remote_id, mapping.local, tab_id=mapping.tab_id)
    outcome = PullOutcome(local_path=mapping.local, action="fast-forward", result=result)
    if result.status in ("ok", "warning") and os.path.exists(mapping.local):
        with open(mapping.local, encoding="utf-8") as fh:
            new_content = fh.read()
        _record_state(
            state, state_path, state_dir, mapping.local, mapping,
            new_content, remote_version,
        )
    return outcome


def _merge_pull(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
    local_content: str,
    remote_version: str,
    base_hash: str,
) -> PullOutcome:
    assert mapping.remote_id is not None
    orig_path = mapping.local + ORIG_SUFFIX
    with open(orig_path, "w", encoding="utf-8") as fh:
        fh.write(local_content)

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp_path = tmp.name
        tmp_result = backend.pull(mapping.remote_id, tmp_path, tab_id=mapping.tab_id)
        if tmp_result.status in ("ok", "warning"):
            with open(tmp_path, encoding="utf-8") as fh:
                theirs_content = fh.read()
            os.unlink(tmp_path)
        else:
            os.unlink(tmp_path)
            return PullOutcome(
                local_path=mapping.local, action="error", result=tmp_result
            )
    except Exception as exc:
        return PullOutcome(
            local_path=mapping.local,
            action="error",
            result=PullResult(
                status="error",
                doc_id=mapping.remote_id,
                local_path=mapping.local,
                message=str(exc),
            ),
        )

    base_content = get_base_content(state_dir, base_hash)
    merge_result = three_way_merge(base_content, theirs_content, local_content)

    with open(mapping.local, "w", encoding="utf-8") as fh:
        fh.write(merge_result.merged)

    _record_state(
        state, state_path, state_dir, mapping.local, mapping,
        merge_result.merged, remote_version,
    )

    return PullOutcome(
        local_path=mapping.local,
        action="merged",
        result=tmp_result,
        has_conflicts=merge_result.has_conflicts,
        conflict_count=merge_result.conflict_count,
    )


def _orchestrate_pull_sectioned(
    mapping: "Mapping",
    backend: Backend,
    state: SyncState,
    state_dir: str,
    state_path: str,
) -> PullOutcome:
    """Pull dispatch for `mapping.sectioned` mappings.

    A Google Doc has one remote_version for the whole document, so unlike
    the single-file path that alone can't tell us which *section* changed.
    Instead this always fetches a fresh split into a throwaway temp
    directory, then runs the same first-sync/fast-forward/local-only/
    three-way-merge decision independently per section file — each keyed by
    that section file's own path in `state`, so a conflict in one section
    never touches another section's merge base.
    """
    assert mapping.remote_id is not None

    try:
        remote_version = backend.get_remote_version(mapping.remote_id)
    except Exception as exc:
        return PullOutcome(
            local_path=mapping.local,
            action="error",
            result=PullResult(
                status="error", doc_id=mapping.remote_id, local_path=mapping.local, message=str(exc),
            ),
        )

    canonical_dir = mapping.local

    with tempfile.TemporaryDirectory() as tmp_dir:
        pull_result = backend.pull_sectioned(
            mapping.remote_id, tmp_dir, split_level=mapping.split_level, tab_id=mapping.tab_id,
        )
        if pull_result.status not in ("ok", "warning"):
            return PullOutcome(local_path=mapping.local, action="error", result=pull_result)

        # Story 2.2 / Task 2.2.2: detect section renames via heading_id-match
        # (section_splitter.split_nodes already applied this when producing
        # the fresh split in `tmp_dir`) before the per-file merge loop below,
        # so a renamed section's prior state entry is found under its *new*
        # path rather than looking like a fresh first-sync. Also detect
        # orphaned sections (Domain Glossary: "surfaced as conflict, never
        # silently dropped") the same way, before anything is written.
        renumbered_only, content_renamed = _detect_section_renames(canonical_dir, tmp_dir)
        orphaned_entries = _detect_orphaned_sections(canonical_dir, tmp_dir)

        # Epic 6 Story 6.1: stage every write for this pull — renames,
        # per-section merges, and the fresh manifest — in a scratch
        # directory seeded from the current live directory, then swap it
        # into place with a single `_atomic_replace_dir` call at the end.
        # Before this, each `open(local_section_path, "w")` in the merge
        # loop below wrote straight into the live `mapping.local` directory,
        # so a crash partway through left a mix of old and new section
        # files; only the throwaway fetch into `tmp_dir` above was ever
        # atomic. Persisting `state` is deferred the same way (`save=False`
        # below, single `state.save(state_path)` after the swap succeeds)
        # so the state file never describes section content that a crash
        # before the swap kept the real directory from ever receiving.
        staging_parent = os.path.dirname(os.path.abspath(canonical_dir))
        os.makedirs(staging_parent, exist_ok=True)
        staging_dir = tempfile.mkdtemp(
            dir=staging_parent,
            prefix=f".{os.path.basename(canonical_dir)}.staging.",
            suffix=".tmp",
        )
        try:
            if os.path.isdir(canonical_dir):
                shutil.copytree(canonical_dir, staging_dir, dirs_exist_ok=True)

            if renumbered_only or content_renamed:
                _rekey_renamed_sections(
                    canonical_dir, staging_dir, state, renumbered_only + content_renamed
                )

            fresh_manifest_path = os.path.join(tmp_dir, MANIFEST_FILENAME)
            if os.path.exists(fresh_manifest_path):
                shutil.copyfile(
                    fresh_manifest_path, os.path.join(staging_dir, MANIFEST_FILENAME)
                )

            any_merge = False
            any_conflicts = False
            conflict_total = 0
            written_files = 0
            first_sync_files = 0

            for filename in _section_files(tmp_dir):
                theirs_path = os.path.join(tmp_dir, filename)
                # `local_section_path` is the canonical (real, post-swap)
                # path — used only as the `state` key, since that key must
                # survive the eventual swap unchanged. `staged_section_path`
                # is both where this loop writes *and* where it reads the
                # section's current local content from: `staging_dir` was
                # seeded from `canonical_dir` and already has any rename
                # from `_rekey_renamed_sections` applied, so it (not
                # `canonical_dir`, which for a renamed section is still
                # sitting under the *old* filename) is the accurate source
                # for "what does this section look like right now."
                local_section_path = os.path.join(canonical_dir, filename)
                staged_section_path = os.path.join(staging_dir, filename)
                with open(theirs_path, encoding="utf-8") as fh:
                    theirs_content = fh.read()

                entry = state.get(local_section_path)

                if entry is None:
                    with open(staged_section_path, "w", encoding="utf-8") as fh:
                        fh.write(theirs_content)
                    _record_state(
                        state, state_path, state_dir, local_section_path, mapping,
                        theirs_content, remote_version, save=False,
                    )
                    written_files += 1
                    first_sync_files += 1
                    continue

                local_exists = os.path.exists(staged_section_path)
                local_content = ""
                if local_exists:
                    with open(staged_section_path, encoding="utf-8") as fh:
                        local_content = fh.read()

                current_local_hash = sha256_of_content(local_content) if local_exists else ""
                local_changed = local_exists and current_local_hash != entry.local_hash
                remote_changed = sha256_of_content(theirs_content) != entry.base_hash

                if not remote_changed and not local_changed:
                    continue

                if remote_changed and not local_changed:
                    with open(staged_section_path, "w", encoding="utf-8") as fh:
                        fh.write(theirs_content)
                    _record_state(
                        state, state_path, state_dir, local_section_path, mapping,
                        theirs_content, remote_version, save=False,
                    )
                    written_files += 1
                    continue

                if local_changed and not remote_changed:
                    # Local-only edit to this section — leave it for the user to push.
                    continue

                # Both sides changed this section — three-way merge, scoped to it alone.
                # Mirror _merge_pull's .orig backup so `conflicts resolve --accept
                # local` has the pre-merge section content to restore instead of
                # silently falling back to the merge base.
                any_merge = True
                written_files += 1
                orig_path = staged_section_path + ORIG_SUFFIX
                with open(orig_path, "w", encoding="utf-8") as fh:
                    fh.write(local_content)
                base_content = get_base_content(state_dir, entry.base_hash)
                merge_result = three_way_merge(base_content, theirs_content, local_content)
                with open(staged_section_path, "w", encoding="utf-8") as fh:
                    fh.write(merge_result.merged)
                _record_state(
                    state, state_path, state_dir, local_section_path, mapping,
                    merge_result.merged, remote_version, save=False,
                )
                if merge_result.has_conflicts:
                    any_conflicts = True
                    conflict_total += merge_result.conflict_count

            # Orphaned sections: present in the prior manifest but absent
            # from this pull's fresh manifest. Per plan.md's Domain
            # Glossary this must be "surfaced as conflict, never silently
            # dropped" — so rather than leaving the staged copy (inherited
            # unchanged from `canonical_dir` via the copytree above) as if
            # nothing happened, or deleting it, convert it into an explicit
            # conflict in its own file, exactly like a genuine two-sided
            # merge conflict above, so `docspan conflicts list/resolve`
            # finds it.
            for orphan_entry in orphaned_entries:
                local_section_path = os.path.join(canonical_dir, orphan_entry.filename)
                staged_section_path = os.path.join(staging_dir, orphan_entry.filename)
                if not os.path.exists(staged_section_path):
                    continue  # nothing local survives to protect
                with open(staged_section_path, encoding="utf-8") as fh:
                    local_content = fh.read()

                orig_path = staged_section_path + ORIG_SUFFIX
                with open(orig_path, "w", encoding="utf-8") as fh:
                    fh.write(local_content)

                conflict_content = (
                    "<<<<<<< ours\n"
                    f"{local_content}"
                    "=======\n"
                    ">>>>>>> theirs (section removed upstream)\n"
                )
                with open(staged_section_path, "w", encoding="utf-8") as fh:
                    fh.write(conflict_content)
                _record_state(
                    state, state_path, state_dir, local_section_path, mapping,
                    conflict_content, remote_version, save=False,
                )
                any_conflicts = True
                conflict_total += 1
                written_files += 1

            _atomic_replace_dir(staging_dir, canonical_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        # Only reached once the atomic swap above has actually succeeded —
        # see the `save=False` calls throughout the loop.
        state.save(state_path)

        if any_merge or any_conflicts:
            action = "merged"
        elif written_files > 0 and written_files == first_sync_files:
            action = "first-sync"
        elif written_files > 0:
            action = "fast-forward"
        else:
            action = "up-to-date"

        return PullOutcome(
            local_path=mapping.local,
            action=action,
            result=pull_result,
            has_conflicts=any_conflicts,
            conflict_count=conflict_total,
            renumbered_only=renumbered_only,
            content_renamed=content_renamed,
            orphaned_sections=[e.filename for e in orphaned_entries],
        )
