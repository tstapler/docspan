"""
Converters for transforming Markdown nodes to ADF nodes.

This module implements the converter pattern for transforming
Markdown nodes to Atlassian Document Format (ADF) nodes.
"""

import logging
import re as _re
from typing import Any, Dict, List

# Matches Jira issue URLs: .../browse/PROJ-123
_JIRA_ISSUE_URL_RE = _re.compile(
    r'https?://[^/]+/browse/([A-Z][A-Z0-9_]+-\d+)',
    _re.IGNORECASE,
)
# Matches a bare issue key like "INCDNT-1" or "PROJ-42"
_JIRA_ISSUE_KEY_RE = _re.compile(r'^[A-Z][A-Z0-9_]+-\d+$', _re.IGNORECASE)

from docspan.backends.confluence.adf.interfaces import AdfDocumentBuilder, TypedNodeConverter
from docspan.backends.confluence.adf.nodes import AdfBuilder, AdfNode
from docspan.backends.confluence.adf.visitors import AdfNodeVisitor, NodeVisitorRegistry
from docspan.backends.confluence.markdown.ast import (
    BlockquoteNode,
    BulletListNode,
    CodeBlockMacroNode,
    CodeBlockNode,
    ColoredTextNode,
    DateNode,
    EmojiNode,
    ExcerptNode,
    ExpandMacroNode,
    ExpandNode,
    HeadingNode,
    HighlightedTextNode,
    HorizontalRuleNode,
    ImageNode,
    InfoNode,
    InlineCodeNode,
    LayoutColumnNode,
    LayoutSectionNode,
    LinkNode,
    ListItemNode,
    MarkdownNode,
    MediaGroupNode,
    MentionNode,
    MermaidNode,
    NoteNode,
    OrderedListNode,
    ParagraphNode,
    StatusBadgeNode,
    StatusMacroNode,
    TableNode,
    TaskItemNode,
    TaskListNode,
    TextNode,
    TocNode,
    URLEmbedNode,
    WarningNode,
    WikiLinkNode,
)


class AdfDocumentBuilderImpl(AdfDocumentBuilder):
    """
    Implementation of ADF document builder.
    
    This class is responsible for building ADF documents from ADF nodes.
    
    Attributes:
        builder: ADF node builder
        logger: Logger instance
    """
    
    def __init__(self, builder: AdfBuilder):
        """
        Initialize the document builder.
        
        Args:
            builder: ADF node builder
        """
        self.builder = builder
        self.logger = logging.getLogger(__name__)
    
    def build_document(self, nodes: List[AdfNode]) -> Dict[str, Any]:
        """
        Build an ADF document from a list of ADF nodes.
        
        Args:
            nodes: List of ADF nodes
            
        Returns:
            ADF document as a dictionary
        """
        result = self.builder.document(nodes)
        self.logger.debug(f"Built ADF document with {len(nodes)} top-level nodes")
        return result


class MarkdownToAdfConverter:
    """
    Converter for transforming Markdown AST to ADF.
    
    This class orchestrates the conversion of Markdown nodes to ADF nodes
    using visitors and builds the final ADF document.
    
    Attributes:
        visitor: ADF node visitor
        document_builder: ADF document builder
        logger: Logger instance
    """
    
    def __init__(
        self, 
        visitor: AdfNodeVisitor,
        document_builder: AdfDocumentBuilder
    ):
        """
        Initialize the converter.
        
        Args:
            visitor: ADF node visitor
            document_builder: ADF document builder
        """
        self.visitor = visitor
        self.document_builder = document_builder
        self.logger = logging.getLogger(__name__)
    
    def convert(self, nodes: List[MarkdownNode]) -> Dict[str, Any]:
        """
        Convert Markdown nodes to ADF.
        
        Args:
            nodes: List of Markdown AST nodes
            
        Returns:
            ADF document as a dictionary
        """
        self.logger.debug(f"Converting {len(nodes)} Markdown nodes to ADF")
        adf_nodes = []
        
        for node in nodes:
            try:
                adf_node = self.visitor.visit(node)
                adf_nodes.append(adf_node)
            except Exception as e:
                self.logger.error(f"Error converting node of type {node.type}: {str(e)}")
                # Continue with other nodes, providing resilience to conversion errors
        
        return self.document_builder.build_document(adf_nodes)


