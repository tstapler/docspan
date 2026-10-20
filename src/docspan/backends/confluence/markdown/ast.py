"""
Abstract Syntax Tree nodes for Markdown parsing.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MarkdownNode:
    """
    Base class for nodes in the Markdown Abstract Syntax Tree (AST).

    Attributes:
        type: Node type
        content: Optional raw content
        children: List of child nodes
        attrs: Additional attributes
    """

    type: str = ""
    content: Optional[str] = None
    children: List["MarkdownNode"] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TextNode(MarkdownNode):
    """
    Node for plain text.

    Attributes:
        content: Text content
        marks: Text formatting marks (bold, italic, etc.)
    """

    marks: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "text"


@dataclass
class HeadingNode(MarkdownNode):
    """
    Node for headings.

    Attributes:
        level: Heading level (1-6)
    """

    level: int = 1

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "heading"


@dataclass
class ParagraphNode(MarkdownNode):
    """Node for paragraphs."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "paragraph"


@dataclass
class ListItemNode(MarkdownNode):
    """
    Node for list items.

    Attributes:
        bullet: Bullet character or number
    """

    bullet: str = "*"

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "listItem"


@dataclass
class BulletListNode(MarkdownNode):
    """Node for bullet lists."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "bulletList"


@dataclass
class OrderedListNode(MarkdownNode):
    """Node for ordered lists."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "orderedList"


@dataclass
class CodeBlockNode(MarkdownNode):
    """
    Node for code blocks.

    Attributes:
        language: Programming language for syntax highlighting
    """

    language: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "codeBlock"


@dataclass
class InlineCodeNode(MarkdownNode):
    """Node for inline code."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "inlineCode"


@dataclass
class BlockquoteNode(MarkdownNode):
    """Node for blockquotes."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "blockquote"


