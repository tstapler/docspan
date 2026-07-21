"""Parse a Google Docs JSON document into a list of DocsParagraphNode objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class TextSpan:
    text: str
    bold: bool = False
    italic: bool = False
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

        for element in content:
            if "paragraph" in element:
                node = self._parse_paragraph(element, lists)
                if node is not None:
                    nodes.append(node)
            elif "table" in element:
                nodes.append(self._parse_table(element))
            # sectionBreak, tableOfContents are silently skipped

        return nodes

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
                continue
            run_content = text_run.get("content", "")
            text_style = text_run.get("textStyle", {})
            bold = text_style.get("bold", False)
            italic = text_style.get("italic", False)
            link = text_style.get("link", {}).get("url") if text_style.get("link") else None
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
        )

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
