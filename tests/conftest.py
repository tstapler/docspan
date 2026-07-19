"""Shared pytest fixtures for docspan's Google Docs backend tests.

Factory fixtures (returning a callable) rather than fixed values, since
callers need to parametrize (e.g. _make_http_error(status, message)) or the
same fixture is reused multiple times per test.
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.config import GoogleDocsConfig


@pytest.fixture
def make_client() -> Callable[[], GoogleDocsClient]:
    """Factory for a GoogleDocsClient with docs_service/drive_service mocked out."""

    def _make() -> GoogleDocsClient:
        client = GoogleDocsClient.__new__(GoogleDocsClient)
        client.docs_service = MagicMock()
        client.drive_service = MagicMock()
        return client

    return _make


@pytest.fixture
def make_http_error() -> Callable[[int, str], HttpError]:
    """Factory for an HttpError with a fake httplib2-style response and body."""

    def _make(status: int, message: str) -> HttpError:
        resp = MagicMock()
        resp.status = status
        return HttpError(resp, message.encode("utf-8"))

    return _make


@pytest.fixture
def make_backend() -> Callable[[], tuple[GoogleDocsBackend, MagicMock]]:
    """Factory for a GoogleDocsBackend wired to a fully-mocked client."""

    def _make() -> tuple[GoogleDocsBackend, MagicMock]:
        backend = GoogleDocsBackend(GoogleDocsConfig())
        fake_client = MagicMock()
        backend._client = fake_client
        return backend, fake_client

    return _make