@dataclass
class TableNode(MarkdownNode):
    """
    Node for tables.

    Attributes:
        headers: Table headers
        rows: Table rows
    """

    headers: List[str] = field(default_factory=list)
    rows: List[List[MarkdownNode]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "table"


@dataclass
class LinkNode(MarkdownNode):
    """
    Node for links.

    Attributes:
        url: Link URL
        title: Link title
    """

    url: str = ""
    title: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "link"


@dataclass
class ImageNode(MarkdownNode):
    """
    Node for images.

    Attributes:
        src: Image source URL or Confluence file ID
        alt: Alternative text
        title: Image title
        width: Image width (pixels or percentage)
        height: Image height (pixels)
        layout: Layout option (center, wide, full-width, wrap-left, wrap-right)
        is_confluence_attachment: True if using Confluence file ID instead of external URL
        collection: Confluence Media Services collection name (for Confluence attachments)
        occurrence_key: Confluence occurrence key (for deletion support)
    """

    src: str = ""
    alt: str = ""
    title: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    layout: Optional[str] = None  # center, wide, full-width, wrap-left, wrap-right
    is_confluence_attachment: bool = False
    collection: Optional[str] = None
    occurrence_key: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "image"


@dataclass
class MediaGroupNode(MarkdownNode):
    """
    Node for media groups (image galleries).

    A container for multiple media items displayed together, commonly used
    for image galleries or multiple file attachments.

    Attributes:
        children: List of ImageNode items in the group
    """

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "mediaGroup"


@dataclass
class WikiLinkNode(MarkdownNode):
    """
    Node for wiki-style links ([[page]] or [[page|title]]).

    Attributes:
        target: Link target (page name)
        display: Display text
    """

    target: str = ""
    display: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "wikiLink"


@dataclass
class MentionNode(MarkdownNode):
    """
    Node for user mentions (@username).

    Attributes:
        username: Username being mentioned (without @ prefix)
        user_id: Confluence user ID (optional, resolved during conversion)
        text: Display text for the mention (defaults to @username)
    """

    username: str = ""
    user_id: Optional[str] = None
    text: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "mention"
        # Set default text if not provided
        if not self.text and self.username:
            self.text = f"@{self.username}"


@dataclass
class EmojiNode(MarkdownNode):
    """
    Node for emoji expressions (:emoji_name:).

    Attributes:
        short_name: Emoji short name (e.g., "smile", "tada", "rocket")
        emoji_id: Confluence emoji ID (optional, resolved during conversion)
        text: Fallback text representation (defaults to :short_name:)
    """

    short_name: str = ""
    emoji_id: Optional[str] = None
    text: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "emoji"
        # Set default text if not provided
        if not self.text and self.short_name:
            self.text = f":{self.short_name}:"


@dataclass
class ExpandNode(MarkdownNode):
    """
    Node for collapsible/expandable sections.

    Converts HTML <details><summary> tags to Confluence expand nodes.

    Attributes:
        title: Title shown in the expand header (from <summary> tag)
        children: Child nodes contained within the expand section
    """

    title: str = ""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "expand"


@dataclass
class HorizontalRuleNode(MarkdownNode):
    """Node for horizontal rules."""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "horizontalRule"


@dataclass
class MermaidNode(MarkdownNode):
    """
    Node for Mermaid diagrams.

    Attributes:
        code: Mermaid diagram code
    """

    code: str = ""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "mermaid"


@dataclass
class TaskListNode(MarkdownNode):
    """
    Node for task lists (checklists).

    Converts GitHub-style task lists to Confluence task lists.

    Attributes:
        children: List of TaskItemNode items
    """

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "taskList"


@dataclass
class TaskItemNode(MarkdownNode):
    """
    Node for individual task items in a task list.

    Attributes:
        checked: Whether the task is checked/completed
        children: Content of the task item (typically paragraphs)
    """

    checked: bool = False

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "taskItem"


@dataclass
class StatusBadgeNode(MarkdownNode):
    """
    Node for status badges/lozenges.

    Converts [!STATUS] syntax to Confluence status lozenges.

    Attributes:
        text: Status text (e.g., "DONE", "IN PROGRESS")
        color: Status color (neutral, blue, green, yellow, red, purple)
    """

    text: str = ""
    color: str = "neutral"

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "statusBadge"


@dataclass
class DateNode(MarkdownNode):
    """
    Node for date fields.

    Converts ISO date strings to Confluence date nodes.

    Attributes:
        timestamp: Unix timestamp in milliseconds
        date_string: Original date string (for display)
    """

    timestamp: int = 0
    date_string: str = ""

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "date"


@dataclass
class LayoutSectionNode(MarkdownNode):
    """
    Node for multi-column layout sections.

    Converts ::: columns syntax to Confluence layout sections.

    Attributes:
        children: List of LayoutColumnNode items
    """

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "layoutSection"


@dataclass
class LayoutColumnNode(MarkdownNode):
    """
    Node for individual columns within a layout section.

    Attributes:
        width: Column width percentage (e.g., 50 for 50%)
        children: Content within the column
    """

    width: Optional[int] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "layoutColumn"


@dataclass
class HighlightedTextNode(MarkdownNode):
    """
    Node for highlighted/marked text.

    Converts ==text== syntax to highlighted text with background color.

    Attributes:
        content: Text content
        bg_color: Background color (default: yellow #ffff00)
        marks: Additional text formatting marks
    """

    bg_color: str = "#ffff00"
    marks: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "highlightedText"


@dataclass
class ColoredTextNode(MarkdownNode):
    """
    Node for colored text.

    Converts <mark style="color:..."> syntax to colored text.

    Attributes:
        content: Text content
        color: Text color (hex code or CSS color name)
        bg_color: Optional background color
        marks: Additional text formatting marks
    """

    color: Optional[str] = None
    bg_color: Optional[str] = None
    marks: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "coloredText"


@dataclass
class URLEmbedNode(MarkdownNode):
    """
    Node for embedded URLs (videos, rich content).

    Auto-detects URLs and creates appropriate embed cards.

    Attributes:
        url: URL to embed
        embed_type: Type of embed (video, card)
        layout: Layout mode (center, wide, full-width)
        width: Optional width in pixels
        height: Optional height in pixels
    """

    url: str = ""
    embed_type: str = "card"  # video, card
    layout: str = "center"
    width: Optional[int] = None
    height: Optional[int] = None

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "urlEmbed"


@dataclass
class ExtensionNode(MarkdownNode):
    """
    Base class for Confluence macro/extension nodes.

    Extensions represent Confluence-specific functionality that goes beyond
    standard markdown, such as TOC, info panels, status lozenges, etc.

    Attributes:
        extension_key: Macro name (e.g., "toc", "excerpt", "info")
        parameters: Macro parameters as key-value pairs
        extension_type: Confluence extension type (default: com.atlassian.confluence.macro.core)
    """

    extension_key: str = ""
    parameters: Dict[str, str] = field(default_factory=dict)
    extension_type: str = "com.atlassian.confluence.macro.core"

    def __post_init__(self) -> None:
        """Initialize type."""
        self.type = "extension"


@dataclass
class TocNode(ExtensionNode):
    """
    Node for Table of Contents macro.

    Generates an automatic table of contents from page headings.

    Attributes:
        max_level: Maximum heading level to include (1-7, default: 7)
        min_level: Minimum heading level to include (1-7, default: 1)
        include: Regex pattern for headings to include
        exclude: Regex pattern for headings to exclude
        toc_type: Display style - "list" or "flat"
        printable: Include printable version
        separator: Separator for flat type
    """

    max_level: int = 7
    min_level: int = 1
    include: Optional[str] = None
    exclude: Optional[str] = None
    toc_type: str = "list"  # list or flat
    printable: bool = True
    separator: str = "dots"

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "toc"
        self.extension_key = "toc"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.max_level != 7:
            self.parameters["maxLevel"] = str(self.max_level)
        if self.min_level != 1:
            self.parameters["minLevel"] = str(self.min_level)
        if self.include:
            self.parameters["include"] = self.include
        if self.exclude:
            self.parameters["exclude"] = self.exclude
        if self.toc_type != "list":
            self.parameters["type"] = self.toc_type
        if not self.printable:
            self.parameters["printable"] = "false"
        if self.separator != "dots":
            self.parameters["separator"] = self.separator


@dataclass
class ExcerptNode(ExtensionNode):
    """
    Node for Excerpt macro.

    Defines a page excerpt that appears in search results and page listings.
    The excerpt content is stored in the children list.

    Attributes:
        hidden: Hide excerpt from page content (only show in listings)
        atlassian_macro_output_type: Output type (BLOCK or INLINE)
    """

    hidden: bool = False
    atlassian_macro_output_type: str = "BLOCK"

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "excerpt"
        self.extension_key = "excerpt"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.hidden:
            self.parameters["hidden"] = "true"
        if self.atlassian_macro_output_type != "BLOCK":
            self.parameters["atlassian-macro-output-type"] = self.atlassian_macro_output_type


@dataclass
class InfoNode(ExtensionNode):
    """
    Node for Info panel macro.

    Creates a colored information panel for highlighting important content.
    Content is stored in the children list.

    Attributes:
        title: Panel title (optional)
        icon: Whether to show icon (default: true)
    """

    title: Optional[str] = None
    icon: bool = True

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "info"
        self.extension_key = "info"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.title:
            self.parameters["title"] = self.title
        if not self.icon:
            self.parameters["icon"] = "false"


@dataclass
class WarningNode(ExtensionNode):
    """
    Node for Warning panel macro.

    Creates a colored warning panel for highlighting cautions.
    Content is stored in the children list.

    Attributes:
        title: Panel title (optional)
        icon: Whether to show icon (default: true)
    """

    title: Optional[str] = None
    icon: bool = True

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "warning"
        self.extension_key = "warning"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.title:
            self.parameters["title"] = self.title
        if not self.icon:
            self.parameters["icon"] = "false"


@dataclass
class NoteNode(ExtensionNode):
    """
    Node for Note panel macro.

    Creates a colored note panel for additional information.
    Content is stored in the children list.

    Attributes:
        title: Panel title (optional)
        icon: Whether to show icon (default: true)
    """

    title: Optional[str] = None
    icon: bool = True

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "note"
        self.extension_key = "note"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.title:
            self.parameters["title"] = self.title
        if not self.icon:
            self.parameters["icon"] = "false"


@dataclass
class PanelNode(ExtensionNode):
    """
    Node for generic Panel macro.

    Creates a generic panel (can also be used for tip, success, error panels).
    Content is stored in the children list.

    Attributes:
        panel_type: Type of panel (info, warning, note, tip, success, error)
        title: Panel title (optional)
        icon: Whether to show icon (default: true)
    """

    panel_type: str = "info"
    title: Optional[str] = None
    icon: bool = True

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "panel"
        self.extension_key = self.panel_type  # Use panel_type as extension_key

        # Build parameters dict from attributes
        self.parameters = {}
        if self.title:
            self.parameters["title"] = self.title
        if not self.icon:
            self.parameters["icon"] = "false"


@dataclass
class ExpandMacroNode(ExtensionNode):
    """
    Node for Expand macro (collapsible section).

    Creates a collapsible content section with a title.
    Content is stored in the children list.

    Attributes:
        title: Title shown in the expand header
    """

    title: str = ""

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "expandMacro"
        self.extension_key = "expand"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.title:
            self.parameters["title"] = self.title


@dataclass
class StatusMacroNode(ExtensionNode):
    """
    Node for Status lozenge macro.

    Creates an inline status badge with color and text.

    Attributes:
        status_text: Status text to display
        color: Status color (Green, Yellow, Red, Blue, Grey)
        subtle: Use subtle style (default: false)
    """

    status_text: str = ""
    color: str = "Grey"
    subtle: bool = False

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "statusMacro"
        self.extension_key = "status"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.status_text:
            self.parameters["title"] = self.status_text
        if self.color:
            self.parameters["colour"] = self.color  # Confluence uses British spelling
        if self.subtle:
            self.parameters["subtle"] = "true"


@dataclass
class CodeBlockMacroNode(ExtensionNode):
    """
    Node for enhanced Code Block macro.

    Creates a code block with enhanced features like title and line numbers.
    Code content is stored in the children list or content attribute.

    Attributes:
        language: Programming language for syntax highlighting
        title: Code block title
        linenumbers: Show line numbers (default: false)
        theme: Syntax highlighting theme
        collapse: Collapsible code block (default: false)
    """

    language: Optional[str] = None
    title: Optional[str] = None
    linenumbers: bool = False
    theme: Optional[str] = None
    collapse: bool = False

    def __post_init__(self) -> None:
        """Initialize type and extension_key."""
        self.type = "codeBlockMacro"
        self.extension_key = "code"

        # Build parameters dict from attributes
        self.parameters = {}
        if self.language:
            self.parameters["language"] = self.language
        if self.title:
            self.parameters["title"] = self.title
        if self.linenumbers:
            self.parameters["linenumbers"] = "true"
        if self.theme:
            self.parameters["theme"] = self.theme
        if self.collapse:
            self.parameters["collapse"] = "true"
