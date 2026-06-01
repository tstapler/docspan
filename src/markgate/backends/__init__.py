"""Backend registry — maps backend names to their classes."""

from markgate.backends.base import Backend, SyncDirection, RemoteDoc, PushResult, PullResult
from markgate.backends.google_docs.backend import GoogleDocsBackend
from markgate.backends.confluence.backend import ConfluenceBackend

BACKENDS: dict[str, type[Backend]] = {
    "google_docs": GoogleDocsBackend,
    "confluence": ConfluenceBackend,
}

__all__ = [
    "Backend",
    "SyncDirection",
    "RemoteDoc",
    "PushResult",
    "PullResult",
    "BACKENDS",
]
