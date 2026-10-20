"""
Configuration validation with typo detection.

Validates configuration dictionaries and suggests corrections for typos using Levenshtein distance.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculate Levenshtein distance between two strings.

    Args:
        s1: First string
        s2: Second string

    Returns:
        Edit distance between strings
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost of insertions, deletions, or substitutions
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def find_closest_match(key: str, valid_keys: Set[str], max_distance: int = 3) -> Optional[str]:
    """
    Find the closest matching key using Levenshtein distance.

    Args:
        key: The invalid key to match
        valid_keys: Set of valid keys
        max_distance: Maximum edit distance to consider (default: 3)

    Returns:
        Closest matching key, or None if no close match found
    """
    best_match = None
    best_distance = max_distance + 1

    for valid_key in valid_keys:
        distance = levenshtein_distance(key.lower(), valid_key.lower())
        if distance < best_distance:
            best_distance = distance
            best_match = valid_key

    return best_match if best_distance <= max_distance else None


def normalize_key(key: str) -> str:
    """
    Normalize a config key to snake_case.

    Handles common variations:
    - camelCase -> snake_case
    - PascalCase -> snake_case
    - kebab-case -> snake_case

    Args:
        key: Key to normalize

    Returns:
        Normalized key in snake_case
    """
    import re

    # Replace hyphens with underscores
    key = key.replace('-', '_')

    # Insert underscores before uppercase letters (camelCase -> snake_case)
    key = re.sub('([a-z0-9])([A-Z])', r'\1_\2', key)

    # Handle consecutive capitals (HTTPSConnection -> https_connection)
    key = re.sub('([A-Z]+)([A-Z][a-z])', r'\1_\2', key)

    return key.lower()


# Valid configuration keys
VALID_CONFLUENCE_KEYS = {
    'base_url',
    'parent_id',
    'username',
    'api_token',
    'space_key',
}

VALID_PUBLISH_KEYS = {
    'folder_to_publish',
    'use_file_path_as_title',
    'prepend_file_path_to_title',
    'frontmatter_from_document_start',
    'skip_metadata',
    'resolve_relative_links',
    'respect_link_dependencies',
    'auto_fix_hierarchy',
    'auto_handle_archived',
    'auto_migrate_legacy',
    'duplicate_similarity_threshold',
    'render_mermaid_diagrams',
    'process_assets',
    'ignore_patterns',
    'archive_ignored',
    'enable_sync',
    'auto_resolve_conflicts',
    'prefer_remote_on_conflict',
    'default_visibility',
}

# Common typo mappings (camelCase/kebab-case to snake_case)
COMMON_VARIATIONS = {
    'baseUrl': 'base_url',
    'base-url': 'base_url',
    'parentId': 'parent_id',
    'parent-id': 'parent_id',
    'userName': 'username',
    'user-name': 'username',
    'apiToken': 'api_token',
    'api-token': 'api_token',
    'spaceKey': 'space_key',
    'space-key': 'space_key',
    'folderToPublish': 'folder_to_publish',
    'folder-to-publish': 'folder_to_publish',
    'useFilePathAsTitle': 'use_file_path_as_title',
    'use-file-path-as-title': 'use_file_path_as_title',
    'prependFilePathToTitle': 'prepend_file_path_to_title',
    'prepend-file-path-to-title': 'prepend_file_path_to_title',
    'frontmatterFromDocumentStart': 'frontmatter_from_document_start',
    'frontmatter-from-document-start': 'frontmatter_from_document_start',
    'skipMetadata': 'skip_metadata',
    'skip-metadata': 'skip_metadata',
    'resolveRelativeLinks': 'resolve_relative_links',
    'resolve-relative-links': 'resolve_relative_links',
    'respectLinkDependencies': 'respect_link_dependencies',
    'respect-link-dependencies': 'respect_link_dependencies',
    'autoFixHierarchy': 'auto_fix_hierarchy',
    'auto-fix-hierarchy': 'auto_fix_hierarchy',
    'autoHandleArchived': 'auto_handle_archived',
    'auto-handle-archived': 'auto_handle_archived',
    'autoMigrateLegacy': 'auto_migrate_legacy',
    'auto-migrate-legacy': 'auto_migrate_legacy',
    'duplicateSimilarityThreshold': 'duplicate_similarity_threshold',
    'duplicate-similarity-threshold': 'duplicate_similarity_threshold',
    'renderMermaidDiagrams': 'render_mermaid_diagrams',
    'render-mermaid-diagrams': 'render_mermaid_diagrams',
    'processAssets': 'process_assets',
    'process-assets': 'process_assets',
    'ignorePatterns': 'ignore_patterns',
    'ignore-patterns': 'ignore_patterns',
    'archiveIgnored': 'archive_ignored',
    'archive-ignored': 'archive_ignored',
    'enableSync': 'enable_sync',
    'enable-sync': 'enable_sync',
    'autoResolveConflicts': 'auto_resolve_conflicts',
    'auto-resolve-conflicts': 'auto_resolve_conflicts',
    'preferRemoteOnConflict': 'prefer_remote_on_conflict',
    'prefer-remote-on-conflict': 'prefer_remote_on_conflict',
}


def validate_config_section(
    config: Dict[str, Any],
    valid_keys: Set[str],
    section_name: str,
    auto_correct: bool = False
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Validate a configuration section.

    Args:
        config: Configuration dictionary to validate
        valid_keys: Set of valid keys for this section
        section_name: Name of the section (for error messages)
        auto_correct: Whether to auto-correct known typos (default: False)

    Returns:
        Tuple of (corrected_config, errors, warnings)
    """
    errors = []
    warnings = []
    corrected = config.copy()

    for key in list(corrected.keys()):
        if key in valid_keys:
            continue

        # Check for exact match in common variations
        if key in COMMON_VARIATIONS:
            correct_key = COMMON_VARIATIONS[key]
            if auto_correct:
                warnings.append(
                    f"'{section_name}.{key}': Auto-corrected to '{correct_key}' "
                    f"(use snake_case instead of camelCase/kebab-case)"
                )
                corrected[correct_key] = corrected.pop(key)
            else:
                errors.append(
                    f"'{section_name}.{key}': Invalid key. Did you mean '{correct_key}'? "
                    f"(use snake_case instead of camelCase/kebab-case)"
                )
            continue

        # Try to find close match using Levenshtein distance
        closest = find_closest_match(key, valid_keys)
        if closest:
            errors.append(
                f"'{section_name}.{key}': Invalid key. Did you mean '{closest}'?"
            )
        else:
            errors.append(
                f"'{section_name}.{key}': Invalid key. Valid keys are: {', '.join(sorted(valid_keys))}"
            )

    return corrected, errors, warnings


