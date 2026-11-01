"""Heading-tree extraction and compound heading-path resolution over parsed Docs nodes.

New logic, not ``split_nodes``: that only splits at one heading level and cannot
resolve ``Tab > H2 > H3``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
)
from docspan.backends.google_docs.heading_anchors import is_heading_style
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.projection import project
from docspan.backends.google_docs.tabs import flatten_tabs, resolve_document_tab
from docspan.mcp.errors import DocspanToolError

PATH_SEP = ">"
_DOC_URL = "https://docs.google.com/document/d/{doc_id}/edit"
_STYLE_LEVEL = {"TITLE": 1, "SUBTITLE": 2}


def doc_url(doc_id: str, tab_id: str = "", heading_id: Optional[str] = None) -> str:
    """Canonical Docs link; ``?tab=`` and ``#heading=`` are what Docs itself emits."""
    url = _DOC_URL.format(doc_id=doc_id)
    if tab_id:
        url += f"?tab={tab_id}"
    if heading_id:
        url += f"#heading={heading_id}"
    return url


def heading_level(style: object) -> int:
    s = str(style)
    if s in _STYLE_LEVEL:
        return _STYLE_LEVEL[s]
    return int(s.split("_", 1)[1])


@dataclass
class Section:
    tab_id: str
    tab_name: str
    level: int  # 0 = the tab's whole body (no heading)
    text: str
    heading_id: Optional[str]
    path: str
    start: int  # node index of the heading (0 for the tab body)
    end: int  # exclusive; runs to the next same-or-higher heading, so includes subsections
    own_end: int  # exclusive; runs to the next heading of any level (the section's own body)


@dataclass
class TabContent:
    tab_id: str
    tab_name: str
    nodes: list
    sections: List[Section]


def _render(nodes: Sequence) -> str:
    return render_nodes_to_markdown(list(nodes)) if nodes else ""


def build_sections(nodes: list, tab_id: str, tab_name: str) -> List[Section]:
    """One section per heading, preceded by a level-0 entry covering the whole tab."""
    heads = [
        i for i, n in enumerate(nodes)
        if isinstance(n, DocsParagraphNode) and is_heading_style(n.style)
    ]
    sections = [Section(tab_id, tab_name, 0, tab_name, None, tab_name, 0, len(nodes),
                        heads[0] if heads else len(nodes))]
    stack: List[Tuple[int, str]] = []
    for pos, i in enumerate(heads):
        node = nodes[i]
        level = heading_level(node.style)
        while stack and stack[-1][0] >= level:
            stack.pop()
        text = node.text.strip()
        stack.append((level, text))
        path = f" {PATH_SEP} ".join([tab_name] + [t for _, t in stack])
        end = len(nodes)
        for j in heads[pos + 1:]:
            if heading_level(nodes[j].style) <= level:
                end = j
                break
        own_end = heads[pos + 1] if pos + 1 < len(heads) else len(nodes)
        sections.append(Section(tab_id, tab_name, level, text, node.heading_id, path, i, end, own_end))
    return sections


def parse_tabs(doc: dict) -> List[TabContent]:
    """Parse every tab of a ``documents.get(includeTabsContent=True)`` response.

    A legacy doc with no ``tabs`` yields one tab with an empty id, named after the doc.
    """
    tabs = flatten_tabs(doc.get("tabs") or [])
    entries = (
        [(t.get("tabProperties", {}).get("tabId", ""), t.get("tabProperties", {}).get("title", ""))
         for t in tabs]
        if tabs else [("", doc.get("title", ""))]
    )
    out: List[TabContent] = []
    for tab_id, title in entries:
        resolved, _, _ = resolve_document_tab(doc, tab_id or None)
        nodes, _residue = project(DocsStructureParser().parse(resolved))
        out.append(TabContent(tab_id, title, nodes, build_sections(nodes, tab_id, title)))
    return out


def section_markdown(tab: TabContent, section: Section) -> str:
    return _render(tab.nodes[section.start:section.end])


def own_markdown(tab: TabContent, section: Section) -> str:
    """The section's own body only, for indexing without duplicating subsections."""
    return _render(tab.nodes[section.start:section.own_end])


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def split_path(heading_path: str) -> List[str]:
    return [p.strip() for p in heading_path.split(PATH_SEP) if p.strip()]


def find_section_by_path(
    tabs: List[TabContent], heading_path: str, doc_id: str = ""
) -> Tuple[TabContent, Section]:
    """Resolve ``Tab > H2 > H3`` (case/whitespace-insensitive) to exactly one section.

    The tab name may be omitted for a single-tab doc. Ancestors may be skipped, so
    ``Tab > H3`` matches an H3 under any H2 of that tab; if that is ambiguous the
    error lists every candidate with its url.
    """
    parts = [_norm(p) for p in split_path(heading_path)]
    if not parts:
        raise DocspanToolError("invalid_heading_path", "heading_path is empty")

    def matches(sec: Section, want: List[str]) -> bool:
        have = [_norm(p) for p in split_path(sec.path)]
        if sec.level == 0:
            return want == have
        if have[-1] != want[-1] or len(want) > len(have):
            return False
        it = iter(have[:-1])  # want[:-1] must be an ordered subsequence of the ancestors
        return all(any(w == h for h in it) for w in want[:-1])

    candidates = [(t, s) for t in tabs for s in t.sections if matches(s, parts)]
    if not candidates and len(tabs) == 1:
        # Tab name omitted: retry with it prepended.
        parts_with_tab = [_norm(tabs[0].tab_name)] + parts
        candidates = [(t, s) for t in tabs for s in t.sections if matches(s, parts_with_tab)]
    if not candidates:
        raise DocspanToolError(
            "section_not_found", f"No section matches {heading_path!r}",
            {"available": [s.path for t in tabs for s in t.sections][:50]},
        )
    if len(candidates) > 1:
        raise DocspanToolError(
            "ambiguous_heading_path",
            f"{heading_path!r} matches {len(candidates)} sections; qualify it with a parent heading",
            {"matches": [
                {"heading_path": s.path, "heading_id": s.heading_id,
                 "url": doc_url(doc_id, s.tab_id, s.heading_id)}
                for _, s in candidates
            ]},
        )
    return candidates[0]


def section_sizes(tabs: List[TabContent]) -> Dict[Tuple[str, str], int]:
    """(tab_id, path) -> markdown char count for every section, rendered once each."""
    return {(t.tab_id, s.path): len(section_markdown(t, s)) for t in tabs for s in t.sections}
