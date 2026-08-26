"""In-Doc appendix that recovers a pushed mermaid diagram's source, no git required.

`mermaid_cache_sidecar.py` recovers a pulled mermaid diagram's source *if you
have the git repo the sidecar is committed to*. This module is the
independent, Doc-only counterpart: push appends a trailing
"Appendix: Diagram Sources" section (one H3 + fenced code block per diagram,
keyed by the same `sha256(rendered PNG bytes)` domain `mermaid_renderer.py`
and `mermaid_cache_sidecar.py` already use, since pull only ever has the
bytes, never the source), and pull strips that section back out before
rendering to markdown, using it only to populate the recovery lookup. The
appendix is never written into the local source-of-truth markdown -- push
always rebuilds it fresh from the document's current mermaid images, never
reading back a previous appendix's content.

Nodes are hand-built to exactly match `MarkdownToParagraphParser`'s real
shapes (`HeadingTokenConverter`, `_nodes_from_code_block` with
`emit_language_marker=True`) rather than built by round-tripping through
that parser: a diagram's raw source can itself contain backticks or other
markdown-special characters, which would need escaping if assembled into an
actual markdown string first.

The appendix's own fenced code block always uses language tag "text", never
"mermaid" -- `CodeTokenConverter.convert` special-cases a `mermaid`-tagged
fence into a rendered image, which would turn the appendix into another
diagram image on the next push.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode, TextSpan

APPENDIX_HEADING = "Appendix: Diagram Sources"
APPENDIX_CODE_LANG = "text"
FENCE_MARKER = "```"


def _entry_heading(sha256_hex: str) -> str:
    return f"Diagram {sha256_hex}"


def build_appendix_nodes(entries: List[Tuple[str, str]]) -> List[DocsParagraphNode]:
    """Build the trailing appendix section's nodes, in document order.

    `entries` is `[(sha256_hex, diagram_source), ...]`, in document order.
    Returns `[]` for no entries -- a doc with no mermaid diagrams gets no
    appendix at all.
    """
    if not entries:
        return []

    nodes: List[DocsParagraphNode] = [
        DocsParagraphNode(
            style="HEADING_2",
            text=APPENDIX_HEADING,
            start_index=0,
            end_index=0,
            spans=[],
        )
    ]
    for sha256_hex, diagram in entries:
        nodes.append(
            DocsParagraphNode(
                style="HEADING_3",
                text=_entry_heading(sha256_hex),
                start_index=0,
                end_index=0,
                spans=[],
            )
        )
        nodes.append(
            DocsParagraphNode(
                style="NORMAL_TEXT",
                text=f"{FENCE_MARKER}{APPENDIX_CODE_LANG}",
                start_index=0,
                end_index=0,
                spans=[],
            )
        )
        for line in diagram.splitlines():
            nodes.append(
                DocsParagraphNode(
                    style="NORMAL_TEXT",
                    text=line,
                    start_index=0,
                    end_index=0,
                    spans=[TextSpan(text=line, monospace=True)] if line else [],
                )
            )
        nodes.append(
            DocsParagraphNode(
                style="NORMAL_TEXT",
                text=FENCE_MARKER,
                start_index=0,
                end_index=0,
                spans=[],
            )
        )
    return nodes


def find_appendix_boundary(nodes: List) -> Optional[int]:
    """Index of the appendix's leading HEADING_2 node, if present."""
    for i, node in enumerate(nodes):
        if (
            isinstance(node, DocsParagraphNode)
            and node.style == "HEADING_2"
            and node.text.strip() == APPENDIX_HEADING
        ):
            return i
    return None


def extract_appendix_entries(nodes: List) -> Dict[str, str]:
    """Inverse of `build_appendix_nodes`: recover `{sha256_hex: diagram_source}`.

    Only ever called on the trailing slice starting at
    `find_appendix_boundary`'s result -- walks HEADING_3 entries and the
    literal-fence-marker/monospace-line shape `build_appendix_nodes` emits.
    Malformed or unrecognized shapes are skipped rather than raising: this
    is a best-effort recovery aid, not something that should ever fail a
    pull.
    """
    entries: Dict[str, str] = {}
    i, n = 0, len(nodes)
    while i < n:
        node = nodes[i]
        if not (
            isinstance(node, DocsParagraphNode)
            and node.style == "HEADING_3"
            and node.text.startswith("Diagram ")
        ):
            i += 1
            continue
        sha256_hex = node.text[len("Diagram "):].strip()
        i += 1
        if i >= n or not (
            isinstance(nodes[i], DocsParagraphNode)
            and nodes[i].text == f"{FENCE_MARKER}{APPENDIX_CODE_LANG}"
        ):
            continue
        i += 1
        lines: List[str] = []
        while i < n and isinstance(nodes[i], DocsParagraphNode) and nodes[i].text != FENCE_MARKER:
            lines.append(nodes[i].text)
            i += 1
        if i < n:
            i += 1  # consume the closing fence marker
        if sha256_hex:
            entries[sha256_hex] = "\n".join(lines)
    return entries


_MARKDOWN_HEADING = re.compile(
    r"^##\s+" + re.escape(APPENDIX_HEADING) + r"\s*$", re.MULTILINE
)


def strip_appendix_from_markdown(markdown_content: str) -> str:
    """Truncate rendered markdown at the appendix heading, if present.

    Counterpart to `find_appendix_boundary`/`extract_appendix_entries` for
    the default (Drive HTML export) pull path, whose `markdown_content`
    comes from `DocumentConverter.html_to_markdown` -- an already-rendered
    markdown string, not a node list -- so there's nothing to slice by
    index. `converter.py` configures ATX headings (`#`), matching what
    `build_appendix_nodes`' HEADING_2 node round-trips to on push.
    """
    match = _MARKDOWN_HEADING.search(markdown_content)
    if match is None:
        return markdown_content
    return markdown_content[: match.start()].rstrip() + "\n"
