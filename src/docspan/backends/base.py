"""Abstract backend interface. Every platform adapter implements this."""

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Optional


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
    status: Literal["ok", "conflict", "error", "skipped", "blocked", "warning"]
    doc_id: str
    message: Optional[str] = None
    url: Optional[str] = None
    # Drive file ids for images uploaded as part of this push but not yet
    # deleted -- populated only when push failed after upload, so a retry
    # can clean them up or reuse them instead of leaking orphaned files.
    retryable_temp_drive_file_ids: List[str] = field(default_factory=list)


@dataclass
class PullResult:
    status: Literal["ok", "conflict", "error", "skipped", "warning"]
    doc_id: str
    local_path: str
    message: Optional[str] = None


@dataclass
class CreateResult:
    doc_id: str
    title: str
    url: Optional[str] = None


class Backend(ABC):
    """
    Base class for all docspan platform backends.

    Implementing a new backend:
      1. Subclass Backend
      2. Set class attribute `name` (e.g. name = "my_backend")
      3. Implement push(), pull(), auth_setup(), and validate_config()
      4. Register in src/docspan/backends/__init__.py
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
    def create(self, title: str, **kwargs: object) -> CreateResult:
        """Create a new remote document/page and return its id/title/url."""

    @abstractmethod
    def auth_setup(self, config_path: Optional[str] = None) -> None:
        """Interactive / instructional setup wizard for this backend.

        `config_path` is the markgate.yaml path (if any) so setup can persist choices.
        """

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

    # Sectioned sync (push_sectioned/pull_sectioned) is only implemented by
    # backends that opt in (currently google_docs). These are concrete, not
    # @abstractmethod, so existing single-file-only backends (e.g.
    # Confluence) aren't forced to implement them. The primary guard against
    # calling these on an unsupported backend is
    # docspan.config.Mapping's cross-field validator, which rejects
    # `sectioned: true` for a backend not in its capability list at
    # config-load time; these defaults are defense in depth for callers that
    # construct a Backend directly, bypassing config validation.
    def push_sectioned(
        self,
        local_dir: str,
        doc_id: str,
        force: bool = False,
        tab_id: Optional[str] = None,
        **kwargs: object,
    ) -> PushResult:
        raise NotImplementedError(f"{self.name} does not support sectioned sync")

    def pull_sectioned(
        self,
        doc_id: str,
        local_dir: str,
        split_level: Optional[str] = None,
        tab_id: Optional[str] = None,
        **kwargs: object,
    ) -> PullResult:
        raise NotImplementedError(f"{self.name} does not support sectioned sync")
