"""
Confluence API integration module.

This module provides functionality for interacting with the Confluence API,
including specialized clients for different Confluence resources.
"""

from docspan.backends.confluence.services.confluence.attachment_client import AttachmentClient
from docspan.backends.confluence.services.confluence.base_client import (
    ArchivedPageError,
    BaseConfluenceClient,
    ConfluenceApiError,
    PageNotFoundError,
    RestrictedPageError,
)
from docspan.backends.confluence.services.confluence.client import ConfluenceClient
from docspan.backends.confluence.services.confluence.comment_client import (
    CommentNotFoundError,
    ConfluenceCommentClient,
    InlineCommentNotSupportedError,
)
from docspan.backends.confluence.services.confluence.label_client import LabelClient
from docspan.backends.confluence.services.confluence.page_client import PageClient
from docspan.backends.confluence.services.confluence.space_client import SpaceClient

__all__ = [
    "ConfluenceClient",
    "BaseConfluenceClient",
    "PageClient",
    "AttachmentClient",
    "LabelClient",
    "SpaceClient",
    "ConfluenceCommentClient",
    "ConfluenceApiError",
    "ArchivedPageError",
    "PageNotFoundError",
    "RestrictedPageError",
    "CommentNotFoundError",
    "InlineCommentNotSupportedError",
]