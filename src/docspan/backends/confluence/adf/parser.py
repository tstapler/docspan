"""
ADF (Atlassian Document Format) parser and analyzer.

This module provides functionality to parse, validate, and analyze ADF documents
extracted from Confluence pages.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class AdfNodeType(Enum):
    """ADF node types."""

    DOC = "doc"
    PARAGRAPH = "paragraph"
    TEXT = "text"
    HEADING = "heading"
    BULLET_LIST = "bulletList"
    ORDERED_LIST = "orderedList"
    LIST_ITEM = "listItem"
    CODE_BLOCK = "codeBlock"
    BLOCK_QUOTE = "blockquote"
    PANEL = "panel"
    TABLE = "table"
    TABLE_ROW = "tableRow"
    TABLE_CELL = "tableCell"
    TABLE_HEADER = "tableHeader"
    MEDIA_SINGLE = "mediaSingle"
    MEDIA = "media"
    MENTION = "mention"
    EMOJI = "emoji"
    HARD_BREAK = "hardBreak"
    RULE = "rule"
    INLINE_CARD = "inlineCard"
    BLOCK_CARD = "blockCard"
    EXPAND = "expand"
    DECISION_LIST = "decisionList"
    DECISION_ITEM = "decisionItem"
    TASK_LIST = "taskList"
    TASK_ITEM = "taskItem"
    EXTENSION = "extension"
    BODIED_EXTENSION = "bodiedExtension"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, value: str) -> "AdfNodeType":
        """Convert string to AdfNodeType."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class MarkType(Enum):
    """ADF mark types."""

    STRONG = "strong"
    EM = "em"
    CODE = "code"
    STRIKE = "strike"
    UNDERLINE = "underline"
    SUBSUP = "subsup"
    LINK = "link"
    TEXT_COLOR = "textColor"
    BACKGROUND_COLOR = "backgroundColor"
    ALIGNMENT = "alignment"
    INDENTATION = "indentation"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, value: str) -> "MarkType":
        """Convert string to MarkType."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass
class AdfMark:
    """Represents an ADF mark (text formatting)."""

    type: MarkType
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdfNode:
    """Represents an ADF node in the document tree."""

    type: AdfNodeType
    attrs: Dict[str, Any] = field(default_factory=dict)
    content: List["AdfNode"] = field(default_factory=list)
    marks: List[AdfMark] = field(default_factory=list)
    text: Optional[str] = None

    def get_text_content(self) -> str:
        """Extract all text content from this node and its children."""
        if self.text:
            return self.text

        text_parts = []
        for child in self.content:
            text_parts.append(child.get_text_content())

        return "".join(text_parts)

    def find_nodes_by_type(self, node_type: AdfNodeType) -> List["AdfNode"]:
        """Find all nodes of a specific type in the tree."""
        results = []

        if self.type == node_type:
            results.append(self)

        for child in self.content:
            results.extend(child.find_nodes_by_type(node_type))

        return results

    def count_nodes_by_type(self) -> Dict[AdfNodeType, int]:
        """Count nodes by type in the tree."""
        counts: Dict[AdfNodeType, int] = {}

        def count_recursive(node: AdfNode) -> None:
            counts[node.type] = counts.get(node.type, 0) + 1
            for child in node.content:
                count_recursive(child)

        count_recursive(self)
        return counts

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary representation."""
        result: Dict[str, Any] = {"type": self.type.value}

        if self.attrs:
            result["attrs"] = self.attrs

        if self.content:
            result["content"] = [child.to_dict() for child in self.content]

        if self.marks:
            result["marks"] = [
                {"type": mark.type.value, "attrs": mark.attrs} for mark in self.marks
            ]

        if self.text is not None:
            result["text"] = self.text

        return result