class ConverterFactory:
    """
    Factory for creating Markdown to ADF converters.
    
    This class creates and configures all the components needed for
    Markdown to ADF conversion.
    """
    
    @staticmethod
    def create_converter() -> MarkdownToAdfConverter:
        """
        Create a fully configured Markdown to ADF converter.
        
        Returns:
            Configured converter
        """
        # Create the builder
        builder = AdfBuilder()
        
        # Create the registry and visitor
        registry = NodeVisitorRegistry()
        visitor = AdfNodeVisitor(registry, builder)
        
        # Create and register all node visitors
        registry.register("text", TextNodeConverter(builder))
        registry.register("heading", HeadingNodeConverter(builder, visitor))
        registry.register("paragraph", ParagraphNodeConverter(builder, visitor))
        registry.register("listItem", ListItemNodeConverter(builder, visitor))
        registry.register("bulletList", BulletListNodeConverter(builder, visitor))
        registry.register("orderedList", OrderedListNodeConverter(builder, visitor))
        registry.register("codeBlock", CodeBlockNodeConverter(builder))
        registry.register("inlineCode", InlineCodeNodeConverter())
        registry.register("blockquote", BlockquoteNodeConverter(builder, visitor))
        registry.register("table", TableNodeConverter(builder, visitor))
        registry.register("link", LinkNodeConverter(builder, visitor))
        registry.register("image", ImageNodeConverter(builder))
        registry.register("mediaGroup", MediaGroupNodeConverter(builder, visitor))
        registry.register("wikiLink", WikiLinkNodeConverter(builder))
        registry.register("mention", MentionNodeConverter(builder))
        registry.register("emoji", EmojiNodeConverter(builder))
        registry.register("expand", ExpandNodeConverter(builder, visitor))
        registry.register("horizontalRule", HorizontalRuleNodeConverter(builder))
        registry.register("mermaid", MermaidNodeConverter(builder))
        registry.register("taskList", TaskListNodeConverter(builder, visitor))
        registry.register("taskItem", TaskItemNodeConverter(builder, visitor))
        registry.register("statusBadge", StatusBadgeNodeConverter(builder))
        registry.register("date", DateNodeConverter(builder))
        registry.register("layoutSection", LayoutSectionNodeConverter(builder, visitor))
        registry.register("layoutColumn", LayoutColumnNodeConverter(builder, visitor))
        registry.register("highlightedText", HighlightedTextNodeConverter(builder))
        registry.register("coloredText", ColoredTextNodeConverter(builder))
        registry.register("urlEmbed", URLEmbedNodeConverter(builder))

        # Extension/macro converters
        registry.register("toc", TocNodeConverter(builder))
        registry.register("statusMacro", StatusMacroNodeConverter(builder))
        registry.register("info", InfoNodeConverter(builder, visitor))
        registry.register("warning", WarningNodeConverter(builder, visitor))
        registry.register("note", NoteNodeConverter(builder, visitor))
        registry.register("excerpt", ExcerptNodeConverter(builder, visitor))
        registry.register("expandMacro", ExpandMacroNodeConverter(builder, visitor))
        registry.register("codeBlockMacro", CodeBlockMacroNodeConverter(builder))

        # Create the document builder
        document_builder = AdfDocumentBuilderImpl(builder)
        
        # Create and return the converter
        return MarkdownToAdfConverter(visitor, document_builder)


# Node converters

class TextNodeConverter(TypedNodeConverter):
    """Converter for text nodes."""
    
    node_type = "text"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
    
    def convert_typed(self, node: TextNode) -> AdfNode:
        """Convert a text node to ADF."""
        # If the node has no content, return an empty text node
        if not node.content:
            return self.builder.text("")
        
        # Process marks if they exist
        marks = list(node.marks)
        
        return self.builder.text(node.content, marks)


class HeadingNodeConverter(TypedNodeConverter):
    """Converter for heading nodes."""
    
    node_type = "heading"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: HeadingNode) -> AdfNode:
        """Convert a heading node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.heading(children, node.level)


class ParagraphNodeConverter(TypedNodeConverter):
    """Converter for paragraph nodes."""
    
    node_type = "paragraph"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: ParagraphNode) -> AdfNode:
        """Convert a paragraph node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.paragraph(children)


class ListItemNodeConverter(TypedNodeConverter):
    """Converter for list item nodes."""
    
    node_type = "listItem"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: ListItemNode) -> AdfNode:
        """Convert a list item node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.list_item(children)


class BulletListNodeConverter(TypedNodeConverter):
    """Converter for bullet list nodes."""
    
    node_type = "bulletList"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: BulletListNode) -> AdfNode:
        """Convert a bullet list node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.bullet_list(children)


