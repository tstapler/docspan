"""Sync orchestration logic — decoupled from the CLI layer.

Each public function handles one push or pull scenario and returns a typed
outcome that the CLI can render without knowing any sync logic.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

from docspan.backends.base import Backend, PullResult, PushResult
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
) -> bool:
    """Persist sync state after a successful operation. Returns True on success."""
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
) -> bool:
    return record_state(
        state, state_path, state_dir, local_path,
        mapping.remote_id, mapping.backend, content, remote_version,
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
) -> PushOutcome:
    result = backend.push(mapping.local, mapping.remote_id)
    outcome = PushOutcome(local_path=mapping.local, result=result)

    if result.status == "ok" and os.path.exists(mapping.local):
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
    result = backend.pull(mapping.remote_id, mapping.local)
    outcome = PullOutcome(local_path=mapping.local, action="first-sync", result=result)
    if result.status == "ok" and os.path.exists(mapping.local):
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
    result = backend.pull(mapping.remote_id, mapping.local)
    outcome = PullOutcome(local_path=mapping.local, action="fast-forward", result=result)
    if result.status == "ok" and os.path.exists(mapping.local):
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
    orig_path = mapping.local + ORIG_SUFFIX
    with open(orig_path, "w", encoding="utf-8") as fh:
        fh.write(local_content)

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp_path = tmp.name
        tmp_result = backend.pull(mapping.remote_id, tmp_path)
        if tmp_result.status == "ok":
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
        has_conflicts=merge_result.has_conflicts,
        conflict_count=merge_result.conflict_count,
    )