@dataclass
class AdfDocument:
    """Represents a complete ADF document."""

    version: int
    root: AdfNode

    def get_all_text(self) -> str:
        """Get all text content from the document."""
        return self.root.get_text_content()

    def get_node_statistics(self) -> Dict[str, int]:
        """Get statistics about node types in the document."""
        counts = self.root.count_nodes_by_type()
        return {node_type.value: count for node_type, count in counts.items()}

    def find_links(self) -> List[Dict[str, Any]]:
        """Find all links in the document."""
        links = []

        def find_link_marks(node: AdfNode) -> None:
            for mark in node.marks:
                if mark.type == MarkType.LINK:
                    links.append(
                        {
                            "href": mark.attrs.get("href"),
                            "text": node.get_text_content(),
                            "title": mark.attrs.get("title"),
                        }
                    )

            for child in node.content:
                find_link_marks(child)

        find_link_marks(self.root)
        return links

    def find_mentions(self) -> List[Dict[str, Any]]:
        """Find all user mentions in the document."""
        mention_nodes = self.root.find_nodes_by_type(AdfNodeType.MENTION)
        return [
            {
                "id": node.attrs.get("id"),
                "text": node.attrs.get("text"),
                "access_level": node.attrs.get("accessLevel"),
            }
            for node in mention_nodes
        ]

    def find_media(self) -> List[Dict[str, Any]]:
        """Find all media (images, attachments) in the document."""
        media_nodes = self.root.find_nodes_by_type(AdfNodeType.MEDIA)
        return [
            {
                "id": node.attrs.get("id"),
                "type": node.attrs.get("type"),
                "collection": node.attrs.get("collection"),
                "alt": node.attrs.get("alt"),
                "width": node.attrs.get("width"),
                "height": node.attrs.get("height"),
            }
            for node in media_nodes
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Convert document to dictionary representation."""
        return {
            "version": self.version,
            "type": "doc",
            "content": [child.to_dict() for child in self.root.content],
        }


class AdfParser:
    """
    Parser for ADF (Atlassian Document Format) documents.

    This class provides methods to parse ADF JSON documents into a structured
    tree representation for analysis and comparison.
    """

    def parse(self, adf_data: Dict[str, Any]) -> AdfDocument:
        """
        Parse ADF JSON data into an AdfDocument.

        Args:
            adf_data: ADF document as a dictionary

        Returns:
            Parsed ADF document

        Raises:
            ValueError: If the ADF data is invalid
        """
        if not isinstance(adf_data, dict):
            raise ValueError("ADF data must be a dictionary")

        version = adf_data.get("version", 1)
        doc_type = adf_data.get("type", "doc")

        if doc_type != "doc":
            raise ValueError(f"Expected document type 'doc', got '{doc_type}'")

        root_content = adf_data.get("content", [])
        root = AdfNode(
            type=AdfNodeType.DOC,
            content=[self._parse_node(node) for node in root_content],
        )

        return AdfDocument(version=version, root=root)

    def parse_file(self, file_path: Path) -> AdfDocument:
        """
        Parse an ADF JSON file.

        Args:
            file_path: Path to the ADF JSON file

        Returns:
            Parsed ADF document
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            adf_data = json.load(f)

        return self.parse(adf_data)

    def _parse_node(self, node_data: Dict[str, Any]) -> AdfNode:
        """Parse a single ADF node."""
        if not isinstance(node_data, dict):
            raise ValueError(f"Node data must be a dictionary, got {type(node_data)}")

        node_type = AdfNodeType.from_string(node_data.get("type", "unknown"))
        attrs = node_data.get("attrs", {})
        text = node_data.get("text")

        # Parse marks
        marks = []
        for mark_data in node_data.get("marks", []):
            mark_type = MarkType.from_string(mark_data.get("type", "unknown"))
            mark_attrs = mark_data.get("attrs", {})
            marks.append(AdfMark(type=mark_type, attrs=mark_attrs))

        # Parse content
        content = []
        for child_data in node_data.get("content", []):
            content.append(self._parse_node(child_data))

        return AdfNode(
            type=node_type,
            attrs=attrs,
            content=content,
            marks=marks,
            text=text,
        )


@dataclass
class AdfAnalysisReport:
    """Report from analyzing an ADF document."""

    node_count: int
    node_statistics: Dict[str, int]
    total_text_length: int
    link_count: int
    links: List[Dict[str, Any]]
    mention_count: int
    mentions: List[Dict[str, Any]]
    media_count: int
    media: List[Dict[str, Any]]
    unknown_node_types: Set[str] = field(default_factory=set)
    unknown_mark_types: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "node_count": self.node_count,
            "node_statistics": self.node_statistics,
            "total_text_length": self.total_text_length,
            "link_count": self.link_count,
            "links": self.links,
            "mention_count": self.mention_count,
            "mentions": self.mentions,
            "media_count": self.media_count,
            "media": self.media,
            "unknown_node_types": list(self.unknown_node_types),
            "unknown_mark_types": list(self.unknown_mark_types),
        }


class AdfAnalyzer:
    """
    Analyzer for ADF documents.

    Provides methods to analyze and report on ADF document structure and content.
    """

    def analyze(self, document: AdfDocument) -> AdfAnalysisReport:
        """
        Analyze an ADF document and generate a report.

        Args:
            document: ADF document to analyze

        Returns:
            Analysis report
        """
        node_statistics = document.get_node_statistics()
        node_count = sum(node_statistics.values())
        total_text = document.get_all_text()
        links = document.find_links()
        mentions = document.find_mentions()
        media = document.find_media()

        # Find unknown types
        unknown_nodes = set()
        unknown_marks = set()

        def find_unknowns(node: AdfNode) -> None:
            if node.type == AdfNodeType.UNKNOWN:
                # Try to get original type string from dict representation
                unknown_nodes.add(str(node.attrs.get("__original_type", "unknown")))

            for mark in node.marks:
                if mark.type == MarkType.UNKNOWN:
                    unknown_marks.add(str(mark.attrs.get("__original_type", "unknown")))

            for child in node.content:
                find_unknowns(child)

        find_unknowns(document.root)

        return AdfAnalysisReport(
            node_count=node_count,
            node_statistics=node_statistics,
            total_text_length=len(total_text),
            link_count=len(links),
            links=links,
            mention_count=len(mentions),
            mentions=mentions,
            media_count=len(media),
            media=media,
            unknown_node_types=unknown_nodes,
            unknown_mark_types=unknown_marks,
        )
