"""Tests for the interactive Google Docs onboarding (helpers + flow; no browser/network)."""

import json

import docspan.backends.google_docs.backend as be
from docspan.backends.google_docs import onboarding
from docspan.config import GoogleDocsConfig

OAUTH_CLIENT = {"installed": {"client_id": "x", "client_secret": "y", "redirect_uris": ["http://localhost"]}}
SA_KEY = {"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com"}


def _write_json(path, data):
    path.write_text(json.dumps(data))
    return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_autodetect_finds_client_secret(tmp_path):
    _write_json(tmp_path / "client_secret_123.json", OAUTH_CLIENT)
    assert onboarding.autodetect_client_secret([str(tmp_path)]) is not None


def test_autodetect_none_when_absent(tmp_path):
    assert onboarding.autodetect_client_secret([str(tmp_path)]) is None


def test_validate_client_secret_ok(tmp_path):
    p = _write_json(tmp_path / "cs.json", OAUTH_CLIENT)
    ok, msg = onboarding.validate_client_secret(p)
    assert ok and msg == ""


def test_validate_client_secret_missing():
    ok, msg = onboarding.validate_client_secret("/nope/cs.json")
    assert not ok and "no file" in msg


def test_validate_client_secret_rejects_service_account(tmp_path):
    p = _write_json(tmp_path / "sa.json", SA_KEY)
    ok, msg = onboarding.validate_client_secret(p)
    assert not ok and "service-account" in msg


def test_validate_service_account_returns_email(tmp_path):
    p = _write_json(tmp_path / "sa.json", SA_KEY)
    ok, email = onboarding.validate_service_account(p)
    assert ok and email == "bot@proj.iam.gserviceaccount.com"


def test_persist_merges_and_preserves(tmp_path):
    cfg = tmp_path / "markgate.yaml"
    cfg.write_text(
        "backends:\n  google_docs:\n    token_path: t.json\n"
        "mappings:\n  - local: a.md\n    backend: google_docs\n    remote_id: X\n    direction: both\n"
    )
    onboarding.persist_google_docs_config(str(cfg), {"oauth_client_secret_path": "/cs.json"})
    import yaml
    data = yaml.safe_load(cfg.read_text())
    assert data["backends"]["google_docs"]["oauth_client_secret_path"] == "/cs.json"
    assert data["backends"]["google_docs"]["token_path"] == "t.json"  # preserved
    assert data["mappings"][0]["local"] == "a.md"  # preserved


# ─────────────────────────────────────────────────────────────────────────────
# Interactive flow
# ─────────────────────────────────────────────────────────────────────────────

class _StubOAuth:
    def __init__(self, **kwargs):
        self.token_path = kwargs.get("token_path") or "/tmp/docspan-test-token.json"

    def has_valid_credentials(self):
        return True

    def get_credentials(self, allow_interactive=True):
        return "CREDS"


class _StubClient:
    def __init__(self, creds):
        self.creds = creds


def test_interactive_oauth_flow_persists(monkeypatch, tmp_path):
    cs = _write_json(tmp_path / "cs.json", OAUTH_CLIENT)
    # Start with NO credentials so the guided flow runs (not the already-configured path).
    cfg = GoogleDocsConfig(token_path=str(tmp_path / "tok.json"))
    backend = be.GoogleDocsBackend(cfg)

    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(be, "is_interactive", lambda: True)
    monkeypatch.setattr(be, "autodetect_client_secret", lambda *a, **k: None)
    monkeypatch.setattr(be, "OAuthAuthenticator", _StubOAuth)
    monkeypatch.setattr(be, "GoogleDocsClient", _StubClient)

    persisted = {}
    monkeypatch.setattr(
        be, "persist_google_docs_config",
        lambda config_path, updates: persisted.update(updates) or "markgate.yaml",
    )

    answers = iter(["1", cs, "y"])  # method → OAuth; client-secret path; save → yes
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    backend.auth_setup(config_path="markgate.yaml")
    assert persisted.get("oauth_client_secret_path") == cs


def test_non_interactive_prints_instructions(monkeypatch, capsys):
    backend = be.GoogleDocsBackend(GoogleDocsConfig())
    monkeypatch.setattr(be, "is_interactive", lambda: False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS", raising=False)
    monkeypatch.delenv("ACCOUNT_A_CREDENTIALS_PATH", raising=False)
    backend.auth_setup()
    out = capsys.readouterr().out
    assert "Google Docs Auth Setup" in out and "OAuth" in out
