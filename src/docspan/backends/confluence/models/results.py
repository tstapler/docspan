"""
Result models for operations.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PublishResult:
    """
    Result of a publish operation.

    Attributes:
        success: Whether the operation was successful
        page_id: ID of the published page
        page_url: URL of the published page
        errors: List of error messages
        deferred: Whether the operation was deferred for later processing
        requires_parent: ID of the parent page that needs to be created first
    """

    success: bool
    page_id: Optional[str] = None
    page_url: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    deferred: bool = False
    requires_parent: Optional[str] = None
