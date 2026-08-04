"""Parse a Google Docs JSON document into a list of DocsParagraphNode objects."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Union

from docspan.backends.google_docs.heading_anchors import (
    anchor_target,
    heading_id_to_slug,
    is_anchor,
)

# Structural elements whose leading newline the Docs API refuses to delete on
# its own: "Deleting the newline character before a Table, TableOfContents or
# SectionBreak without deleting the element" is an invalid deleteContentRange
# (documents.request reference, DeleteContentRangeRequest). A paragraph's
# trailing newline IS that newline when one of these follows it, so a delete
# covering the paragraph's full [start_index, end_index) is rejected with
# "Invalid deletion range. Cannot delete the requested range." — see
# DocsRequestBuilder._make_delete_requests.
UNDELETABLE_BOUNDARY_KEYS = ("table", "tableOfContents", "sectionBreak")


def _person_display_text(person: dict) -> str:
    """Return display text for a Docs API `person` structural element.

    An @-mention "smart chip" (Insert > Smart chip > Person) is represented
    in the Docs API v1 JSON model as a `person` structural element — a
    sibling of `textRun` inside `paragraph.elements`, never a textRun
    itself. Its `personProperties` dict carries `name` and/or `email`;
    prefer the name, falling back to email when name is absent (e.g. the
    person hasn't shared a display name with the doc's viewer/API caller).
    Returns "" if neither is present so callers can skip emitting a span.
    """
    if not isinstance(person, dict):
        return ""
    person_properties = person.get("personProperties")
    if not isinstance(person_properties, dict):
        return ""
    name = person_properties.get("name")
    if isinstance(name, str) and name:
        return name
    email = person_properties.get("email")
    if isinstance(email, str) and email:
        return email
    return ""


@dataclass
class TextSpan:
    text: str
    bold: bool = False
    italic: bool = False
    # Either a URL or an internal anchor (`#slug`). A leading "#" is the whole
    # discriminator — no URL, absolute or relative, starts with one — so a
    # Docs `Link` carrying a `headingId` reads back into this same field as
    # "#" + the heading's slug rather than needing a parallel field. See
    # heading_anchors.
    link: Optional[str] = None
    monospace: bool = False


@dataclass
class DocsParagraphNode:
    """Represents a single paragraph in a Google Docs document."""
    style: str  # e.g. "NORMAL_TEXT", "HEADING_1", "HEADING_2", ...
    text: str   # Concatenated plain text (trailing \n stripped)
    is_list_item: bool = False
    nesting_level: int = 0
    start_index: int = 0
    end_index: int = 0
    spans: List[TextSpan] = field(default_factory=list)
    # True when this paragraph's bullet resolves to a native BULLET_CHECKBOX
    # glyph (glyphType == GLYPH_TYPE_UNSPECIFIED), resolved live by
    # DocsStructureParser from the document's `lists` map. NOT part of the
    # diff key (style, text, is_list_item) — feeds GlyphShapeCheck only
    # (via DiffEntry.current_is_native_checkbox), never
    # DocsRequestBuilder.build()'s equality/opcode logic. See ADR-001,
    # plan.md Task 1.2.2d.
    is_native_checkbox: bool = False
    # True when the very next structural element in the document body is a
    # Table, TableOfContents or SectionBreak (UNDELETABLE_BOUNDARY_KEYS). This
    # paragraph's trailing newline is then the newline that anchors that
    # element, and the Docs API rejects any deleteContentRange covering it
    # unless the element itself is deleted in the same range. Resolved live by
    # DocsStructureParser from the raw body content (sectionBreak and
    # tableOfContents elements are never turned into nodes, so this flag is the
    # only trace of them). NOT part of the diff key — consumed solely by
    # DocsRequestBuilder._make_delete_requests.
    precedes_structural_element: bool = False
    # paragraphStyle.headingId — Docs' own id for a heading, and the only thing
    # a `headingId` link can point at. Present on headings, None elsewhere.
    # NOT part of the diff key: it is assigned by Docs, so treating it as
    # identity would make every freshly written heading look like a different
    # paragraph from the one the markdown describes.
    heading_id: Optional[str] = None


@dataclass
class DocsTableNode:
    """Represents a table in a Google Docs document (plain-text cells)."""
    rows: List[List[str]] = field(default_factory=list)
    start_index: int = 0
    end_index: int = 0

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


class DocsStructureParser:
    """Parse a Google Docs document dict into a list of DocsParagraphNode."""

    def parse(self, doc: dict) -> List[Union[DocsParagraphNode, DocsTableNode]]:
        """
        Parse a Google Docs document dict.

        Handles both tabs-based format (doc['tabs'][0]['documentTab']['body']['content'])
        and legacy single-tab format (doc['body']['content']).

        Args:
            doc: Full Google Docs document resource dict (from documents.get())

        Returns:
            List of DocsParagraphNode in document order.

        Raises:
            KeyError: If the document has neither 'tabs' nor 'body' key.
        """
        # Determine body content — handle tabs-based and legacy structure
        if "tabs" in doc and doc["tabs"]:
            tab_doc = doc["tabs"][0].get("documentTab", doc)
            body = tab_doc.get("body", {})
            lists = tab_doc.get("lists", {})
        elif "body" in doc:
            body = doc["body"]
            lists = doc.get("lists", {})
        else:
            raise KeyError("Document has neither 'tabs' nor 'body' key")

        content = body.get("content", [])
        nodes: List[Union[DocsParagraphNode, DocsTableNode]] = []

        for position, element in enumerate(content):
            if "paragraph" in element:
                node = self._parse_paragraph(element, lists)
                if node is None:
                    continue
                node.precedes_structural_element = self._precedes_structural_element(
                    content, position
                )
                nodes.append(node)
            elif "table" in element:
                nodes.append(self._parse_table(element))
            # sectionBreak, tableOfContents are silently skipped as nodes — a
            # preceding paragraph still records them via
            # precedes_structural_element so the delete path can see them.

        self._resolve_heading_links(nodes)
        return nodes

    @staticmethod
    def _resolve_heading_links(nodes: List[Union[DocsParagraphNode, DocsTableNode]]) -> None:
        """Rewrite `headingId` links into markdown anchors, in place.

        Runs after the whole body is parsed because a heading link can point
        backwards or forwards and the slug of any heading depends on every
        heading before it (duplicate suffixes).

        Without this the read direction loses the link outright — a `headingId`
        link carries no `url`, so _parse_paragraph sees no link at all and
        `pull` renders plain text. The anchor then disappears from the markdown
        file while the Doc keeps it, and nothing reports the divergence.

        _parse_paragraph has already written "#" + headingId into span.link, so
        this only upgrades an id to the friendlier slug. An id with no heading
        of its own in this body (a link into a deleted heading, or into another
        tab) keeps the bare id rather than being dropped:
        heading_anchors.resolve_anchor tries ids before slugs, so the write
        direction takes it straight back.
        """
        id_to_slug = heading_id_to_slug(nodes)
        if not id_to_slug:
            return
        for node in nodes:
            if not isinstance(node, DocsParagraphNode):
                continue
            for span in node.spans:
                if span.link is None or not is_anchor(span.link):
                    continue
                slug = id_to_slug.get(anchor_target(span.link))
                if slug is not None:
                    span.link = "#" + slug

    @staticmethod
    def _precedes_structural_element(content: List[dict], position: int) -> bool:
        """Whether content[position] is directly followed by an undeletable boundary."""
        following = content[position + 1] if position + 1 < len(content) else None
        if not isinstance(following, dict):
            return False
        return any(key in following for key in UNDELETABLE_BOUNDARY_KEYS)

    def _parse_table(self, element: dict) -> DocsTableNode:
        """Parse a structural element that contains a table into a DocsTableNode."""
        table = element["table"]
        rows: List[List[str]] = []
        for table_row in table.get("tableRows", []):
            cells: List[str] = []
            for cell in table_row.get("tableCells", []):
                parts: List[str] = []
                for cell_element in cell.get("content", []):
                    paragraph = cell_element.get("paragraph")
                    if paragraph is None:
                        continue
                    for pe in paragraph.get("elements", []):
                        text_run = pe.get("textRun")
                        if text_run is not None:
                            parts.append(text_run.get("content", ""))
                            continue
                        person = pe.get("person")
                        if person is not None:
                            parts.append(_person_display_text(person))
                cells.append("".join(parts).strip())
            rows.append(cells)
        return DocsTableNode(
            rows=rows,
            start_index=element.get("startIndex", 0),
            end_index=element.get("endIndex", 0),
        )

    def _parse_paragraph(
        self, element: dict, lists: Optional[dict] = None
    ) -> Optional[DocsParagraphNode]:
        """Parse a structural element that contains a paragraph."""
        paragraph = element["paragraph"]
        paragraph_style = paragraph.get("paragraphStyle", {})
        style = paragraph_style.get("namedStyleType", "NORMAL_TEXT")

        start_index = element.get("startIndex", 0)
        end_index = element.get("endIndex", 0)

        # Extract text from all TextRuns, collecting spans
        spans: List[TextSpan] = []
        text_parts: List[str] = []

        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run is None:
                person = pe.get("person")
                if person is not None:
                    name = _person_display_text(person)
                    if name:
                        text_parts.append(name)
                        spans.append(TextSpan(text=name))
                continue
            run_content = text_run.get("content", "")
            text_style = text_run.get("textStyle", {})
            bold = text_style.get("bold", False)
            italic = text_style.get("italic", False)
            link = self._parse_link(text_style.get("link"))
            # Monospace: check weightedFontFamily.fontFamily for "Courier New" or similar
            font_family = text_style.get("weightedFontFamily", {}).get("fontFamily", "")
            monospace = "Courier" in font_family or "mono" in font_family.lower()

            text_parts.append(run_content)
            spans.append(TextSpan(
                text=run_content,
                bold=bool(bold),
                italic=bool(italic),
                link=link,
                monospace=monospace,
            ))

        raw_text = "".join(text_parts)
        # Strip trailing newline (each paragraph ends with \n in the Docs model)
        text = raw_text.rstrip("\n")
        spans = self._trim_spans_to_text(spans, len(text))

        # Check for bullet / list item
        bullet = paragraph.get("bullet")
        is_list_item = bullet is not None
        nesting_level = bullet.get("nestingLevel", 0) if bullet else 0
        is_native_checkbox = self._resolve_is_native_checkbox(bullet, lists or {})

        return DocsParagraphNode(
            style=style,
            text=text,
            is_list_item=is_list_item,
            nesting_level=nesting_level,
            start_index=start_index,
            end_index=end_index,
            spans=spans,
            is_native_checkbox=is_native_checkbox,
            heading_id=paragraph_style.get("headingId"),
        )

    @staticmethod
    def _trim_spans_to_text(spans: List[TextSpan], keep: int) -> List[TextSpan]:
        """Drop the paragraph-terminating newline from the spans, as .text already does.

        Every Docs paragraph ends with "\\n", and it arrives inside the *last
        textRun's* content — so it lands in that run's span while `.text`
        rstrips it. Two things break on the resulting disagreement, both when
        the run carrying the newline is also the run carrying a mark:

        * `pull` renders the newline *inside* the markdown link, emitting
          ``[it\\n](https://…)`` — which re-parses as a literal
          ``](https://…)`` line rather than a link.
        * The spans then total one code unit more than the paragraph can hold,
          so DocsRequestBuilder._spans_overflow reports the paragraph and pass 2
          drops its styling.

        Trimming to `.text`'s length restores the invariant the rest of the
        pipeline already assumes: the spans concatenate to exactly `.text`.

        Empty spans are dropped on the way out. That is a separate concern from
        the newline — a run whose whole content *is* the newline is removed by
        the trim itself — and covers an empty textRun, which the API can send
        and which the loop above would leave in place: a span with no text
        styles nothing and renders as stray marks (``****``).
        """
        trimmed = list(spans)
        total = sum(len(span.text) for span in trimmed)
        while trimmed and total > keep:
            excess = total - keep
            last = trimmed[-1]
            if len(last.text) <= excess:
                trimmed.pop()
                total -= len(last.text)
            else:
                trimmed[-1] = replace(last, text=last.text[: len(last.text) - excess])
                total = keep
        return [span for span in trimmed if span.text]

    @staticmethod
    def _parse_link(link: Optional[dict]) -> Optional[str]:
        """Flatten a Docs `Link` union into the markdown href it round-trips as.

        Reading only `url` makes a heading link indistinguishable from no link at
        all, so `pull` silently drops the cross-reference.

        `Link` has **six** members, and which one a heading link arrives as
        depends on a request flag rather than on the document: with
        `includeTabsContent=true` it is `heading: {id, tabId}`, and only without
        it is the flat `headingId` returned. Google documents the flat forms as
        legacy. `client.get_document` defaults the flag to True, so the object
        form is the one this parser actually sees — handling only `headingId`
        parses a live tab-scoped document to zero links:

            $ # same doc, one flag apart
            $ ...includeTabsContent=false -> 5 linked spans
            $ ...includeTabsContent=true  -> 0 linked spans

        Both are accepted here so the parser does not depend on how it was
        fetched. Either becomes "#" + the id; _resolve_heading_links then
        upgrades it to the heading's slug once the whole body is known.

        `bookmark`/`bookmarkId` and `tabId` are still unhandled (out of scope)
        and return None, which is the pre-existing behaviour. They are named
        here so the next reader knows the union is closed and what is left.
        """
        if not isinstance(link, dict):
            return None
        url = link.get("url")
        if isinstance(url, str) and url:
            return url
        heading_id = link.get("headingId")
        if isinstance(heading_id, str) and heading_id:
            return "#" + heading_id
        heading = link.get("heading")
        if isinstance(heading, dict):
            heading_id = heading.get("id")
            if isinstance(heading_id, str) and heading_id:
                return "#" + heading_id
        return None

    def _resolve_is_native_checkbox(self, bullet: Optional[dict], lists: dict) -> bool:
        """Resolve whether a bullet paragraph is a native BULLET_CHECKBOX glyph.

        Looks up bullet.listId -> lists[listId].listProperties.nestingLevels[n]
        .glyphType and returns True only when it equals GLYPH_TYPE_UNSPECIFIED
        (the confirmed, if counter-intuitive, signature Google Docs uses for a
        checkbox bullet — see ADR-001's Verification Evidence). Defensively
        returns False (never raises) on any missing/malformed piece — e.g. a
        bullet paragraph with no listId, or a lists map that doesn't contain
        the referenced list.
        """
        if not bullet:
            return False
        list_id = bullet.get("listId")
        if not list_id:
            return False
        nesting_level = bullet.get("nestingLevel", 0)

        list_entry = lists.get(list_id)
        if not isinstance(list_entry, dict):
            return False
        list_properties = list_entry.get("listProperties")
        if not isinstance(list_properties, dict):
            return False
        nesting_levels = list_properties.get("nestingLevels")
        if not isinstance(nesting_levels, list):
            return False
        if not isinstance(nesting_level, int) or nesting_level < 0 or nesting_level >= len(nesting_levels):
            return False
        level_props = nesting_levels[nesting_level]
        if not isinstance(level_props, dict):
            return False

        return level_props.get("glyphType") == "GLYPH_TYPE_UNSPECIFIED"
