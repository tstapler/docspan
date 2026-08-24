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
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Match, Optional

import requests

from docspan.backends.google_docs import mermaid_cache_sidecar
from docspan.backends.google_docs.docs_structure_parser import DocsImageNode
from docspan.backends.google_docs.mermaid_renderer import lookup_mermaid_source

_DATA_URI_IMAGE = re.compile(r"!\[([^\]]*)\]\((data:image/[^)]+)\)")

# Structural pull's own image links (contentUri), not base64 -- see
# recover_structural_mermaid_images's docstring for why this needs a
# separate fetch-then-hash path instead of _DATA_URI_IMAGE's regex match.
_FETCH_TIMEOUT_SECONDS = 10

Fetcher = Callable[[str], Optional[bytes]]


@dataclass
class ImageRecoveryResult:
    markdown: str
    mermaid_restored: int
    base64_deflated: int


def recover_pulled_images(
    markdown_content: str,
    image_nodes: List[DocsImageNode],
    markdown_path: Optional[str] = None,
    appendix_entries: Optional[Dict[str, str]] = None,
) -> ImageRecoveryResult:
    """Replace each base64 data-URI image in `markdown_content`.

    A match is replaced with a restored ```mermaid fence when its exact
    rendered bytes are found in the local mermaid render cache (see
    mermaid_renderer.lookup_mermaid_source), failing that the git-committed
    cross-machine sidecar (mermaid_cache_sidecar.py, checked only when
    `markdown_path` is given -- the sidecar lives next to that file), or
    failing that the pulled document's own in-body appendix
    (`mermaid_appendix.py`, checked only when `appendix_entries` is given --
    a doc-only recovery source keyed the same way, `sha256(png_bytes)`, that
    needs no git checkout to consult). Falls back to the corresponding
    structural node's real image URL when none of the three has it -- either
    way, the inline blob is gone.

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
        text, is_mermaid = _replacement_for(match, node, markdown_path, appendix_entries)
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
    match: "Match[str]",
    node: DocsImageNode,
    markdown_path: Optional[str],
    appendix_entries: Optional[Dict[str, str]] = None,
) -> tuple:
    alt, data_uri = match.group(1), match.group(2)
    png_bytes = _decode_data_uri(data_uri)
    diagram = None
    if png_bytes is not None:
        diagram = lookup_mermaid_source(png_bytes)
        if diagram is None and markdown_path is not None:
            diagram = mermaid_cache_sidecar.lookup(markdown_path, png_bytes)
        if diagram is None and appendix_entries is not None:
            diagram = appendix_entries.get(hashlib.sha256(png_bytes).hexdigest())
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


def _fetch_via_http(url: str) -> Optional[bytes]:
    """Default `Fetcher` for `recover_structural_mermaid_images` -- best-effort.

    Any failure (network, timeout, non-2xx, malformed URL) returns None
    rather than raising: a `contentUri` fetch failing must degrade to
    "leave the image link as-is", the same as every other recovery miss in
    this module, never turn into a broken pull.
    """
    try:
        response = requests.get(url, timeout=_FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None


def recover_structural_mermaid_images(
    markdown_content: str,
    image_nodes: List[DocsImageNode],
    fetch: Fetcher = _fetch_via_http,
    markdown_path: Optional[str] = None,
    appendix_entries: Optional[Dict[str, str]] = None,
) -> ImageRecoveryResult:
    """Restore ```mermaid fences on the structural pull path.

    Unlike the default (Drive HTML export) path, the structural renderer
    (`nodes_to_markdown.py`'s `ImageNodeRenderer`) never embeds base64 data --
    it emits `![alt](src)` with `node.src` set to the Docs API's real (but
    short-lived) `contentUri` (confirmed: `docs_structure_parser.py`'s
    embedded-object handling never base64-encodes). So `recover_pulled_images`'s
    `_DATA_URI_IMAGE` regex can never match anything on this path, and this
    module's whole fallback chain would silently never fire here without a
    separate way to get from "a `contentUri` link" to "the rendered bytes",
    which is the one comparison key every tier of the chain needs
    (`sha256(png_bytes)`). This function closes that gap: fetch each image's
    bytes over HTTP, hash them, and run the same three-tier lookup
    (`lookup_mermaid_source` -> `mermaid_cache_sidecar.lookup` ->
    `appendix_entries`) `_replacement_for` already implements for the
    data-URI path.

    `fetch` is injected (defaults to a real HTTP GET) so tests don't need a
    live `contentUri`, matching this codebase's existing
    uploader/renderer-injection convention (see `image_source.py`). A fetch
    failure for one image degrades to leaving that image's link untouched --
    it must never fail the whole pull.

    Correlates matches to `image_nodes` positionally, same reasoning and
    same all-or-nothing guard as `recover_pulled_images`.
    """
    matches = list(re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", markdown_content))
    if not matches or len(matches) != len(image_nodes):
        return ImageRecoveryResult(markdown=markdown_content, mermaid_restored=0, base64_deflated=0)

    mermaid_restored = 0
    pieces: List[str] = []
    cursor = 0
    for match, node in zip(matches, image_nodes):
        pieces.append(markdown_content[cursor : match.start()])
        alt = match.group(1)
        diagram = None
        png_bytes = fetch(node.src) if node.src else None
        if png_bytes is not None:
            diagram = lookup_mermaid_source(png_bytes)
            if diagram is None and markdown_path is not None:
                diagram = mermaid_cache_sidecar.lookup(markdown_path, png_bytes)
            if diagram is None and appendix_entries is not None:
                diagram = appendix_entries.get(hashlib.sha256(png_bytes).hexdigest())
        if diagram is not None:
            pieces.append(f"```mermaid\n{diagram}\n```")
            mermaid_restored += 1
        else:
            pieces.append(f"![{alt}]({node.src})")
        cursor = match.end()
    pieces.append(markdown_content[cursor:])
    return ImageRecoveryResult(
        markdown="".join(pieces),
        mermaid_restored=mermaid_restored,
        base64_deflated=0,
    )
