"""Push-preview and comment/glyph risk-flagging for the Google Docs backend.

See project_plans/wedding-planning-workflow/implementation/plan.md Epic 1.2
and ADR-002 for the design rationale: a read-only substring cross-reference
against Drive's `quotedFileContent.value` (CommentCrossReference), plus a
live native-checkbox-glyph check (GlyphShapeCheck), both folded into
`find_high_risk_paragraphs()` and enforced inside `GoogleDocsBackend.push()`
itself — never by a separately-fetched CLI-layer preview.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from docspan.backends.google_docs.docs_request_builder import DiffEntry, Node


@dataclass
class HighRiskParagraph:
    """A `remove`/`change` DiffEntry that is high-risk for one or both reasons.

    A single paragraph can carry both reasons at once (e.g. a native-glyph
    paragraph that also has an open comment). `comment_quoted_text`/
    `comment_author` are only populated when "comment" is among `reasons`.
    """
    paragraph_text: str
    reasons: List[Literal["comment", "native_glyph"]]
    comment_quoted_text: Optional[str] = None
    comment_author: Optional[str] = None


def find_high_risk_paragraphs(
    entries: List[DiffEntry], comments: List[dict]
) -> List[HighRiskParagraph]:
    """Flag any `remove`/`change` DiffEntry that is high-risk to write over.

    Two independent, read-only checks, both against already-computed data
    (no second diff pass, no second document fetch):

    - CommentCrossReference ("comment"): the entry's current_text contains
      an open comment's `quotedFileContent.value` as a substring (ADR-002 —
      deliberately an approximation, not a semantic anchor decode).
    - GlyphShapeCheck ("native_glyph"): the entry's current_is_native_checkbox
      is True, i.e. DocsStructureParser resolved this paragraph as a native
      BULLET_CHECKBOX glyph on push()'s own live fetch (ADR-001).

    A paragraph with both reasons gets a single HighRiskParagraph combining
    both, not two separate entries.
    """
    high_risk: List[HighRiskParagraph] = []

    for entry in entries:
        if entry.kind not in ("remove", "change"):
            continue

        reasons: List[Literal["comment", "native_glyph"]] = []
        comment_quoted_text: Optional[str] = None
        comment_author: Optional[str] = None

        current_text = entry.current_text or ""
        for comment in comments:
            quoted = (comment.get("quotedFileContent") or {}).get("value")
            if not quoted:
                continue
            if quoted in current_text:
                reasons.append("comment")
                comment_quoted_text = quoted
                comment_author = (comment.get("author") or {}).get("displayName")
                break

        if entry.current_is_native_checkbox:
            reasons.append("native_glyph")

        if reasons:
            high_risk.append(
                HighRiskParagraph(
                    paragraph_text=entry.current_text or "",
                    reasons=reasons,
                    comment_quoted_text=comment_quoted_text,
                    comment_author=comment_author,
                )
            )

    return high_risk


def render_high_risk(high_risk: List[HighRiskParagraph]) -> str:
    """Render the ⚠ warning block(s) for a list of HighRiskParagraph.

    Shared by PushPreview.render() (--dry-run) and push()'s blocked-path
    message, so the warning text is identical in both places. Renders a
    distinct block per reason present on each paragraph — a paragraph with
    both "comment" and "native_glyph" gets both blocks, never merged into
    one message that could obscure either reason.
    """
    blocks: List[str] = []
    for hr in high_risk:
        if "comment" in hr.reasons:
            author = hr.comment_author or "unknown"
            blocks.append(
                f'⚠ COMMENT AT RISK: paragraph "{hr.paragraph_text}" has an open comment\n'
                f'  from {author} ("{hr.comment_quoted_text}") and would be changed. '
                "Resolve manually in Google\n"
                "  Docs, or re-run with --force to proceed anyway."
            )
        if "native_glyph" in hr.reasons:
            blocks.append(
                f'⚠ NATIVE CHECKBOX GLYPH: paragraph "{hr.paragraph_text}" is a native Google Docs\n'
                "  checkbox (checked/unchecked state not readable via the API) — editing it here\n"
                "  would layer literal [x]/[ ] text on top of the existing glyph. Toggle this\n"
                "  line by hand in Google Docs UI instead, or re-run with --force to proceed\n"
                "  anyway."
            )
    return "\n".join(blocks)


def _is_checklist_marker(text: Optional[str]) -> bool:
    """True if text (once stripped) starts with a literal `[ ]`/`[x]`/`[X]` marker."""
    if not text:
        return False
    return text.strip()[:3].lower() in ("[x]", "[ ]")


@dataclass
class PushPlan:
    """The internal, single-fetch snapshot a real push is gated against.

    Built by GoogleDocsBackend._build_push_plan() from exactly one
    get_document() call and exactly one list_comments() call. push() and
    preview_push() each call _build_push_plan() independently — they never
    share a plan computed by the other (see plan.md Story 1.2.3).
    """
    current_nodes: List[Node]
    target_nodes: List[Node]
    requests: List[dict]
    doc: dict
    entries: List[DiffEntry]
    unchanged_count: int
    comments: List[dict]
    high_risk: List[HighRiskParagraph]


@dataclass
class PushPreview:
    """The in-memory, human-renderable --dry-run summary.

    Read-only and cosmetic only — never consulted by a real push() to decide
    whether to write. Staleness here is acceptable because it never gates a
    write.

    `error` is set (with all other fields at their empty defaults) when
    GoogleDocsBackend.preview_push() caught a failure (expired auth, network
    error, malformed doc) while building the plan — mirroring push()'s
    try/except HttpError/except Exception -> PushResult(status="error", ...)
    convention, so a --dry-run failure renders as one clean line instead of
    a raw traceback.
    """
    entries: List[DiffEntry]
    unchanged_count: int
    high_risk: List[HighRiskParagraph]
    request_count: int
    error: Optional[str] = None

    def render(self) -> str:
        if self.error is not None:
            return f"✗ dry-run failed: {self.error}"

        additions = sum(1 for e in self.entries if e.kind == "add")
        removals = sum(1 for e in self.entries if e.kind == "remove")
        changes = sum(1 for e in self.entries if e.kind == "change")

        lines = [
            f"Preview: {changes} change(s), {additions} addition(s), "
            f"{removals} removal(s), {self.unchanged_count} unchanged"
        ]

        for entry in self.entries:
            if entry.kind == "add":
                lines.append(f"  + {entry.target_text}")
            elif entry.kind == "remove":
                lines.append(f"  - {entry.current_text}")
            elif entry.kind == "change":
                lines.append(f"  ~ {entry.current_text} → {entry.target_text}")

        checklist_flags = [
            _is_checklist_marker(e.current_text) or _is_checklist_marker(e.target_text)
            for e in self.entries
        ]
        n_checklist = sum(checklist_flags)
        n_other = len(self.entries) - n_checklist
        if n_checklist > 0 and n_other > 0:
            lines.append(
                f"ⓘ This push mixes {n_checklist} checklist toggle(s) with {n_other} "
                "other edit(s) — consider pushing checklist-only changes separately "
                "(see workflow runbook)."
            )

        if self.high_risk:
            lines.append(render_high_risk(self.high_risk))

        return "\n".join(lines)
