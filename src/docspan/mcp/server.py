"""stdio MCP server exposing the eight read tools. Requires the ``mcp`` extra (SDK >= 2.0)."""
from __future__ import annotations

import json
from typing import Any, Callable, List, Optional, TypeVar

from docspan.mcp.auth import translate_exception
from docspan.mcp.links import extract_doc_id
from docspan.mcp.models import (
    CommentsResult,
    DiffResult,
    FetchResult,
    LinksResult,
    MappingsResult,
    OutlineResult,
    SearchResult,
    SectionResult,
)
from docspan.mcp.service import DEFAULT_MAX_CHARS, DocService

T = TypeVar("T")

INSTRUCTIONS = (
    "Query Google Docs without reading whole documents. Call outline first, then "
    "read_section for just the sections you need; search_docs works over docs already "
    "indexed (docspan index / fetch_doc). Every result carries a url you can cite."
)


def build_server(service: DocService) -> Any:
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "The MCP server needs the 'mcp' extra (Python >= 3.10): pip install 'docspan[mcp]'"
        ) from exc

    server = MCPServer("docspan", instructions=INSTRUCTIONS)

    def guarded(fn: Callable[[], T]) -> T:
        try:
            return fn()
        except Exception as exc:
            structured = translate_exception(exc)
            if structured is None:
                raise
            raise ToolError(json.dumps({"error": structured.to_dict()})) from exc

    @server.tool()
    def list_mappings() -> MappingsResult:
        """List mapped docs: local path, remote id, backend, direction, last sync."""
        return guarded(service.list_mappings)

    @server.tool()
    def fetch_doc(remote_id_or_url: str, tab: Optional[str] = None,
                  to_path: Optional[str] = None) -> FetchResult:
        """Pull a doc (or one tab, by id or name) to markdown files; returns paths and an outline, not the body."""
        return guarded(lambda: service.fetch_doc(extract_doc_id(remote_id_or_url), tab, to_path))

    @server.tool()
    def outline(remote_id_or_url: str) -> OutlineResult:
        """Heading tree with tab names, per-section char counts and last-modified; no body text."""
        return guarded(lambda: service.outline(extract_doc_id(remote_id_or_url)))

    @server.tool()
    def read_section(remote_id_or_url: str, heading_path: str,
                     max_chars: int = DEFAULT_MAX_CHARS) -> SectionResult:
        """Read one section by heading path, e.g. 'Tab > H2 > H3' (includes its subsections), up to max_chars."""
        return guarded(lambda: service.read_section(
            extract_doc_id(remote_id_or_url), heading_path, max_chars))

    @server.tool()
    def search_docs(query: str, scope: Optional[List[str]] = None) -> SearchResult:
        """Full-text search over indexed docs; scope optionally limits to a list of doc ids."""
        return guarded(lambda: service.search_docs(
            query, [extract_doc_id(s) for s in scope] if scope else None))

    @server.tool()
    def list_comments(remote_id_or_url: str, resolved: Optional[bool] = None) -> CommentsResult:
        """Comment threads with the heading each is anchored under; resolved=True/False filters."""
        return guarded(lambda: service.list_comments(extract_doc_id(remote_id_or_url), resolved))

    @server.tool()
    def resolve_links(remote_id_or_url: str) -> LinksResult:
        """Outbound Google Doc links (with tab and heading fragments) for walking the doc graph."""
        return guarded(lambda: service.resolve_links(extract_doc_id(remote_id_or_url)))

    @server.tool()
    def diff_since(remote_id_or_url: str) -> DiffResult:
        """What changed in the live doc since it was last pulled with fetch_doc."""
        return guarded(lambda: service.diff_since(extract_doc_id(remote_id_or_url)))

    return server
