"""Abstract backend interface. Every platform adapter implements this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    status: str  # "ok" | "conflict" | "error" | "skipped"
    doc_id: str
    message: Optional[str] = None
    url: Optional[str] = None


@dataclass
class PullResult:
    status: str  # "ok" | "conflict" | "error" | "skipped"
    doc_id: str
    local_path: str
    message: Optional[str] = None


class Backend(ABC):
    """
    Base class for all markgate platform backends.

    Implementing a new backend:
      1. Subclass Backend
      2. Implement push(), pull(), auth_setup(), and validate_config()
      3. Register in src/markgate/backends/__init__.py
    """

    name: str  # e.g. "google_docs", "confluence"

    @abstractmethod
    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        """Convert local markdown file and update the remote document."""

    @abstractmethod
    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        """Fetch the remote document and write it as local markdown."""

    @abstractmethod
    def auth_setup(self) -> None:
        """Interactive OAuth / token setup wizard for this backend."""

    @abstractmethod
    def validate_config(self, config: dict) -> None:
        """Raise ValueError with a clear message if config is missing required keys."""
