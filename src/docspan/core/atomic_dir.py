"""Shared atomic directory-swap helper.

Used by both `core/orchestrator.py` (string paths) and
`backends/google_docs/backend.py` (`pathlib.Path`s) to atomically replace a
target directory's contents with a fully-staged temp directory's — see
`atomic_replace_dir`'s docstring for the swap strategy.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from os import PathLike
from typing import Optional, Union

logger = logging.getLogger(__name__)

StrOrPath = Union[str, "PathLike[str]"]


def atomic_replace_dir(tmp_dir: StrOrPath, target_dir: StrOrPath) -> None:
    """Atomically replace `target_dir`'s contents with `tmp_dir`'s.

    `os.replace` refuses to replace a non-empty directory outright (POSIX
    rename semantics), so a direct `os.replace(tmp_dir, target_dir)` would
    raise `OSError` whenever `target_dir` already has content in it — the
    common re-pull/re-push case. Instead, an existing `target_dir` is first
    moved aside to an empty sibling temp directory (itself a single atomic
    rename), then `tmp_dir` takes its place. Both are individually atomic
    renames on the same filesystem; a crash between them leaves the prior
    content recoverable, still intact, at the `.old.` sibling rather than
    lost.

    On a failure to swap `tmp_dir` into place, the prior contents are
    restored from the `.old.` sibling before the original exception is
    re-raised — unless that restore itself also fails, in which case both
    failures are logged (never silently masking one with the other) and the
    restore failure is raised, chained to the original swap-in failure.
    """
    target_dir = os.fspath(target_dir)
    tmp_dir = os.fspath(tmp_dir)

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
    except Exception as swap_exc:
        if old_dir is not None:
            try:
                os.replace(old_dir, target_dir)
            except Exception as restore_exc:
                # Double failure: the swap-in failed *and* restoring the
                # prior contents from the `.old.` sibling also failed.
                # Letting `restore_exc` propagate unguarded would silently
                # mask `swap_exc` with no record of either -- log both
                # before raising the restore failure (the more urgent of
                # the two: `target_dir` may now be missing entirely),
                # chained to the original for full context.
                logger.error(
                    "Failed to swap %r into %r: %r", tmp_dir, target_dir, swap_exc,
                    exc_info=swap_exc,
                )
                logger.error(
                    "Restoring %r from %r after the failed swap also failed: %r",
                    target_dir, old_dir, restore_exc, exc_info=restore_exc,
                )
                raise restore_exc from swap_exc
        raise
    else:
        if old_dir is not None:
            shutil.rmtree(old_dir, ignore_errors=True)


__all__ = ["atomic_replace_dir"]
