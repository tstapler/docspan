"""Pydantic I/O models for the MCP tools. Every top-level response carries a ``url``."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class HeadingNode(BaseModel):
    level: int
    text: str
    heading_path: str
    heading_id: Optional[str] = None
    tab_id: str = ""
    tab_name: str = ""
    # Characters in the section as read_section would return it (heading plus subsections).
    char_count: int
    url: str


class TabOutline(BaseModel):
    tab_id: str
    tab_name: str
    char_count: int
    url: str


class OutlineResult(BaseModel):
    doc_id: str
    title: str
    revision_id: str
    last_modified: Optional[str] = None
    tabs: List[TabOutline]
    headings: List[HeadingNode]
    url: str


class FetchedTab(BaseModel):
    tab_id: str
    tab_name: str
    local_path: str
    char_count: int


class FetchResult(BaseModel):
    doc_id: str
    revision_id: str
    files: List[FetchedTab]
    outline: OutlineResult
    warning: Optional[str] = None
    url: str


class SectionResult(BaseModel):
    doc_id: str
    heading_path: str
    text: str
    char_count: int
    truncated: bool
    url: str


class SearchHit(BaseModel):
    doc_id: str
    tab_id: str
    heading_path: str
    snippet: str
    stale: bool = False
    url: str


class SearchResult(BaseModel):
    query: str
    hits: List[SearchHit]
    warning: Optional[str] = None
    url: str = ""


class CommentThread(BaseModel):
    comment_id: str
    content: str
    author: str
    resolved: bool
    quoted_text: Optional[str] = None
    heading_path: Optional[str] = None
    url: str


class CommentsResult(BaseModel):
    doc_id: str
    comments: List[CommentThread]
    warning: Optional[str] = None
    url: str


class DocLink(BaseModel):
    target_doc_id: str
    target_tab: Optional[str] = None
    target_heading_id: Optional[str] = None
    anchor_text: str
    url: str


class LinksResult(BaseModel):
    doc_id: str
    links: List[DocLink]
    url: str


class DiffResult(BaseModel):
    doc_id: str
    last_pulled_revision: str
    current_revision: str
    unchanged: bool
    added: List[str]
    removed: List[str]
    changed: List[str]
    url: str


class MappingEntry(BaseModel):
    local: str
    remote_id: str
    backend: str
    direction: str
    tab_id: Optional[str] = None
    last_synced: Optional[str] = None
    url: str


class MappingsResult(BaseModel):
    mappings: List[MappingEntry]
    url: str = ""
