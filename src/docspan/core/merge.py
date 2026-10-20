"""Three-way merge using the merge3 package."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MergeResult:
    merged: str
    has_conflicts: bool
    conflict_count: int


def three_way_merge(base: str, theirs: str, ours: str) -> MergeResult:
    from merge3 import Merge3

    base_lines = base.splitlines(keepends=True)
    ours_lines = ours.splitlines(keepends=True)
    theirs_lines = theirs.splitlines(keepends=True)
    m3 = Merge3(base_lines, ours_lines, theirs_lines)
    merged_lines = list(m3.merge_lines(
        name_a="ours",
        name_b="theirs",
        start_marker="<<<<<<< ours",
        mid_marker="=======",
        end_marker=">>>>>>> theirs",
    ))
    merged = "".join(merged_lines)
    conflicts = sum(1 for line in merged_lines if line.startswith("<<<<<<<"))
    return MergeResult(merged=merged, has_conflicts=conflicts > 0, conflict_count=conflicts)