class OrderedListNodeConverter(TypedNodeConverter):
    """Converter for ordered list nodes."""
    
    node_type = "orderedList"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: OrderedListNode) -> AdfNode:
        """Convert an ordered list node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.ordered_list(children)


class CodeBlockNodeConverter(TypedNodeConverter):
    """Converter for code block nodes."""
    
    node_type = "codeBlock"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
    
    def convert_typed(self, node: CodeBlockNode) -> AdfNode:
        """Convert a code block node to ADF."""
        return self.builder.code_block(node.content or "", node.language)


class InlineCodeNodeConverter(TypedNodeConverter):
    """Converter for inline code nodes."""
    
    node_type = "inlineCode"
    
    def convert_typed(self, node: InlineCodeNode) -> AdfNode:
        """Convert an inline code node to ADF."""
        return AdfNode(type="text", text=node.content or "", marks=[{"type": "code"}])


class BlockquoteNodeConverter(TypedNodeConverter):
    """Converter for blockquote nodes."""
    
    node_type = "blockquote"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: BlockquoteNode) -> AdfNode:
        """Convert a blockquote node to ADF."""
        children = [self.visitor.visit(child) for child in node.children]
        return self.builder.blockquote(children)


class TableNodeConverter(TypedNodeConverter):
    """Converter for table nodes."""
    
    node_type = "table"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: TableNode) -> AdfNode:
        """Convert a table node to ADF."""
        # Convert rows to ADF nodes
        adf_rows = []

        # Handle headers if present
        has_headers = bool(node.headers)

        if has_headers:
            header_cells = []
            for header in node.headers:
                header_content = self.builder.paragraph([self.builder.text(header)])
                header_cells.append(header_content)
            adf_rows.append(header_cells)

        # Handle data rows
        for row in node.rows:
            row_cells = []
            for cell in row:
                if isinstance(cell, MarkdownNode):
                    row_cells.append(self.visitor.visit(cell))
                else:
                    # Handle case where cell is raw content
                    cell_content = self.builder.paragraph([self.builder.text(str(cell))])
                    row_cells.append(cell_content)
            adf_rows.append(row_cells)

        return self.builder.table(adf_rows, has_headers)


class LinkNodeConverter(TypedNodeConverter):
    """Converter for link nodes."""
    
    node_type = "link"
    
    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
    
    def convert_typed(self, node: LinkNode) -> AdfNode:
        """Convert a link node to ADF.

        Jira issue URLs (*/browse/PROJ-NNN) are rendered as inlineCard smart links
        when the display text is the issue key, the full URL, or empty — i.e. when
        the author hasn't chosen custom display text. Custom text like
        '[see the ticket](jira-url)' keeps the linked text format.
        """
        # Extract text content from child nodes or use link content
        text_content = " ".join(
            child.content or ""
            for child in node.children if hasattr(child, 'content')
        ) or node.content or ""

        # Smart link detection: Jira issue URLs → inlineCard when display text is
        # the issue key, the bare URL, or absent.
        jira_match = _JIRA_ISSUE_URL_RE.match(node.url)
        if jira_match:
            jira_match.group(1).upper()
            text_is_key = bool(_JIRA_ISSUE_KEY_RE.match(text_content))
            text_is_url = (not text_content or text_content == node.url)
            if text_is_key or text_is_url:
                return self.builder.inline_card(node.url)

        # Extract marks from child text nodes (e.g., strong, em)
        additional_marks = []
        for child in node.children:
            if hasattr(child, 'marks') and child.marks:
                additional_marks.extend(child.marks)

        return self.builder.link(text_content, node.url, node.title, additional_marks=additional_marks)


class ImageNodeConverter(TypedNodeConverter):
    """
    Converter for image nodes to ADF media nodes.

    Supports:
    - External images with dimensions
    - Confluence attachments
    - Layout wrapping in mediaSingle
    - Width and height attributes
    """

    node_type = "image"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: ImageNode) -> AdfNode:
        """
        Convert an image node to ADF media node.

        Args:
            node: ImageNode with src, alt, title, width, height, layout attributes

        Returns:
            AdfNode - either media or mediaSingle (if layout specified)

        Notes:
            - Handles both external URLs and Confluence attachments
            - Wraps in mediaSingle if layout attribute is specified
            - Passes width and height to builder
        """
        # Determine if this is a Confluence attachment or external image
        if node.is_confluence_attachment:
            self.logger.debug(f"Converting Confluence attachment image: {node.src}")
            media_node = self.builder.confluence_image(
                file_id=node.src,
                alt=node.alt,
                title=node.title,
                width=node.width,
                height=node.height,
                collection=node.collection or "contentId",
                occurrence_key=node.occurrence_key
            )
        else:
            self.logger.debug(f"Converting external image: {node.src}")
            media_node = self.builder.image(
                url=node.src,
                alt=node.alt,
                title=node.title,
                width=node.width,
                height=node.height
            )

        # Wrap in mediaSingle if layout is specified
        if node.layout:
            self.logger.debug(f"Wrapping image in mediaSingle with layout: {node.layout}")
            return self.builder.media_single(media_node, layout=node.layout)

        return media_node


class MediaGroupNodeConverter(TypedNodeConverter):
    """
    Converter for media group nodes to ADF.

    Converts MediaGroupNode (image galleries) to ADF mediaGroup nodes
    containing multiple media items.
    """

    node_type = "mediaGroup"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """
        Initialize the converter.

        Args:
            builder: ADF node builder
            visitor: Node visitor for recursively converting child nodes
        """
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: MediaGroupNode) -> AdfNode:
        """
        Convert a media group node to ADF mediaGroup node.

        Args:
            node: MediaGroupNode with children (ImageNode items)

        Returns:
            AdfNode representing a mediaGroup

        Notes:
            - Recursively converts all child image nodes
            - Children should be ImageNode items
            - Creates ADF mediaGroup structure for galleries
        """
        self.logger.debug(f"Converting media group with {len(node.children)} items")

        # Convert child nodes (should be ImageNode items)
        media_items = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    media_items.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in media group: {e}"
                )

        if not media_items:
            self.logger.warning("Media group has no items, creating empty group")

        return self.builder.media_group(media_items)


class WikiLinkNodeConverter(TypedNodeConverter):
    """
    Converter for wiki link nodes to ADF smart cards.

    Converts [[Wiki Link]] syntax to Confluence inlineCard nodes for
    native smart link rendering with page metadata and icons.
    """

    node_type = "wikiLink"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: WikiLinkNode) -> AdfNode:
        """
        Convert a wiki link node to ADF inline card.

        Args:
            node: WikiLinkNode with target and optional display text

        Returns:
            AdfNode representing an inlineCard

        Notes:
            - Uses anchor-style URLs for now (#target)
            - TODO: Implement Confluence page ID resolution service
            - Falls back to text link if conversion fails
        """
        try:
            # Get target and display text
            target = node.target
            display = node.display or target

            # For now, use anchor-style URLs until we implement page ID resolution
            # Format: #PageName (Confluence handles these as internal page refs)
            url = f"#{target}"

            # Create Confluence metadata for better smart card rendering
            confluence_metadata = {
                "linkType": "page",
                "contentTitle": display,
                "isRenamedTitle": bool(node.display)  # True if custom display text provided
            }

            self.logger.debug(
                f"Converting wiki link [[{target}]] to inline card with URL: {url}"
            )

            # Create inline card using the builder method
            return self.builder.inline_card(
                url=url,
                title=display,
                confluence_metadata=confluence_metadata
            )

        except Exception as e:
            # Fallback to simple link if inline card creation fails
            self.logger.warning(
                f"Failed to convert wiki link [[{node.target}]] to inline card: {e}. "
                f"Falling back to simple link."
            )
            display = node.display or node.target
            return self.builder.link(display, f"#{node.target}")


class MentionNodeConverter(TypedNodeConverter):
    """
    Converter for user mention nodes to ADF.

    Converts @username syntax to Confluence mention nodes with user ID resolution.
    """

    node_type = "mention"

    def __init__(self, builder: AdfBuilder, user_resolver=None):
        """
        Initialize the converter.

        Args:
            builder: ADF node builder
            user_resolver: Optional UserResolver instance for username resolution
        """
        self.builder = builder
        self.user_resolver = user_resolver
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: MentionNode) -> AdfNode:
        """
        Convert a mention node to ADF mention node.

        Args:
            node: MentionNode with username and optional user_id

        Returns:
            AdfNode representing a mention

        Notes:
            - If user_id not provided, attempts resolution via UserResolver
            - Falls back to placeholder ID if resolution fails
            - Falls back to text node if conversion fails
        """
        try:
            username = node.username
            user_id = node.user_id
            display_text = node.text or f"@{username}"

            # If no user_id provided, try to resolve it
            if not user_id and self.user_resolver:
                self.logger.debug(f"Attempting to resolve user ID for @{username}")
                try:
                    user_id = self.user_resolver.resolve_username(username)
                    if user_id:
                        self.logger.info(f"Resolved @{username} to user ID: {user_id}")
                    else:
                        self.logger.warning(f"Could not resolve user ID for @{username}")
                except Exception as e:
                    self.logger.error(f"Error resolving user @{username}: {e}")

            # If still no user_id, use placeholder
            if not user_id:
                self.logger.warning(
                    f"Mention for @{username} has no user_id. "
                    f"Using placeholder ID. User may not be notified."
                )
                # Use username as temporary user_id
                user_id = f"unresolved-{username}"

            self.logger.debug(
                f"Converting mention @{username} to ADF mention node with user_id: {user_id}"
            )

            # Create mention node using the builder method
            return self.builder.mention(
                user_id=user_id,
                text=display_text
            )

        except Exception as e:
            # Fallback to text node if mention creation fails
            self.logger.warning(
                f"Failed to convert mention @{node.username} to ADF: {e}. "
                f"Falling back to text node."
            )
            display_text = node.text or f"@{node.username}"
            return self.builder.text(display_text)


class EmojiNodeConverter(TypedNodeConverter):
    """
    Converter for emoji nodes to ADF.

    Converts :emoji_name: syntax to Confluence emoji nodes with Unicode mapping.
    """

    node_type = "emoji"

    def __init__(self, builder: AdfBuilder, emoji_mapper=None):
        """
        Initialize the converter.

        Args:
            builder: ADF node builder
            emoji_mapper: Optional EmojiMapper instance for emoji resolution
        """
        self.builder = builder
        self.emoji_mapper = emoji_mapper
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: EmojiNode) -> AdfNode:
        """
        Convert an emoji node to ADF emoji node.

        Args:
            node: EmojiNode with short_name and optional emoji_id

        Returns:
            AdfNode representing an emoji

        Notes:
            - If emoji_id not provided, attempts resolution via EmojiMapper
            - Falls back to text node if emoji not supported
            - Uses Unicode codepoint and character from mapping
        """
        try:
            short_name = node.short_name
            emoji_id = node.emoji_id
            text = node.text

            # If no emoji_id provided, try to resolve it
            if not emoji_id and self.emoji_mapper:
                self.logger.debug(f"Attempting to resolve emoji :{short_name}:")
                try:
                    result = self.emoji_mapper.get_emoji(short_name)
                    if result:
                        emoji_id, character = result
                        # Use the character from mapping if not provided
                        if not text or text == f":{short_name}:":
                            text = character
                        self.logger.info(f"Resolved :{short_name}: to emoji ID: {emoji_id}")
                    else:
                        self.logger.warning(f"Could not resolve emoji :{short_name}:")
                except Exception as e:
                    self.logger.error(f"Error resolving emoji :{short_name}: {e}")

            # If still no emoji_id, check if mapper says it's supported
            if not emoji_id:
                if self.emoji_mapper and not self.emoji_mapper.is_supported(short_name):
                    self.logger.warning(
                        f"Emoji :{short_name}: is not supported. "
                        f"Falling back to text node."
                    )
                    fallback_text = node.text or f":{short_name}:"
                    return self.builder.text(fallback_text)
                else:
                    # No mapper available, use placeholder
                    self.logger.warning(
                        f"Emoji :{short_name}: has no emoji_id and no mapper available. "
                        f"Falling back to text node."
                    )
                    fallback_text = node.text or f":{short_name}:"
                    return self.builder.text(fallback_text)

            # Ensure text is set
            if not text:
                text = f":{short_name}:"

            self.logger.debug(
                f"Converting emoji :{short_name}: to ADF emoji node with ID: {emoji_id}"
            )

            # Create emoji node using the builder method
            # Include short_name with colons for consistency
            short_name_with_colons = f":{short_name}:"
            return self.builder.emoji(
                short_name=short_name_with_colons,
                emoji_id=emoji_id,
                text=text
            )

        except Exception as e:
            # Fallback to text node if emoji creation fails
            self.logger.warning(
                f"Failed to convert emoji :{node.short_name}: to ADF: {e}. "
                f"Falling back to text node."
            )
            fallback_text = node.text or f":{node.short_name}:"
            return self.builder.text(fallback_text)


class ExpandNodeConverter(TypedNodeConverter):
    """
    Converter for expand (collapsible) nodes to ADF.

    Converts HTML <details><summary> blocks to Confluence expand nodes.
    """

    node_type = "expand"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """
        Initialize the converter.

        Args:
            builder: ADF node builder
            visitor: Node visitor for recursively converting child nodes
        """
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: ExpandNode) -> AdfNode:
        """
        Convert an expand node to ADF expand node.

        Args:
            node: ExpandNode with title and children

        Returns:
            AdfNode representing an expand section

        Notes:
            - Recursively converts all child nodes
            - If no children, creates empty paragraph
            - Title can be empty string
        """
        try:
            title = node.title or ""
            self.logger.debug(f"Converting expand node with title: {title!r}")

            # Convert child nodes
            content_nodes = []
            for child in node.children:
                try:
                    converted_child = self.visitor.visit(child)
                    if converted_child:
                        content_nodes.append(converted_child)
                except Exception as e:
                    self.logger.warning(
                        f"Failed to convert child node {child.type} in expand: {e}"
                    )

            # Ensure at least one content node for valid ADF
            if not content_nodes:
                self.logger.debug("No content in expand, adding empty paragraph")
                content_nodes.append(self.builder.paragraph([]))

            self.logger.debug(
                f"Converting expand '{title}' with {len(content_nodes)} child nodes"
            )

            # Create expand node using the builder method
            return self.builder.expand(title=title, content=content_nodes)

        except Exception as e:
            # Fallback: convert children as normal paragraphs
            self.logger.warning(
                f"Failed to convert expand node: {e}. "
                f"Converting children as normal content."
            )
            # Return children as a list or wrap in paragraph
            if node.children:
                return self.visitor.visit(node.children[0])
            else:
                return self.builder.paragraph([])


class HorizontalRuleNodeConverter(TypedNodeConverter):
    """Converter for horizontal rule nodes."""

    node_type = "horizontalRule"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder

    def convert_typed(self, node: HorizontalRuleNode) -> AdfNode:
        """Convert a horizontal rule node to ADF."""
        return self.builder.horizontal_rule()


class MermaidNodeConverter(TypedNodeConverter):
    """Converter for mermaid diagram nodes."""
    
    node_type = "mermaid"
    
    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)
    
    def convert_typed(self, node: MermaidNode) -> AdfNode:
        """Convert a mermaid diagram node to ADF."""
        self.logger.info("Processing Mermaid diagram for ADF conversion")
        self.logger.debug(f"Mermaid node attributes: {node.attrs}")
        
        # First, check if the node has storage_format_html attribute (highest priority)
        if hasattr(node, 'storage_format_html') and node.storage_format_html:
            self.logger.info("Using node's storage_format_html for direct HTML embedding")
            
            # Create a paragraph node that will be replaced with HTML content
            note_paragraph = self.builder.paragraph([
                self.builder.text("")
            ])
            
            # Transfer the HTML content to the paragraph node
            # This will be included in the JSON output via the AdfNode.to_dict method
            note_paragraph.storage_format_html = node.storage_format_html
            
            return note_paragraph
        
        # Check if we have an iframe HTML for direct embedding
        elif "iframe_html" in node.attrs:
            # Create a paragraph with HTML content using the raw macro
            self.logger.info("Using iframe HTML embedding for diagram")
            html_content = node.attrs["iframe_html"]
            
            # Create a paragraph node that will be replaced with HTML content
            note_paragraph = self.builder.paragraph([
                self.builder.text("")
            ])
            
            # Store HTML content in the storage_format_html attribute
            # This will be included in the JSON output via the AdfNode.to_dict method
            note_paragraph.storage_format_html = html_content
            
            return note_paragraph
            
        # Check if the diagram has been rendered as image
        elif "rendered_url" in node.attrs:
            # Get the rendered URL
            image_url = node.attrs["rendered_url"]
            self.logger.info(f"Found rendered URL for Mermaid diagram: {image_url}")
            
            # Create a more descriptive alt text if possible
            alt_text = "Mermaid diagram"
            if node.code.strip().startswith("flowchart") or node.code.strip().startswith("graph"):
                alt_text = "Flowchart diagram"
            elif node.code.strip().startswith("sequenceDiagram"):
                alt_text = "Sequence diagram"
            elif node.code.strip().startswith("classDiagram"):
                alt_text = "Class diagram"
            
            self.logger.info(f"Converting Mermaid diagram to ADF image with alt text: {alt_text}")
            
            # Create the image node
            image_node = self.builder.image(image_url, alt_text)

            # Add attachment ID if available, as this is crucial for proper rendering
            if "attachment_id" in node.attrs:
                attachment_id = node.attrs["attachment_id"]
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

            # Wrap the image in media_single for better layout control
            # Extract layout and width from node attributes if specified
            layout = node.attrs.get("layout", "center")
            width = node.attrs.get("width", 800)  # Default to 800px for diagrams

            self.logger.debug(f"Wrapping image in media_single with layout={layout}, width={width}")
            return self.builder.media_single(
                image_node,
                layout=layout,
                width=width,
                width_type="pixel"
            )
            
        # Check if the diagram has a live link
        elif "live_link" in node.attrs:
            # Get the live link
            live_link = node.attrs["live_link"]
            self.logger.info(f"Found Mermaid Live link: {live_link}")
            
            # Create a link with the mermaid diagram
            return self.builder.link("View Mermaid diagram", live_link, "Mermaid Live diagram")

        # Log the issue and fall back to code block
        self.logger.warning("Mermaid diagram could not be rendered as image or embedded, using code block fallback")
        self.logger.warning(f"Available attributes: {list(node.attrs.keys() if hasattr(node, 'attrs') else [])}")
        
        # Fallback to a code block if rendering failed
        return self.builder.code_block(node.code, "mermaid")


class TaskListNodeConverter(TypedNodeConverter):
    """Converter for task list nodes."""

    node_type = "taskList"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor

    def convert_typed(self, node: "TaskListNode") -> AdfNode:
        """Convert a task list node to ADF."""
        items = [self.visitor.visit(child) for child in node.children]
        return self.builder.task_list(items)


class TaskItemNodeConverter(TypedNodeConverter):
    """Converter for task item nodes."""

    node_type = "taskItem"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor

    def convert_typed(self, node: "TaskItemNode") -> AdfNode:
        """
        Convert a task item node to ADF.

        Confluence's taskItem nodes don't support paragraph children - they only
        accept inline content. This method flattens paragraph nodes to extract
        their inline content.
        """
        content = []
        for child in node.children:
            visited = self.visitor.visit(child)
            # If child is a paragraph, extract its inline content instead
            # Confluence taskItem nodes don't support paragraph children
            if visited.type == "paragraph" and visited.content:
                content.extend(visited.content)
            else:
                content.append(visited)

        state = "DONE" if node.checked else "TODO"
        return self.builder.task_item(content, state)


class StatusBadgeNodeConverter(TypedNodeConverter):
    """Converter for status badge nodes."""

    node_type = "statusBadge"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder

    def convert_typed(self, node: "StatusBadgeNode") -> AdfNode:
        """Convert a status badge node to ADF."""
        return self.builder.status(node.text, node.color)


class DateNodeConverter(TypedNodeConverter):
    """Converter for date nodes."""

    node_type = "date"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder

    def convert_typed(self, node: "DateNode") -> AdfNode:
        """Convert a date node to ADF."""
        return self.builder.date(node.timestamp)


class LayoutSectionNodeConverter(TypedNodeConverter):
    """Converter for layout section nodes."""

    node_type = "layoutSection"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor

    def convert_typed(self, node: "LayoutSectionNode") -> AdfNode:
        """Convert a layout section node to ADF."""
        columns = [self.visitor.visit(child) for child in node.children]
        return self.builder.layout_section(columns)


class LayoutColumnNodeConverter(TypedNodeConverter):
    """Converter for layout column nodes."""

    node_type = "layoutColumn"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor

    def convert_typed(self, node: "LayoutColumnNode") -> AdfNode:
        """Convert a layout column node to ADF."""
        content = [self.visitor.visit(child) for child in node.children]
        return self.builder.layout_column(content, node.width)


class HighlightedTextNodeConverter(TypedNodeConverter):
    """Converter for highlighted text nodes."""

    node_type = "highlightedText"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder

    def convert_typed(self, node: "HighlightedTextNode") -> AdfNode:
        """Convert a highlighted text node to ADF."""
        return self.builder.highlighted_text(node.content or "", node.bg_color, node.marks)


class ColoredTextNodeConverter(TypedNodeConverter):
    """Converter for colored text nodes."""

    node_type = "coloredText"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder

    def convert_typed(self, node: "ColoredTextNode") -> AdfNode:
        """Convert a colored text node to ADF."""
        text = node.content or ""

        # If both color and bg_color are specified, combine them
        if node.color and node.bg_color:
            # Create text with both marks
            marks = list(node.marks) if node.marks else []
            marks.append({"type": "textColor", "attrs": {"color": node.color}})
            marks.append({"type": "backgroundColor", "attrs": {"color": node.bg_color}})
            return self.builder.text(text, marks)
        elif node.color:
            return self.builder.colored_text(text, node.color, node.marks)
        elif node.bg_color:
            return self.builder.highlighted_text(text, node.bg_color, node.marks)
        else:
            # Fallback to plain text
            return self.builder.text(text, node.marks)


class URLEmbedNodeConverter(TypedNodeConverter):
    """Converter for URL embed nodes."""

    node_type = "urlEmbed"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "URLEmbedNode") -> AdfNode:
        """Convert a URL embed node to ADF."""
        if node.embed_type == "video":
            # Use rich_media for video embeds
            return self.builder.rich_media(
                node.url,
                layout=node.layout,
                width=node.width,
                height=node.height
            )
        else:
            # Use embed_card for other URL embeds
            return self.builder.embed_card(node.url, layout=node.layout)


class TocNodeConverter(TypedNodeConverter):
    """Converter for Table of Contents nodes."""

    node_type = "toc"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "TocNode") -> AdfNode:
        """Convert a TOC node to ADF."""
        self.logger.debug(f"Converting TOC node with max_level={node.max_level}")

        return self.builder.toc_macro(
            max_level=node.max_level,
            min_level=node.min_level,
            include=node.include
        )


class StatusMacroNodeConverter(TypedNodeConverter):
    """Converter for status macro nodes."""

    node_type = "statusMacro"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "StatusMacroNode") -> AdfNode:
        """Convert a status macro node to ADF."""
        self.logger.debug(f"Converting status macro: {node.status_text} ({node.color})")

        return self.builder.status_macro(
            status_text=node.status_text,
            color=node.color,
            subtle=node.subtle
        )


class InfoNodeConverter(TypedNodeConverter):
    """Converter for info panel nodes."""

    node_type = "info"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "InfoNode") -> AdfNode:
        """Convert an info panel node to ADF."""
        self.logger.debug(f"Converting info panel with title: {node.title}")

        # Convert child nodes
        content_nodes = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    content_nodes.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in info panel: {e}"
                )

        # Ensure at least one content node for valid ADF
        if not content_nodes:
            self.logger.debug("No content in info panel, adding empty paragraph")
            content_nodes.append(self.builder.paragraph([]))

        return self.builder.info_panel(content=content_nodes, title=node.title)


class WarningNodeConverter(TypedNodeConverter):
    """Converter for warning panel nodes."""

    node_type = "warning"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "WarningNode") -> AdfNode:
        """Convert a warning panel node to ADF."""
        self.logger.debug(f"Converting warning panel with title: {node.title}")

        # Convert child nodes
        content_nodes = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    content_nodes.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in warning panel: {e}"
                )

        # Ensure at least one content node for valid ADF
        if not content_nodes:
            self.logger.debug("No content in warning panel, adding empty paragraph")
            content_nodes.append(self.builder.paragraph([]))

        return self.builder.warning_panel(
            content=content_nodes,
            title=node.title,
            icon=node.icon
        )


class NoteNodeConverter(TypedNodeConverter):
    """Converter for note panel nodes."""

    node_type = "note"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "NoteNode") -> AdfNode:
        """Convert a note panel node to ADF."""
        self.logger.debug(f"Converting note panel with title: {node.title}")

        # Convert child nodes
        content_nodes = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    content_nodes.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in note panel: {e}"
                )

        # Ensure at least one content node for valid ADF
        if not content_nodes:
            self.logger.debug("No content in note panel, adding empty paragraph")
            content_nodes.append(self.builder.paragraph([]))

        return self.builder.note_panel(
            content=content_nodes,
            title=node.title,
            icon=node.icon
        )


class ExcerptNodeConverter(TypedNodeConverter):
    """Converter for excerpt nodes."""

    node_type = "excerpt"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "ExcerptNode") -> AdfNode:
        """Convert an excerpt node to ADF."""
        self.logger.debug(f"Converting excerpt (hidden={node.hidden})")

        # Convert child nodes
        content_nodes = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    content_nodes.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in excerpt: {e}"
                )

        # Ensure at least one content node for valid ADF
        if not content_nodes:
            self.logger.debug("No content in excerpt, adding empty paragraph")
            content_nodes.append(self.builder.paragraph([]))

        return self.builder.excerpt_macro(content=content_nodes, hidden=node.hidden)


class ExpandMacroNodeConverter(TypedNodeConverter):
    """Converter for expand macro nodes."""

    node_type = "expandMacro"

    def __init__(self, builder: AdfBuilder, visitor: AdfNodeVisitor):
        """Initialize the converter."""
        self.builder = builder
        self.visitor = visitor
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "ExpandMacroNode") -> AdfNode:
        """Convert an expand macro node to ADF."""
        self.logger.debug(f"Converting expand macro with title: {node.title}")

        # Convert child nodes
        content_nodes = []
        for child in node.children:
            try:
                converted_child = self.visitor.visit(child)
                if converted_child:
                    content_nodes.append(converted_child)
            except Exception as e:
                self.logger.warning(
                    f"Failed to convert child node {child.type} in expand macro: {e}"
                )

        # Ensure at least one content node for valid ADF
        if not content_nodes:
            self.logger.debug("No content in expand macro, adding empty paragraph")
            content_nodes.append(self.builder.paragraph([]))

        return self.builder.expand_macro(content=content_nodes, title=node.title)


class CodeBlockMacroNodeConverter(TypedNodeConverter):
    """Converter for enhanced code block macro nodes."""

    node_type = "codeBlockMacro"

    def __init__(self, builder: AdfBuilder):
        """Initialize the converter."""
        self.builder = builder
        self.logger = logging.getLogger(__name__)

    def convert_typed(self, node: "CodeBlockMacroNode") -> AdfNode:
        """Convert a code block macro node to ADF."""
        self.logger.debug(
            f"Converting code block macro (language={node.language}, title={node.title})"
        )

        return self.builder.code_block_macro(
            code=node.content or "",
            language=node.language,
            title=node.title,
            linenumbers=node.linenumbers,
            theme=node.theme,
            collapse=node.collapse
        )