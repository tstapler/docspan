"""
Visitors for converting Markdown nodes to ADF.

This module implements the visitor pattern for converting Markdown nodes
to Atlassian Document Format (ADF) nodes.
"""

import logging
from typing import Dict, List, Optional, cast

from docspan.backends.confluence.adf.interfaces import BaseNodeVisitor, NodeConverter, NodeRegistry
from docspan.backends.confluence.adf.nodes import AdfBuilder, AdfNode
from docspan.backends.confluence.markdown.ast import (
    BlockquoteNode,
    BulletListNode,
    CodeBlockNode,
    HeadingNode,
    ImageNode,
    InlineCodeNode,
    LinkNode,
    ListItemNode,
    MarkdownNode,
    MermaidNode,
    OrderedListNode,
    ParagraphNode,
    TableNode,
    TextNode,
    WikiLinkNode,
)


class NodeVisitorRegistry(NodeRegistry):
    """
    Registry for node converters.
    
    Attributes:
        converters: Dictionary mapping node types to converters
        logger: Logger instance
    """
    
    def __init__(self):
        """Initialize the registry."""
        self.converters: Dict[str, NodeConverter] = {}
        self.logger = logging.getLogger(__name__)
    
    def register(self, node_type: str, converter: NodeConverter) -> None:
        """Register a converter for a specific node type."""
        self.converters[node_type] = converter
        self.logger.debug(f"Registered converter for node type: {node_type}")
    
    def get(self, node_type: str) -> Optional[NodeConverter]:
        """Get the converter for a specific node type."""
        return self.converters.get(node_type)
    
    def has(self, node_type: str) -> bool:
        """Check if a converter exists for a specific node type."""
        return node_type in self.converters


class AdfNodeVisitor:
    """
    Visitor for converting Markdown nodes to ADF.
    
    This visitor traverses a Markdown AST and converts each node to its
    ADF representation using registered converters.
    
    Attributes:
        registry: Registry of node converters
        builder: ADF node builder
        logger: Logger instance
    """
    
    def __init__(self, registry: NodeRegistry, builder: AdfBuilder):
        """
        Initialize the visitor.
        
        Args:
            registry: Registry of node converters
            builder: ADF node builder
        """
        self.registry = registry
        self.builder = builder
        self.logger = logging.getLogger(__name__)
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """
        Visit a markdown node and convert it to an ADF node.
        
        Args:
            node: Markdown node to convert
            
        Returns:
            Converted ADF node
            
        Raises:
            ValueError: If no converter is found for the node type
        """
        converter = self.registry.get(node.type)
        if converter:
            return converter.convert(node)
        
        self.logger.error(f"No converter found for node type: {node.type}")
        raise ValueError(f"Unknown node type: {node.type}")
    
    def visit_children(self, node: MarkdownNode) -> List[AdfNode]:
        """
        Visit and convert all child nodes.
        
        Args:
            node: Parent node
            
        Returns:
            List of converted child nodes
        """
        return [self.visit(child) for child in node.children]


class TextNodeVisitor(BaseNodeVisitor):
    """Visitor for text nodes."""
    
    node_type = "text"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a text node to ADF."""
        text_node = cast(TextNode, node)
        
        # If the node has no content, return an empty text node
        if not text_node.content:
            return self.builder.text("")
        
        # Process marks if they exist
        marks = list(text_node.marks)
        
        return self.builder.text(text_node.content, marks)


class HeadingNodeVisitor(BaseNodeVisitor):
    """Visitor for heading nodes."""
    
    node_type = "heading"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a heading node to ADF."""
        heading_node = cast(HeadingNode, node)
        children = self.parent_visitor.visit_children(heading_node)
        return self.builder.heading(children, heading_node.level)


class ParagraphNodeVisitor(BaseNodeVisitor):
    """Visitor for paragraph nodes."""
    
    node_type = "paragraph"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a paragraph node to ADF."""
        paragraph_node = cast(ParagraphNode, node)
        children = self.parent_visitor.visit_children(paragraph_node)
        return self.builder.paragraph(children)


class ListItemNodeVisitor(BaseNodeVisitor):
    """Visitor for list item nodes."""
    
    node_type = "listItem"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a list item node to ADF."""
        list_item_node = cast(ListItemNode, node)
        children = self.parent_visitor.visit_children(list_item_node)
        return self.builder.list_item(children)


