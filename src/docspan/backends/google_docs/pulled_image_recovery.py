"""Recover mermaid fences and eliminate base64 image bloat from pulled markdown.

`GoogleDocsBackend.pull()`'s default path (no explicit `tab_id`) renders
markdown from Drive's HTML export (`files.export`), which inlines every
embedded image as a `data:image/...;base64,...` URI. A pushed ```mermaid
fence renders through the same path it always did (mermaid_renderer.py ->
image_source.py -> insertInlineImage), so it comes back the same way: a
multi-hundred-KB embedded blob instead of the diagram source, confirmed
against a real doc where two diagrams round-tripped into 401KB of inline PNG
data.

This module patches that markdown after the fact rather than fixing Drive's
export (not something docspan controls) or switching the default pull path
to the structural (`tab_id`-scoped) renderer wholesale -- see `pull()`'s own
docstring for why the two paths exist; `nodes_to_markdown.py`'s image
renderer has the same "no source, only rendered bytes" limitation and would
need this same recovery layered on top of it either way.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import List, Match, Optional

from docspan.backends.google_docs import mermaid_cache_sidecar
from docspan.backends.google_docs.docs_structure_parser import DocsImageNode
from docspan.backends.google_docs.mermaid_renderer import lookup_mermaid_source

_DATA_URI_IMAGE = re.compile(r"!\[([^\]]*)\]\((data:image/[^)]+)\)")


@dataclass
class ImageRecoveryResult:
    markdown: str
    mermaid_restored: int
    base64_deflated: int


def recover_pulled_images(
    markdown_content: str,
    image_nodes: List[DocsImageNode],
    markdown_path: Optional[str] = None,
) -> ImageRecoveryResult:
    """Replace each base64 data-URI image in `markdown_content`.

    A match is replaced with a restored ```mermaid fence when its exact
    rendered bytes are found in the local mermaid render cache (see
    mermaid_renderer.lookup_mermaid_source) or, failing that, the
    git-committed cross-machine sidecar (mermaid_cache_sidecar.py, checked
    only when `markdown_path` is given -- the sidecar lives next to that
    file). Falls back to the corresponding structural node's real image URL
    when neither has it -- either way, the inline blob is gone.

    Matches are correlated to `image_nodes` positionally, in document order:
    both are read from the same pulled document top-to-bottom. If the counts
    don't line up -- e.g. some image in the doc did not export as base64,
    which would mean this function's own load-bearing assumption doesn't
    hold for this document -- this is a no-op. Returning the original
    markdown unchanged is safe; guessing at a misaligned pairing and
    silently mislabeling one image as another is not.
    """
    matches = list(_DATA_URI_IMAGE.finditer(markdown_content))
    if not matches or len(matches) != len(image_nodes):
        return ImageRecoveryResult(markdown=markdown_content, mermaid_restored=0, base64_deflated=0)

    mermaid_restored = 0
    base64_deflated = 0
    pieces: List[str] = []
    cursor = 0
    for match, node in zip(matches, image_nodes):
        pieces.append(markdown_content[cursor : match.start()])
        text, is_mermaid = _replacement_for(match, node, markdown_path)
        if is_mermaid:
            mermaid_restored += 1
        else:
            base64_deflated += 1
        pieces.append(text)
        cursor = match.end()
    pieces.append(markdown_content[cursor:])
    return ImageRecoveryResult(
        markdown="".join(pieces),
        mermaid_restored=mermaid_restored,
        base64_deflated=base64_deflated,
    )


def _replacement_for(
    match: "Match[str]", node: DocsImageNode, markdown_path: Optional[str]
) -> tuple:
    alt, data_uri = match.group(1), match.group(2)
    png_bytes = _decode_data_uri(data_uri)
    diagram = None
    if png_bytes is not None:
        diagram = lookup_mermaid_source(png_bytes)
        if diagram is None and markdown_path is not None:
            diagram = mermaid_cache_sidecar.lookup(markdown_path, png_bytes)
    if diagram is not None:
        return f"```mermaid\n{diagram}\n```", True
    # No cache hit -- still worth losing the blob. node.src is the doc's
    # real (short-lived) contentUri from the structural parse; keep the
    # export's own alt text (real, since Alt Text is UI-settable even
    # though the API can't write it -- see mermaid_renderer._by_hash_dir's
    # docstring) over node.alt, which is always empty for the same reason.
    return f"![{alt}]({node.src or data_uri})", False


def _decode_data_uri(data_uri: str) -> Optional[bytes]:
    _, _, payload = data_uri.partition(",")
    if not payload:
        return None
    try:
        return base64.b64decode(payload)
    except (ValueError, binascii.Error):
        return None
