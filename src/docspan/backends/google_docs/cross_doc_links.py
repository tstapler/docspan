"""Cross-document markdown link resolution.

A relative markdown link from one mapped file to another
(`[link](../other-doc/README.md#some-heading)`) has no equivalent in
`heading_anchors.py` — that module only resolves a same-document `#fragment`
against the document being pushed. Without this module such a link fell into
the same `{"url": href}` branch as any other URL, so the *relative path
itself* got written into the Doc as a literal (broken) link.

This module:

* recognizes an href as a candidate cross-document link (`parse_cross_doc_href`);
* resolves the path it names to a `Mapping` entry, relative to the pushing
  file (`resolve_local_mapping`) — loudly refusing if two mappings normalize
  to the same path, per the loud-failure principle the rest of this backend
  follows;
* resolves any `#fragment` against the *target* document's live headings,
  fetched and cached at most once per (doc_id, tab_id) for the lifetime of a
  `CrossDocLinkResolver` (`resolve`);
* wraps `heading_anchors.link_payload` so same-document anchors are
  unaffected (`link_payload`).

Never degrades an unresolvable cross-doc reference to a written `url` link —
consistent with `heading_anchors.link_payload`'s contract, an unresolved
cross-doc link is reported (via the returned "kind"), not silently written.
"""
from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set
from urllib.parse import urlsplit

from docspan.backends.google_docs.heading_anchors import (
    is_anchor,
    resolve_anchor,
)
from docspan.backends.google_docs.heading_anchors import (
    link_payload as _same_doc_link_payload,
)

if TYPE_CHECKING:
    from docspan.config import Mapping

logger = logging.getLogger(__name__)


class AmbiguousMappingError(Exception):
    """Two mapping entries normalize to the same local path (criterion 7)."""


@dataclass(frozen=True)
class CrossDocHref:
    """A parsed candidate cross-document href: a relative path plus optional fragment."""

    path: str
    fragment: Optional[str]


def parse_cross_doc_href(href: Optional[str]) -> Optional[CrossDocHref]:
    """Split `href` into (path, fragment) if it is a same-file-system relative
    reference, or None if it's a same-document anchor, an absolute URL
    (`http(s)://`, `mailto:`, ...), an absolute filesystem path, or otherwise
    not a cross-doc candidate.

    Deliberately conservative: any scheme, network location, or leading `/`
    rules a link out, so an absolute URL or absolute path is never
    misclassified as a cross-doc reference (criterion 8) — this module's
    contract is "relative to the pushing file," and `resolve_local_mapping`'s
    `posixpath.join` would silently discard that relativity for a
    root-relative href otherwise. A path-only href with no fragment is still
    a candidate — fragment resolution is optional, the path is not.
    """
    if not href or is_anchor(href):
        return None
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return None
    return CrossDocHref(path=parts.path, fragment=parts.fragment or None)


def normalize_local_path(path: str) -> str:
    """Normalize a mapping/href-relative path for comparison.

    POSIX-style and `..`/`.`-resolved so `../a/b.md` and `a/../a/b.md` match
    the same mapping (criterion 8). Case is preserved deliberately —
    mappings are matched byte-for-byte, matching this backend's existing
    anchor-matching stance of exact match over guesswork.
    """
    return posixpath.normpath(path.replace("\\", "/"))


