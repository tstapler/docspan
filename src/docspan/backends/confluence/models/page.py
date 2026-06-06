"""
Models for Confluence pages.

This module enforces ADF (Atlassian Document Format) as the exclusive format
for Confluence Cloud pages, with no fallback to legacy storage format.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class ConfluencePage:
    """
    Represents a Confluence page using ADF (Atlassian Document Format).

    This class enforces ADF as the exclusive format for Confluence Cloud.
    Storage format is not supported and will raise errors.

    Attributes:
        title: Page title
        content: Page content in ADF format (must be dict)
        space_key: Confluence space key
        parent_id: ID of the parent page
        id: Confluence page ID (for updating existing pages)
        version: Current page version (for updates)
        labels: List of labels to apply to the page
        force_update: Flag to force an update even if content appears unchanged
        restrictions: List of restriction objects defining read/update permissions
    """

    title: str
    content: Union[str, Dict[str, Any]]  # Should be ADF dict; string will raise error
    parent_id: str
    space_key: Optional[str] = None  # Can be derived from parent_id if not specified
    id: Optional[str] = None
    version: Optional[int] = None
    labels: List[str] = field(default_factory=list)
    force_update: bool = False
    restrictions: Optional[List[Dict[str, Any]]] = None

    def to_api_data(self, for_update: bool = False) -> Dict[str, Any]:
        """
        Convert to data suitable for Confluence API using ADF format.

        This method enforces ADF (Atlassian Document Format) as the exclusive format.
        Storage format is not supported and will raise errors.

        Args:
            for_update: Whether this is for an update operation

        Returns:
            Dictionary with API data in ADF format

        Raises:
            ValueError: If required fields are missing for update
            UnsupportedADFFeatureError: If content contains unsupported features
            ADFConversionError: If content cannot be converted to valid ADF
        """
        if for_update:
            if not self.id:
                raise ValueError("Page ID is required for update")
            if self.version is None:
                raise ValueError("Page version is required for update")

            data = {
                "id": self.id,
                "type": "page",
                "title": self.title,
                "version": {"number": self.version + 1},
            }
        else:
            data = {
                "type": "page",
                "title": self.title,
                "ancestors": [{"id": self.parent_id}] if self.parent_id else [],
            }

            # Add space key if provided
            if self.space_key:
                data["space"] = {"key": self.space_key}

        # Add restrictions if provided (works for both create and update)
        if self.restrictions:
            data["restrictions"] = self.restrictions

        # Handle content - always use ADF format
        if isinstance(self.content, dict):
            # Content is already ADF document
            self._validate_adf(self.content)
            data["body"] = {"atlas_doc_format": self.content}
        elif isinstance(self.content, str) and self.content.startswith("{") and self.content.endswith("}"):
            # Content looks like JSON string - parse and validate
            try:
                adf_content = json.loads(self.content)
                self._validate_adf(adf_content)
                data["body"] = {"atlas_doc_format": adf_content}
            except json.JSONDecodeError as e:
                from docspan.backends.confluence.services.confluence.base_client import ADFConversionError
                raise ADFConversionError(
                    f"Failed to parse JSON content: {e}",
                    markdown_content=self.content[:200]
                )
        else:
            # String content is not supported - must be ADF dict
            from docspan.backends.confluence.services.confluence.base_client import ADFConversionError
            raise ADFConversionError(
                "Content must be ADF dictionary format. String/storage format is not supported.",
                markdown_content=str(self.content)[:200] if self.content else None
            )

        return data

    def _validate_adf(self, adf_content: Dict[str, Any]) -> None:
        """
        Validate ADF content for unsupported features.

        Args:
            adf_content: ADF content to validate

        Raises:
            UnsupportedADFFeatureError: If content contains unsupported features
            InvalidADFError: If ADF structure is invalid
        """
        from docspan.backends.confluence.services.confluence.base_client import (
            UnsupportedADFFeatureError,
            InvalidADFError
        )

        # Check basic structure
        if not isinstance(adf_content, dict):
            raise InvalidADFError(
                "ADF content must be a dictionary",
                adf_content=adf_content
            )

        # Check for storage_format_html (indicates legacy format)
        # NOTE: Disabled this validation because storage_format_html is legitimately
        # used by the Mermaid plugin to embed rendered diagrams.
        # The original validation was too strict and rejected valid ADF content.
        # if self._has_storage_format_html(adf_content):
        #     raise UnsupportedADFFeatureError(
        #         "Content contains 'storage_format_html' attribute. "
        #         "This indicates legacy storage format which is no longer supported. "
        #         "Content must be converted to pure ADF format.",
        #         feature_name="storage_format_html"
        #     )

        # Validate node types are supported
        self._validate_node_types(adf_content)

    def _has_storage_format_html(self, adf_content: Dict[str, Any]) -> bool:
        """
        Check if any node in the ADF content has storage_format_html.

        Args:
            adf_content: ADF content to check

        Returns:
            True if any node has storage_format_html, False otherwise
        """
        # Check current node
        if "attrs" in adf_content and "storage_format_html" in adf_content["attrs"]:
            return True

        # Check content nodes recursively
        if "content" in adf_content and isinstance(adf_content["content"], list):
            for node in adf_content["content"]:
                if isinstance(node, dict) and self._has_storage_format_html(node):
                    return True

        return False

    def _validate_node_types(self, node: Dict[str, Any]) -> None:
        """
        Recursively validate that all node types are supported.

        Args:
            node: ADF node to validate

        Raises:
            UnsupportedADFFeatureError: If node contains unsupported types
        """
        from docspan.backends.confluence.services.confluence.base_client import UnsupportedADFFeatureError

        node_type = node.get("type")

        # List of known unsupported features (expand as we discover them)
        # Note: This is intentionally minimal - we discover unsupported features through errors
        unsupported_types = {
            # Add types here as we discover them
            # Example: "customPanel", "unknownExtension"
        }

        if node_type in unsupported_types:
            raise UnsupportedADFFeatureError(
                f"ADF node type '{node_type}' is not yet supported. "
                f"Please implement ADF conversion for this type.",
                feature_name=node_type
            )

        # Recursively check children
        if "content" in node and isinstance(node["content"], list):
            for child in node["content"]:
                if isinstance(child, dict):
                    self._validate_node_types(child)
