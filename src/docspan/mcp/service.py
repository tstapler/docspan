"""Tool implementations, independent of the MCP SDK so they can be unit-tested and reused by the CLI."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, Callable, Dict, List, Optional

from googleapiclient.errors import HttpError

from docspan.backends.google_docs.comments import bucket_comments_by_section
from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
from docspan.config import MarkgateConfig
from docspan.mcp import links as links_mod
from docspan.mcp.errors import DocspanToolError
from docspan.mcp.index import STALE_AFTER_SECONDS, DocspanIndex, IndexedSection
from docspan.mcp.models import (
    CommentsResult,
    CommentThread,
    DiffResult,
    FetchedTab,
    FetchResult,
    HeadingNode,
    LinksResult,
    MappingEntry,
    MappingsResult,
    OutlineResult,
    SearchHit,
    SearchResult,
    SectionResult,
    TabOutline,
)
from docspan.mcp.sections import (
    TabContent,
    doc_url,
    find_section_by_path,
    own_markdown,
    parse_tabs,
    section_markdown,
)

DEFAULT_MAX_CHARS = 4000
SNAPSHOT_NAME = "snapshot.json"


def _plain(tab: TabContent, start: int, end: int) -> str:
    return "\n".join(
        n.text for n in tab.nodes[start:end] if isinstance(n, DocsParagraphNode))


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "tab"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class DocService:
    """Read-only operations over one authenticated Docs client.

    ``client`` is a ``GoogleDocsClient`` (or a test double with ``get_document``,
    ``get_comments`` and ``get_modified_time``).
    """

    def __init__(
        self,
        client_factory: Callable[[], object],
        scratch_dir: pathlib.Path,
        index: Optional[DocspanIndex] = None,
        config: Optional[MarkgateConfig] = None,
        state_path: Optional[str] = None,
    ) -> None:
        self._client_factory = client_factory
        self._client: Any = None
        self.scratch_dir = pathlib.Path(scratch_dir)
        self.index = index
        self.config = config
        self.state_path = state_path

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _doc(self, doc_id: str) -> dict:
        doc: dict = self.client.get_document(doc_id, include_tabs_content=True)
        return doc

    # -- list_mappings -------------------------------------------------------
    def list_mappings(self) -> MappingsResult:
        if self.config is None:
            raise DocspanToolError("no_config", "No docspan config is loaded for this server")
        synced: Dict[str, str] = {}
        if self.state_path:
            from docspan.core import SyncState
            state = SyncState.load(self.state_path)
            synced = {k: v.last_synced_at for k, v in state.mappings.items()}
        entries = [
            MappingEntry(
                local=m.local, remote_id=m.remote_id, backend=m.backend, direction=m.direction,
                tab_id=m.tab_id, last_synced=synced.get(m.local),
                url=doc_url(m.remote_id, m.tab_id or "") if m.backend == "google_docs" else "",
            )
            for m in self.config.mappings if m.remote_id
        ]
        return MappingsResult(mappings=entries)

    # -- outline -------------------------------------------------------------
    def _outline_from(self, doc_id: str, doc: dict, tabs: List[TabContent],
                      last_modified: Optional[str]) -> OutlineResult:
        tab_rows, headings = [], []
        for t in tabs:
            for s in t.sections:
                size = len(section_markdown(t, s))
                if s.level == 0:
                    tab_rows.append(TabOutline(
                        tab_id=t.tab_id, tab_name=t.tab_name, char_count=size,
                        url=doc_url(doc_id, t.tab_id)))
                else:
                    headings.append(HeadingNode(
                        level=s.level, text=s.text, heading_path=s.path,
                        heading_id=s.heading_id, tab_id=t.tab_id, tab_name=t.tab_name,
                        char_count=size, url=doc_url(doc_id, t.tab_id, s.heading_id)))
        return OutlineResult(
            doc_id=doc_id, title=doc.get("title", ""), revision_id=str(doc.get("revisionId", "")),
            last_modified=last_modified, tabs=tab_rows, headings=headings, url=doc_url(doc_id))

    def _last_modified(self, doc_id: str) -> Optional[str]:
        try:
            return str(self.client.get_modified_time(doc_id).isoformat())
        except HttpError:
            return None  # Drive metadata is a nicety; auth errors still propagate

    def outline(self, doc_id: str) -> OutlineResult:
        doc = self._doc(doc_id)
        return self._outline_from(doc_id, doc, parse_tabs(doc), self._last_modified(doc_id))

    # -- read_section --------------------------------------------------------
    def read_section(self, doc_id: str, heading_path: str,
                     max_chars: int = DEFAULT_MAX_CHARS) -> SectionResult:
        tabs = parse_tabs(self._doc(doc_id))
        tab, sec = find_section_by_path(tabs, heading_path, doc_id)
        text = section_markdown(tab, sec)
        limit = max(0, max_chars)
        return SectionResult(
            doc_id=doc_id, heading_path=sec.path, text=text[:limit], char_count=len(text),
            truncated=len(text) > limit, url=doc_url(doc_id, sec.tab_id, sec.heading_id))

    # -- fetch_doc -----------------------------------------------------------
    def _check_destination(self, to_path: str) -> pathlib.Path:
        dest = pathlib.Path(to_path).expanduser().resolve()
        roots = [self.scratch_dir.resolve(), pathlib.Path.cwd().resolve()]
        if not any(dest == r or r in dest.parents for r in roots):
            raise DocspanToolError(
                "path_not_allowed", "to_path must be inside the scratch dir or the working directory",
                {"scratch_dir": str(self.scratch_dir)})
        return dest

    def _snapshot(self, tabs: List[TabContent]) -> Dict[str, List]:
        return {
            f"{t.tab_id}|{s.path}": [_hash(own_markdown(t, s)), len(own_markdown(t, s))]
            for t in tabs for s in t.sections
        }

    def fetch_doc(self, doc_id: str, tab: Optional[str] = None,
                  to_path: Optional[str] = None) -> FetchResult:
        doc = self._doc(doc_id)
        return self._fetch_from(doc_id, doc, tab, to_path)

    def _fetch_from(self, doc_id: str, doc: dict, tab: Optional[str],
                    to_path: Optional[str]) -> FetchResult:
        tabs = parse_tabs(doc)
        if tab is not None:
            selected = [t for t in tabs if tab in (t.tab_id, t.tab_name)]
            if not selected:
                raise DocspanToolError(
                    "tab_not_found", f"No tab {tab!r} in this document",
                    {"tabs": [{"tab_id": t.tab_id, "tab_name": t.tab_name} for t in tabs]})
        else:
            selected = tabs

        base = self.scratch_dir / doc_id
        dest = self._check_destination(to_path) if to_path else None
        files: List[FetchedTab] = []
        for t in selected:
            md = section_markdown(t, t.sections[0])
            if dest is not None and len(selected) == 1:
                path = dest
            else:
                path = (dest or base) / f"{_slug(t.tab_name) if t.tab_id else _slug(doc_id)}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(md)
            files.append(FetchedTab(tab_id=t.tab_id, tab_name=t.tab_name,
                                    local_path=str(path), char_count=len(md)))
        # The snapshot always covers every tab so diff_since compares like with like.
        base.mkdir(parents=True, exist_ok=True)
        (base / SNAPSHOT_NAME).write_text(json.dumps(
            {"revision_id": str(doc.get("revisionId", "")), "sections": self._snapshot(tabs)}))
        if self.index is not None:
            self.index_doc(doc_id, doc, tabs)
        outline = self._outline_from(doc_id, doc, tabs, None)
        return FetchResult(doc_id=doc_id, revision_id=outline.revision_id, files=files,
                           outline=outline, url=doc_url(doc_id))

    # -- index ---------------------------------------------------------------
    def index_doc(self, doc_id: str, doc: Optional[dict] = None,
                  tabs: Optional[List[TabContent]] = None, force: bool = False) -> bool:
        """Index one doc; returns False (and does no parsing) when its revision is unchanged."""
        assert self.index is not None
        doc = doc if doc is not None else self._doc(doc_id)
        revision = str(doc.get("revisionId", ""))
        if not force and not self.index.is_stale(doc_id, revision):
            return False
        tabs = tabs if tabs is not None else parse_tabs(doc)
        self.index.ingest(doc_id, revision, doc.get("title", ""), [
            IndexedSection(t.tab_id, s.heading_id, s.path, own_markdown(t, s))
            for t in tabs for s in t.sections
        ])
        return True

    # -- search_docs ---------------------------------------------------------
    def search_docs(self, query: str, scope: Optional[List[str]] = None,
                    limit: int = 10) -> SearchResult:
        if self.index is None:
            raise DocspanToolError("no_index", "No index available; run 'docspan index'")
        import time
        rows = self.index.search(query, limit=limit, doc_ids=scope)
        warning = None
        if self.index.count_docs() == 0:
            warning = "Index is empty — run 'docspan index' to build it"
        now = time.time()
        hits = [
            SearchHit(
                doc_id=r.doc_id, tab_id=r.tab_id, heading_path=r.heading_path, snippet=r.snippet,
                stale=now - r.pulled_at > STALE_AFTER_SECONDS,
                url=doc_url(r.doc_id, r.tab_id, r.heading_id))
            for r in rows
        ]
        return SearchResult(query=query, hits=hits, warning=warning)

    # -- list_comments -------------------------------------------------------
    def list_comments(self, doc_id: str, resolved: Optional[bool] = None) -> CommentsResult:
        raw = self.client.get_comments(doc_id)
        tabs = parse_tabs(self._doc(doc_id))
        keyed = [(s.path, _plain(t, s.start, s.own_end)) for t in tabs for s in t.sections if s.level]
        tab_of = {s.path: (t.tab_id, s.heading_id) for t in tabs for s in t.sections}
        bucket = bucket_comments_by_section(raw, keyed)
        path_of = {c["id"]: p for p, cs in bucket.by_section.items() for c in cs}
        threads = []
        for c in raw:
            if resolved is not None and bool(c.get("resolved")) != resolved:
                continue
            path = path_of.get(c["id"])
            tab_id, heading_id = tab_of.get(path, ("", None)) if path else ("", None)
            threads.append(CommentThread(
                comment_id=c["id"], content=c.get("content", ""),
                author=(c.get("author") or {}).get("displayName", ""),
                resolved=bool(c.get("resolved")),
                quoted_text=(c.get("quotedFileContent") or {}).get("value"),
                heading_path=path, url=doc_url(doc_id, tab_id, heading_id)))
        return CommentsResult(doc_id=doc_id, comments=threads, url=doc_url(doc_id))

    # -- resolve_links -------------------------------------------------------
    def resolve_links(self, doc_id: str) -> LinksResult:
        return LinksResult(doc_id=doc_id, links=links_mod.resolve_links(self._doc(doc_id)),
                           url=doc_url(doc_id))

    # -- diff_since ----------------------------------------------------------
    def diff_since(self, doc_id: str) -> DiffResult:
        """Diff the live doc against the copy last written by ``fetch_doc``.

        The Docs API cannot return content at a past revision, so "since a revision"
        is not implementable; "since last pull" is.
        """
        snap_path = self.scratch_dir / doc_id / SNAPSHOT_NAME
        if not snap_path.exists():
            raise DocspanToolError(
                "no_pulled_copy", "No pulled copy of this doc; call fetch_doc first",
                {"doc_id": doc_id})
        old = json.loads(snap_path.read_text())
        doc = self._doc(doc_id)
        new = self._snapshot(parse_tabs(doc))
        old_sections: Dict[str, List] = old["sections"]
        label = lambda key: key.split("|", 1)[1]  # noqa: E731
        added = [label(k) for k in new if k not in old_sections]
        removed = [label(k) for k in old_sections if k not in new]
        changed = [
            f"{label(k)} ({new[k][1] - old_sections[k][1]:+d} chars)"
            for k in new if k in old_sections and new[k][0] != old_sections[k][0]
        ]
        return DiffResult(
            doc_id=doc_id, last_pulled_revision=old.get("revision_id", ""),
            current_revision=str(doc.get("revisionId", "")),
            unchanged=not (added or removed or changed),
            added=added, removed=removed, changed=changed, url=doc_url(doc_id))

