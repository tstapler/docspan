"""
Converter for transforming Markdown AST to ADF.

This module provides the main entry point for converting Markdown AST to
Atlassian Document Format (ADF).
"""

from typing import Any, Dict, List

from docspan.backends.confluence.adf.converters import ConverterFactory
from docspan.backends.confluence.adf.nodes import AdfBuilder
from docspan.backends.confluence.markdown.ast import MarkdownNode


class AdfConverter:
    """
    Convert Markdown AST to Atlassian Document Format (ADF).
    
    This class serves as a facade for the ADF conversion system, maintaining
    backward compatibility with existing code while delegating to the new
    visitor-based conversion system.
    """

    def __init__(self) -> None:
        """Initialize the converter."""
        self.builder = AdfBuilder()
        self.converter = ConverterFactory.create_converter()
        
    def convert(self, nodes: List[MarkdownNode], title: str = None, skip_first_h1_matching_title: bool = False) -> Dict[str, Any]:
        """
        Convert Markdown nodes to ADF.

        Args:
            nodes: List of Markdown AST nodes
            title: Optional page title to compare against first H1
            skip_first_h1_matching_title: If True, skip the first H1 heading that matches the title

        Returns:
            ADF document as a dictionary
        """
        # Filter nodes to skip first H1 if it matches the title
        filtered_nodes = self._filter_duplicate_title_heading(nodes, title, skip_first_h1_matching_title)

        # Get the standard ADF document
        adf = self.converter.convert(filtered_nodes)

        # Check if any nodes have HTML that needs to be inserted into the storage format
        # This is a bit of a hack, but it allows us to inject raw HTML for things like iframes
        # that aren't directly supported in ADF
        for node in filtered_nodes:
            if hasattr(node, 'storage_format_html') and node.storage_format_html:
                # Store the HTML content to be handled during the page update process
                # This information will be picked up by the page client
                if not hasattr(adf, 'storage_format_html'):
                    adf['storage_format_html'] = []
                adf['storage_format_html'].append(node.storage_format_html)

        return adf

    def _filter_duplicate_title_heading(
        self,
        nodes: List[MarkdownNode],
        title: str = None,
        skip_first_h1: bool = False
    ) -> List[MarkdownNode]:
        """
        Filter out the first H1 heading if it matches the page title.

        This prevents duplicate titles when the title is extracted from the first H1
        and then that same H1 appears in the content.

        Args:
            nodes: List of Markdown AST nodes
            title: The page title to compare against
            skip_first_h1: Whether to perform filtering

        Returns:
            Filtered list of nodes
        """
        if not skip_first_h1 or not title or not nodes:
            return nodes

        # Find the first H1 heading
        from docspan.backends.confluence.markdown.ast import HeadingNode

        for i, node in enumerate(nodes):
            if isinstance(node, HeadingNode) and node.level == 1:
                # Extract text from the heading's children
                heading_text = self._extract_text_from_nodes(node.children)

                # If it matches the title, skip this node
                if heading_text.strip() == title.strip():
                    return nodes[:i] + nodes[i+1:]

                # If we found an H1 that doesn't match, don't filter anything
                break

        return nodes

    def _extract_text_from_nodes(self, nodes: List[MarkdownNode]) -> str:
        """
        Extract plain text from a list of nodes (recursively).

        Args:
            nodes: List of nodes to extract text from

        Returns:
            Concatenated text content
        """
        from docspan.backends.confluence.markdown.ast import TextNode

        text_parts = []
        for node in nodes:
            if isinstance(node, TextNode):
                text_parts.append(node.content)
            elif hasattr(node, 'children'):
                text_parts.append(self._extract_text_from_nodes(node.children))

        return ''.join(text_parts)