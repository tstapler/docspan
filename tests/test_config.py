"""Tests for config loading, validation, and Mapping constraints."""

from __future__ import annotations

import os

import pytest
import yaml

from docspan.config import (
    ConfigConflictError,
    ConfluenceConfig,
    GoogleDocsConfig,
    Mapping,
    MarkgateConfig,
    config_mtime,
    load_config,
    save_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# Mapping.direction validation
# ─────────────────────────────────────────────────────────────────────────────

def test_mapping_direction_defaults_to_both() -> None:
    m = Mapping(local="a.md", backend="confluence", remote_id="123")
    assert m.direction == "both"


def test_mapping_remote_id_optional_defaults_to_none() -> None:
    m = Mapping(local="a.md", backend="google_docs")
    assert m.remote_id is None


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


def test_load_config_mapping_without_remote_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "markgate.yaml"
    config_file.write_text(
        yaml.dump({"mappings": [{"local": "docs/page.md", "backend": "google_docs"}]})
    )
    cfg = load_config(str(config_file))
    assert cfg.mappings[0].remote_id is None


# ─────────────────────────────────────────────────────────────────────────────
# save_config / config_mtime
# ─────────────────────────────────────────────────────────────────────────────

def test_save_config_writes_atomically_and_round_trips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "markgate.yaml"
    cfg = MarkgateConfig(mappings=[Mapping(local="a.md", backend="google_docs", remote_id="doc1")])
    save_config(cfg, str(config_file))

    assert config_file.exists()
    # no leftover temp files
    assert list(tmp_path.glob(".*.tmp")) == []
    reloaded = load_config(str(config_file))
    assert reloaded.mappings[0].remote_id == "doc1"


def test_save_config_omits_none_remote_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "markgate.yaml"
    cfg = MarkgateConfig(mappings=[Mapping(local="a.md", backend="google_docs")])
    save_config(cfg, str(config_file))
    raw = yaml.safe_load(config_file.read_text())
    assert "remote_id" not in raw["mappings"][0]


def test_config_mtime_none_when_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert config_mtime(str(tmp_path / "missing.yaml")) is None


def test_save_config_detects_concurrent_edit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import time

    config_file = tmp_path / "markgate.yaml"
    cfg = MarkgateConfig(mappings=[Mapping(local="a.md", backend="google_docs", remote_id="doc1")])
    save_config(cfg, str(config_file))
    mtime_before = config_mtime(str(config_file))

    # Simulate a concurrent edit by another process changing mtime.
    time.sleep(0.01)
    config_file.write_text(config_file.read_text() + "\n# edited concurrently\n")
    os.utime(config_file, (time.time() + 5, time.time() + 5))

    with pytest.raises(ConfigConflictError):
        save_config(cfg, str(config_file), expected_mtime=mtime_before)


def test_save_config_succeeds_when_mtime_matches(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config_file = tmp_path / "markgate.yaml"
    cfg = MarkgateConfig(mappings=[Mapping(local="a.md", backend="google_docs", remote_id="doc1")])
    save_config(cfg, str(config_file))
    mtime_before = config_mtime(str(config_file))

    save_config(cfg, str(config_file), expected_mtime=mtime_before)  # must not raise


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


@pytest.mark.parametrize("missing_field,expected_fragment", [
    ("base_url", "base_url / CONFLUENCE_BASE_URL"),
    ("username", "username / ATLASSIAN_USER_NAME"),
    ("api_token", "api_token / CONFLUENCE_API_TOKEN"),
])
def test_confluence_validate_config_reports_each_missing_field(
    monkeypatch,  # type: ignore[no-untyped-def]
    missing_field: str,
    expected_fragment: str,
) -> None:
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    monkeypatch.delenv("ATLASSIAN_USER_NAME", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)

    all_creds = {"base_url": "https://x.atlassian.net", "username": "user@x.com", "api_token": "tok"}
    del all_creds[missing_field]

    from docspan.backends.confluence.backend import ConfluenceBackend
    with pytest.raises(ValueError, match=expected_fragment):
        ConfluenceBackend(ConfluenceConfig(**all_creds)).validate_config()


def test_confluence_validate_config_passes_via_env_vars(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://env.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_USER_NAME", "env_user")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "env_tok")

    from docspan.backends.confluence.backend import ConfluenceBackend
    ConfluenceBackend(ConfluenceConfig()).validate_config()  # must not raise


def test_google_docs_validate_config_raises_without_credentials(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)

    from docspan.backends.google_docs.backend import GoogleDocsBackend
    # Isolate from a real cached OAuth token that may exist at the default
    # XDG path on the machine running the tests (_has_any_credentials()
    # checks that file's existence, not just env vars).
    cfg = GoogleDocsConfig(token_path=str(tmp_path / "no_such_token.json"))
    backend = GoogleDocsBackend(cfg)
    with pytest.raises(ValueError, match="Missing Google Docs credentials"):
        backend.validate_config()


def test_google_docs_validate_config_passes_with_credentials_path() -> None:
    from docspan.backends.google_docs.backend import GoogleDocsBackend
    cfg = GoogleDocsConfig(credentials_path="/path/to/creds.json")
    GoogleDocsBackend(cfg).validate_config()  # must not raise


def test_google_docs_validate_config_passes_with_env_var_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ACCOUNT_A_CREDENTIALS_PATH", "/env/path/to/creds.json")
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)

    from docspan.backends.google_docs.backend import GoogleDocsBackend
    GoogleDocsBackend(GoogleDocsConfig()).validate_config()  # must not raise


def test_google_docs_validate_config_passes_with_env_var_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    monkeypatch.setenv("ACCOUNT_A_CREDENTIALS", '{"type": "service_account"}')

    from docspan.backends.google_docs.backend import GoogleDocsBackend
    GoogleDocsBackend(GoogleDocsConfig()).validate_config()  # must not raise