def resolve_local_mapping(
    source_local_path: str,
    href_path: str,
    mappings: List["Mapping"],
) -> Optional["Mapping"]:
    """Resolve `href_path` (relative to `source_local_path`) to the `Mapping`
    entry for the file it points at.

    Returns None when no mapping matches — the caller leaves such a link
    untouched (criterion 3), since a relative link to an un-mapped file
    (e.g. one outside this project's scope) is not an error. Raises
    AmbiguousMappingError when more than one mapping entry normalizes to the
    same path (criterion 7) rather than silently picking one.
    """
    source_dir = posixpath.dirname(normalize_local_path(source_local_path))
    target = normalize_local_path(posixpath.join(source_dir, href_path))

    matches = [
        m for m in mappings if normalize_local_path(m.local) == target
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousMappingError(
            f"{href_path!r} (resolved to {target!r}) matches {len(matches)} mapping "
            f"entries: {', '.join(m.local for m in matches)} — fix markgate.yaml so "
            "only one mapping's local path resolves here."
        )
    return matches[0]


@dataclass
class TargetHeadings:
    slug_to_id: Dict[str, str]
    known_ids: Set[str]


# (doc_id, tab_id) -> DocsParagraphNode/DocsTableNode list of the target document.
FetchHeadings = Callable[[str, Optional[str]], list]


@dataclass
class CrossDocResolution:
    """Outcome of resolving one href through `CrossDocLinkResolver.resolve`.

    kind:
        "not_cross_doc"    — href isn't a cross-doc candidate; caller should
                              fall through to same-document anchor handling.
        "unmapped"         — path resolves to no mapping entry; href is left
                              untouched (criterion 3).
        "unsupported"      — mapping exists but isn't a usable Google Docs
                              push target (wrong backend, or no remote_id yet).
        "ambiguous"        — resolve_local_mapping found >1 matching mapping.
        "fetch_failed"     — target document could not be fetched.
        "unresolved_anchor"— target fetched, but its fragment matches no heading.
        "resolved"         — `url` holds the payload to write.
    """

    kind: str
    url: Optional[str] = None
    detail: Optional[str] = None


class CrossDocLinkResolver:
    """Resolves cross-document links against a run's mapping table.

    Each distinct (doc_id, tab_id) target is fetched via `fetch_headings` at
    most once per resolver instance — a `push --all` run with many links to
    the same target document (criterion 6) or many mappings (criterion 10)
    still issues one fetch per distinct target, not one per link or mapping.

    `cache`/`fetch_errors` may be supplied externally (and mutated in place)
    so a whole `push --all` run — which constructs one resolver per pushed
    document, since each mapping gets its own backend instance — still shares
    a single fetch per distinct target across all of them, not one per
    pushed document (criterion 11).
    """

    def __init__(
        self,
        mappings: List["Mapping"],
        fetch_headings: FetchHeadings,
        cache: Optional[Dict[tuple, Optional[TargetHeadings]]] = None,
        fetch_errors: Optional[Dict[tuple, str]] = None,
    ):
        self._mappings = mappings
        self._fetch_headings = fetch_headings
        self._cache: Dict[tuple, Optional[TargetHeadings]] = {} if cache is None else cache
        self._fetch_errors: Dict[tuple, str] = {} if fetch_errors is None else fetch_errors

    def resolve(self, source_local_path: str, href: str) -> CrossDocResolution:
        parsed = parse_cross_doc_href(href)
        if parsed is None:
            return CrossDocResolution(kind="not_cross_doc")

        try:
            mapping = resolve_local_mapping(source_local_path, parsed.path, self._mappings)
        except AmbiguousMappingError as exc:
            return CrossDocResolution(kind="ambiguous", detail=str(exc))

        if mapping is None:
            return CrossDocResolution(kind="unmapped")

        if mapping.backend != "google_docs" or not mapping.remote_id:
            return CrossDocResolution(
                kind="unsupported",
                detail=(
                    f"{href!r} points at {mapping.local!r}, which has no "
                    f"{'google_docs backend' if mapping.backend != 'google_docs' else 'remote_id yet'} "
                    "— cross-document links are only resolved to Google Docs targets that "
                    "have already been created."
                ),
            )

        base_url = f"https://docs.google.com/document/d/{mapping.remote_id}/edit"
        if parsed.fragment is None:
            return CrossDocResolution(kind="resolved", url=base_url)

        headings = self._headings_for(mapping.remote_id, mapping.tab_id)
        if headings is None:
            detail = f"{href!r} — could not fetch target document {mapping.remote_id!r} to resolve its heading"
            error = self._fetch_errors.get((mapping.remote_id, mapping.tab_id))
            if error:
                detail += f": {error}"
            return CrossDocResolution(kind="fetch_failed", detail=detail)

        heading_id = resolve_anchor("#" + parsed.fragment, headings.slug_to_id, headings.known_ids)
        if heading_id is None:
            return CrossDocResolution(
                kind="unresolved_anchor",
                detail=f"{href!r} — no heading named {parsed.fragment!r} in {mapping.local!r}",
            )

        return CrossDocResolution(kind="resolved", url=f"{base_url}#heading={heading_id}")

    def _headings_for(self, doc_id: str, tab_id: Optional[str]) -> Optional[TargetHeadings]:
        key = (doc_id, tab_id)
        if key not in self._cache:
            self._cache[key] = self._fetch_target_headings(doc_id, tab_id)
        return self._cache[key]

    def _fetch_target_headings(self, doc_id: str, tab_id: Optional[str]) -> Optional[TargetHeadings]:
        from docspan.backends.google_docs.heading_anchors import heading_slug_to_id

        try:
            nodes = self._fetch_headings(doc_id, tab_id)
        except Exception as exc:
            logger.warning(
                "cross-doc link resolution: failed to fetch target document %r (tab %r): %s",
                doc_id, tab_id, exc,
            )
            self._fetch_errors[(doc_id, tab_id)] = str(exc)
            return None
        slug_to_id = heading_slug_to_id(nodes)
        return TargetHeadings(slug_to_id=slug_to_id, known_ids=set(slug_to_id.values()))


def link_payload(
    href: Optional[str],
    source_local_path: Optional[str],
    resolver: Optional[CrossDocLinkResolver],
    slug_to_id: Optional[Dict[str, str]] = None,
    known_ids: Optional[Set[str]] = None,
) -> "tuple[Optional[dict], Optional[str]]":
    """Cross-doc-aware drop-in for `heading_anchors.link_payload`.

    Returns `(payload, unresolved_detail)`. `payload` mirrors
    `heading_anchors.link_payload`'s contract exactly (a dict to write, or
    None to write nothing). `unresolved_detail` is None unless the link
    should be reported instead of written — an unmapped cross-doc link still
    returns `(payload, None)` since criterion 3 says it is left untouched,
    not reported.

    Falls through to `heading_anchors.link_payload` unchanged whenever
    `resolver`/`source_local_path` is absent or `href` isn't a cross-doc
    candidate — same-document anchor resolution (criterion 2) is unaffected.
    """
    if resolver is not None and source_local_path is not None and href:
        resolution = resolver.resolve(source_local_path, href)
        if resolution.kind == "unmapped":
            return {"url": href}, None
        if resolution.kind == "resolved":
            return {"url": resolution.url}, None
        if resolution.kind in ("ambiguous", "unsupported", "fetch_failed", "unresolved_anchor"):
            return None, resolution.detail
        # "not_cross_doc" falls through below.

    if href is None:
        return None, None
    return _same_doc_link_payload(href, slug_to_id, known_ids), None
