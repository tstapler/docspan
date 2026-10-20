"""
Configuration loading utilities.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from docspan.backends.confluence.config.models import MarkdownConfluenceConfig
from docspan.backends.confluence.config.validation import validate_config_dict

logger = logging.getLogger(__name__)


def load_config(path: Union[str, Path], allow_env_only: bool = True, folder_to_publish: Optional[str] = None, require_parent_id: bool = False) -> MarkdownConfluenceConfig:
    """
    Load configuration from a JSON file or environment variables.

    Args:
        path: Path to the configuration file
        allow_env_only: Whether to allow configuration from environment variables only
                        when config file is not found
        folder_to_publish: Override the folder to publish in the config
        require_parent_id: Whether to require parent_id in configuration (needed for publishing, not for crawling)

    Returns:
        Configuration object

    Raises:
        FileNotFoundError: If the file doesn't exist and allow_env_only is False
        json.JSONDecodeError: If the file isn't valid JSON
        ValueError: If the configuration is invalid
    """
    config_path = Path(path)
    config_data = {}

    if config_path.exists():
        # Load from file
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    elif not allow_env_only:
        raise FileNotFoundError(f"Configuration file not found: {path}")
    else:
        # Try to load configuration from environment variables
        # This will create an empty config_data and rely on environment variables
        # for the required configuration values
        pass

    # If folder_to_publish is provided, override the setting
    if folder_to_publish:
        if "publish" not in config_data:
            config_data["publish"] = {}
        config_data["publish"]["folder_to_publish"] = folder_to_publish

    return load_config_from_dict(config_data, require_parent_id=require_parent_id)


def load_config_from_dict(config_data: Dict[str, Any], require_parent_id: bool = False) -> MarkdownConfluenceConfig:
    """
    Load configuration from a dictionary.

    Args:
        config_data: Dictionary with configuration values
        require_parent_id: Whether to require parent_id in configuration (needed for publishing, not for crawling)

    Returns:
        Configuration object

    Raises:
        ValueError: If the configuration is invalid
    """
    # Validate configuration structure and detect typos
    corrected_config, validation_errors, validation_warnings = validate_config_dict(
        config_data,
        auto_correct=True  # Auto-correct known typos like camelCase -> snake_case
    )

    # Show warnings for auto-corrections
    for warning in validation_warnings:
        logger.warning(f"Configuration: {warning}")

    # Raise errors if validation failed
    if validation_errors:
        error_msg = "Invalid configuration:\n" + "\n".join(f"  - {err}" for err in validation_errors)
        raise ValueError(error_msg)

    # Create config object with corrected data
    config = MarkdownConfluenceConfig.from_dict(corrected_config)

    # Validate required fields
    field_errors = config.confluence.validate(require_parent_id=require_parent_id)
    if field_errors:
        raise ValueError(f"Invalid configuration: {', '.join(field_errors)}")

    return config


def get_api_token_from_env() -> Optional[str]:
    """
    Get Atlassian API token from environment variable.

    Returns:
        API token or None if not set
    """
    return os.environ.get("ATLASSIAN_API_TOKEN")
