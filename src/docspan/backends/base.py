"""Abstract backend interface. Every platform adapter implements this."""

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class SyncDirection(str, Enum):
    PUSH = "push"   # local markdown → remote doc
    PULL = "pull"   # remote doc → local markdown
    BOTH = "both"


@dataclass
class RemoteDoc:
    """Represents a remote document retrieved from a platform."""
    doc_id: str
    title: str
    content_markdown: str
    last_modified: Optional[str] = None
    url: Optional[str] = None


@dataclass
class PushResult:
    status: Literal["ok", "conflict", "error", "skipped"]
    doc_id: str
    message: Optional[str] = None
    url: Optional[str] = None


@dataclass
class PullResult:
    status: Literal["ok", "conflict", "error", "skipped"]
    doc_id: str
    local_path: str
    message: Optional[str] = None


class Backend(ABC):
    """
    Base class for all markgate platform backends.

    Implementing a new backend:
      1. Subclass Backend
      2. Set class attribute `name` (e.g. name = "my_backend")
      3. Implement push(), pull(), auth_setup(), and validate_config()
      4. Register in src/markgate/backends/__init__.py
    """

    name: str  # must be overridden in every concrete subclass

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls) and "name" not in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} must define a 'name' class attribute (e.g. name = 'my_backend')"
            )

    @abstractmethod
    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        """Convert local markdown file and update the remote document."""

    @abstractmethod
    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        """Fetch the remote document and write it as local markdown."""

    @abstractmethod
    def auth_setup(self) -> None:
        """Interactive / instructional setup wizard for this backend."""

    @abstractmethod
    def get_remote_version(self, doc_id: str) -> str:
        """
        Return an opaque version token for the current remote document state.
        - Google Docs: returns doc['revisionId'] (opaque string)
        - Confluence: returns str(page['version']['number']) (monotonic integer as string)
        Used by == comparison to detect remote changes between syncs.
        """

    @abstractmethod
    def validate_config(self) -> None:
        """Raise ValueError with a clear message if config is missing required keys."""
