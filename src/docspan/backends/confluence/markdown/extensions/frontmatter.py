"""
Frontmatter parser extension.
"""

import re
from typing import Any, Dict, Optional, Tuple


class FrontmatterParser:
    """
    Parser for YAML frontmatter in Markdown files.
    """

    @staticmethod
    def extract(content: str) -> Tuple[Dict[str, Any], str]:
        """
        Extract YAML frontmatter from content.

        Args:
            content: Markdown content with frontmatter

        Returns:
            Tuple of (frontmatter dict, content without frontmatter)
        """
        frontmatter_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = frontmatter_pattern.match(content)

        if match:
            try:
                import yaml

                frontmatter = yaml.safe_load(match.group(1)) or {}
                # Remove frontmatter from content
                remaining_content = content[match.end() :]
                return frontmatter, remaining_content
            except (yaml.YAMLError, ImportError):
                # Invalid YAML or yaml module not available
                pass

        return {}, content

    @staticmethod
    def get_title(frontmatter: Dict[str, Any], default: Optional[str] = None) -> Optional[str]:
        """
        Get title from frontmatter.

        Args:
            frontmatter: Frontmatter dictionary
            default: Default value if title not found

        Returns:
            Title or default value
        """
        return frontmatter.get("connie-title", frontmatter.get("title", default))

    @staticmethod
    def get_page_id(frontmatter: Dict[str, Any]) -> Optional[str]:
        """
        Get Confluence page ID from frontmatter.

        Args:
            frontmatter: Frontmatter dictionary

        Returns:
            Page ID or None
        """
        return frontmatter.get("connie-page-id")

    @staticmethod
    def should_publish(frontmatter: Dict[str, Any]) -> bool:
        """
        Check if page should be published based on frontmatter.

        Args:
            frontmatter: Frontmatter dictionary

        Returns:
            True if page should be published, False otherwise
        """
        return frontmatter.get("connie-publish", True)
