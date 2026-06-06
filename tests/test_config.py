"""Tests for config loading, validation, and Mapping constraints."""

from __future__ import annotations

import os

import pytest
import yaml

from docspan.config import (
    BackendsConfig,
    ConfluenceConfig,
    GoogleDocsConfig,
    Mapping,
    MarkgateConfig,
    load_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mapping.direction validation
# ─────────────────────────────────────────────────────────────────────────────

def test_mapping_direction_defaults_to_both() -> None:
    m = Mapping(local="a.md", backend="confluence", remote_id="123")
    assert m.direction == "both"


@pytest.mark.parametrize("direction", ["push", "pull", "both"])
def test_mapping_direction_accepts_valid_values(direction: str) -> None:
    m = Mapping(local="a.md", backend="confluence", remote_id="123", direction=direction)
    assert m.direction == direction


def test_mapping_direction_rejects_invalid_value() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Mapping(local="a.md", backend="confluence", remote_id="123", direction="both-ways")


def test_mapping_direction_rejects_wrong_case() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Mapping(local="a.md", backend="confluence", remote_id="123", direction="Push")


# ─────────────────────────────────────────────────────────────────────────────
# load_config
# ─────────────────────────────────────────────────────────────────────────────

def test_load_config_returns_empty_defaults_when_no_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = load_config(str(tmp_path / "nonexistent.yaml"))
    assert isinstance(cfg, MarkgateConfig)
    assert cfg.mappings == []
    assert cfg.backends.confluence is None or cfg.backends.confluence.base_url is None


def test_load_config_parses_mappings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "markgate.yaml"
    config_file.write_text(
        yaml.dump({
            "mappings": [
                {"local": "docs/page.md", "backend": "confluence", "remote_id": "999", "direction": "push"},
            ]
        })
    )
    cfg = load_config(str(config_file))
    assert len(cfg.mappings) == 1
    assert cfg.mappings[0].local == "docs/page.md"
    assert cfg.mappings[0].direction == "push"


def test_load_config_env_var_overrides_confluence(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("ATLASSIAN_USER_NAME", "env_user")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "env_token")

    cfg = load_config(str(tmp_path / "missing.yaml"))

    assert cfg.backends.confluence is not None
    assert cfg.backends.confluence.base_url == "https://env.example.com"
    assert cfg.backends.confluence.username == "env_user"
    assert cfg.backends.confluence.api_token == "env_token"


def test_load_config_yaml_takes_precedence_over_env(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://env.example.com")
    config_file = tmp_path / "markgate.yaml"
    config_file.write_text(
        yaml.dump({
            "backends": {
                "confluence": {
                    "base_url": "https://yaml.example.com",
                    "username": "yaml_user",
                    "api_token": "yaml_token",
                }
            }
        })
    )
    cfg = load_config(str(config_file))
    # YAML value should win (setdefault doesn't overwrite existing values)
    assert cfg.backends.confluence.base_url == "https://yaml.example.com"


def test_load_config_no_sync_interval_field() -> None:
    """sync_interval was removed; ensure it doesn't appear on MarkgateConfig."""
    assert not hasattr(MarkgateConfig(), "sync_interval")


# ─────────────────────────────────────────────────────────────────────────────
# Backend validate_config
# ─────────────────────────────────────────────────────────────────────────────

def test_confluence_validate_config_raises_on_missing_creds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    monkeypatch.delenv("ATLASSIAN_USER_NAME", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)

    from docspan.backends.confluence.backend import ConfluenceBackend
    backend = ConfluenceBackend(ConfluenceConfig())
    with pytest.raises(ValueError, match="Missing Confluence config"):
        backend.validate_config()


def test_confluence_validate_config_passes_with_full_creds() -> None:
    from docspan.backends.confluence.backend import ConfluenceBackend
    cfg = ConfluenceConfig(
        base_url="https://x.atlassian.net",
        username="user@x.com",
        api_token="tok",
    )
    ConfluenceBackend(cfg).validate_config()  # must not raise


def test_google_docs_validate_config_raises_without_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)

    from docspan.backends.google_docs.backend import GoogleDocsBackend
    backend = GoogleDocsBackend(GoogleDocsConfig())
    with pytest.raises(ValueError, match="Missing Google Docs credentials"):
        backend.validate_config()


def test_google_docs_validate_config_passes_with_credentials_path() -> None:
    from docspan.backends.google_docs.backend import GoogleDocsBackend
    cfg = GoogleDocsConfig(credentials_path="/path/to/creds.json")
    GoogleDocsBackend(cfg).validate_config()  # must not raise
