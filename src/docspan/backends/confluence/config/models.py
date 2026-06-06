"""
Configuration data models for markdown-confluence.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConfluenceConfig:
    """
    Configuration for Confluence connection.

    Attributes:
        base_url: Base URL for Confluence instance
        parent_id: ID of the parent page in Confluence (optional, only required for publishing)
        username: Atlassian username
        api_token: Atlassian API token (optional if set in environment)
        space_key: Confluence space key (optional)
    """

    base_url: str
    username: str
    parent_id: Optional[str] = None
    api_token: Optional[str] = None
    space_key: Optional[str] = None

    def __post_init__(self) -> None:
        """
        Post-initialization processing.

        Loads API token from environment if not provided.
        """
        if not self.api_token:
            self.api_token = os.environ.get("ATLASSIAN_API_TOKEN")

    def validate(self, require_parent_id: bool = False) -> List[str]:
        """
        Validate the configuration.

        Args:
            require_parent_id: Whether to require parent_id (needed for publishing, not for crawling)

        Returns:
            List of validation errors, empty if valid
        """
        errors = []
        if not self.base_url:
            errors.append("base_url is required")
        if require_parent_id and not self.parent_id:
            errors.append("parent_id is required for publishing operations")
        if not self.username:
            errors.append("username is required")
        if not self.api_token:
            errors.append(
                "api_token is required (either in config or ATLASSIAN_API_TOKEN environment variable)"
            )
        return errors


@dataclass
class PublishConfig:
    """
    Configuration for publishing behavior.

    Attributes:
        folder_to_publish: Folder to publish (relative to working directory)
        use_file_path_as_title: Whether to use file path as page title
        prepend_file_path_to_title: Whether to prepend file path to page title
        frontmatter_from_document_start: Whether to extract frontmatter from document start
        skip_metadata: Whether to skip adding metadata to published pages
        resolve_relative_links: Whether to resolve relative links between documents
        respect_link_dependencies: Whether to respect dependencies between documents when publishing
        auto_fix_hierarchy: Whether to automatically fix page hierarchy based on directory structure
        auto_migrate_legacy: Whether to automatically migrate legacy editor pages to new editor before publishing
        duplicate_similarity_threshold: Threshold for considering pages as duplicates (0.0-1.0, default 0.8)
        render_mermaid_diagrams: Whether to render mermaid diagrams as images
        process_assets: Whether to process assets (images, diagrams) for embedding
        ignore_patterns: List of file patterns to ignore (supports glob patterns like **/TODO.md)
        archive_ignored: Whether to archive/delete ignored files from Confluence if they have page IDs
        enable_sync: Whether to check for remote changes before publishing (default: True)
        auto_resolve_conflicts: Whether to automatically resolve conflicts (default: False)
        prefer_remote_on_conflict: When auto-resolving conflicts, prefer remote changes (default: False, prefers local)
        default_visibility: Default visibility for pages when not specified in frontmatter ("private" or "public", optional)
    """

    folder_to_publish: str = "."
    use_file_path_as_title: bool = False
    prepend_file_path_to_title: bool = False
    frontmatter_from_document_start: bool = True
    skip_metadata: bool = False
    resolve_relative_links: bool = True
    respect_link_dependencies: bool = True
    auto_fix_hierarchy: bool = True
    auto_handle_archived: bool = True
    auto_migrate_legacy: bool = True
    duplicate_similarity_threshold: float = 0.8
    render_mermaid_diagrams: bool = True
    process_assets: bool = True
    ignore_patterns: List[str] = field(default_factory=list)
    archive_ignored: bool = True
    enable_sync: bool = True
    auto_resolve_conflicts: bool = False
    prefer_remote_on_conflict: bool = False
    default_visibility: Optional[str] = None


@dataclass
class MarkdownConfluenceConfig:
    """
    Complete configuration for markdown-confluence.

    Attributes:
        confluence: Confluence connection configuration
        publish: Publishing behavior configuration
    """

    confluence: ConfluenceConfig
    publish: PublishConfig = field(default_factory=PublishConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarkdownConfluenceConfig":
        """
        Create configuration from dictionary.

        Args:
            data: Dictionary with configuration values (supports both nested and flat structures)

        Returns:
            Configuration object
        """
        # Handle both nested structure ({"confluence": {...}, "publish": {...}})
        # and flat structure ({"base_url": ..., "ignore_patterns": ...})
        confluence_dict = data.get("confluence", data)
        publish_dict = data.get("publish", data)

        confluence_data = {
            "base_url": confluence_dict.get("base_url") or confluence_dict.get("confluenceBaseUrl") or os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("CONFLUENCE_URL") or "",
            "parent_id": confluence_dict.get("parent_id") or confluence_dict.get("confluenceParentId") or os.environ.get("CONFLUENCE_PARENT_ID") or "",
            "username": confluence_dict.get("username") or confluence_dict.get("atlassianUserName") or os.environ.get("ATLASSIAN_USER_NAME") or os.environ.get("CONFLUENCE_USERNAME") or "",
            "api_token": confluence_dict.get("api_token") or confluence_dict.get("atlassianApiToken"),
            "space_key": confluence_dict.get("space_key") or confluence_dict.get("confluenceSpaceKey") or os.environ.get("CONFLUENCE_SPACE_KEY") or "",
        }

        publish_data = {
            "folder_to_publish": publish_dict.get("folder_to_publish", publish_dict.get("folderToPublish", ".")),
            "use_file_path_as_title": publish_dict.get("use_file_path_as_title", publish_dict.get("useFilePathAsTitle", False)),
            "prepend_file_path_to_title": publish_dict.get("prepend_file_path_to_title", publish_dict.get("prependFilePathToTitle", False)),
            "frontmatter_from_document_start": publish_dict.get("frontmatter_from_document_start", publish_dict.get("frontmatterFromDocumentStart", True)),
            "skip_metadata": publish_dict.get("skip_metadata", publish_dict.get("skipMetadata", False)),
            "resolve_relative_links": publish_dict.get("resolve_relative_links", publish_dict.get("resolveRelativeLinks", True)),
            "respect_link_dependencies": publish_dict.get("respect_link_dependencies", publish_dict.get("respectLinkDependencies", True)),
            "auto_fix_hierarchy": publish_dict.get("auto_fix_hierarchy", publish_dict.get("autoFixHierarchy", True)),
            "auto_handle_archived": publish_dict.get("auto_handle_archived", publish_dict.get("autoHandleArchived", True)),
            "auto_migrate_legacy": publish_dict.get("auto_migrate_legacy", publish_dict.get("autoMigrateLegacy", True)),
            "duplicate_similarity_threshold": publish_dict.get("duplicate_similarity_threshold", publish_dict.get("duplicateSimilarityThreshold", 0.8)),
            "render_mermaid_diagrams": publish_dict.get("render_mermaid_diagrams", publish_dict.get("renderMermaidDiagrams", True)),
            "process_assets": publish_dict.get("process_assets", publish_dict.get("processAssets", True)),
            "ignore_patterns": publish_dict.get("ignore_patterns", publish_dict.get("ignorePatterns", [])),
            "archive_ignored": publish_dict.get("archive_ignored", publish_dict.get("archiveIgnored", True)),
            "default_visibility": publish_dict.get("default_visibility", publish_dict.get("defaultVisibility")),
        }

        return cls(
            confluence=ConfluenceConfig(**confluence_data), publish=PublishConfig(**publish_data)
        )
