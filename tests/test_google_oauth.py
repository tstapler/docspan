"""Unit tests for per-user OAuth auth on the Google Docs backend (no network/browser)."""

import datetime

import pytest
from google.oauth2.credentials import Credentials

import docspan.backends.google_docs.backend as be
from docspan.backends.google_docs.auth import OAuthAuthenticator
from docspan.config import GoogleDocsConfig, MarkgateConfig


def _write_token(path, *, expiry):
    """Write a realistic cached-token JSON (round-trips via to_json/from_authorized_user_file)."""
    creds = Credentials(
        token="fake-access-token",
        refresh_token="fake-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="secret",
        scopes=["https://www.googleapis.com/auth/documents"],
    )
    creds.expiry = expiry  # naive UTC
    path.write_text(creds.to_json())
    return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def test_config_parses_oauth_fields() -> None:
    cfg = MarkgateConfig(**{
        "backends": {"google_docs": {
            "oauth_client_secret_path": "/tmp/cs.json",
            "token_path": "/tmp/tok.json",
        }}
    })
    gd = cfg.backends.google_docs
    assert gd.oauth_client_secret_path == "/tmp/cs.json"
    assert gd.token_path == "/tmp/tok.json"


# ─────────────────────────────────────────────────────────────────────────────
# OAuthAuthenticator
# ─────────────────────────────────────────────────────────────────────────────

def test_no_token_file_is_not_valid(tmp_path) -> None:
    a = OAuthAuthenticator(token_path=str(tmp_path / "missing.json"))
    assert a.has_valid_credentials() is False


def test_future_token_is_valid_and_returned_without_network(tmp_path) -> None:
    future = datetime.datetime.utcnow() + datetime.timedelta(days=365)
    tok = _write_token(tmp_path / "tok.json", expiry=future)
    a = OAuthAuthenticator(token_path=tok)
    assert a.has_valid_credentials() is True
    creds = a.get_credentials(allow_interactive=False)  # must not hit the network
    assert creds.valid


def test_expired_but_refreshable_token_reports_valid(tmp_path) -> None:
    past = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    tok = _write_token(tmp_path / "tok.json", expiry=past)
    a = OAuthAuthenticator(token_path=tok)
    # Refreshable (has a refresh token) — reported usable without a browser.
    assert a.has_valid_credentials() is True


def test_no_token_non_interactive_raises(tmp_path) -> None:
    a = OAuthAuthenticator(token_path=str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="interactive auth is disabled"):
        a.get_credentials(allow_interactive=False)


def test_no_client_secret_interactive_raises(tmp_path) -> None:
    a = OAuthAuthenticator(token_path=str(tmp_path / "missing.json"), client_secret_path=None)
    with pytest.raises(ValueError, match="OAuth client secret not configured"):
        a.get_credentials(allow_interactive=True)


# ─────────────────────────────────────────────────────────────────────────────
# Backend auth selection
# ─────────────────────────────────────────────────────────────────────────────

def test_backend_selects_oauth_when_configured(monkeypatch) -> None:
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)

    captured = {}

    class StubClient:
        def __init__(self, creds):
            captured["creds"] = creds

    class StubOAuth:
        def __init__(self, **kwargs):
            captured["oauth_kwargs"] = kwargs

        def has_valid_credentials(self):
            return False

        def get_credentials(self, allow_interactive=True):
            return "OAUTH_CREDS"

    monkeypatch.setattr(be, "GoogleDocsClient", StubClient)
    monkeypatch.setattr(be, "OAuthAuthenticator", StubOAuth)

    backend = be.GoogleDocsBackend(GoogleDocsConfig(oauth_client_secret_path="/x/cs.json"))
    backend._ensure_client()
    assert captured["creds"] == "OAUTH_CREDS"
    assert captured["oauth_kwargs"]["client_secret_path"] == "/x/cs.json"


def test_backend_errors_without_any_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    # token_path points nowhere → no cached token, no client secret → error.
    cfg = GoogleDocsConfig(token_path=str(tmp_path / "none.json"))
    backend = be.GoogleDocsBackend(cfg)
    with pytest.raises(RuntimeError, match="credentials not found"):
        backend._ensure_client()
