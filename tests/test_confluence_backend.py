"""Tests for ConfluenceBackend.create() — new-page creation for `docspan map`."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from docspan.backends.confluence.backend import ConfluenceBackend
from docspan.config import ConfluenceConfig


def _make_backend(**config_kwargs: object) -> tuple[ConfluenceBackend, MagicMock]:
    backend = ConfluenceBackend(ConfluenceConfig(**config_kwargs))
    fake_client = MagicMock()
    backend._client = fake_client
    return backend, fake_client


class TestCreate:
    def test_create_calls_client_with_space_key_and_returns_result(self) -> None:
        backend, client = _make_backend(base_url="https://x.atlassian.net", space_key="ENG")
        client.create_page.return_value = {"id": "new-page-1", "title": "My Page"}

        result = backend.create("My Page")

        assert client.create_page.call_count == 1
        page_arg = client.create_page.call_args[0][0]
        assert page_arg.title == "My Page"
        assert page_arg.space_key == "ENG"
        assert result.doc_id == "new-page-1"
        assert result.title == "My Page"
        assert result.url == "https://x.atlassian.net/pages/new-page-1"

    def test_create_uses_explicit_space_kwarg_over_config(self) -> None:
        backend, client = _make_backend(base_url="https://x.atlassian.net", space_key="ENG")
        client.create_page.return_value = {"id": "new-page-2", "title": "My Page"}

        backend.create("My Page", space="OTHER")

        page_arg = client.create_page.call_args[0][0]
        assert page_arg.space_key == "OTHER"

    def test_create_raises_without_space_key(self) -> None:
        backend, _client = _make_backend(base_url="https://x.atlassian.net")

        with pytest.raises(ValueError, match="space key"):
            backend.create("My Page")
