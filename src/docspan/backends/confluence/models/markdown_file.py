"""
Models for Markdown files and their contents.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Known frontmatter keys that markdown_confluence understands
KNOWN_CONNIE_KEYS: Set[str] = {
    "connie-title",           # Page title override
    "connie-page-id",         # Confluence page ID
    "connie-parent-id",       # Parent page ID (legacy)
    "connie-parent-page-id",  # Parent page ID (preferred)
    "connie-publish",         # Whether to publish this file
    "connie-visibility",      # Page visibility (private/public)
    "connie-last-remote-version",   # Last known remote version
    "connie-last-sync-timestamp",   # Last sync timestamp
}

# Common typos/mistakes mapped to the correct key
CONNIE_KEY_SUGGESTIONS: Dict[str, str] = {
    "connie-page-title": "connie-title",
    "connie-pagetitle": "connie-title",
    "connie-name": "connie-title",
    "connie-id": "connie-page-id",
    "connie-pageid": "connie-page-id",
    "connie-parent": "connie-parent-id",
    "connie-parentid": "connie-parent-id",
    "connie-parent-page": "connie-parent-id",
}


@dataclass
class MarkdownFile:
    """
    Represents a Markdown file with its content and metadata.

    Attributes:
        path: Path to the file
        content: Raw content of the file
        frontmatter: Extracted frontmatter as a dictionary
        title: Title for the page (from filename or frontmatter)
        title_from_h1: Whether the title was extracted from a first-level heading
    """

    path: Path
    content: str
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    title: Optional[str] = None
    title_from_h1: bool = False

    @classmethod
    def from_file(cls, file_path: Path) -> "MarkdownFile":
        """
        Create a MarkdownFile instance from a file.

        Args:
            file_path: Path to the Markdown file

        Returns:
            MarkdownFile instance

        Raises:
            FileNotFoundError: If the file doesn't exist
            UnicodeDecodeError: If the file can't be read as UTF-8
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        instance = cls(path=file_path, content=content)
        instance.extract_frontmatter()
        instance.validate_frontmatter()
        instance.determine_title()

        return instance

    def extract_frontmatter(self) -> None:
        """
        Extract YAML frontmatter from content and update the instance.
        """
        frontmatter_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = frontmatter_pattern.match(self.content)

        if match:
            try:
                import yaml

                yaml_content = match.group(1)
                self.frontmatter = yaml.safe_load(yaml_content) or {}

                # Remove frontmatter from content
                self.content = self.content[match.end() :]
            except ImportError:
                # If pyyaml is not available, try simple key-value parsing
                self._parse_frontmatter_simple(match.group(1))
                self.content = self.content[match.end() :]
            except Exception:
                logger.warning("Failed to parse frontmatter; keeping raw content", exc_info=True)

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """
        Calculate the Levenshtein distance between two strings.

        Args:
            s1: First string
            s2: Second string

        Returns:
            Edit distance between the strings
        """
        if len(s1) < len(s2):
            return MarkdownFile._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                # Cost is 0 if characters match, 1 otherwise
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _find_closest_key(self, unknown_key: str, max_distance: int = 5) -> Optional[str]:
        """
        Find the closest known key to an unknown key using Levenshtein distance.

        Args:
            unknown_key: The unrecognized frontmatter key
            max_distance: Maximum edit distance to consider a suggestion

        Returns:
            The closest known key if within max_distance, None otherwise
        """
        # First check explicit suggestions mapping
        if unknown_key in CONNIE_KEY_SUGGESTIONS:
            return CONNIE_KEY_SUGGESTIONS[unknown_key]

        # Otherwise, find closest by Levenshtein distance
        best_match = None
        best_distance = max_distance + 1

        for known_key in KNOWN_CONNIE_KEYS:
            distance = self._levenshtein_distance(unknown_key, known_key)
            if distance < best_distance:
                best_distance = distance
                best_match = known_key

        return best_match if best_distance <= max_distance else None

    def validate_frontmatter(self) -> List[str]:
        """
        Validate frontmatter keys and warn about unrecognized connie-* keys.

        Uses Levenshtein distance to suggest the closest known key for typos.

        Returns:
            List of warning messages for unrecognized keys
        """
        warnings = []

        for key in self.frontmatter.keys():
            # Only validate keys that start with "connie-"
            if not key.startswith("connie-"):
                continue

            if key in KNOWN_CONNIE_KEYS:
                continue

            # Find the closest known key
            suggestion = self._find_closest_key(key)

            if suggestion:
                msg = (
                    f"Unrecognized frontmatter key '{key}' in {self.path}. "
                    f"Did you mean '{suggestion}'?"
                )
            else:
                msg = (
                    f"Unrecognized frontmatter key '{key}' in {self.path}. "
                    f"Known keys are: {', '.join(sorted(KNOWN_CONNIE_KEYS))}"
                )

            logger.warning(msg)
            warnings.append(msg)

        return warnings

    def _parse_frontmatter_simple(self, yaml_content: str) -> None:
        """
        Simple YAML parser for frontmatter (no pyyaml dependency).

        Args:
            yaml_content: YAML content to parse
        """
        for line in yaml_content.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                # Handle quoted values
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]

                self.frontmatter[key] = value

    def determine_title(self) -> None:
        """
        Determine the title for the page.

        Sets title_from_h1=True if the title is extracted from a first-level heading,
        which allows the ADF converter to skip the duplicate heading in content.
        """
        # First, try to get the title from frontmatter
        title = self.frontmatter.get("connie-title")
        if title:
            self.title = title
            self.title_from_h1 = False
            return

        # Otherwise, extract the first header from the content
        header_pattern = re.compile(r"^#\s+(.+?)$", re.MULTILINE)
        match = header_pattern.search(self.content)
        if match:
            self.title = match.group(1)
            self.title_from_h1 = True  # Mark that title came from H1
        else:
            # If no header found, use the filename
            self.title = self.path.stem
            self.title_from_h1 = False

    def save(self) -> None:
        """
        Save changes back to the file.
        """
        try:
            # Generate frontmatter
            import yaml

            frontmatter_str = yaml.dump(self.frontmatter, default_flow_style=False)
            output = f"---\n{frontmatter_str}---\n\n{self.content}"
        except ImportError:
            # Fallback if pyyaml is not available
            frontmatter_lines = []
            for key, value in self.frontmatter.items():
                if isinstance(value, str) and " " in value:
                    frontmatter_lines.append(f"{key}: \"{value}\"")
                else:
                    frontmatter_lines.append(f"{key}: {value}")
            frontmatter_str = "\n".join(frontmatter_lines)
            output = f"---\n{frontmatter_str}\n---\n\n{self.content}"

        with open(self.path, "w", encoding="utf-8") as f:
            f.write(output)

    def get_confluence_page_id(self) -> Optional[str]:
        """
        Get Confluence page ID from frontmatter.
        
        Returns:
            Page ID or None if not found
        """
        return self.frontmatter.get("connie-page-id")

    def get_parent_id(self) -> Optional[str]:
        """
        Get parent page ID from frontmatter.

        Checks both 'connie-parent-page-id' (preferred) and 'connie-parent-id' (legacy)
        for backward compatibility.

        Returns:
            Parent page ID or None if not found
        """
        # Check preferred field name first
        parent_id = self.frontmatter.get("connie-parent-page-id")
        if parent_id:
            return parent_id
        # Fall back to legacy field name
        return self.frontmatter.get("connie-parent-id")

    def remove_confluence_page_id(self) -> None:
        """
        Remove Confluence page ID from frontmatter.
        """
        if "connie-page-id" in self.frontmatter:
            del self.frontmatter["connie-page-id"]

    def get_tags(self) -> List[str]:
        """
        Get tags from frontmatter.

        Returns:
            List of tags
        """
        tags = self.frontmatter.get("tags", [])
        if isinstance(tags, str):
            return [tag.strip() for tag in tags.split(",")]
        elif isinstance(tags, list):
            return tags
        return []

    def should_publish(self) -> bool:
        """
        Check if this file should be published based on frontmatter.

        Returns:
            True if file should be published, False otherwise
        """
        return self.frontmatter.get("connie-publish", True)

    # Sync metadata methods

    def get_remote_version(self) -> Optional[int]:
        """
        Get last known remote version from frontmatter.

        Returns:
            Remote version number or None if not tracked
        """
        version = self.frontmatter.get("connie-last-remote-version")
        return int(version) if version is not None else None

    def set_remote_version(self, version: int) -> None:
        """
        Set last known remote version in frontmatter.

        Args:
            version: Remote version number
        """
        self.frontmatter["connie-last-remote-version"] = version

    def get_last_sync_timestamp(self) -> Optional[str]:
        """
        Get last sync timestamp from frontmatter.

        Returns:
            ISO 8601 timestamp or None if never synced
        """
        return self.frontmatter.get("connie-last-sync-timestamp")

    def set_last_sync_timestamp(self, timestamp: str) -> None:
        """
        Set last sync timestamp in frontmatter.

        Args:
            timestamp: ISO 8601 timestamp
        """
        self.frontmatter["connie-last-sync-timestamp"] = timestamp

    def get_local_modified_timestamp(self) -> Optional[float]:
        """
        Get file's last modification timestamp.

        Returns:
            Unix timestamp of last modification
        """
        if self.path.exists():
            return self.path.stat().st_mtime
        return None

    def was_modified_since_sync(self) -> bool:
        """
        Check if local file was modified since last sync.

        Returns:
            True if file was modified after last sync, False otherwise
        """
        last_sync = self.get_last_sync_timestamp()
        if not last_sync:
            # Never synced, consider it modified
            return True

        try:
            from datetime import datetime
            last_sync_time = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
            file_mtime = self.get_local_modified_timestamp()

            if file_mtime is None:
                return False

            file_time = datetime.fromtimestamp(file_mtime)
            # Allow 2 second grace period for file system timestamp precision
            return file_time.timestamp() > last_sync_time.timestamp() + 2
        except Exception:
            logger.warning("Could not determine file modification time; assuming modified", exc_info=True)
            return True