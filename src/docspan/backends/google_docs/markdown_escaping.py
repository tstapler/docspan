"""Shared CommonMark inline-code escaping helpers.

Used by both pull paths that render a "monospace" run into a Markdown code
span: nodes_to_markdown.py's tab-scoped structural renderer, and
converter.py's markdownify-based HTML-export renderer (_GoogleDocsMarkdownConverter.
convert_span). The two paths detect monospace differently (span-shape vs.
CSS font-family), but once detected, both need the identical CommonMark
backtick-fence-escaping rule, so it lives here rather than duplicated or
imported privately across module boundaries.
"""
from __future__ import annotations


def run_of_char(text: str, target: str) -> int:
    """The longest run of consecutive occurrences of `target` in text."""
    max_run = run = 0
    for ch in text:
        if ch == target:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run


def run_of_backticks(text: str) -> int:
    """The longest run of consecutive backticks in text."""
    return run_of_char(text, "`")


def wrap_inline_code(text: str) -> str:
    """Wrap text as a CommonMark code span, escaping any backticks inside it.

    CommonMark's rule: the delimiter must be a run of backticks longer than
    the longest run inside the content, and if the content starts or ends
    with a backtick (or starts and ends with a space around non-space
    content), a single space is added inside the delimiters so the content
    doesn't fuse with them.

    Empty text is left unwrapped: a 1-backtick fence around no content is
    two adjacent backticks, which CommonMark parses as literal text, not an
    empty code span — wrapping would silently produce that ambiguous output.
    """
    if not text:
        return text
    delim = "`" * (run_of_backticks(text) + 1)
    needs_pad = text.startswith("`") or text.endswith("`")
    if not needs_pad and text[:1] == " " and text[-1:] == " " and text.strip():
        needs_pad = True
    if needs_pad:
        return f"{delim} {text} {delim}"
    return f"{delim}{text}{delim}"
