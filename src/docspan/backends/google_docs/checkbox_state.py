"""Recover native-checkbox checked/unchecked state for the default pull path.

`documents.get()` cannot expose a native checkbox's checked bit — see ADR-001.
But Drive's `files.export(mimeType='text/markdown')` is a *different* renderer
that does: it emits GFM `- [ ]`/`- [x]` for a native checkbox paragraph, `*`
(not `-`) for an ordinary bullet, and backslash-escapes a literal `[ ]` a user
typed as text. Verified against real documents: pairing the ordered
native-checkbox paragraphs from a structural parse against the ordered
checklist lines from the markdown export matches exactly (count and text)
across nested lists, interleaved tables, and mixed bullet presets.

This module only extracts states from that export (`extract_checkbox_states`)
and applies them to the default path's already-rendered markdown
(`patch_checkbox_lines`). It never guesses: `patch_checkbox_lines` reports
whether every expected checkbox paragraph was actually found as a distinct
list-item line, and the caller (`GoogleDocsBackend.pull()`) is responsible for
falling back to the untouched, honestly-unrecoverable rendering — today's
behavior — whenever that isn't the case (count mismatch, transport failure,
or an unmatched line).
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

# Only a line-leading "- [ ]"/"- [x]" (optional leading whitespace for nested
# items) is a checkbox marker. A plain bullet uses "*" in the markdown
# export, and a literal "[ ]" a user typed as text comes back
# backslash-escaped ("\[ \]") — neither matches this pattern.
_CHECKBOX_LINE_RE = re.compile(r"^[ \t]*-\s\[([ xX])\]\s")

# A markdown-emphasis run possibly wrapped around the paragraph's plain text
# in the HTML-converted output (e.g. "**bold**") — stripped before comparing
# against the plain text a structural parse reports for the same paragraph.
_EMPHASIS_CHARS_RE = re.compile(r"[*_`]")


def extract_checkbox_states(markdown_text: str) -> List[bool]:
    """Ordered list of checked/unchecked (True=checked) for each checklist
    line in `markdown_text`, in document order.

    Only line-leading `- [ ]`/`- [x]` markers count (criterion 3) — a plain
    bullet or an escaped literal bracket never matches `_CHECKBOX_LINE_RE`.
    """
    states: List[bool] = []
    for line in markdown_text.splitlines():
        match = _CHECKBOX_LINE_RE.match(line)
        if match:
            states.append(match.group(1).lower() == "x")
    return states


def patch_checkbox_lines(
    markdown_content: str, checkbox_paragraphs: Sequence[Tuple[str, bool]]
) -> Tuple[str, bool]:
    """Rewrite each native-checkbox paragraph's line in `markdown_content` to
    an explicit `- [x] {text}` / `- [ ] {text}` checklist item.

    `checkbox_paragraphs` is the ordered `(paragraph_text, checked)` pairs for
    every native-checkbox paragraph in the document (paragraph_text is the
    plain text from a structural parse of `documents.get()`). For each pair,
    in order, this looks for the next not-yet-consumed list-item line
    (`- ...`) in `markdown_content` whose content, once markdown decorators
    are stripped, contains that paragraph's text — matching by *content*
    rather than position, since the HTML-to-markdown converter can insert
    decorative glyph text ahead of the real content that this function has no
    other way to know the shape of.

    Returns `(patched_markdown, all_found)`. `all_found` is False the moment
    any paragraph's line can't be located — callers must not use a partial
    patch, since the paragraphs not yet visited could easily be mismatched
    against the wrong list-item line from here on.
    """
    lines = markdown_content.split("\n")
    cursor = 0
    all_found = True

    for text, checked in checkbox_paragraphs:
        found_at = None
        for i in range(cursor, len(lines)):
            line = lines[i]
            stripped = line.lstrip()
            if not stripped.startswith("- ") and not stripped.startswith("-\t"):
                continue
            rest = stripped[1:].lstrip()
            rest_plain = _EMPHASIS_CHARS_RE.sub("", rest)
            if text and text in rest_plain:
                found_at = i
                break

        if found_at is None:
            all_found = False
            continue

        indent = lines[found_at][: len(lines[found_at]) - len(lines[found_at].lstrip())]
        marker = "[x]" if checked else "[ ]"
        lines[found_at] = f"{indent}- {marker} {text}"
        cursor = found_at + 1

    return "\n".join(lines), all_found
