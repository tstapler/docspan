"""
Atlassian Document Format node definitions.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AdfNode:
    """
    Represents a node in the ADF structure.

    Attributes:
        type: Node type
        attrs: Node attributes
        content: Child nodes
        marks: Formatting marks for text nodes
        text: Text content for text nodes
        storage_format_html: Optional HTML content for storage format
    """

    type: str
    attrs: Dict[str, Any] = field(default_factory=dict)
    content: List["AdfNode"] = field(default_factory=list)
    marks: List[Dict[str, Any]] = field(default_factory=list)
    text: Optional[str] = None
    storage_format_html: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the node to a dictionary for JSON serialization.

        Returns:
            Dictionary representation of the node
        """
        result = {"type": self.type}

        if self.attrs:
            result["attrs"] = self.attrs

        if self.content:
            result["content"] = [node.to_dict() for node in self.content]

        if self.marks:
            result["marks"] = self.marks

        if self.text is not None:
            result["text"] = self.text
            
        # Include storage_format_html if present
        if self.storage_format_html is not None:
            if "attrs" not in result:
                result["attrs"] = {}
            result["attrs"]["storage_format_html"] = self.storage_format_html

        return result


class AdfBuilder:
    """
    Builder for creating ADF documents.
    """

    @staticmethod
    def document(content: List[AdfNode]) -> Dict[str, Any]:
        """
        Create an ADF document.

        Args:
            content: Document content nodes

        Returns:
            ADF document dictionary
        """
        return {"version": 1, "type": "doc", "content": [node.to_dict() for node in content]}

    @staticmethod
    def paragraph(content: List[AdfNode] = None) -> AdfNode:
        """
        Create a paragraph node.

        Args:
            content: Paragraph content

        Returns:
            Paragraph node
        """
        return AdfNode(type="paragraph", content=content or [])

    @staticmethod
    def text(text: str, marks: List[Dict[str, Any]] = None) -> AdfNode:
        """
        Create a text node.

        Args:
            text: Text content
            marks: Text formatting marks

        Returns:
            Text node
        """
        return AdfNode(type="text", text=text, marks=marks or [])

    @staticmethod
    def heading(content: List[AdfNode], level: int) -> AdfNode:
        """
        Create a heading node.

        Args:
            content: Heading content
            level: Heading level (1-6)

        Returns:
            Heading node
        """
        return AdfNode(type="heading", attrs={"level": level}, content=content)

    @staticmethod
    def code_block(content: str, language: Optional[str] = None) -> AdfNode:
        """
        Create a code block node.

        Args:
            content: Code content
            language: Programming language

        Returns:
            Code block node
        """
        attrs = {}
        if language:
            attrs["language"] = language

        return AdfNode(type="codeBlock", attrs=attrs, content=[AdfNode(type="text", text=content)])

    @staticmethod
    def bullet_list(items: List[AdfNode]) -> AdfNode:
        """
        Create a bullet list node.

        Args:
            items: List items

        Returns:
            Bullet list node
        """
        return AdfNode(type="bulletList", content=items)

    @staticmethod
    def ordered_list(items: List[AdfNode]) -> AdfNode:
        """
        Create an ordered list node.

        Args:
            items: List items

        Returns:
            Ordered list node
        """
        return AdfNode(type="orderedList", content=items)

    @staticmethod
    def list_item(content: List[AdfNode]) -> AdfNode:
        """
        Create a list item node.

        Args:
            content: List item content

        Returns:
            List item node
        """
        return AdfNode(type="listItem", content=content)

    @staticmethod
    def blockquote(content: List[AdfNode]) -> AdfNode:
        """
        Create a blockquote node.

        Args:
            content: Blockquote content

        Returns:
            Blockquote node
        """
        return AdfNode(type="blockquote", content=content)

    @staticmethod
    def panel(content: List[AdfNode], panel_type: str) -> AdfNode:
        """
        Create a panel node.

        Args:
            content: Panel content
            panel_type: Panel type (note, info, warning, error, success)

        Returns:
            Panel node
        """
        return AdfNode(type="panel", attrs={"panelType": panel_type}, content=content)

    @staticmethod
    def table(rows: List[List[AdfNode]], headers: bool = False) -> AdfNode:
        """
        Create a table node.

        Args:
            rows: Table rows
            headers: Whether the first row contains headers

        Returns:
            Table node
        """
        attrs = {}
        if headers:
            attrs["isNumberColumnEnabled"] = False
            attrs["layout"] = "default"

        table_rows = []
        for row in rows:
            cells = []
            for cell_content in row:
                cells.append(AdfNode(type="tableCell", content=[cell_content]))
            table_rows.append(AdfNode(type="tableRow", content=cells))

        return AdfNode(type="table", attrs=attrs, content=table_rows)

    @staticmethod
    def link(text: str, href: str, title: Optional[str] = None, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create a link node.

        Args:
            text: Link text
            href: Link URL
            title: Link title
            additional_marks: Additional marks to apply (e.g., strong, em) from child nodes

        Returns:
            Text node with link mark and any additional marks
        """
        attrs = {"href": href}
        if title:
            attrs["title"] = title

        node = AdfNode(type="text", text=text)

        # Start with any additional marks (e.g., strong, em)
        marks = list(additional_marks) if additional_marks else []
        # Add the link mark
        marks.append({"type": "link", "attrs": attrs})

        node.marks = marks
        return node

    @staticmethod
    def image(
        url: str,
        alt: str = "",
        title: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> AdfNode:
        """
        Create an image node.

        Args:
            url: Image URL
            alt: Alternative text
            title: Image title
            width: Image width in pixels
            height: Image height in pixels

        Returns:
            Media node for image

        Example:
            >>> AdfBuilder.image("https://example.com/img.jpg", "Photo")
            >>> AdfBuilder.image("https://example.com/img.jpg", "Photo", width=800, height=600)

        Notes:
            - For layout control (center, wide, wrap), wrap in media_single()
            - Width and height are optional display dimensions
            - Use media_group() for image galleries
        """
        logger.debug(f"Creating image node with URL: {url}")

        if not url:
            logger.warning("Empty URL provided for image node, creating placeholder")
            return AdfNode(type="paragraph", content=[
                AdfNode(type="text", text="[Image placeholder - URL was empty]")
            ])

        attrs = {"type": "external", "url": url}

        if alt:
            attrs["alt"] = alt
        if title:
            attrs["title"] = title
        if width is not None:
            attrs["width"] = width
        if height is not None:
            attrs["height"] = height

        logger.debug(f"Image node attributes: {attrs}")
        return AdfNode(type="media", attrs=attrs)

    @staticmethod
    def confluence_image(
        file_id: str,
        alt: str = "",
        title: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        collection: str = "contentId",
        occurrence_key: Optional[str] = None
    ) -> AdfNode:
        """
        Create a Confluence image node.

        Args:
            file_id: Confluence file ID
            alt: Alternative text
            title: Image title
            width: Image width in pixels
            height: Image height in pixels
            collection: Confluence Media Services collection name (default: "contentId")
            occurrence_key: Occurrence key for deletion support

        Returns:
            Media node for Confluence image

        Example:
            >>> AdfBuilder.confluence_image("abc123", "Diagram")
            >>> AdfBuilder.confluence_image("abc123", "Diagram", width=800, height=600)

        Notes:
            - Uses Confluence file ID instead of external URL
            - For layout control, wrap in media_single()
            - occurrence_key enables proper deletion tracking
        """
        attrs = {"type": "file", "id": file_id, "collection": collection}

        if alt:
            attrs["alt"] = alt
        if title:
            attrs["title"] = title
        if width is not None:
            attrs["width"] = width
        if height is not None:
            attrs["height"] = height
        if occurrence_key is not None:
            attrs["occurrenceKey"] = occurrence_key

        return AdfNode(type="media", attrs=attrs)

    @staticmethod
    def horizontal_rule() -> AdfNode:
        """
        Create a horizontal rule node.

        Returns:
            Rule node
        """
        return AdfNode(type="rule")

    @staticmethod
    def inline_card(url: str, title: Optional[str] = None, confluence_metadata: Optional[Dict[str, Any]] = None) -> AdfNode:
        """
        Create an inline smart card node.

        Inline cards are used for wiki links and page references within text.

        Args:
            url: URL to link to (Confluence page URL or external URL)
            title: Optional display title (if different from page title)
            confluence_metadata: Optional Confluence-specific metadata (linkType, contentTitle, etc.)

        Returns:
            InlineCard node

        Example:
            >>> AdfBuilder.inline_card(
            ...     url="https://example.atlassian.net/wiki/spaces/SPACE/pages/12345/Page+Title",
            ...     title="Page Title",
            ...     confluence_metadata={
            ...         "linkType": "page",
            ...         "contentTitle": "Page Title",
            ...         "isRenamedTitle": True
            ...     }
            ... )
        """
        attrs = {"url": url}

        # Add Confluence metadata if provided
        if confluence_metadata:
            attrs["__confluenceMetadata"] = confluence_metadata

        return AdfNode(type="inlineCard", attrs=attrs)

    @staticmethod
    def block_card(url: str, datasource: Optional[Dict[str, Any]] = None) -> AdfNode:
        """
        Create a block smart card node.

        Block cards are used for rich data sources like JIRA queries, dashboards, etc.
        They appear as standalone blocks rather than inline elements.

        Args:
            url: URL to the data source
            datasource: Optional datasource configuration (for JIRA queries, etc.)

        Returns:
            BlockCard node

        Example:
            >>> AdfBuilder.block_card(
            ...     url="https://example.atlassian.net/issues/?jql=project=PROJ",
            ...     datasource={
            ...         "id": "datasource-id",
            ...         "parameters": {"jql": "project = PROJ"},
            ...         "views": [{"type": "table", "properties": {...}}]
            ...     }
            ... )
        """
        attrs = {"url": url}

        # Add datasource configuration if provided
        if datasource:
            attrs["datasource"] = datasource

        return AdfNode(type="blockCard", attrs=attrs)

    @staticmethod
    def mention(user_id: str, text: str, access_level: Optional[str] = None) -> AdfNode:
        """
        Create a user mention node.

        Mention nodes are used to reference Confluence users in content.

        Args:
            user_id: Confluence user ID (account ID)
            text: Display text for the mention (e.g., "@John Doe")
            access_level: Optional access level (e.g., "CONTAINER", "APPLICATION")

        Returns:
            Mention node

        Example:
            >>> AdfBuilder.mention(
            ...     user_id="6400c3ebc6e77744a1dd3cec",
            ...     text="@John Doe"
            ... )

        Notes:
            - user_id should be the Confluence account ID (obtained via user resolution)
            - text typically includes @ prefix for display
            - access_level can be used for permission-based mentions
        """
        attrs = {
            "id": user_id,
            "text": text
        }

        # Add access level if provided
        if access_level:
            attrs["accessLevel"] = access_level

        return AdfNode(type="mention", attrs=attrs)

    @staticmethod
    def emoji(short_name: str, emoji_id: Optional[str] = None, text: Optional[str] = None) -> AdfNode:
        """
        Create an emoji node.

        Emoji nodes are used to display emoji expressions in content.

        Args:
            short_name: Emoji short name with colons (e.g., ":smile:", ":tada:")
            emoji_id: Unicode emoji ID/codepoint (e.g., "1f604" for smile)
            text: Unicode emoji character (e.g., "😄")

        Returns:
            Emoji node

        Example:
            >>> AdfBuilder.emoji(
            ...     short_name=":smile:",
            ...     emoji_id="1f604",
            ...     text="😄"
            ... )

        Notes:
            - short_name should include colons for consistency
            - emoji_id is the unicode codepoint (hex)
            - text is the actual emoji character
            - If emoji_id or text not provided, uses short_name as fallback
        """
        attrs = {"shortName": short_name}

        # Add emoji ID if provided
        if emoji_id:
            attrs["id"] = emoji_id

        # Add text (emoji character) if provided, otherwise use short_name
        if text:
            attrs["text"] = text
        else:
            # Fallback to short_name if text not provided
            attrs["text"] = short_name

        return AdfNode(type="emoji", attrs=attrs)

    @staticmethod
    def expand(title: str, content: List[AdfNode]) -> AdfNode:
        """
        Create an expand (collapsible) node.

        Expand nodes are used to create collapsible sections that can be
        expanded or collapsed by the user.

        Args:
            title: Title shown in the expand header
            content: List of child nodes contained within the expand section

        Returns:
            Expand node

        Example:
            >>> AdfBuilder.expand(
            ...     title="Click to expand",
            ...     content=[
            ...         AdfBuilder.paragraph([AdfBuilder.text("Hidden content")])
            ...     ]
            ... )

        Notes:
            - Title can be empty string for expand without title
            - Content should be a list of block nodes (paragraphs, lists, etc.)
            - At least one content node is required for valid ADF
        """
        attrs = {"title": title}
        return AdfNode(type="expand", attrs=attrs, content=content)

    @staticmethod
    def hard_break() -> AdfNode:
        """
        Create a hard break (line break) node.

        Hard break nodes insert an explicit line break within text content,
        similar to <br> in HTML.

        Returns:
            HardBreak node

        Example:
            >>> AdfBuilder.paragraph([
            ...     AdfBuilder.text("First line"),
            ...     AdfBuilder.hard_break(),
            ...     AdfBuilder.text("Second line")
            ... ])

        Notes:
            - Used for explicit line breaks within paragraphs
            - Different from starting a new paragraph (which adds spacing)
        """
        return AdfNode(type="hardBreak")

    @staticmethod
    def placeholder(text: str) -> AdfNode:
        """
        Create a placeholder node.

        Placeholder nodes display placeholder text in the editor,
        typically used for template fields or content hints.

        Args:
            text: Placeholder text to display

        Returns:
            Placeholder node

        Example:
            >>> AdfBuilder.placeholder("Enter description here...")

        Notes:
            - Appears as grayed-out text in the editor
            - Often used in templates or forms
        """
        return AdfNode(type="placeholder", attrs={"text": text})

    @staticmethod
    def status(text: str, color: str = "neutral") -> AdfNode:
        """
        Create a status lozenge node.

        Status nodes display colored status badges/lozenges, commonly used
        for indicating task states, priorities, or other status information.

        Args:
            text: Status text to display
            color: Status color (neutral, blue, green, yellow, red, purple)

        Returns:
            Status node

        Example:
            >>> AdfBuilder.status("In Progress", "blue")
            >>> AdfBuilder.status("Complete", "green")
            >>> AdfBuilder.status("Blocked", "red")

        Notes:
            - Valid colors: neutral (gray), blue, green, yellow, red, purple
            - Status badges are inline elements
            - Commonly used in project management pages
        """
        valid_colors = {"neutral", "blue", "green", "yellow", "red", "purple"}
        if color not in valid_colors:
            logging.warning(f"Invalid status color '{color}', using 'neutral'. Valid colors: {valid_colors}")
            color = "neutral"

        return AdfNode(type="status", attrs={"text": text, "color": color})

    @staticmethod
    def date(timestamp: int) -> AdfNode:
        """
        Create a date node.

        Date nodes display formatted dates in Confluence, with the timestamp
        stored as Unix milliseconds.

        Args:
            timestamp: Unix timestamp in milliseconds

        Returns:
            Date node

        Example:
            >>> import time
            >>> timestamp_ms = int(time.time() * 1000)
            >>> AdfBuilder.date(timestamp_ms)

        Notes:
            - Timestamp must be in milliseconds (not seconds)
            - Confluence will display the date according to user preferences
            - Dates are inline elements
        """
        return AdfNode(type="date", attrs={"timestamp": str(timestamp)})

    # Mark Helper Methods

    @staticmethod
    def colored_text(text: str, color: str, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create text with a color mark.

        Args:
            text: Text content
            color: Text color (hex color code like "#ff0000" or CSS color name)
            additional_marks: Additional marks to apply (e.g., strong, em)

        Returns:
            Text node with textColor mark

        Example:
            >>> AdfBuilder.colored_text("Red text", "#ff0000")
            >>> AdfBuilder.colored_text("Bold red text", "#ff0000",
            ...     additional_marks=[{"type": "strong"}])

        Notes:
            - Color should be a valid hex code (#RRGGBB) or CSS color name
            - Multiple marks can be combined
        """
        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "textColor", "attrs": {"color": color}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def highlighted_text(text: str, bg_color: str, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create text with a background color mark.

        Args:
            text: Text content
            bg_color: Background color (hex color code like "#ffff00" or CSS color name)
            additional_marks: Additional marks to apply (e.g., strong, em)

        Returns:
            Text node with backgroundColor mark

        Example:
            >>> AdfBuilder.highlighted_text("Highlighted text", "#ffff00")
            >>> AdfBuilder.highlighted_text("Bold highlighted", "#ffff00",
            ...     additional_marks=[{"type": "strong"}])

        Notes:
            - Color should be a valid hex code (#RRGGBB) or CSS color name
            - Commonly used for highlighting important information
        """
        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "backgroundColor", "attrs": {"color": bg_color}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def aligned_text(text: str, align: str, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create text with an alignment mark.

        Args:
            text: Text content
            align: Text alignment (start, center, end)
            additional_marks: Additional marks to apply

        Returns:
            Text node with alignment mark

        Example:
            >>> AdfBuilder.aligned_text("Centered text", "center")
            >>> AdfBuilder.aligned_text("Right-aligned bold", "end",
            ...     additional_marks=[{"type": "strong"}])

        Notes:
            - Valid alignments: start (left), center, end (right)
            - Alignment applies within the parent block
        """
        valid_alignments = {"start", "center", "end"}
        if align not in valid_alignments:
            logging.warning(f"Invalid alignment '{align}', using 'start'. Valid: {valid_alignments}")
            align = "start"

        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "alignment", "attrs": {"align": align}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def indented_text(text: str, level: int = 1, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create text with an indentation mark.

        Args:
            text: Text content
            level: Indentation level (1-6)
            additional_marks: Additional marks to apply

        Returns:
            Text node with indentation mark

        Example:
            >>> AdfBuilder.indented_text("Indented once", 1)
            >>> AdfBuilder.indented_text("Double indent", 2)

        Notes:
            - Valid levels: 1-6
            - Each level typically adds ~30px of indentation
        """
        if not 1 <= level <= 6:
            logger.warning(f"Invalid indentation level {level}, clamping to 1-6")
            level = max(1, min(6, level))

        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "indentation", "attrs": {"level": level}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def subscript(text: str, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create subscript text.

        Args:
            text: Text content
            additional_marks: Additional marks to apply

        Returns:
            Text node with subsup mark (subscript)

        Example:
            >>> AdfBuilder.paragraph([
            ...     AdfBuilder.text("H"),
            ...     AdfBuilder.subscript("2"),
            ...     AdfBuilder.text("O")
            ... ])  # Creates H₂O

        Notes:
            - Used for chemical formulas, mathematical notation, etc.
        """
        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "subsup", "attrs": {"type": "sub"}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def superscript(text: str, additional_marks: Optional[List[Dict[str, Any]]] = None) -> AdfNode:
        """
        Create superscript text.

        Args:
            text: Text content
            additional_marks: Additional marks to apply

        Returns:
            Text node with subsup mark (superscript)

        Example:
            >>> AdfBuilder.paragraph([
            ...     AdfBuilder.text("E=mc"),
            ...     AdfBuilder.superscript("2")
            ... ])  # Creates E=mc²

        Notes:
            - Used for exponents, footnotes, mathematical notation, etc.
        """
        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "subsup", "attrs": {"type": "sup"}})
        return AdfNode(type="text", text=text, marks=marks)

    # Task Management Nodes

    @staticmethod
    def task_list(items: List[AdfNode], local_id: Optional[str] = None) -> AdfNode:
        """
        Create a task list (checklist) node.

        Task lists contain task items that can be checked/unchecked,
        commonly used for to-do lists and checklists.

        Args:
            items: List of task items
            local_id: Optional local identifier for the task list

        Returns:
            TaskList node

        Example:
            >>> AdfBuilder.task_list([
            ...     AdfBuilder.task_item("Buy groceries", state="TODO"),
            ...     AdfBuilder.task_item("Finish report", state="DONE")
            ... ])

        Notes:
            - Items should be TaskItem nodes created with task_item()
            - local_id is used for tracking and synchronization
        """
        attrs = {}
        if local_id:
            attrs["localId"] = local_id

        return AdfNode(type="taskList", attrs=attrs if attrs else {}, content=items)

    @staticmethod
    def task_item(content: List[AdfNode], state: str = "TODO", local_id: Optional[str] = None) -> AdfNode:
        """
        Create a task item node.

        Task items are individual items within a task list that can be
        marked as done or todo.

        Args:
            content: Task item content (typically paragraphs with text)
            state: Task state ("TODO" or "DONE")
            local_id: Optional local identifier for the task item

        Returns:
            TaskItem node

        Example:
            >>> AdfBuilder.task_item(
            ...     [AdfBuilder.paragraph([AdfBuilder.text("Complete task")])],
            ...     state="TODO"
            ... )
            >>> AdfBuilder.task_item(
            ...     [AdfBuilder.paragraph([AdfBuilder.text("Finished task")])],
            ...     state="DONE",
            ...     local_id="task-001"
            ... )

        Notes:
            - Valid states: TODO, DONE
            - local_id is recommended for persistent task tracking
            - Content should be block nodes (paragraphs, nested lists, etc.)
        """
        valid_states = {"TODO", "DONE"}
        if state not in valid_states:
            logging.warning(f"Invalid task state '{state}', using 'TODO'. Valid states: {valid_states}")
            state = "TODO"

        attrs = {"state": state}
        if local_id:
            attrs["localId"] = local_id

        return AdfNode(type="taskItem", attrs=attrs, content=content)

    @staticmethod
    def decision_list(items: List[AdfNode], local_id: Optional[str] = None) -> AdfNode:
        """
        Create a decision list node.

        Decision lists contain decision items for tracking decisions
        and their states.

        Args:
            items: List of decision items
            local_id: Optional local identifier for the decision list

        Returns:
            DecisionList node

        Example:
            >>> AdfBuilder.decision_list([
            ...     AdfBuilder.decision_item("Approve budget", state="DECIDED"),
            ...     AdfBuilder.decision_item("Choose vendor", state="PENDING")
            ... ])

        Notes:
            - Items should be DecisionItem nodes
            - Used for decision tracking and documentation
        """
        attrs = {}
        if local_id:
            attrs["localId"] = local_id

        return AdfNode(type="decisionList", attrs=attrs if attrs else {}, content=items)

    @staticmethod
    def decision_item(content: List[AdfNode], state: str = "PENDING", local_id: Optional[str] = None) -> AdfNode:
        """
        Create a decision item node.

        Decision items represent individual decisions within a decision list.

        Args:
            content: Decision item content (typically paragraphs with text)
            state: Decision state ("DECIDED" or "PENDING")
            local_id: Optional local identifier for the decision item

        Returns:
            DecisionItem node

        Example:
            >>> AdfBuilder.decision_item(
            ...     [AdfBuilder.paragraph([AdfBuilder.text("Approve new feature")])],
            ...     state="DECIDED"
            ... )

        Notes:
            - Valid states: DECIDED, PENDING
            - local_id is recommended for tracking
        """
        valid_states = {"DECIDED", "PENDING"}
        if state not in valid_states:
            logging.warning(f"Invalid decision state '{state}', using 'PENDING'. Valid states: {valid_states}")
            state = "PENDING"

        attrs = {"state": state}
        if local_id:
            attrs["localId"] = local_id

        return AdfNode(type="decisionItem", attrs=attrs, content=content)

    # Media Nodes

    @staticmethod
    def media_group(media_items: List[AdfNode]) -> AdfNode:
        """
        Create a media group node.

        Media groups contain multiple media items displayed together,
        commonly used for image galleries or multiple file attachments.

        Args:
            media_items: List of media nodes (created with image() or confluence_image())

        Returns:
            MediaGroup node

        Example:
            >>> AdfBuilder.media_group([
            ...     AdfBuilder.image("https://example.com/image1.jpg", "Image 1"),
            ...     AdfBuilder.image("https://example.com/image2.jpg", "Image 2")
            ... ])

        Notes:
            - Displays media items in a gallery/grid layout
            - Each item should be a media node
            - Useful for showing multiple related images
        """
        return AdfNode(type="mediaGroup", content=media_items)

    # Layout Nodes

    @staticmethod
    def layout_section(columns: List[AdfNode]) -> AdfNode:
        """
        Create a layout section node.

        Layout sections create multi-column layouts in Confluence pages,
        allowing content to be organized side-by-side.

        Args:
            columns: List of layout column nodes (created with layout_column())

        Returns:
            LayoutSection node

        Example:
            >>> AdfBuilder.layout_section([
            ...     AdfBuilder.layout_column([
            ...         AdfBuilder.paragraph([AdfBuilder.text("Left column content")])
            ...     ], width=50),
            ...     AdfBuilder.layout_column([
            ...         AdfBuilder.paragraph([AdfBuilder.text("Right column content")])
            ...     ], width=50)
            ... ])

        Notes:
            - Sections typically contain 2-3 columns
            - Column widths should sum to 100 (percentage)
            - Nested layouts are supported
        """
        return AdfNode(type="layoutSection", content=columns)

    @staticmethod
    def layout_column(content: List[AdfNode], width: Optional[int] = None) -> AdfNode:
        """
        Create a layout column node.

        Layout columns are containers within a layout section that hold content
        in a multi-column layout.

        Args:
            content: Column content (paragraphs, lists, etc.)
            width: Column width as percentage (e.g., 50 for 50%)

        Returns:
            LayoutColumn node

        Example:
            >>> AdfBuilder.layout_column([
            ...     AdfBuilder.paragraph([AdfBuilder.text("Column content")]),
            ...     AdfBuilder.bullet_list([...])
            ... ], width=33)

        Notes:
            - Width is optional; if not provided, columns share space equally
            - Width is specified as a percentage (integer)
            - Common widths: 33 (1/3), 50 (1/2), 66 (2/3), 100 (full)
        """
        attrs = {}
        if width is not None:
            attrs["width"] = width

        return AdfNode(type="layoutColumn", attrs=attrs if attrs else {}, content=content)

    # Extension Nodes (Macros)

    @staticmethod
    def bodied_extension(
        extension_type: str,
        extension_key: str,
        parameters: Optional[Dict[str, Any]] = None,
        content: Optional[List[AdfNode]] = None
    ) -> AdfNode:
        """
        Create a bodied extension node.

        Bodied extensions are Confluence macros that contain nested content,
        like expand macros, info panels, or custom macro blocks.

        Args:
            extension_type: Extension type identifier (e.g., "com.atlassian.confluence.macro.core")
            extension_key: Extension key (e.g., "toc", "info", "expand")
            parameters: Optional macro parameters (configuration)
            content: Optional nested content within the macro

        Returns:
            BodiedExtension node

        Example:
            >>> # Table of Contents macro
            >>> AdfBuilder.bodied_extension(
            ...     extension_type="com.atlassian.confluence.macro.core",
            ...     extension_key="toc",
            ...     parameters={"maxLevel": "3"}
            ... )
            >>> # Info panel with content
            >>> AdfBuilder.bodied_extension(
            ...     extension_type="com.atlassian.confluence.macro.core",
            ...     extension_key="info",
            ...     content=[AdfBuilder.paragraph([AdfBuilder.text("Important information")])]
            ... )

        Notes:
            - Used for block-level macros with optional content
            - Parameters are macro-specific configuration options
            - Content can include paragraphs, lists, and other block elements
        """
        attrs = {
            "extensionType": extension_type,
            "extensionKey": extension_key
        }
        if parameters:
            attrs["parameters"] = parameters

        return AdfNode(
            type="bodiedExtension",
            attrs=attrs,
            content=content or []
        )

    @staticmethod
    def inline_extension(
        extension_type: str,
        extension_key: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> AdfNode:
        """
        Create an inline extension node.

        Inline extensions are Confluence macros that appear inline with text,
        like status badges, user macros, or inline code snippets.

        Args:
            extension_type: Extension type identifier
            extension_key: Extension key
            parameters: Optional macro parameters

        Returns:
            InlineExtension node

        Example:
            >>> # Status macro
            >>> AdfBuilder.inline_extension(
            ...     extension_type="com.atlassian.confluence.macro.core",
            ...     extension_key="status",
            ...     parameters={"colour": "Green", "title": "Complete"}
            ... )
            >>> # Custom inline macro
            >>> AdfBuilder.inline_extension(
            ...     extension_type="com.example.macros",
            ...     extension_key="badge",
            ...     parameters={"text": "NEW", "color": "red"}
            ... )

        Notes:
            - Used for inline macros that don't contain nested content
            - Appears as part of text flow
            - Parameters configure the macro's appearance and behavior
        """
        attrs = {
            "extensionType": extension_type,
            "extensionKey": extension_key
        }
        if parameters:
            attrs["parameters"] = parameters

        return AdfNode(type="inlineExtension", attrs=attrs)

    # Extension Helper Methods

    @staticmethod
    def toc_macro(max_level: int = 7, min_level: int = 1, include: Optional[str] = None) -> AdfNode:
        """
        Create a Table of Contents macro.

        Args:
            max_level: Maximum heading level to include (1-7)
            min_level: Minimum heading level to include (1-7)
            include: Optional regex pattern for headings to include

        Returns:
            BodiedExtension node configured as TOC macro

        Example:
            >>> # Standard TOC with levels 1-3
            >>> AdfBuilder.toc_macro(max_level=3)
            >>> # TOC for specific sections
            >>> AdfBuilder.toc_macro(max_level=4, include="^\\d+\\.")

        Notes:
            - Automatically generates a table of contents from page headings
            - Level 7 includes all headings
        """
        parameters = {
            "maxLevel": str(max_level),
            "minLevel": str(min_level)
        }
        if include:
            parameters["include"] = include

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="toc",
            parameters=parameters
        )

    @staticmethod
    def info_panel(content: List[AdfNode], title: Optional[str] = None) -> AdfNode:
        """
        Create an info panel macro.

        Args:
            content: Panel content
            title: Optional panel title

        Returns:
            BodiedExtension node configured as info panel

        Example:
            >>> AdfBuilder.info_panel([
            ...     AdfBuilder.paragraph([AdfBuilder.text("This is important information")])
            ... ], title="Note")

        Notes:
            - Displays content in a blue info-styled panel
            - Similar to panel() but uses extension framework
        """
        parameters = {}
        if title:
            parameters["title"] = title

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="info",
            parameters=parameters if parameters else None,
            content=content
        )

    @staticmethod
    def jira_issue(issue_key: str, server_id: Optional[str] = None) -> AdfNode:
        """
        Create a JIRA issue macro.

        Args:
            issue_key: JIRA issue key (e.g., "PROJ-123")
            server_id: Optional JIRA server ID for multiple connections

        Returns:
            BodiedExtension node configured as JIRA issue macro

        Example:
            >>> AdfBuilder.jira_issue("PROJ-1234")
            >>> AdfBuilder.jira_issue("TEAM-567", server_id="abc-123")

        Notes:
            - Embeds a JIRA issue in the Confluence page
            - Requires JIRA-Confluence integration
        """
        parameters = {"key": issue_key}
        if server_id:
            parameters["serverId"] = server_id

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="jira",
            parameters=parameters
        )

    @staticmethod
    def warning_panel(content: List[AdfNode], title: Optional[str] = None, icon: bool = True) -> AdfNode:
        """
        Create a warning panel macro.

        Args:
            content: Panel content
            title: Optional panel title
            icon: Whether to show warning icon (default: True)

        Returns:
            BodiedExtension node configured as warning panel

        Example:
            >>> AdfBuilder.warning_panel([
            ...     AdfBuilder.paragraph([AdfBuilder.text("Be careful with this operation")])
            ... ], title="Caution")

        Notes:
            - Displays content in a warning-styled panel
            - Used for cautionary information
        """
        parameters = {}
        if title:
            parameters["title"] = title
        if not icon:
            parameters["icon"] = "false"

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="warning",
            parameters=parameters if parameters else None,
            content=content
        )

    @staticmethod
    def note_panel(content: List[AdfNode], title: Optional[str] = None, icon: bool = True) -> AdfNode:
        """
        Create a note panel macro.

        Args:
            content: Panel content
            title: Optional panel title
            icon: Whether to show note icon (default: True)

        Returns:
            BodiedExtension node configured as note panel

        Example:
            >>> AdfBuilder.note_panel([
            ...     AdfBuilder.paragraph([AdfBuilder.text("Remember to save your work")])
            ... ])

        Notes:
            - Displays content in a note-styled panel
            - Used for additional information
        """
        parameters = {}
        if title:
            parameters["title"] = title
        if not icon:
            parameters["icon"] = "false"

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="note",
            parameters=parameters if parameters else None,
            content=content
        )

    @staticmethod
    def excerpt_macro(content: List[AdfNode], hidden: bool = False) -> AdfNode:
        """
        Create an excerpt macro.

        Args:
            content: Excerpt content
            hidden: Whether excerpt is hidden from page content (default: False)

        Returns:
            BodiedExtension node configured as excerpt

        Example:
            >>> AdfBuilder.excerpt_macro([
            ...     AdfBuilder.paragraph([AdfBuilder.text("This is the page summary")])
            ... ])

        Notes:
            - Defines a page excerpt for search results and listings
            - Hidden excerpts don't appear on the page but show in searches
        """
        parameters = {}
        if hidden:
            parameters["hidden"] = "true"

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="excerpt",
            parameters=parameters if parameters else None,
            content=content
        )

    @staticmethod
    def expand_macro(content: List[AdfNode], title: str = "") -> AdfNode:
        """
        Create an expand (collapsible) macro.

        Args:
            content: Collapsible content
            title: Title shown in expand header

        Returns:
            BodiedExtension node configured as expand

        Example:
            >>> AdfBuilder.expand_macro([
            ...     AdfBuilder.paragraph([AdfBuilder.text("Hidden details here")])
            ... ], title="Click to expand")

        Notes:
            - Creates a collapsible section
            - Content is hidden until user clicks to expand
        """
        parameters = {}
        if title:
            parameters["title"] = title

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="expand",
            parameters=parameters if parameters else None,
            content=content
        )

    @staticmethod
    def status_macro(status_text: str, color: str = "Grey", subtle: bool = False) -> AdfNode:
        """
        Create a status lozenge macro.

        Args:
            status_text: Status text to display
            color: Status color (Green, Yellow, Red, Blue, Grey) - default: Grey
            subtle: Use subtle style (default: False)

        Returns:
            InlineExtension node configured as status

        Example:
            >>> AdfBuilder.status_macro("Complete", color="Green")
            >>> AdfBuilder.status_macro("In Progress", color="Blue", subtle=True)

        Notes:
            - Creates an inline status badge
            - Colors: Green (success), Yellow (warning), Red (error), Blue (info), Grey (neutral)
            - Subtle style uses lighter colors
        """
        parameters = {
            "title": status_text,
            "colour": color  # Confluence uses British spelling
        }
        if subtle:
            parameters["subtle"] = "true"

        return AdfBuilder.inline_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="status",
            parameters=parameters
        )

    @staticmethod
    def code_block_macro(
        code: str,
        language: Optional[str] = None,
        title: Optional[str] = None,
        linenumbers: bool = False,
        theme: Optional[str] = None,
        collapse: bool = False
    ) -> AdfNode:
        """
        Create an enhanced code block macro with additional features.

        Args:
            code: Code content
            language: Programming language for syntax highlighting
            title: Code block title
            linenumbers: Show line numbers (default: False)
            theme: Syntax highlighting theme
            collapse: Make code block collapsible (default: False)

        Returns:
            BodiedExtension node configured as code block

        Example:
            >>> AdfBuilder.code_block_macro(
            ...     "def hello():\\n    print('Hello')",
            ...     language="python",
            ...     title="main.py",
            ...     linenumbers=True
            ... )

        Notes:
            - Enhanced version of standard code block
            - Supports titles, line numbers, themes, and collapse
            - Use standard code_block() for simple code blocks
        """
        parameters = {}
        if language:
            parameters["language"] = language
        if title:
            parameters["title"] = title
        if linenumbers:
            parameters["linenumbers"] = "true"
        if theme:
            parameters["theme"] = theme
        if collapse:
            parameters["collapse"] = "true"

        # Create code block with plain text content
        # The code content is stored as a plain text node
        code_content = [AdfBuilder.paragraph([AdfBuilder.text(code)])]

        return AdfBuilder.bodied_extension(
            extension_type="com.atlassian.confluence.macro.core",
            extension_key="code",
            parameters=parameters if parameters else None,
            content=code_content
        )

    # Advanced Table Features

    @staticmethod
    def advanced_table(
        rows: List[List[AdfNode]],
        headers: bool = False,
        column_widths: Optional[List[int]] = None,
        layout: str = "default",
        numbered_column: bool = False
    ) -> AdfNode:
        """
        Create a table node with advanced features.

        Args:
            rows: Table rows
            headers: Whether the first row contains headers
            column_widths: Optional list of column widths in pixels
            layout: Table layout ("default", "wide", "full-width")
            numbered_column: Whether to show a numbered column

        Returns:
            Table node with advanced attributes

        Example:
            >>> # Table with custom column widths
            >>> AdfBuilder.advanced_table(
            ...     rows=[[cell1, cell2, cell3]],
            ...     column_widths=[100, 200, 300],
            ...     layout="wide"
            ... )

        Notes:
            - column_widths are in pixels
            - layout options affect table display width
            - numbered_column adds row numbers automatically
        """
        attrs = {
            "isNumberColumnEnabled": numbered_column,
            "layout": layout
        }

        if column_widths:
            attrs["width"] = column_widths

        table_rows = []
        for row in rows:
            cells = []
            for cell_content in row:
                cells.append(AdfNode(type="tableCell", content=[cell_content]))
            table_rows.append(AdfNode(type="tableRow", content=cells))

        return AdfNode(type="table", attrs=attrs, content=table_rows)

    @staticmethod
    def table_cell(
        content: List[AdfNode],
        background_color: Optional[str] = None,
        colspan: int = 1,
        rowspan: int = 1
    ) -> AdfNode:
        """
        Create a table cell node with advanced attributes.

        Args:
            content: Cell content
            background_color: Optional background color (hex code)
            colspan: Number of columns to span
            rowspan: Number of rows to span

        Returns:
            TableCell node

        Example:
            >>> AdfBuilder.table_cell(
            ...     [AdfBuilder.paragraph([AdfBuilder.text("Cell content")])],
            ...     background_color="#ffff00",
            ...     colspan=2
            ... )

        Notes:
            - background_color should be hex format (#RRGGBB)
            - colspan/rowspan > 1 create merged cells
        """
        attrs = {}
        if background_color:
            attrs["background"] = background_color
        if colspan > 1:
            attrs["colspan"] = colspan
        if rowspan > 1:
            attrs["rowspan"] = rowspan

        return AdfNode(type="tableCell", attrs=attrs if attrs else {}, content=content)

    # Additional Mark Helper Methods

    @staticmethod
    def annotated_text(
        text: str,
        annotation_id: str,
        annotation_type: str = "inlineComment",
        additional_marks: Optional[List[Dict[str, Any]]] = None
    ) -> AdfNode:
        """
        Create text with an annotation mark (inline comment).

        Args:
            text: Text content
            annotation_id: Unique annotation identifier
            annotation_type: Type of annotation (default: "inlineComment")
            additional_marks: Additional marks to apply

        Returns:
            Text node with annotation mark

        Example:
            >>> AdfBuilder.annotated_text(
            ...     "This needs review",
            ...     annotation_id="comment-123",
            ...     annotation_type="inlineComment"
            ... )

        Notes:
            - Used for inline comments and annotations
            - annotation_id must be unique within the document
            - Requires Confluence permissions to view/edit
        """
        marks = list(additional_marks) if additional_marks else []
        marks.append({
            "type": "annotation",
            "attrs": {
                "id": annotation_id,
                "annotationType": annotation_type
            }
        })
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def bordered_text(
        text: str,
        border_color: Optional[str] = None,
        border_size: int = 1,
        additional_marks: Optional[List[Dict[str, Any]]] = None
    ) -> AdfNode:
        """
        Create text with a border mark.

        Args:
            text: Text content
            border_color: Border color (hex code)
            border_size: Border width in pixels
            additional_marks: Additional marks to apply

        Returns:
            Text node with border mark

        Example:
            >>> AdfBuilder.bordered_text("Boxed text", "#000000", 2)

        Notes:
            - Creates bordered/boxed text effect
            - border_color should be hex format
        """
        attrs = {"size": border_size}
        if border_color:
            attrs["color"] = border_color

        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "border", "attrs": attrs})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def breakout_text(
        text: str,
        mode: str = "wide",
        additional_marks: Optional[List[Dict[str, Any]]] = None
    ) -> AdfNode:
        """
        Create text with a breakout mark.

        Args:
            text: Text content
            mode: Breakout mode ("wide" or "full-width")
            additional_marks: Additional marks to apply

        Returns:
            Text node with breakout mark

        Example:
            >>> AdfBuilder.breakout_text("Full width content", "full-width")

        Notes:
            - Makes content break out of normal page width
            - Valid modes: "wide", "full-width"
            - Affects layout rendering
        """
        valid_modes = {"wide", "full-width"}
        if mode not in valid_modes:
            logging.warning(f"Invalid breakout mode '{mode}', using 'wide'. Valid: {valid_modes}")
            mode = "wide"

        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "breakout", "attrs": {"mode": mode}})
        return AdfNode(type="text", text=text, marks=marks)

    @staticmethod
    def fragment_text(
        text: str,
        local_id: str,
        name: Optional[str] = None,
        additional_marks: Optional[List[Dict[str, Any]]] = None
    ) -> AdfNode:
        """
        Create text with a fragment mark.

        Args:
            text: Text content
            local_id: Fragment local identifier
            name: Optional fragment name
            additional_marks: Additional marks to apply

        Returns:
            Text node with fragment mark

        Example:
            >>> AdfBuilder.fragment_text(
            ...     "Fragment content",
            ...     local_id="fragment-1",
            ...     name="Section A"
            ... )

        Notes:
            - Used for fragment references and linking
            - local_id must be unique within document
        """
        attrs = {"localId": local_id}
        if name:
            attrs["name"] = name

        marks = list(additional_marks) if additional_marks else []
        marks.append({"type": "fragment", "attrs": attrs})
        return AdfNode(type="text", text=text, marks=marks)

    # Advanced Media Nodes

    @staticmethod
    def media_single(
        media: AdfNode,
        layout: str = "center",
        width: Optional[int] = None,
        width_type: str = "pixel"
    ) -> AdfNode:
        """
        Create a media single node with advanced layout options.

        MediaSingle wraps a media node to control its display layout and sizing.

        Args:
            media: Media node (from image() or confluence_image())
            layout: Layout mode ("center", "wrap-left", "wrap-right", "wide", "full-width")
            width: Optional width value
            width_type: Width type ("pixel" or "percentage")

        Returns:
            MediaSingle node

        Example:
            >>> img = AdfBuilder.image("https://example.com/img.jpg", "Photo")
            >>> AdfBuilder.media_single(img, layout="wide", width=800)

        Notes:
            - Provides fine-grained control over media display
            - Layout options affect how text wraps around media
            - width_type determines how width value is interpreted
        """
        attrs = {"layout": layout}
        if width is not None:
            attrs["width"] = width
            attrs["widthType"] = width_type

        return AdfNode(type="mediaSingle", attrs=attrs, content=[media])

    @staticmethod
    def media_inline(
        media_id: str,
        collection: str = "contentId",
        media_type: str = "file",
        alt: Optional[str] = None
    ) -> AdfNode:
        """
        Create an inline media node.

        MediaInline displays media inline with text, similar to an inline image.

        Args:
            media_id: Media file ID
            collection: Collection identifier (default: "contentId")
            media_type: Type of media ("file", "link", "external")
            alt: Alternative text

        Returns:
            MediaInline node

        Example:
            >>> AdfBuilder.media_inline("file-123", alt="Icon")

        Notes:
            - Displays inline with text flow
            - Typically used for icons or small images
            - Requires Confluence media ID
        """
        attrs = {
            "id": media_id,
            "type": media_type,
            "collection": collection
        }
        if alt:
            attrs["alt"] = alt

        return AdfNode(type="mediaInline", attrs=attrs)

    @staticmethod
    def caption(content: List[AdfNode]) -> AdfNode:
        """
        Create a caption node for media.

        Captions provide descriptive text for media elements.

        Args:
            content: Caption content (typically text)

        Returns:
            Caption node

        Example:
            >>> AdfBuilder.caption([
            ...     AdfBuilder.text("Figure 1: System Architecture")
            ... ])

        Notes:
            - Used within media nodes to add captions
            - Appears below the media element
        """
        return AdfNode(type="caption", content=content)

    @staticmethod
    def rich_media(
        url: str,
        layout: str = "center",
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> AdfNode:
        """
        Create a rich media node for embedded content.

        RichMedia embeds external media like videos, interactive content, etc.

        Args:
            url: URL of the rich media content
            layout: Layout mode ("center", "wide", "full-width")
            width: Optional width in pixels
            height: Optional height in pixels

        Returns:
            RichMedia node

        Example:
            >>> AdfBuilder.rich_media(
            ...     "https://youtube.com/embed/video123",
            ...     layout="wide",
            ...     width=800,
            ...     height=450
            ... )

        Notes:
            - Used for video embeds, interactive content
            - URL should point to embeddable content
            - Layout affects display width
        """
        attrs = {"url": url, "layout": layout}
        if width is not None:
            attrs["width"] = width
        if height is not None:
            attrs["height"] = height

        return AdfNode(type="richMedia", attrs=attrs)

    @staticmethod
    def embed_card(url: str, layout: str = "center") -> AdfNode:
        """
        Create an embed card node.

        EmbedCard embeds external content as a card with preview.

        Args:
            url: URL to embed
            layout: Layout mode ("center", "wide", "full-width")

        Returns:
            EmbedCard node

        Example:
            >>> AdfBuilder.embed_card(
            ...     "https://example.com/content",
            ...     layout="wide"
            ... )

        Notes:
            - Creates a card with URL preview
            - Shows title, description, thumbnail from URL
            - Requires embeddable URL
        """
        attrs = {"url": url, "layout": layout}
        return AdfNode(type="embedCard", attrs=attrs)

    # Advanced Content Nodes

    @staticmethod
    def nested_expand(title: str, content: List[AdfNode]) -> AdfNode:
        """
        Create a nested expand node.

        NestedExpand is similar to expand but can be nested within other expands.

        Args:
            title: Expand title
            content: Nested content

        Returns:
            NestedExpand node

        Example:
            >>> AdfBuilder.nested_expand(
            ...     "Details",
            ...     [AdfBuilder.paragraph([AdfBuilder.text("Nested content")])]
            ... )

        Notes:
            - Used for nested collapsible sections
            - Can be placed inside expand nodes
            - Supports multiple nesting levels
        """
        attrs = {"title": title}
        return AdfNode(type="nestedExpand", attrs=attrs, content=content)

    @staticmethod
    def sync_block(local_id: str, resource_id: Optional[str] = None, content: Optional[List[AdfNode]] = None) -> AdfNode:
        """
        Create a sync block node.

        SyncBlock represents synchronized content that can be shared across pages.

        Args:
            local_id: Local identifier for the sync block
            resource_id: Optional resource identifier for synchronization
            content: Block content

        Returns:
            SyncBlock node

        Example:
            >>> AdfBuilder.sync_block(
            ...     local_id="sync-1",
            ...     resource_id="resource-abc",
            ...     content=[AdfBuilder.paragraph([AdfBuilder.text("Synced content")])]
            ... )

        Notes:
            - Used for content that syncs across multiple pages
            - Requires Confluence synchronization setup
            - Changes propagate to all instances
        """
        attrs = {"localId": local_id}
        if resource_id:
            attrs["resourceId"] = resource_id

        return AdfNode(type="syncBlock", attrs=attrs, content=content or [])

    @staticmethod
    def extension_frame(extension: AdfNode) -> AdfNode:
        """
        Create an extension frame node.

        ExtensionFrame wraps extension nodes to provide additional context.

        Args:
            extension: Extension node to wrap

        Returns:
            ExtensionFrame node

        Example:
            >>> ext = AdfBuilder.bodied_extension(
            ...     "com.example.macros", "custom", {}
            ... )
            >>> AdfBuilder.extension_frame(ext)

        Notes:
            - Provides frame/wrapper for extensions
            - Used for extension isolation
            - Rarely needed directly (automatic in some cases)
        """
        return AdfNode(type="extensionFrame", content=[extension])

    @staticmethod
    def multi_bodied_extension(
        extension_type: str,
        extension_key: str,
        parameters: Optional[Dict[str, Any]] = None,
        sections: Optional[List[Dict[str, Any]]] = None
    ) -> AdfNode:
        """
        Create a multi-bodied extension node.

        MultiBodiedExtension supports macros with multiple content sections.

        Args:
            extension_type: Extension type identifier
            extension_key: Extension key
            parameters: Macro parameters
            sections: List of content sections (each with content)

        Returns:
            MultiBodiedExtension node

        Example:
            >>> AdfBuilder.multi_bodied_extension(
            ...     extension_type="com.atlassian.confluence.macro.core",
            ...     extension_key="multi-section",
            ...     parameters={"param1": "value1"},
            ...     sections=[
            ...         {"content": [AdfBuilder.paragraph([AdfBuilder.text("Section 1")])]},
            ...         {"content": [AdfBuilder.paragraph([AdfBuilder.text("Section 2")])]}
            ...     ]
            ... )

        Notes:
            - Used for complex macros with multiple sections
            - Each section can have different content
            - Less common than bodied_extension
        """
        attrs = {
            "extensionType": extension_type,
            "extensionKey": extension_key
        }
        if parameters:
            attrs["parameters"] = parameters

        # Convert sections to ADF nodes if provided
        content = []
        if sections:
            for section in sections:
                if "content" in section:
                    # Wrap section content in a structure
                    section_nodes = section["content"] if isinstance(section["content"], list) else [section["content"]]
                    content.extend(section_nodes)

        return AdfNode(type="multiBodiedExtension", attrs=attrs, content=content)

    @staticmethod
    def block_task_item(content: List[AdfNode], state: str = "TODO", local_id: Optional[str] = None) -> AdfNode:
        """
        Create a block-level task item node.

        BlockTaskItem is similar to taskItem but used for block-level tasks
        that can contain more complex content.

        Args:
            content: Task content (can include multiple blocks)
            state: Task state ("TODO" or "DONE")
            local_id: Optional local identifier

        Returns:
            BlockTaskItem node

        Example:
            >>> AdfBuilder.block_task_item(
            ...     [
            ...         AdfBuilder.paragraph([AdfBuilder.text("Complex task")]),
            ...         AdfBuilder.bullet_list([...])
            ...     ],
            ...     state="TODO"
            ... )

        Notes:
            - Supports richer content than regular taskItem
            - Can contain paragraphs, lists, and other blocks
            - Used in action items and complex task tracking
        """
        valid_states = {"TODO", "DONE"}
        if state not in valid_states:
            logging.warning(f"Invalid task state '{state}', using 'TODO'. Valid states: {valid_states}")
            state = "TODO"

        attrs = {"state": state}
        if local_id:
            attrs["localId"] = local_id

        return AdfNode(type="blockTaskItem", attrs=attrs, content=content)
