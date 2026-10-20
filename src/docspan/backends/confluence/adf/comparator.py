"""
Markdown-to-ADF comparison tool.

This module provides functionality to compare markdown input with generated ADF output
to identify discrepancies and issues in the conversion process.
"""

import difflib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from docspan.backends.confluence.adf.converter import AdfConverter
from docspan.backends.confluence.adf.parser import AdfDocument, AdfNodeType, AdfParser
from docspan.backends.confluence.markdown.ast import MarkdownNode
from docspan.backends.confluence.markdown.parser import MarkdownParser


class DifferenceType(Enum):
    """Types of differences that can be found."""

    MISSING_CONTENT = "missing_content"
    EXTRA_CONTENT = "extra_content"
    STRUCTURE_MISMATCH = "structure_mismatch"
    TEXT_MISMATCH = "text_mismatch"
    ATTRIBUTE_MISMATCH = "attribute_mismatch"
    LINK_MISMATCH = "link_mismatch"
    FORMAT_MISMATCH = "format_mismatch"


@dataclass
class Difference:
    """Represents a difference between markdown and ADF."""

    type: DifferenceType
    location: str
    expected: Any
    actual: Any
    severity: str = "medium"  # low, medium, high
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "location": self.location,
            "expected": str(self.expected),
            "actual": str(self.actual),
            "severity": self.severity,
            "description": self.description,
        }


@dataclass
class ComparisonReport:
    """Report from comparing markdown and ADF."""

    markdown_file: Optional[Path] = None
    total_differences: int = 0
    differences: List[Difference] = field(default_factory=list)
    markdown_stats: Dict[str, Any] = field(default_factory=dict)
    adf_stats: Dict[str, Any] = field(default_factory=dict)
    success: bool = True

    def add_difference(
        self,
        diff_type: DifferenceType,
        location: str,
        expected: Any,
        actual: Any,
        severity: str = "medium",
        description: Optional[str] = None,
    ) -> None:
        """Add a difference to the report."""
        diff = Difference(
            type=diff_type,
            location=location,
            expected=expected,
            actual=actual,
            severity=severity,
            description=description,
        )
        self.differences.append(diff)
        self.total_differences += 1
        self.success = False

    def get_differences_by_type(self, diff_type: DifferenceType) -> List[Difference]:
        """Get all differences of a specific type."""
        return [d for d in self.differences if d.type == diff_type]

    def get_differences_by_severity(self, severity: str) -> List[Difference]:
        """Get all differences of a specific severity."""
        return [d for d in self.differences if d.severity == severity]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "markdown_file": str(self.markdown_file) if self.markdown_file else None,
            "success": self.success,
            "total_differences": self.total_differences,
            "differences": [d.to_dict() for d in self.differences],
            "differences_by_type": {
                diff_type.value: len(self.get_differences_by_type(diff_type))
                for diff_type in DifferenceType
            },
            "differences_by_severity": {
                severity: len(self.get_differences_by_severity(severity))
                for severity in ["low", "medium", "high"]
            },
            "markdown_stats": self.markdown_stats,
            "adf_stats": self.adf_stats,
        }


