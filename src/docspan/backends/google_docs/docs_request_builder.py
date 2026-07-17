"""Build Google Docs batchUpdate request lists from structural AST diffs."""
from __future__ import annotations

import difflib
from typing import List, Tuple, Union

from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsStructureParser,
    DocsTableNode,
)

Node = Union[DocsParagraphNode, DocsTableNode]


def _utf16_len(text: str) -> int:
    """Return the number of UTF-16 code units in text (surrogate pairs count as 2)."""
    return len(text.encode("utf-16-le")) // 2


def _body_content(doc: dict) -> list:
    """Return the body content list, handling tabs-based and legacy structures."""
    if "tabs" in doc and doc["tabs"]:
        body = doc["tabs"][0].get("documentTab", doc).get("body", {})
    elif "body" in doc:
        body = doc["body"]
    else:
        return []
    return body.get("content", [])


class DocsRequestBuilder:
    """Diff two node ASTs and produce minimal Google Docs batchUpdate requests."""

    def _node_key(self, node: Node) -> Tuple:
        """Key used by SequenceMatcher for comparing nodes."""
        if isinstance(node, DocsTableNode):
            return ("__table__", tuple(tuple(row) for row in node.rows))
        return ("__para__", node.style, node.text, node.is_list_item)

    def build(
        self,
        current: List[Node],
        target: List[Node],
        doc_end_index: int,
    ) -> List[dict]:
        """
        Build a minimal list of batchUpdate request dicts (pass 1).

        Tables are inserted empty here; call build_table_fill_requests() after re-fetching
        the document to populate their cells (pass 2).

        Args:
            current: Nodes parsed from the live Google Doc.
            target:  Nodes parsed from the local markdown file.
            doc_end_index: endIndex of the last body element (used to protect the terminal
                newline that Docs API requires).

        Returns:
            List of request dicts sorted by descending startIndex (write-backwards).
        """
        current_keys = [self._node_key(n) for n in current]
        target_keys = [self._node_key(n) for n in target]

        matcher = difflib.SequenceMatcher(None, current_keys, target_keys, autojunk=False)
        opcodes = matcher.get_opcodes()

        all_requests: List[dict] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for ci, ti in zip(range(i1, i2), range(j1, j2)):
                    all_requests.extend(
                        self._make_style_update_requests(current[ci], target[ti])
                    )

            elif tag == "delete":
                all_requests.extend(
                    self._make_delete_requests(current[i1:i2], doc_end_index)
                )

            elif tag == "insert":
                if i1 > 0:
                    insert_at = current[i1 - 1].end_index - 1
                else:
                    insert_at = 1  # start of document body
                all_requests.extend(
                    self._make_insert_requests(target[j1:j2], insert_at)
                )

            elif tag == "replace":
                delete_start = current[i1].start_index
                all_requests.extend(
                    self._make_delete_requests(current[i1:i2], doc_end_index)
                )
                all_requests.extend(
                    self._make_insert_requests(target[j1:j2], delete_start)
                )

        all_requests.sort(key=lambda r: self._extract_start_index(r), reverse=True)
        return all_requests

    # ──────────────────────────────────────────────
    # Pass 2 — fill table cells from a re-fetched doc
    # ──────────────────────────────────────────────

    def build_table_fill_requests(self, doc: dict, target: List[Node]) -> List[dict]:
        """
        Emit insertText requests to fill empty tables created by a prior push (pass 1).

        Matches the empty tables in the re-fetched document (in document order) to the
        DocsTableNodes in ``target`` (in order), reading real cell indices from ``doc`` so
        no index prediction is required.
        """
        target_tables = [n for n in target if isinstance(n, DocsTableNode)]
        if not target_tables:
            return []

        inserts: List[Tuple[int, str]] = []
        ti = 0
        for element in _body_content(doc):
            table = element.get("table")
            if table is None:
                continue
            if not self._table_is_empty(table):
                continue  # already populated (or a pre-existing content table)
            if ti >= len(target_tables):
                break
            inserts.extend(self._cell_inserts(table, target_tables[ti]))
            ti += 1

        # Insert highest index first so earlier inserts don't shift later cell indices.
        inserts.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"insertText": {"location": {"index": idx}, "text": text}}
            for idx, text in inserts
            if text
        ]

    def build_span_style_requests(self, doc: dict, target: List[Node]) -> List[dict]:
        """
        Emit updateTextStyle requests for inline styling (links/bold/italic/monospace).

        Runs against the re-fetched document so ranges use real post-insert indices. Aligns
        each styled target paragraph to the matching paragraph in ``doc`` by text (in order).
        """
        if not any(isinstance(n, DocsParagraphNode) and n.spans for n in target):
            return []

        current = DocsStructureParser().parse(doc)
        requests: List[dict] = []
        j = 0
        for tnode in target:
            if isinstance(tnode, DocsTableNode):
                while j < len(current) and not isinstance(current[j], DocsTableNode):
                    j += 1
                j += 1
                continue
            # Find the next current paragraph with matching text.
            k = j
            while k < len(current):
                cnode = current[k]
                if isinstance(cnode, DocsParagraphNode) and cnode.text == tnode.text:
                    break
                k += 1
            if k >= len(current):
                continue
            if tnode.spans:
                requests.extend(self._span_style_requests(tnode, current[k].start_index))
            j = k + 1

        return requests

    def build_second_pass_requests(self, doc: dict, target: List[Node]) -> List[dict]:
        """
        Combined pass-2 requests: table cell fills + inline text styling.

        Both read indices from the re-fetched ``doc``; the combined list is applied
        highest-index-first so cell inserts don't invalidate other ranges.
        """
        requests = self.build_table_fill_requests(doc, target)
        requests += self.build_span_style_requests(doc, target)
        requests.sort(key=lambda r: self._extract_start_index(r), reverse=True)
        return requests

    @staticmethod
    def _table_is_empty(table: dict) -> bool:
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                for element in cell.get("content", []):
                    paragraph = element.get("paragraph")
                    if paragraph is None:
                        continue
                    for pe in paragraph.get("elements", []):
                        run = pe.get("textRun")
                        if run and run.get("content", "").strip():
                            return False
        return True

    @staticmethod
    def _cell_inserts(table: dict, node: DocsTableNode) -> List[Tuple[int, str]]:
        """Pair each cell's first-content startIndex with the target cell text."""
        pairs: List[Tuple[int, str]] = []
        rows = table.get("tableRows", [])
        for r, row in enumerate(rows):
            cells = row.get("tableCells", [])
            for c, cell in enumerate(cells):
                content = cell.get("content", [])
                if not content:
                    continue
                idx = content[0].get("startIndex")
                if idx is None:
                    continue
                text = ""
                if r < len(node.rows) and c < len(node.rows[r]):
                    text = node.rows[r][c]
                if text:
                    pairs.append((idx, text))
        return pairs

    # ──────────────────────────────────────────────
    # Request factories
    # ──────────────────────────────────────────────

    def _make_delete_requests(self, nodes: List[Node], doc_end_index: int) -> List[dict]:
        requests = []
        for node in nodes:
            start = node.start_index
            end = node.end_index
            if end >= doc_end_index:
                end = doc_end_index - 1
            if start >= end:
                continue
            requests.append({
                "deleteContentRange": {
                    "range": {"startIndex": start, "endIndex": end}
                }
            })
        return requests

    def _make_insert_requests(self, nodes: List[Node], insert_at_index: int) -> List[dict]:
        """
        Emit insert requests per node.

        Paragraphs: insertText + updateParagraphStyle (+ bullets). Inline text styling
        (links/bold/italic/monospace) is applied in pass 2 (build_span_style_requests),
        against real post-insert indices.
        Tables: insertTable (empty; filled in pass 2).

        All inserts share ``insert_at_index``; because the caller/build() sorts descending
        later, ordering inside a single insert group is preserved.
        """
        requests: List[dict] = []
        for node in reversed(nodes):
            if isinstance(node, DocsTableNode):
                requests.append({
                    "insertTable": {
                        "location": {"index": insert_at_index},
                        "rows": max(node.num_rows, 1),
                        "columns": max(node.num_cols, 1),
                    }
                })
                continue

            text = node.text + "\n"
            requests.append({
                "insertText": {"location": {"index": insert_at_index}, "text": text}
            })
            text_len = _utf16_len(text)
            paragraph_range = {
                "startIndex": insert_at_index,
                "endIndex": insert_at_index + text_len,
            }
            requests.append({
                "updateParagraphStyle": {
                    "range": paragraph_range,
                    "paragraphStyle": {"namedStyleType": node.style},
                    "fields": "namedStyleType",
                }
            })
            if node.is_list_item:
                requests.append({
                    "createParagraphBullets": {
                        "range": paragraph_range,
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                })
        return requests

    def _span_style_requests(self, node: DocsParagraphNode, insert_at_index: int) -> List[dict]:
        """Emit updateTextStyle for each styled span in a paragraph starting at insert_at_index."""
        requests: List[dict] = []
        offset = insert_at_index
        for span in node.spans:
            span_len = _utf16_len(span.text)
            if span_len == 0:
                continue
            attrs: dict = {}
            if span.bold:
                attrs["bold"] = True
            if span.italic:
                attrs["italic"] = True
            if span.link:
                attrs["link"] = span.link
            if span.monospace:
                attrs["monospace"] = True
            if attrs:
                requests.extend(self._make_text_style_requests(
                    span.text, attrs,
                    {"startIndex": offset, "endIndex": offset + span_len},
                ))
            offset += span_len
        return requests

    def _make_style_update_requests(self, current_node: Node, target_node: Node) -> List[dict]:
        """Emit updateParagraphStyle when a paragraph's style differs (text is equal)."""
        if isinstance(current_node, DocsTableNode) or isinstance(target_node, DocsTableNode):
            return []
        if current_node.style == target_node.style:
            return []
        return [{
            "updateParagraphStyle": {
                "range": {
                    "startIndex": current_node.start_index,
                    "endIndex": current_node.end_index,
                },
                "paragraphStyle": {"namedStyleType": target_node.style},
                "fields": "namedStyleType",
            }
        }]

    def _make_text_style_requests(
        self, text: str, style_attrs: dict, range_dict: dict
    ) -> List[dict]:
        """Emit updateTextStyle with a specific FieldMask (never '*')."""
        fields = []
        text_style: dict = {}

        if "bold" in style_attrs:
            fields.append("bold")
            text_style["bold"] = style_attrs["bold"]
        if "italic" in style_attrs:
            fields.append("italic")
            text_style["italic"] = style_attrs["italic"]
        if "link" in style_attrs:
            fields.append("link")
            text_style["link"] = {"url": style_attrs["link"]}
        if style_attrs.get("monospace"):
            fields.append("weightedFontFamily")
            text_style["weightedFontFamily"] = {"fontFamily": "Courier New", "weight": 400}

        if not fields:
            return []

        return [{
            "updateTextStyle": {
                "range": range_dict,
                "textStyle": text_style,
                "fields": ",".join(fields),
            }
        }]

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_start_index(request: dict) -> int:
        """Extract the primary startIndex from any request dict for sorting."""
        for key in (
            "deleteContentRange",
            "insertText",
            "insertTable",
            "updateParagraphStyle",
            "createParagraphBullets",
            "updateTextStyle",
        ):
            if key in request:
                inner = request[key]
                if "range" in inner:
                    return inner["range"].get("startIndex", 0)
                if "location" in inner:
                    return inner["location"].get("index", 0)
        return 0