class BulletListNodeVisitor(BaseNodeVisitor):
    """Visitor for bullet list nodes."""
    
    node_type = "bulletList"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a bullet list node to ADF."""
        bullet_list_node = cast(BulletListNode, node)
        children = self.parent_visitor.visit_children(bullet_list_node)
        return self.builder.bullet_list(children)


class OrderedListNodeVisitor(BaseNodeVisitor):
    """Visitor for ordered list nodes."""
    
    node_type = "orderedList"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a ordered list node to ADF."""
        ordered_list_node = cast(OrderedListNode, node)
        children = self.parent_visitor.visit_children(ordered_list_node)
        return self.builder.ordered_list(children)


class CodeBlockNodeVisitor(BaseNodeVisitor):
    """Visitor for code block nodes."""
    
    node_type = "codeBlock"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a code block node to ADF."""
        code_block_node = cast(CodeBlockNode, node)
        return self.builder.code_block(code_block_node.content or "", code_block_node.language)


class InlineCodeNodeVisitor(BaseNodeVisitor):
    """Visitor for inline code nodes."""
    
    node_type = "inlineCode"
    
    def __init__(self):
        """Initialize the visitor."""
        pass
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert an inline code node to ADF."""
        inline_code_node = cast(InlineCodeNode, node)
        return AdfNode(type="text", text=inline_code_node.content or "", marks=[{"type": "code"}])


class BlockquoteNodeVisitor(BaseNodeVisitor):
    """Visitor for blockquote nodes."""
    
    node_type = "blockquote"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a blockquote node to ADF."""
        blockquote_node = cast(BlockquoteNode, node)
        children = self.parent_visitor.visit_children(blockquote_node)
        return self.builder.blockquote(children)


class TableNodeVisitor(BaseNodeVisitor):
    """Visitor for table nodes."""
    
    node_type = "table"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a table node to ADF."""
        table_node = cast(TableNode, node)
        
        # Convert rows to ADF nodes
        adf_rows = []

        # Handle headers if present
        has_headers = bool(table_node.headers)

        if has_headers:
            header_cells = []
            for header in table_node.headers:
                header_content = self.builder.paragraph([self.builder.text(header)])
                header_cells.append(header_content)
            adf_rows.append(header_cells)

        # Handle data rows
        for row in table_node.rows:
            row_cells = []
            for cell in row:
                if isinstance(cell, MarkdownNode):
                    row_cells.append(self.parent_visitor.visit(cell))
                else:
                    # Handle case where cell is raw content
                    cell_content = self.builder.paragraph([self.builder.text(str(cell))])
                    row_cells.append(cell_content)
            adf_rows.append(row_cells)

        return self.builder.table(adf_rows, has_headers)


class LinkNodeVisitor(BaseNodeVisitor):
    """Visitor for link nodes."""
    
    node_type = "link"
    
    def __init__(self, builder: AdfBuilder, parent_visitor: AdfNodeVisitor):
        """Initialize the visitor."""
        self.builder = builder
        self.parent_visitor = parent_visitor
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a link node to ADF."""
        link_node = cast(LinkNode, node)
        
        # Extract text content from child nodes or use link content
        text_content = " ".join(
            child.content or "" 
            for child in link_node.children if hasattr(child, 'content')
        ) or link_node.content or ""
        
        return self.builder.link(text_content, link_node.url, link_node.title)


class ImageNodeVisitor(BaseNodeVisitor):
    """Visitor for image nodes."""
    
    node_type = "image"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert an image node to ADF."""
        image_node = cast(ImageNode, node)
        return self.builder.image(image_node.src, image_node.alt, image_node.title)


class WikiLinkNodeVisitor(BaseNodeVisitor):
    """Visitor for wiki link nodes."""
    
    node_type = "wikiLink"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a wiki link node to ADF."""
        wiki_link_node = cast(WikiLinkNode, node)
        display = wiki_link_node.display or wiki_link_node.target
        return self.builder.link(display, f"#{wiki_link_node.target}")


class HorizontalRuleNodeVisitor(BaseNodeVisitor):
    """Visitor for horizontal rule nodes."""
    
    node_type = "horizontalRule"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a horizontal rule node to ADF."""
        return self.builder.horizontal_rule()


