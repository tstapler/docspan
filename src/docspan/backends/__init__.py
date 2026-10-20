"""Backend registry — maps backend names to their classes."""

from docspan.backends.base import Backend, PullResult, PushResult, RemoteDoc, SyncDirection
from docspan.backends.confluence.backend import ConfluenceBackend
from docspan.backends.google_docs.backend import GoogleDocsBackend

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
