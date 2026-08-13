"""Resolve markdown image references to fetchable URIs for insertInlineImage.

`ImageSource` is a closed union (`LocalPathSource | InMemorySource |
UrlSource`) so the future mermaid-diagram follow-on can reuse
`resolve_images()` via `InMemorySource` (rendered bytes, no backing file)
without any changes here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import DocsImageNode

# Practical ceiling before a raw Drive/Docs API 400 -- see validation.md's
# "oversized image" edge case.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

# Magic-byte sniffing instead of `imghdr` (removed in Python 3.13) or a new
# Pillow dependency -- covers the formats insertInlineImage actually supports.
_MAGIC_BYTES: Dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
}


@dataclass(frozen=True)
class LocalPathSource:
    """An image at an already-resolved filesystem path."""

    path: str


@dataclass(frozen=True)
class InMemorySource:
    """Raw image bytes with no backing file (e.g. a rendered mermaid diagram)."""

    data: bytes
    filename: str
    mime_type: Optional[str] = None


@dataclass(frozen=True)
class UrlSource:
    """An image already at a fetchable http(s):// URL -- bypasses Drive upload."""

    url: str


ImageSource = Union[LocalPathSource, InMemorySource, UrlSource]

Uploader = Callable[[bytes, str, str], Dict[str, str]]


@dataclass
class ResolvedImage:
    """A resolved image, ready to feed an insertInlineImage request."""

    uri: str
    temp_drive_file_id: Optional[str] = None


@dataclass
class ImageResolutionError:
    """A source that couldn't be resolved -- surfaced as push residue, never a crash."""

    key: str
    reason: str


class _ResolutionFailure(Exception):
    pass


def build_source(markdown_path: str, image_ref: str) -> ImageSource:
    """Turn a raw markdown image ref into an `ImageSource`, relative to the markdown file.

    Absolute paths and `../` traversal outside the markdown file's directory
    are resolved and passed through rather than rejected: shared asset
    directories are a supported use case, and `markdown_path`'s directory is
    trusted input by the time it reaches here (not derived from network
    input). This is a deliberate scope decision, not an accidental
    side-effect of path-joining -- see validation.md's path-traversal edge
    case.
    """
    if image_ref.startswith(("http://", "https://")):
        return UrlSource(url=image_ref)
    ref_path = Path(image_ref)
    if ref_path.is_absolute():
        resolved = ref_path
    else:
        resolved = Path(markdown_path).resolve().parent / ref_path
    return LocalPathSource(path=str(resolved))


def resolve_images(
    sources: Dict[str, ImageSource], uploader: Uploader
) -> Tuple[Dict[str, ResolvedImage], List[ImageResolutionError]]:
    """Resolve each image source to a URI usable in an insertInlineImage request.

    `uploader` matches `GoogleDocsClient.upload_temp_image`'s call shape
    (`(data, filename, mime_type) -> {"file_id", "uri"}`) -- injected rather
    than constructing a client here, so this module is testable without
    mocking Drive API client construction.

    Returns `(resolved, errors)`: resolved sources feed the request builder;
    errors become push residue warnings (never an exception), keyed the same
    way as `sources` so callers can map failures back to their node.
    """
    resolved: Dict[str, ResolvedImage] = {}
    errors: List[ImageResolutionError] = []
    for key, source in sources.items():
        try:
            resolved[key] = _resolve_one(source, uploader)
        except _ResolutionFailure as exc:
            errors.append(ImageResolutionError(key=key, reason=str(exc)))
    return resolved, errors


def _resolve_one(source: ImageSource, uploader: Uploader) -> ResolvedImage:
    if isinstance(source, UrlSource):
        return ResolvedImage(uri=source.url)

    if isinstance(source, LocalPathSource):
        data, filename = _read_local(source.path)
        mime_type = _sniff_mime_type(data)
    elif isinstance(source, InMemorySource):
        data, filename = source.data, source.filename
        mime_type = source.mime_type or _sniff_mime_type(data)
    else:
        raise _ResolutionFailure(f"unsupported image source type: {type(source).__name__}")

    if len(data) > MAX_IMAGE_BYTES:
        raise _ResolutionFailure(
            f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)}MB limit "
            f"({len(data)} bytes): {filename}"
        )
    if mime_type is None:
        raise _ResolutionFailure(f"not a recognized image format: {filename}")
    if mime_type == "image/svg+xml":
        raise _ResolutionFailure(
            f"SVG is not supported by Google Docs' insertInlineImage: {filename}"
        )

    result = uploader(data, filename, mime_type)
    return ResolvedImage(uri=result["uri"], temp_drive_file_id=result["file_id"])


def _read_local(path: str) -> Tuple[bytes, str]:
    p = Path(path)
    if not p.is_file():
        raise _ResolutionFailure(f"image file not found: {path}")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise _ResolutionFailure(f"could not read image file {path}: {exc}") from exc
    return data, p.name


def _sniff_mime_type(data: bytes) -> Optional[str]:
    for magic, mime in _MAGIC_BYTES.items():
        if data.startswith(magic):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    stripped = data.lstrip()[:256]
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<svg"):
        return "image/svg+xml"
    return None


def resolve_document_images(
    nodes: List[DocsImageNode], markdown_path: str, uploader: Uploader
) -> Tuple[List[Optional[DocsImageNode]], List[str], List[str]]:
    """Resolve every `DocsImageNode.src` in `nodes` to a fetchable URI, in place-equivalent form.

    Convenience wrapper over `resolve_images()` for the `backend.py` push
    pre-pass: builds an `ImageSource` per node from its raw markdown `src`
    (via `build_source`), resolves them all, and returns `(nodes, warnings,
    temp_drive_file_ids)`.

    The returned `nodes` list is positional -- same length and order as the
    input, one slot per input node -- so a caller splicing these back into a
    larger node list (backend.py's target_nodes) can zip them against the
    original `DocsImageNode` positions without having to guess which ones
    failed. A resolved slot holds the node with `src` rewritten to the
    resolved URI; an unresolved slot is `None` (its failure is reported via
    `warnings`, matching the `_render_unstyled`/`_render_dead_anchors`
    residue-warning pattern in `backend.py`) and the caller drops it.
    `temp_drive_file_ids` lets the caller delete on success or retry on
    failure (criterion 5/7/8).
    """
    sources = {str(i): build_source(markdown_path, node.src) for i, node in enumerate(nodes)}
    resolved, errors = resolve_images(sources, uploader)

    warnings = [f"image {sources[e.key]!r}: {e.reason}" for e in errors]
    temp_drive_file_ids = [
        r.temp_drive_file_id for r in resolved.values() if r.temp_drive_file_id
    ]

    out: List[Optional[DocsImageNode]] = []
    for i, node in enumerate(nodes):
        result = resolved.get(str(i))
        out.append(replace(node, src=result.uri) if result else None)
    return out, warnings, temp_drive_file_ids
