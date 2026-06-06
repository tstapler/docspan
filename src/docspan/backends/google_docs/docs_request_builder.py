"""Build Google Docs batchUpdate request lists from structural AST diffs."""
from __future__ import annotations

import difflib
from typing import List, Tuple

from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode, TextSpan


def _utf16_len(text: str) -> int:
    """Return the number of UTF-16 code units in text (surrogate pairs count as 2)."""
    return len(text.encode("utf-16-le")) // 2


class DocsRequestBuilder:
    """Diff two paragraph ASTs and produce minimal Google Docs batchUpdate requests."""

    def _text_key(self, node: DocsParagraphNode) -> Tuple:
        """Key used by SequenceMatcher for comparing nodes."""
        return (node.style, node.text, node.is_list_item)

    def build(
        self,
        current: List[DocsParagraphNode],
        target: List[DocsParagraphNode],
        doc_end_index: int,
    ) -> List[dict]:
        """
        Build a minimal list of batchUpdate request dicts.

        Args:
            current: Paragraph nodes parsed from the live Google Doc.
            target:  Paragraph nodes parsed from the local markdown file.
            doc_end_index: endIndex of the last body element (used to protect
                           the terminal newline that Docs API requires).

        Returns:
            List of request dicts sorted by descending startIndex (write-backwards).
        """
        current_keys = [self._text_key(n) for n in current]
        target_keys = [self._text_key(n) for n in target]

        matcher = difflib.SequenceMatcher(None, current_keys, target_keys, autojunk=False)
        opcodes = matcher.get_opcodes()

        all_requests: List[dict] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                # Nodes match — only emit style update if style differs
                for ci, ti in zip(range(i1, i2), range(j1, j2)):
                    all_requests.extend(
                        self._make_style_update_requests(current[ci], target[ti])
                    )

            elif tag == "delete":
                # Remove current nodes i1..i2 with no replacement
                all_requests.extend(
                    self._make_delete_requests(current[i1:i2], doc_end_index)
                )

            elif tag == "insert":
                # Insert target nodes j1..j2 — determine insertion point
                if i1 > 0:
                    insert_at = current[i1 - 1].end_index - 1
                else:
                    insert_at = 1  # start of document body
                all_requests.extend(
                    self._make_insert_requests(target[j1:j2], insert_at)
                )

            elif tag == "replace":
                # Delete current nodes, then insert target nodes at same position
                delete_start = current[i1].start_index
                all_requests.extend(
                    self._make_delete_requests(current[i1:i2], doc_end_index)
                )
                all_requests.extend(
                    self._make_insert_requests(target[j1:j2], delete_start)
                )

        # Sort descending by startIndex (write-backwards strategy)
        all_requests.sort(
            key=lambda r: self._extract_start_index(r),
            reverse=True,
        )
        return all_requests

    # ──────────────────────────────────────────────
    # Request factories
    # ──────────────────────────────────────────────

    def _make_delete_requests(
        self, nodes: List[DocsParagraphNode], doc_end_index: int
    ) -> List[dict]:
        requests = []
        for node in nodes:
            start = node.start_index
            end = node.end_index
            # Never delete the terminal newline
            if end >= doc_end_index:
                end = doc_end_index - 1
            if start >= end:
                continue
            requests.append({
                "deleteContentRange": {
                    "range": {
                        "startIndex": start,
                        "endIndex": end,
                    }
                }
            })
        return requests

    def _make_insert_requests(
        self, nodes: List[DocsParagraphNode], insert_at_index: int
    ) -> List[dict]:
        """
        Emit insertText + updateParagraphStyle (+ createParagraphBullets) per node.

        All inserts go at the same index; because we sort descending later the
        ordering inside a single insert group is preserved.
        """
        requests = []
        # Insert in reverse order so that each insert lands at the right place
        # when the final descending-sort is applied.
        for node in reversed(nodes):
            text = node.text + "\n"
            requests.append({
                "insertText": {
                    "location": {"index": insert_at_index},
                    "text": text,
                }
            })
            text_len = _utf16_len(text)
            end_index = insert_at_index + text_len
            paragraph_range = {
                "startIndex": insert_at_index,
                "endIndex": end_index,
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

    def _make_style_update_requests(
        self, current_node: DocsParagraphNode, target_node: DocsParagraphNode
    ) -> List[dict]:
        """Emit updateParagraphStyle when style differs (text is equal)."""
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
        """
        Emit updateTextStyle with specific FieldMask (never '*').

        Args:
            text: The text content (for context; not used in request).
            style_attrs: Dict with keys like 'bold', 'italic', 'link', 'monospace'.
            range_dict: {'startIndex': int, 'endIndex': int}
        """
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