class MarkdownAdfComparator:
    """
    Compare markdown input with generated ADF output.

    This class provides methods to identify discrepancies between markdown
    source and the ADF that was generated from it.
    """

    def __init__(self):
        """Initialize the comparator."""
        self.markdown_parser = MarkdownParser()
        self.adf_converter = AdfConverter()
        self.adf_parser = AdfParser()

    def compare_from_markdown(
        self,
        markdown_content: str,
        markdown_file: Optional[Path] = None,
    ) -> ComparisonReport:
        """
        Compare markdown content with its generated ADF.

        Args:
            markdown_content: Markdown content to convert and compare
            markdown_file: Optional path to the markdown file

        Returns:
            Comparison report
        """
        report = ComparisonReport(markdown_file=markdown_file)

        try:
            # Parse markdown
            md_nodes = self.markdown_parser.parse(markdown_content)

            # Convert to ADF
            adf_dict = self.adf_converter.convert(md_nodes)

            # Parse ADF back
            adf_doc = self.adf_parser.parse(adf_dict)

            # Collect statistics
            report.markdown_stats = self._get_markdown_stats(md_nodes)
            report.adf_stats = adf_doc.get_node_statistics()

            # Compare structure
            self._compare_structure(md_nodes, adf_doc, report)

            # Compare text content
            self._compare_text_content(md_nodes, adf_doc, report)

            # Compare links
            self._compare_links(md_nodes, adf_doc, report)

        except Exception as e:
            report.add_difference(
                DifferenceType.STRUCTURE_MISMATCH,
                "root",
                "valid conversion",
                f"conversion failed: {e}",
                severity="high",
                description=str(e),
            )

        return report

    def compare_with_existing_adf(
        self,
        markdown_content: str,
        existing_adf: Dict[str, Any],
        markdown_file: Optional[Path] = None,
    ) -> ComparisonReport:
        """
        Compare markdown with an existing ADF document.

        Args:
            markdown_content: Markdown source content
            existing_adf: Existing ADF document (from Confluence)
            markdown_file: Optional path to the markdown file

        Returns:
            Comparison report
        """
        report = ComparisonReport(markdown_file=markdown_file)

        try:
            # Parse markdown
            md_nodes = self.markdown_parser.parse(markdown_content)

            # Convert to ADF
            generated_adf_dict = self.adf_converter.convert(md_nodes)

            # Parse both ADF documents
            generated_adf = self.adf_parser.parse(generated_adf_dict)
            existing_adf_doc = self.adf_parser.parse(existing_adf)

            # Collect statistics
            report.markdown_stats = self._get_markdown_stats(md_nodes)
            report.adf_stats = {
                "generated": generated_adf.get_node_statistics(),
                "existing": existing_adf_doc.get_node_statistics(),
            }

            # Compare the two ADF documents
            self._compare_adf_documents(generated_adf, existing_adf_doc, report)

        except Exception as e:
            report.add_difference(
                DifferenceType.STRUCTURE_MISMATCH,
                "root",
                "successful comparison",
                f"comparison failed: {e}",
                severity="high",
                description=str(e),
            )

        return report

    def _compare_structure(
        self,
        md_nodes: List[MarkdownNode],
        adf_doc: AdfDocument,
        report: ComparisonReport,
    ) -> None:
        """Compare the overall structure of markdown and ADF."""
        # Count major structural elements
        md_headings = len([n for n in self._flatten_md_nodes(md_nodes) if n.type == "heading"])
        md_lists = len([n for n in self._flatten_md_nodes(md_nodes) if n.type in ["bulletList", "orderedList"]])
        md_code_blocks = len([n for n in self._flatten_md_nodes(md_nodes) if n.type == "codeBlock"])

        adf_headings = len(adf_doc.root.find_nodes_by_type(AdfNodeType.HEADING))
        adf_lists = len(adf_doc.root.find_nodes_by_type(AdfNodeType.BULLET_LIST)) + len(
            adf_doc.root.find_nodes_by_type(AdfNodeType.ORDERED_LIST)
        )
        adf_code_blocks = len(adf_doc.root.find_nodes_by_type(AdfNodeType.CODE_BLOCK))

        # Compare counts
        if md_headings != adf_headings:
            report.add_difference(
                DifferenceType.STRUCTURE_MISMATCH,
                "headings",
                md_headings,
                adf_headings,
                severity="high",
                description=f"Heading count mismatch: {md_headings} in markdown vs {adf_headings} in ADF",
            )

        if md_lists != adf_lists:
            report.add_difference(
                DifferenceType.STRUCTURE_MISMATCH,
                "lists",
                md_lists,
                adf_lists,
                severity="medium",
                description=f"List count mismatch: {md_lists} in markdown vs {adf_lists} in ADF",
            )

        if md_code_blocks != adf_code_blocks:
            report.add_difference(
                DifferenceType.STRUCTURE_MISMATCH,
                "code_blocks",
                md_code_blocks,
                adf_code_blocks,
                severity="medium",
                description=f"Code block count mismatch: {md_code_blocks} in markdown vs {adf_code_blocks} in ADF",
            )

    def _compare_text_content(
        self,
        md_nodes: List[MarkdownNode],
        adf_doc: AdfDocument,
        report: ComparisonReport,
    ) -> None:
        """Compare text content between markdown and ADF."""
        # Get text from markdown
        md_text = self._extract_markdown_text(md_nodes)

        # Get text from ADF
        adf_text = adf_doc.get_all_text()

        # Normalize whitespace for comparison
        md_text_normalized = " ".join(md_text.split())
        adf_text_normalized = " ".join(adf_text.split())

        # Calculate similarity
        similarity = difflib.SequenceMatcher(None, md_text_normalized, adf_text_normalized).ratio()

        if similarity < 0.95:  # Less than 95% similar
            report.add_difference(
                DifferenceType.TEXT_MISMATCH,
                "text_content",
                "similarity: 100%",
                f"similarity: {similarity * 100:.1f}%",
                severity="high" if similarity < 0.8 else "medium",
                description=f"Text content similarity is {similarity * 100:.1f}%",
            )

    def _compare_links(
        self,
        md_nodes: List[MarkdownNode],
        adf_doc: AdfDocument,
        report: ComparisonReport,
    ) -> None:
        """Compare links between markdown and ADF."""
        # Extract links from markdown
        md_links = self._extract_markdown_links(md_nodes)

        # Get links from ADF
        adf_links = adf_doc.find_links()

        # Compare counts
        if len(md_links) != len(adf_links):
            report.add_difference(
                DifferenceType.LINK_MISMATCH,
                "links",
                len(md_links),
                len(adf_links),
                severity="medium",
                description=f"Link count mismatch: {len(md_links)} in markdown vs {len(adf_links)} in ADF",
            )

    def _compare_adf_documents(
        self,
        generated: AdfDocument,
        existing: AdfDocument,
        report: ComparisonReport,
    ) -> None:
        """Compare two ADF documents."""
        # Compare node statistics
        gen_stats = generated.get_node_statistics()
        exist_stats = existing.get_node_statistics()

        for node_type, count in gen_stats.items():
            existing_count = exist_stats.get(node_type, 0)
            if count != existing_count:
                report.add_difference(
                    DifferenceType.STRUCTURE_MISMATCH,
                    f"node_count.{node_type}",
                    count,
                    existing_count,
                    severity="medium",
                    description=f"Node count for {node_type}: {count} generated vs {existing_count} existing",
                )

        # Compare text content
        gen_text = generated.get_all_text()
        exist_text = existing.get_all_text()

        if gen_text != exist_text:
            similarity = difflib.SequenceMatcher(None, gen_text, exist_text).ratio()
            report.add_difference(
                DifferenceType.TEXT_MISMATCH,
                "text_content",
                f"generated text ({len(gen_text)} chars)",
                f"existing text ({len(exist_text)} chars)",
                severity="high" if similarity < 0.8 else "medium",
                description=f"Text similarity: {similarity * 100:.1f}%",
            )

    def _get_markdown_stats(self, nodes: List[MarkdownNode]) -> Dict[str, Any]:
        """Get statistics about markdown nodes."""
        all_nodes = self._flatten_md_nodes(nodes)
        node_types = {}

        for node in all_nodes:
            node_type = node.type  # type is already a string, not an enum
            node_types[node_type] = node_types.get(node_type, 0) + 1

        return {
            "total_nodes": len(all_nodes),
            "node_types": node_types,
        }

    def _flatten_md_nodes(self, nodes: List[MarkdownNode]) -> List[MarkdownNode]:
        """Flatten markdown node tree into a list."""
        result = []

        for node in nodes:
            result.append(node)
            if hasattr(node, 'children') and node.children:
                result.extend(self._flatten_md_nodes(node.children))

        return result

    def _extract_markdown_text(self, nodes: List[MarkdownNode]) -> str:
        """Extract all text from markdown nodes."""
        text_parts = []

        for node in nodes:
            if hasattr(node, 'content') and node.content:
                text_parts.append(node.content)

            if hasattr(node, 'children') and node.children:
                text_parts.append(self._extract_markdown_text(node.children))

        return " ".join(text_parts)

    def _extract_markdown_links(self, nodes: List[MarkdownNode]) -> List[Dict[str, Any]]:
        """Extract all links from markdown nodes."""
        links = []

        for node in self._flatten_md_nodes(nodes):
            if node.type == "link":
                links.append(
                    {
                        "url": getattr(node, 'url', None),
                        "title": getattr(node, 'title', None),
                        "text": getattr(node, 'content', None),
                    }
                )

        return links
