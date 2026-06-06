"""Parse a Google Docs JSON document into a list of DocsParagraphNode objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


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


class DocsStructureParser:
    """Parse a Google Docs document dict into a list of DocsParagraphNode."""

    def parse(self, doc: dict) -> List[DocsParagraphNode]:
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
            body = doc["tabs"][0].get("documentTab", doc).get("body", {})
        elif "body" in doc:
            body = doc["body"]
        else:
            raise KeyError("Document has neither 'tabs' nor 'body' key")

        content = body.get("content", [])
        nodes: List[DocsParagraphNode] = []

        for element in content:
            if "paragraph" in element:
                node = self._parse_paragraph(element)
                if node is not None:
                    nodes.append(node)
            # table, sectionBreak, tableOfContents are silently skipped

        return nodes

    def _parse_paragraph(self, element: dict) -> Optional[DocsParagraphNode]:
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

        return DocsParagraphNode(
            style=style,
            text=text,
            is_list_item=is_list_item,
            nesting_level=nesting_level,
            start_index=start_index,
            end_index=end_index,
            spans=spans,
        )
