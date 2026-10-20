"""Tests for ConfluenceBackend.create() — new-page creation for `docspan map`."""
from __future__ import annotations

import pathlib
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


class TestPull:
    def test_pull_writes_markdown_and_returns_ok(self, tmp_path) -> None:
        backend, client = _make_backend(base_url="https://x.atlassian.net")
        client.get_page.return_value = {
            "title": "My Page",
            "body": {"storage": {"value": "<p>hello world</p>"}},
        }
        local_path = str(tmp_path / "page.md")

        result = backend.pull("page-1", local_path)

        assert result.status == "ok"
        assert "hello world" in pathlib.Path(local_path).read_text(encoding="utf-8")

    def test_pull_surfaces_a_data_uri_warning_when_one_survives_conversion(self, tmp_path) -> None:
        """Confluence's pull has no mermaid/appendix recovery chain at all -- a
        base64-embedded image always comes back as a literal data: URI, so the
        `find_data_uris` backstop (core/paths.py) must always fire for it."""
        backend, client = _make_backend(base_url="https://x.atlassian.net")
        data_uri = "data:image/png;base64," + ("A" * 40)
        client.get_page.return_value = {
            "title": "My Page",
            "body": {"storage": {"value": f'<img src="{data_uri}">'}},
        }
        local_path = str(tmp_path / "page.md")

        result = backend.pull("page-1", local_path)

        assert result.status == "warning"
        assert result.message is not None
        assert "data:" in result.message
        assert data_uri in pathlib.Path(local_path).read_text(encoding="utf-8")
