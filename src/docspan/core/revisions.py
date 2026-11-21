"""Drive revision helpers shared by the CLI and MCP tools (no CLI/MCP imports)."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# A closing run of #s is only an ATX closer when preceded by whitespace (CommonMark).
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
# Up to 3 spaces of indent (4+ is an indented code block); a backtick fence's info string has no backticks.
_FENCE = re.compile(r"^ {0,3}(?:(`{3,})([^`]*)|(~{3,})(.*))$")


class RevisionExportError(Exception):
    """A Drive revision could not be exported (no export link, HTTP failure)."""


class RevisionPermissionError(RevisionExportError, PermissionError):
    """Drive denied access to a revision's export."""


class RevisionExportLinkError(RevisionExportError, ValueError):
    """The revision offers no text/markdown or text/plain export link."""


class RevisionExportHttpError(RevisionExportError, RuntimeError):
    """The revision export request failed with an HTTP error."""


@dataclass
class RevisionText:
    text: str
    mime_type: str
    warning: Optional[str] = None


def fetch_revision_text(client: Any, doc_id: str, revision_id: str,
                        tab: Optional[str] = None) -> RevisionText:
    """Export one Drive revision as markdown, falling back to plain text with a warning."""
    text, mime = client.get_revision_export(doc_id, revision_id)
    warnings: List[str] = []
    if mime != "text/markdown":
        warnings.append(
            f"text/markdown is not in this revision's exportLinks; showing {mime} instead")
    if tab:
        warnings.append(
            "Drive cannot export a single tab of a historical revision (best-effort); "
            "showing the whole document")
    return RevisionText(text, mime, "; ".join(warnings) or None)


def unified_diff(a: str, b: str, a_label: str, b_label: str) -> str:
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), a_label, b_label))


def _headings(md: str) -> List[Tuple[int, int, str]]:
    """(line_index, level, text) for each ATX heading outside code fences."""
    out: List[Tuple[int, int, str]] = []
    fence: Optional[str] = None  # the opening marker run while inside a fence
    for i, line in enumerate(md.splitlines()):
        fm = _FENCE.match(line)
        if fm:
            marker = fm.group(1) or fm.group(3)
            info = fm.group(2) if fm.group(1) else fm.group(4)
            if fence is None:
                fence = marker
            elif (marker[0] == fence[0] and len(marker) >= len(fence)
                  and not info.strip()):
                fence = None
            continue
        m = None if fence is not None else _HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _normalize_heading(heading: str) -> str:
    return heading.strip().lstrip("#").strip().casefold()


def find_section(md: str, heading: str) -> Optional[Tuple[int, int]]:
    """Line range [start, end) of the section under `heading`, including subsections."""
    heads = _headings(md)
    want = _normalize_heading(heading)
    for idx, (line, level, text) in enumerate(heads):
        if text.casefold() == want:
            end = len(md.splitlines())
            for nxt_line, nxt_level, _ in heads[idx + 1:]:
                if nxt_level <= level:
                    end = nxt_line
                    break
            return line, end
    return None


class SectionNotFound(ValueError):
    pass


def restore_section(local_md: str, old_md: str, heading: str, revision_id: str) -> str:
    """Replace `heading`'s section in `local_md` with the one from `old_md`.

    Raises SectionNotFound if the heading is absent from the old revision. If it is
    absent locally the old section is appended.
    """
    old_lines = old_md.splitlines(keepends=True)
    old_range = find_section(old_md, heading)
    if old_range is None:
        raise SectionNotFound(f"heading '{heading}' not found in revision {revision_id}")
    section = old_lines[old_range[0]:old_range[1]]
    if section and not section[-1].endswith("\n"):
        section[-1] += "\n"
    local_lines = local_md.splitlines(keepends=True)
    local_range = find_section(local_md, heading)
    if local_range is None:
        sep = [] if not local_lines or local_lines[-1].strip() == "" else ["\n"]
        if local_lines and not local_lines[-1].endswith("\n"):
            local_lines[-1] += "\n"
        return "".join(local_lines + sep + section)
    start, end = local_range
    return "".join(local_lines[:start] + section + local_lines[end:])
