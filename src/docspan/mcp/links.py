"""Outbound Google Doc links, read straight off the raw ``documents.get`` JSON.

Deliberately not ``parse_cross_doc_href``: that recognizes *relative markdown* links
between mapped files and never sees a ``docs.google.com`` URL.
"""
from __future__ import annotations

import re
from typing import Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from docspan.backends.google_docs.tabs import flatten_tabs
from docspan.mcp.errors import DocspanToolError
from docspan.mcp.models import DocLink
from docspan.mcp.sections import doc_url

_DOC_PATH = re.compile(r"^/(?:a/[^/]+/)?document/d/([A-Za-z0-9_-]+)")


def parse_doc_url(url: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """(doc_id, tab_id, heading_id) for a docs.google.com document URL, else None."""
    parsed = urlparse(url)
    if parsed.netloc != "docs.google.com":
        return None
    m = _DOC_PATH.match(parsed.path)
    if not m:
        return None
    tabs = parse_qs(parsed.query).get("tab")
    tab = tabs[0] if tabs else None
    heading = None
    if parsed.fragment.startswith("heading="):
        heading = parsed.fragment[len("heading="):] or None
    return m.group(1), tab, heading


_DOC_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def extract_doc_id(url_or_id: str) -> str:
    """Accept a bare id or any Docs URL; reject anything else.

    The id becomes a directory name under the scratch dir, so it must not be able
    to carry path separators or ``..``.
    """
    parsed = parse_doc_url(url_or_id.strip())
    doc_id = parsed[0] if parsed else url_or_id.strip()
    if not _DOC_ID.match(doc_id):
        raise DocspanToolError("invalid_doc_id", f"Not a Google Doc id or URL: {url_or_id!r}")
    return doc_id


def _walk_content(content: list) -> Iterator[dict]:
    """Every paragraph element, descending into table cells."""
    for el in content or []:
        para = el.get("paragraph")
        if para:
            yield from para.get("elements", [])
        table = el.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _walk_content(cell.get("content", []))
        toc = el.get("tableOfContents")
        if toc:
            yield from _walk_content(toc.get("content", []))


def _bodies(doc: dict) -> Iterator[list]:
    tabs = flatten_tabs(doc.get("tabs") or [])
    if tabs:
        for t in tabs:
            yield t.get("documentTab", {}).get("body", {}).get("content", [])
    else:
        yield doc.get("body", {}).get("content", [])


def _element_url(el: dict) -> Optional[str]:
    link = (el.get("textRun") or {}).get("textStyle", {}).get("link") or {}
    if link.get("url"):
        return str(link["url"])
    if link.get("documentId") or link.get("tabId"):  # structured in-Docs link
        return doc_url(link.get("documentId", ""), link.get("tabId", ""), link.get("headingId"))
    rich = (el.get("richLink") or {}).get("richLinkProperties", {})
    uri = rich.get("uri")
    return str(uri) if uri else None


def resolve_links(doc: dict) -> List[DocLink]:
    """All distinct outbound Google Doc links, in document order."""
    seen = set()
    links: List[DocLink] = []
    for content in _bodies(doc):
        for el in _walk_content(content):
            url = _element_url(el)
            parsed = parse_doc_url(url) if url else None
            if not parsed:
                continue
            doc_id, tab, heading = parsed
            if (doc_id, tab, heading) in seen:
                continue
            seen.add((doc_id, tab, heading))
            text = (el.get("textRun") or {}).get("content", "")
            title = (el.get("richLink") or {}).get("richLinkProperties", {}).get("title", "")
            links.append(DocLink(
                target_doc_id=doc_id, target_tab=tab, target_heading_id=heading,
                anchor_text=(text or title).strip(), url=doc_url(doc_id, tab or "", heading),
            ))
    return links
