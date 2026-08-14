"""Split a projected node list into sections at a configured heading level.

Runs *after* `projection.project()` — the split has no opinion on residue,
it only groups whatever node list it is handed. A single forward pass over
the flat `List[Node]` starts a new section at each node whose `style`
matches `split_level`; everything before the first such node is the
"preamble" section (`manifest.PREAMBLE_HEADING_ID`), which still gets a
real section file since it is real doc content with no heading of its own.

See `project_plans/gdocs-sectioned-sync/implementation/plan.md`'s Epic 2 for
the spec this implements (Story 2.1: split; Story 2.2: heading_id-based
rename matching against an existing manifest).
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Sequence

from docspan.backends.google_docs.heading_anchors import is_heading_style, slugify_all
from docspan.backends.google_docs.manifest import PREAMBLE_HEADING_ID, SectionManifestEntry
from docspan.backends.google_docs.projection import Node

# The preamble has no heading text to slug — a real heading named "" would be
# a pathological edge case, but the preamble *always* lacks one, so it gets a
# fixed slug rather than whatever `slugify("")` happens to produce. Matches
# plan.md's own Story 2.1 example verbatim ("00-preamble.md").
PREAMBLE_SLUG = "preamble"


class SectionSplitError(Exception):
    """Raised when `split_nodes` cannot honor the configured `split_level`."""


@dataclasses.dataclass
class Section:
    """One contiguous run of nodes belonging to a single section.

    `heading_id` is `PREAMBLE_HEADING_ID` for the pre-first-heading section,
    otherwise the Docs-assigned id of the section's own heading node.
    `title` is the raw heading text ("" for the preamble). `slug` is the
    filename-safe slug (post duplicate-disambiguation). `nodes` is the run
    of nodes belonging to the section, heading node included.
    """

    heading_id: str
    title: str
    nodes: List[Node]
    slug: str


def _heading_style_rank(style: str) -> Optional[int]:
    """Numeric rank for a `HEADING_N` style string, or None if not a heading."""
    if not style.startswith("HEADING_"):
        return None
    try:
        return int(style.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def split_nodes(
    nodes: Sequence[Node],
    split_level: str,
    existing_entries: Optional[Sequence[SectionManifestEntry]] = None,
) -> List[Section]:
    """Split `nodes` into `Section`s at every node whose `style == split_level`.

    Args:
        nodes: The projected node list (post `projection.project()`).
        split_level: A heading style string, e.g. "HEADING_1". Only a node
            whose `style` equals this exactly starts a new section — a
            deeper heading (e.g. HEADING_2 when split_level is HEADING_1)
            stays inside the enclosing section, same as any other content.
        existing_entries: The sectioned mapping's current `_manifest.yaml`
            entries, if any (Story 2.2). When given, a section whose
            heading node's `heading_id` matches an existing entry reuses
            that entry's `slug`/`filename` stem rather than re-deriving one
            from (possibly renamed) heading text — except when the heading
            text has changed, where the point of matching by id is instead
            to reslug and let the caller detect+apply the rename. Concretely:
            matching by heading_id here means "this is the same section",
            which is exposed by keeping heading_id-identity front and center;
            filename/slug *derivation* below always tracks current heading
            text, and the caller (e.g. pull_sectioned) is the one that
            diffs old vs. new filename to detect a rename.

    Returns:
        Sections in document order, preamble first. The preamble is always
        included (even if empty) when `nodes` is empty or has no leading
        content, only when there is a well-defined split target ahead of a
        non-empty doc — see the empty-document handling below.

    Raises:
        SectionSplitError: `split_level` is not present anywhere in `nodes`
            but a different heading level is, naming the deepest heading
            style actually present. An empty document (no headings at all)
            is not an error — it produces a single preamble section, since
            there is nothing to name as "the deepest heading found".
    """
    target_rank = _heading_style_rank(split_level)
    if target_rank is None:
        raise SectionSplitError(
            f"split_level {split_level!r} is not a HEADING_N style"
        )

    heading_ranks_present = {
        rank
        for node in nodes
        if is_heading_style(getattr(node, "style", None))
        and (rank := _heading_style_rank(getattr(node, "style"))) is not None
    }
    if heading_ranks_present and target_rank not in heading_ranks_present:
        deepest = max(heading_ranks_present)
        raise SectionSplitError(
            f"split_level {split_level!r} does not appear in this document; "
            f"the deepest heading style present is HEADING_{deepest}"
        )

    # Group into raw (heading_id_or_None, title, nodes) runs first.
    raw_groups: List[tuple] = []
    current_heading_id: Optional[str] = None
    current_title = ""
    current_nodes: List[Node] = []

    def _flush() -> None:
        raw_groups.append((current_heading_id, current_title, current_nodes))

    for node in nodes:
        style = getattr(node, "style", None)
        if style == split_level:
            _flush()
            current_heading_id = getattr(node, "heading_id", None)
            current_title = getattr(node, "text", "") or ""
            current_nodes = [node]
        else:
            current_nodes.append(node)
    _flush()

    # raw_groups[0] is always the preamble (possibly empty nodes list when
    # the very first node is itself a split-level heading, or when `nodes`
    # is empty entirely).
    _preamble_heading_id, preamble_title, preamble_nodes = raw_groups[0]
    section_groups = raw_groups[1:]

    # `existing_entries` (Story 2.2 / Task 2.2.1): match each section to a
    # prior manifest entry by `heading_id` first. When a match is found *and*
    # the heading text is unchanged (per the entry's stored `title`), reuse
    # the entry's existing slug verbatim rather than re-deriving+
    # re-disambiguating it — this is what prevents an unrelated edit
    # elsewhere in the doc (e.g. a duplicate-titled heading inserted before
    # this one) from spuriously shifting this section's slug even though its
    # own heading_id/content never changed. A match whose heading text *has*
    # changed is re-slugified below (git-mv semantics: same identity, new
    # name) — this is the committed rename behavior (plan.md Story 2.2). A
    # heading_id with no match (or missing entirely — `""` never equals a
    # real Docs-assigned id) is always "genuinely new", so it always gets a
    # freshly derived slug.
    existing_by_id = {
        e.heading_id: e for e in (existing_entries or []) if e.heading_id != PREAMBLE_HEADING_ID
    }

    titles = [title for _, title, _ in section_groups]
    fresh_slugs = slugify_all(titles)

    slugs: List[str] = []
    for (heading_id, title, _), fresh_slug in zip(section_groups, fresh_slugs):
        entry = existing_by_id.get(heading_id) if heading_id else None
        if entry is not None and entry.title == title:
            slugs.append(entry.slug)
        else:
            slugs.append(fresh_slug)

    sections: List[Section] = [
        Section(
            heading_id=PREAMBLE_HEADING_ID,
            title=preamble_title,
            nodes=preamble_nodes,
            slug=PREAMBLE_SLUG,
        )
    ]
    for (heading_id, title, group_nodes), slug in zip(section_groups, slugs):
        sections.append(
            Section(
                heading_id=heading_id or "",
                title=title,
                nodes=group_nodes,
                slug=slug,
            )
        )
    return sections


__all__ = ["PREAMBLE_SLUG", "Section", "SectionSplitError", "split_nodes"]
