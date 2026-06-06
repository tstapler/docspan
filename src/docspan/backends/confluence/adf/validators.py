"""
Validators for ADF content.
"""

from typing import Any, Dict, List


class AdfValidator:
    """
    Validator for Atlassian Document Format (ADF) content.
    """

    @staticmethod
    def validate_document(doc: Dict[str, Any]) -> List[str]:
        """
        Validate an ADF document.

        Args:
            doc: ADF document to validate

        Returns:
            List of validation error messages, empty if valid
        """
        errors = []

        # Check version
        if doc.get("version") != 1:
            errors.append("Invalid document version (must be 1)")

        # Check type
        if doc.get("type") != "doc":
            errors.append("Invalid document type (must be 'doc')")

        # Check content
        content = doc.get("content", [])
        if not isinstance(content, list):
            errors.append("Document content must be a list")
        else:
            for i, node in enumerate(content):
                node_errors = AdfValidator.validate_node(node)
                for error in node_errors:
                    errors.append(f"Content node {i}: {error}")

        return errors

    @staticmethod
    def validate_node(node: Dict[str, Any]) -> List[str]:
        """
        Validate an ADF node.

        Args:
            node: ADF node to validate

        Returns:
            List of validation error messages, empty if valid
        """
        errors = []

        # Check that node is a dict
        if not isinstance(node, dict):
            return ["Node must be a dictionary"]

        # Check type
        node_type = node.get("type")
        if not node_type:
            errors.append("Node must have a type")

        # Validate specific node types
        if node_type == "text":
            if "text" not in node:
                errors.append("Text node must have 'text' content")

            marks = node.get("marks", [])
            if not isinstance(marks, list):
                errors.append("Text marks must be a list")

        elif node_type in (
            "paragraph",
            "heading",
            "bulletList",
            "orderedList",
            "listItem",
            "blockquote",
            "panel",
        ):
            # Check content
            content = node.get("content", [])
            if not isinstance(content, list):
                errors.append(f"{node_type} content must be a list")
            else:
                for i, child in enumerate(content):
                    child_errors = AdfValidator.validate_node(child)
                    for error in child_errors:
                        errors.append(f"Child node {i}: {error}")

            # Check heading level
            if node_type == "heading" and "level" in node:
                level = node.get("level")
                if not isinstance(level, int) or level < 1 or level > 6:
                    errors.append("Heading level must be an integer between 1 and 6")

        elif node_type == "codeBlock":
            # Check content
            content = node.get("content", [])
            if not isinstance(content, list):
                errors.append("CodeBlock content must be a list")

        elif node_type == "media":
            # Check media attrs
            attrs = node.get("attrs", {})
            if not attrs.get("type"):
                errors.append("Media node must have a type attribute")

            if attrs.get("type") == "external" and not attrs.get("url"):
                errors.append("External media must have a URL")

            if attrs.get("type") == "file" and not attrs.get("id"):
                errors.append("File media must have an ID")

        elif node_type == "rule":
            # Horizontal rule doesn't need additional validation
            pass

        return errors

    @staticmethod
    def validate_nested_content(node: Dict[str, Any], allowed_types: List[str]) -> List[str]:
        """
        Validate nested content of a node.

        Args:
            node: ADF node to validate
            allowed_types: List of allowed child node types

        Returns:
            List of validation error messages, empty if valid
        """
        errors = []

        content = node.get("content", [])
        if not isinstance(content, list):
            return [f"{node.get('type')} content must be a list"]

        for i, child in enumerate(content):
            if not isinstance(child, dict):
                errors.append(f"Child node {i} must be a dictionary")
                continue

            child_type = child.get("type")
            if not child_type:
                errors.append(f"Child node {i} must have a type")
            elif child_type not in allowed_types:
                errors.append(
                    f"Child node {i} has invalid type: {child_type} (allowed: {', '.join(allowed_types)})"
                )

            child_errors = AdfValidator.validate_node(child)
            for error in child_errors:
                errors.append(f"Child node {i}: {error}")

        return errors
