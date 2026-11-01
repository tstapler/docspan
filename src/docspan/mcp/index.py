"""SQLite FTS5 index over pulled docs, keyed by (doc_id, tab_id, heading_path).

Incremental per doc: a doc whose ``revisionId`` matches ``doc_meta`` is skipped.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

STALE_AFTER_SECONDS = 24 * 3600
MAX_SCOPE_IDS = 500  # stays under SQLite's bound-variable limit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, title TEXT, pulled_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_sections USING fts5(
    doc_id UNINDEXED, tab_id UNINDEXED, heading_id UNINDEXED,
    heading_path, content, tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS doc_edges (
    source_doc_id TEXT NOT NULL, target_doc_id TEXT NOT NULL, PRIMARY KEY (source_doc_id, target_doc_id)
);
"""


class IndexedSection:
    __slots__ = ("tab_id", "heading_id", "heading_path", "content")

    def __init__(self, tab_id: str, heading_id: Optional[str], heading_path: str, content: str) -> None:
        self.tab_id, self.heading_id, self.heading_path, self.content = (
            tab_id, heading_id, heading_path, content)


class SearchRow:
    __slots__ = ("doc_id", "tab_id", "heading_id", "heading_path", "snippet", "pulled_at")

    def __init__(self, doc_id: str, tab_id: str, heading_id: Optional[str], heading_path: str,
                 snippet: str, pulled_at: float) -> None:
        self.doc_id, self.tab_id, self.heading_id = doc_id, tab_id, heading_id
        self.heading_path, self.snippet, self.pulled_at = heading_path, snippet, pulled_at


def _match_expr(query: str) -> str:
    """Quote each term so user input can never be parsed as FTS5 syntax (AND/NEAR/-/:)."""
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    return " ".join(f'"{t}"' for t in terms)


class DocspanIndex:
    def __init__(self, db_path: Union[str, Path]) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # MCP sync tools run on a worker thread pool, so the connection is shared under a lock.
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.RLock()
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def is_stale(self, doc_id: str, revision_id: str) -> bool:
        if not revision_id:
            return True  # no revision to compare: never claim "unchanged"
        with self._lock:
            row = self._db.execute(
                "SELECT revision_id FROM doc_meta WHERE doc_id = ?", (doc_id,)).fetchone()
        return row is None or row[0] != revision_id

    def ingest(self, doc_id: str, revision_id: str, title: str,
               sections: Iterable[IndexedSection]) -> None:
        """Replace everything indexed for ``doc_id`` atomically."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM doc_sections WHERE doc_id = ?", (doc_id,))
            self._db.executemany(
                "INSERT INTO doc_sections (doc_id, tab_id, heading_id, heading_path, content)"
                " VALUES (?, ?, ?, ?, ?)",
                [(doc_id, s.tab_id, s.heading_id, s.heading_path, s.content) for s in sections])
            self._db.execute(
                "INSERT OR REPLACE INTO doc_meta (doc_id, revision_id, title, pulled_at)"
                " VALUES (?, ?, ?, ?)", (doc_id, revision_id, title, time.time()))

    def count_docs(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM doc_meta").fetchone()[0])

    def search(self, query: str, limit: int = 10,
               doc_ids: Optional[Sequence[str]] = None) -> List[SearchRow]:
        expr = _match_expr(query)
        if not expr:
            return []
        sql = (
            "SELECT s.doc_id, s.tab_id, s.heading_id, s.heading_path,"
            " snippet(doc_sections, 4, '[', ']', '...', 16), m.pulled_at"
            " FROM doc_sections s JOIN doc_meta m ON m.doc_id = s.doc_id"
            " WHERE doc_sections MATCH ?"
        )
        params: List[object] = [expr]
        if doc_ids:
            doc_ids = list(doc_ids)[:MAX_SCOPE_IDS]
            sql += f" AND s.doc_id IN ({','.join('?' * len(doc_ids))})"
            params += list(doc_ids)
        # heading_path weighted above body so a title match outranks a passing mention
        sql += " ORDER BY bm25(doc_sections, 0, 0, 0, 5.0, 1.0) LIMIT ?"
        params.append(limit)
        with self._lock:
            return [SearchRow(*r) for r in self._db.execute(sql, params).fetchall()]

    def record_edge(self, source: str, target: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO doc_edges VALUES (?, ?)", (source, target))

    def edges(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._db.execute("SELECT source_doc_id, target_doc_id FROM doc_edges"))