class MermaidNodeVisitor(BaseNodeVisitor):
    """Visitor for mermaid diagram nodes."""
    
    node_type = "mermaid"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the visitor."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """Convert a mermaid diagram node to ADF."""
        mermaid_node = cast(MermaidNode, node)
        
        self.logger.info("Processing Mermaid diagram for ADF conversion")
        self.logger.debug(f"Mermaid node attributes: {mermaid_node.attrs}")
        self.logger.debug(f"Mermaid code type: {mermaid_node.code.strip().split()[0] if mermaid_node.code.strip() else 'empty'}")
        
        # Check if the diagram has been rendered as image
        if "rendered_url" in mermaid_node.attrs:
            # Get the rendered URL
            image_url = mermaid_node.attrs["rendered_url"]
            self.logger.info(f"Found rendered URL for Mermaid diagram: {image_url}")
            
            # Create a more descriptive alt text if possible
            alt_text = "Mermaid diagram"
            if mermaid_node.code.strip().startswith("flowchart") or mermaid_node.code.strip().startswith("graph"):
                alt_text = "Flowchart diagram"
            elif mermaid_node.code.strip().startswith("sequenceDiagram"):
                alt_text = "Sequence diagram"
            elif mermaid_node.code.strip().startswith("classDiagram"):
                alt_text = "Class diagram"
            
            self.logger.info(f"Converting Mermaid diagram to ADF image with alt text: {alt_text}")
            
            # Check if we have an iframe HTML for direct embedding
            if "iframe_html" in mermaid_node.attrs:
                # Create a paragraph with HTML content using the raw macro
                self.logger.info("Using iframe HTML embedding for diagram")
                html_content = mermaid_node.attrs["iframe_html"]
                
                # Create a paragraph node that will be replaced with the HTML content
                note_paragraph = self.builder.paragraph([
                    self.builder.text("")
                ])
                
                # Store HTML content in a special attribute that will be handled during storage format conversion
                note_paragraph.storage_format_html = html_content
                
                return note_paragraph
            
            # Otherwise add the diagram as an image with proper alt text using Confluence Storage Format approach
            self.logger.debug(f"Creating ADF image node with URL: {image_url}")
            try:
                # First try the standard ADF approach
                image_node = self.builder.image(image_url, alt_text)
                self.logger.debug(f"Successfully created ADF image node: {image_node.to_dict()}")
                
                # Add attachment ID if available, as this is crucial for proper rendering
                if "attachment_id" in mermaid_node.attrs:
                    attachment_id = mermaid_node.attrs["attachment_id"]
                    self.logger.info(f"Adding attachment ID {attachment_id} to image node")
                    
                    # Override attributes with file-based reference that Confluence understands better
                    image_node.attrs = {
                        "type": "file",
                        "id": attachment_id,
                        "collection": "contentId"
                    }
                    
                    if alt_text:
                        image_node.attrs["alt"] = alt_text
                        
                    self.logger.debug(f"Updated image node with attachment ID: {image_node.to_dict()}")
                
                return image_node
            except Exception as e:
                self.logger.error(f"Error creating image node for mermaid diagram: {e}")
                # Fallback to code block
                return self.builder.code_block(mermaid_node.code, "mermaid")
            
        # Check if the diagram has been rendered as iframe embed
        elif "embed_html" in mermaid_node.attrs:
            # Get the HTML
            mermaid_node.attrs["embed_html"]
            self.logger.info("Found embed HTML for Mermaid diagram")
            
            # Instead of trying to embed HTML, use a link to the mermaid live diagram
            live_link = mermaid_node.attrs["live_link"]
            self.logger.info(f"Using link to Mermaid Live diagram: {live_link}")
            
            # Create a paragraph with a link
            return self.builder.paragraph([
                self.builder.text("This diagram is also available at: "),
                self.builder.link("Mermaid Live View", live_link, "Click to view the diagram in Mermaid Live Editor")
            ])
        
        # Check if the diagram has a live link
        elif "live_link" in mermaid_node.attrs:
            # Get the live link
            live_link = mermaid_node.attrs["live_link"]
            self.logger.info(f"Found Mermaid Live link: {live_link}")
            
            # Create a panel with the link
            return self.builder.link("View Mermaid diagram", live_link, "Mermaid Live diagram")

        # Log the issue and fall back to code block
        self.logger.warning("Mermaid diagram could not be rendered as image or embedded, using code block fallback")
        self.logger.warning("No 'rendered_url', 'embed_html', or 'live_link' attribute found in the mermaid node")
        self.logger.debug(f"Available attributes: {list(mermaid_node.attrs.keys())}")
        
        # Fallback to a code block if rendering failed
        return self.builder.code_block(mermaid_node.code, "mermaid")