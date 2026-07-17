"""Render Google Docs comments (Drive API shape) into a markdown sidecar."""
from __future__ import annotations

from typing import List


def _author(node: dict) -> str:
    return (node.get("author") or {}).get("displayName") or "Unknown"


def _render_comment(comment: dict) -> List[str]:
    lines: List[str] = [f"### {_author(comment)} — {comment.get('createdTime', '')}".rstrip(" —"), ""]

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

    lines += ["---", ""]
    return lines


def format_comments_markdown(title: str, comments: List[dict]) -> str:
    """
    Build a `{doc}.comments.md` sidecar from Drive `comments.list` results.

    Groups into Open / Resolved, preserving quoted selections and reply threads.
    Returns an empty string when there are no comments.
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