def validate_config_dict(
    config_data: Dict[str, Any],
    auto_correct: bool = False
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Validate entire configuration dictionary.

    Args:
        config_data: Configuration dictionary to validate
        auto_correct: Whether to auto-correct known typos (default: False)

    Returns:
        Tuple of (corrected_config, errors, warnings)
    """
    all_errors = []
    all_warnings = []
    corrected = config_data.copy()

    # Validate top-level structure
    valid_top_keys = {'confluence', 'publish'}
    for key in list(corrected.keys()):
        if key not in valid_top_keys:
            closest = find_closest_match(key, valid_top_keys)
            if closest:
                all_errors.append(
                    f"'{key}': Invalid top-level key. Did you mean '{closest}'?"
                )
            else:
                all_errors.append(
                    f"'{key}': Invalid top-level key. Valid keys are: {', '.join(sorted(valid_top_keys))}"
                )

    # Validate confluence section
    if 'confluence' in corrected:
        if not isinstance(corrected['confluence'], dict):
            all_errors.append("'confluence': Must be a dictionary")
        else:
            corrected_confluence, conf_errors, conf_warnings = validate_config_section(
                corrected['confluence'],
                VALID_CONFLUENCE_KEYS,
                'confluence',
                auto_correct
            )
            corrected['confluence'] = corrected_confluence
            all_errors.extend(conf_errors)
            all_warnings.extend(conf_warnings)

    # Validate publish section
    if 'publish' in corrected:
        if not isinstance(corrected['publish'], dict):
            all_errors.append("'publish': Must be a dictionary")
        else:
            corrected_publish, pub_errors, pub_warnings = validate_config_section(
                corrected['publish'],
                VALID_PUBLISH_KEYS,
                'publish',
                auto_correct
            )
            corrected['publish'] = corrected_publish
            all_errors.extend(pub_errors)
            all_warnings.extend(pub_warnings)

    return corrected, all_errors, all_warnings
