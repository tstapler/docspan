"""Render Google Docs comments (Drive API shape) into a markdown sidecar,
and parse Reply:/Resolve: directives written back into that sidecar."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def _author(node: dict) -> str:
    return (node.get("author") or {}).get("displayName") or "Unknown"


def _render_comment(comment: dict) -> List[str]:
    comment_id = comment.get("id", "")
    header = f"### {_author(comment)} — {comment.get('createdTime', '')}".rstrip(" —")
    lines: List[str] = [f"{header} <!-- id:{comment_id} -->", ""]

    quoted = (comment.get("quotedFileContent") or {}).get("value", "").strip()
    if quoted:
        lines += [f"> {quoted}", ""]

    content = (comment.get("content") or "").strip()
    if content:
        lines += [content, ""]

    for reply in comment.get("replies") or []:
        rtext = (reply.get("content") or "").strip()
        prefix = f"- **{_author(reply)}**"
        lines.append(f"{prefix}: {rtext}" if rtext else f"{prefix}: (no text)")
    if comment.get("replies"):
        lines.append("")

    if not comment.get("resolved"):
        lines += [
            "<!-- To reply, type after \"Reply:\" below. To resolve, change \"Resolve: no\" to "
            "\"Resolve: yes\". Then run `docspan comments respond <file>`. -->",
            "Reply:",
            "Resolve: no",
            "",
        ]

    lines += ["---", ""]
    return lines


def format_comments_markdown(title: str, comments: List[dict]) -> str:
    """
    Build a `{doc}.comments.md` sidecar from Drive `comments.list` results.

    Groups into Open / Resolved, preserving quoted selections and reply threads.
    Open comments get an editable Reply:/Resolve: directive block for
    `docspan comments respond`. Returns an empty string when there are no comments.
    """
    if not comments:
        return ""

    open_comments = [c for c in comments if not c.get("resolved")]
    resolved_comments = [c for c in comments if c.get("resolved")]

    lines: List[str] = [f"# Comments: {title}", ""]
    if open_comments:
        lines += ["## Open", ""]
        for c in open_comments:
            lines += _render_comment(c)
    if resolved_comments:
        lines += ["## Resolved", ""]
        for c in resolved_comments:
            lines += _render_comment(c)

    return "\n".join(lines).rstrip() + "\n"


@dataclass
class ReplyDirective:
    comment_id: str
    reply: str
    resolve: bool


@dataclass
class RespondResult:
    posted: int
    resolved: int


_ID_RE = re.compile(r"<!--\s*id:(\S+)\s*-->")
_REPLY_RE = re.compile(r"^Reply:\s?(.*)$")
_RESOLVE_RE = re.compile(r"^Resolve:\s*(yes|no)\s*$", re.IGNORECASE)


def parse_reply_directives(markdown_text: str) -> List[ReplyDirective]:
    """
    Parse Reply:/Resolve: directives out of a `.comments.md` sidecar.

    Scans comment-by-comment (split on the `<!-- id:... -->` marker written by
    format_comments_markdown), so each comment's own Reply:/Resolve: lines are
    matched to that comment's id even if a later comment also has directive
    lines. Returns only directives with an actual reply and/or resolve=True —
    a comment left untouched (empty Reply:, Resolve: no) produces nothing.
    """
    directives: List[ReplyDirective] = []
    ids = list(_ID_RE.finditer(markdown_text))
    for i, match in enumerate(ids):
        comment_id = match.group(1)
        block_start = match.end()
        block_end = ids[i + 1].start() if i + 1 < len(ids) else len(markdown_text)
        block = markdown_text[block_start:block_end]

        reply = ""
        resolve = False
        for line in block.splitlines():
            reply_match = _REPLY_RE.match(line.strip())
            if reply_match:
                reply = reply_match.group(1).strip()
                continue
            resolve_match = _RESOLVE_RE.match(line.strip())
            if resolve_match:
                resolve = resolve_match.group(1).lower() == "yes"

        if reply or resolve:
            directives.append(ReplyDirective(comment_id=comment_id, reply=reply, resolve=resolve))

    return directives


@dataclass
class CommentBucketResult:
    """Result of bucketing a doc's comments into per-section groups.

    `by_section` maps a section key (e.g. `SectionManifestEntry.filename`)
    to the comments assigned to it, in manifest order, only for sections
    that ended up with at least one comment. `residue` holds comments whose
    `quotedFileContent.value` matched no section's rendered text at all —
    surfaced as a warning, never silently dropped (plan.md Task 4.1.3).
    """

    by_section: Dict[str, List[dict]] = field(default_factory=dict)
    residue: List[dict] = field(default_factory=list)


def _quoted_text(comment: dict) -> str:
    return (comment.get("quotedFileContent") or {}).get("value", "").strip()


def bucket_comments_by_section(
    comments: List[dict], sections: List[Tuple[str, str]]
) -> CommentBucketResult:
    """Bucket `comments` into the section whose rendered text they quote.

    Chain-of-Responsibility-flavored matching (plan.md Pattern Decisions:
    "Comment bucketing"): for each comment, walk `sections` — a list of
    `(section_key, rendered_markdown_text)` pairs in manifest order — and
    substring-match the comment's `quotedFileContent.value` against each
    section's rendered text in turn. The first matching section wins.

    If a comment's quoted text matches more than one section, that's an
    ambiguous match, not a silent first-wins pick: it's still assigned to
    the first (manifest-order) match for determinism, but a warning is
    logged naming the comment id/snippet and every matching section, so the
    ambiguity is surfaced rather than invisible.

    A comment with no quoted text, or whose quoted text matches no
    section's rendered text at all, is unassigned and lands in
    `CommentBucketResult.residue` — never silently dropped and never
    silently attached to the wrong section.
    """
    result = CommentBucketResult()
    for comment in comments:
        quoted = _quoted_text(comment)
        if not quoted:
            result.residue.append(comment)
            logger.warning(
                "Comment %s has no quoted text to bucket by section — treating as residue.",
                comment.get("id", "<unknown>"),
            )
            continue

        matches = [key for key, text in sections if quoted in text]
        if not matches:
            result.residue.append(comment)
            logger.warning(
                "Comment %s (%r) matched no section's text — unassigned, surfaced as residue.",
                comment.get("id", "<unknown>"),
                quoted[:80],
            )
            continue

        if len(matches) > 1:
            logger.warning(
                "Comment %s (%r) matched multiple sections (%s) — assigned to "
                "the first match (%s) in manifest order.",
                comment.get("id", "<unknown>"),
                quoted[:80],
                ", ".join(matches),
                matches[0],
            )

        result.by_section.setdefault(matches[0], []).append(comment)

    return result
