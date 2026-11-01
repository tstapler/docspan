"""Breadth-first walk of the Google Doc link graph, pulling each doc into the scratch dir."""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from googleapiclient.errors import HttpError

from docspan.mcp import links as links_mod
from docspan.mcp.service import DocService


@dataclass
class CrawlResult:
    fetched: List[str] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
    skipped_scope: List[str] = field(default_factory=list)
    truncated: bool = False  # --max-docs stopped the walk with docs still queued


def crawl(
    service: DocService,
    start_doc_id: str,
    depth: int = 2,
    max_docs: int = 25,
    delay: float = 0.5,
    allowed_ids: Optional[Set[str]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlResult:
    """Pull ``start_doc_id`` and every doc reachable within ``depth`` link hops.

    ``allowed_ids`` restricts which *linked* docs are followed (the start doc is always
    pulled); edges to docs outside it are still recorded. A failure on one doc is recorded
    and the walk continues, except credential failures, which propagate: they would fail
    every remaining doc the same way.
    """
    result = CrawlResult()
    seen: Set[str] = {start_doc_id}
    queue = deque([(start_doc_id, 0)])
    first = True
    while queue:
        doc_id, d = queue.popleft()
        if len(result.fetched) >= max_docs:
            result.truncated = True
            break
        if not first:
            sleep(delay)
        first = False
        try:
            doc = service._doc(doc_id)
            service._fetch_from(doc_id, doc, None, None)
        except HttpError as exc:
            result.failed[doc_id] = f"HTTP {getattr(exc.resp, 'status', '?')}"
            continue
        result.fetched.append(doc_id)
        for link in links_mod.resolve_links(doc):
            target = link.target_doc_id
            if target == doc_id:
                continue
            edge = (doc_id, target)
            if edge not in result.edges:
                result.edges.append(edge)
                if service.index is not None:
                    service.index.record_edge(*edge)
            if target in seen or d >= depth:
                continue
            if allowed_ids is not None and target not in allowed_ids:
                if target not in result.skipped_scope:
                    result.skipped_scope.append(target)
                continue
            seen.add(target)
            queue.append((target, d + 1))
    return result


def write_graph(path: Path, start_doc_id: str, result: CrawlResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "start": start_doc_id,
        "fetched": result.fetched,
        "edges": [list(e) for e in result.edges],
        "failed": result.failed,
        "truncated": result.truncated,
    }, indent=2))
