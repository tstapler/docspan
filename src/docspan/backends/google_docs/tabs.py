"""Tab resolution for multi-tab Google Docs.

A Google Doc's `tabs` field (populated when `documents.get` is called with
`includeTabsContent=True`, see client.py) is a list of Tab resources, each of
which may itself nest more tabs under `childTabs`. Every tab has its own
`documentTab.body`/`documentTab.lists` — the legacy top-level `document.body`/
`document.lists` fields are only ever a copy of the *first* tab's content.

This module centralizes "which tab did the caller mean" so backend.py,
docs_structure_parser.py, and docs_request_builder.py don't each re-implement
the same tabs[0] fallback (they used to, in three places, none of which had
any way to select a non-default tab — that's the root cause of pulling the
wrong tab's content in a multi-tab doc).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class TabNotFoundError(Exception):
    """Raised when a configured tab_id doesn't match any tab in the document."""


@dataclass
class TabInfo:
    tab_id: str
    title: str


def flatten_tabs(tabs: List[dict]) -> List[dict]:
    """Flatten a document's `tabs` list (and any nested `childTabs`) in document order."""
    flat: List[dict] = []
    for tab in tabs:
        flat.append(tab)
        child_tabs = tab.get("childTabs") or []
        if child_tabs:
            flat.extend(flatten_tabs(child_tabs))
    return flat


def heading_ids_by_tab(doc: dict) -> Dict[str, str]:
    """headingId -> tabId, for every heading in *every* tab of ``doc``.

    Anchor resolution otherwise only ever sees one tab, because
    `resolve_document_tab` narrows the document before it is parsed. A link into
    a heading in a sibling tab is then unresolvable: `pull` keeps the bare id
    (there is no slug for it in this tab), and every subsequent push reports it
    as a dead anchor — forever, on a file that is exactly what `pull` produced
    and a Doc whose link is perfectly fine. Worse, the link is lost from the Doc
    the moment a text edit makes pass 1 rewrite that paragraph.

    Needs the **unresolved** document, so callers must capture it before
    narrowing. Returns {} for a document with no `tabs` key at all (the legacy
    shape) — note a *single-tab* document fetched with `includeTabsContent=True`
    still has one entry per heading, which is harmless because `align()` then
    filters out every id the current tab owns.

    **An id that appears in more than one tab is dropped, not won.** Taking the
    last writer made the tab a reader lands in depend on tab order, silently and
    with a green `ok` — the same trade `heading_anchors._match_keyed` refuses in
    the other direction. Whether Docs can actually mint a duplicate `headingId`
    across tabs (Duplicate tab?) is unverified; the discipline is cheap either way.

    Headings inside tables, and a tab with no `tabProperties`, are skipped. Both
    degrade to the pre-existing dead-anchor report rather than a wrong link, and
    the table case matches `known_ids`, whose parser skips tables too.
    """
    ids: Dict[str, str] = {}
    ambiguous: set = set()
    for tab in flatten_tabs(doc.get("tabs") or []):
        tab_id = tab.get("tabProperties", {}).get("tabId", "")
        content = tab.get("documentTab", {}).get("body", {}).get("content", [])
        for element in content:
            style = (element.get("paragraph") or {}).get("paragraphStyle", {})
            heading_id = style.get("headingId")
            if not (heading_id and tab_id):
                continue
            if heading_id in ids and ids[heading_id] != tab_id:
                ambiguous.add(heading_id)
            ids[heading_id] = tab_id
    for heading_id in ambiguous:
        del ids[heading_id]
    return ids


def list_tabs(doc: dict) -> List[TabInfo]:
    """Return every tab in `doc` (empty if the doc has no tabs, i.e. isn't multi-tab)."""
    tabs = doc.get("tabs") or []
    infos = []
    for tab in flatten_tabs(tabs):
        props = tab.get("tabProperties", {})
        infos.append(TabInfo(tab_id=props.get("tabId", ""), title=props.get("title", "")))
    return infos


def resolve_document_tab(
    doc: dict, tab_id: Optional[str] = None
) -> Tuple[dict, Optional[str], Optional[str]]:
    """Resolve which tab's content to operate on and normalize `doc` to expose it.

    Args:
        doc: Full document resource from GoogleDocsClient.get_document()
            (ideally fetched with include_tabs_content=True).
        tab_id: The tab to target, e.g. from Mapping.tab_id. None means
            "no preference".

    Returns:
        (resolved_doc, resolved_tab_id, warning)

        - resolved_doc: a shallow copy of `doc` with `body`/`lists` set from
          the resolved tab's `documentTab`, and `tabs` cleared — so existing
          tabs-unaware code (DocsStructureParser.parse(), _body_content(),
          etc.) that reads `doc["body"]` operates on the *correct* tab
          without any further changes. Legacy documents with no `tabs` at
          all are returned unchanged.
        - resolved_tab_id: the tabId actually selected, or None if the
          document has no tabs (legacy doc).
        - warning: set when the document has more than one tab and `tab_id`
          was not given — the caller is defaulting to the first tab, which
          silently reproduces this feature's original bug if that's not
          what the user wanted. None when there's no ambiguity (single tab,
          no tabs at all, or tab_id was given explicitly and matched).

    Raises:
        TabNotFoundError: `tab_id` was given but no tab in the document has
            that id.
    """
    tabs = doc.get("tabs") or []
    if not tabs:
        if tab_id is not None:
            raise TabNotFoundError(
                f"tab_id {tab_id!r} not found in document. Available tabs: none "
                "(this document has no tabs)"
            )
        return doc, None, None

    flat = flatten_tabs(tabs)

    warning: Optional[str] = None
    if tab_id is not None:
        chosen = next(
            (t for t in flat if t.get("tabProperties", {}).get("tabId") == tab_id), None
        )
        if chosen is None:
            available = ", ".join(
                f"{t.get('tabProperties', {}).get('tabId')!r} ({t.get('tabProperties', {}).get('title')!r})"
                for t in flat
            )
            raise TabNotFoundError(
                f"tab_id {tab_id!r} not found in document. Available tabs: {available or 'none'}"
            )
    else:
        chosen = flat[0]
        if len(flat) > 1:
            titles = ", ".join(
                f"{t.get('tabProperties', {}).get('title') or '(untitled)'} "
                f"[{t.get('tabProperties', {}).get('tabId')}]"
                for t in flat
            )
            warning = (
                f"Document has {len(flat)} tabs ({titles}) but no tab_id is configured — "
                "defaulting to the first tab. Set 'tab_id' in markgate.yaml to target a "
                "specific tab; otherwise this will keep silently operating on whichever "
                "tab happens to be first."
            )

    resolved_tab_id = chosen.get("tabProperties", {}).get("tabId")
    doc_tab = chosen.get("documentTab", {})

    resolved = dict(doc)
    resolved["tabs"] = []
    resolved["body"] = doc_tab.get("body", {})
    resolved["lists"] = doc_tab.get("lists", {})
    return resolved, resolved_tab_id, warning
