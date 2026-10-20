"""
Configuration module.

This module provides functionality for loading and managing configuration
for the markdown-confluence package.
"""

from docspan.backends.confluence.config.loader import load_config, load_config_from_dict
from docspan.backends.confluence.config.models import (
    ConfluenceConfig,
    MarkdownConfluenceConfig,
    PublishConfig,
)

__all__ = [
    "ConfluenceConfig",
    "PublishConfig",
    "MarkdownConfluenceConfig",
    "load_config",
    "load_config_from_dict",
]
