"""Tests for XDG paths, central config, project resolution, and state routing (offline)."""

import os

from docspan.config import load_central_config, resolve_active_project
from docspan.core import get_state_dir, xdg


def _use_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DOCSPAN_PREFIX", raising=False)


def _write_central(tmp_path, text):
    p = tmp_path / "cfg" / "docspan" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# XDG resolution
# ─────────────────────────────────────────────────────────────────────────────

def test_xdg_env_overrides(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    assert xdg.config_home() == (tmp_path / "cfg" / "docspan")
    assert xdg.state_home() == (tmp_path / "state" / "docspan")
    assert xdg.central_config_path() == (tmp_path / "cfg" / "docspan" / "config.yaml")
    assert xdg.state_dir_for_prefix("proj") == (tmp_path / "state" / "docspan" / "proj")
    assert xdg.default_token_path("proj").name == "google_token.json"


def test_xdg_defaults(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    home = os.path.expanduser("~")
    assert str(xdg.config_home()) == os.path.join(home, ".config", "docspan")
    assert str(xdg.state_home()) == os.path.join(home, ".local", "state", "docspan")


# ─────────────────────────────────────────────────────────────────────────────
# Central config + resolution
# ─────────────────────────────────────────────────────────────────────────────

def test_load_central_empty(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    c = load_central_config()
    assert c.default_prefix is None and c.projects == {}


def test_resolve_config_path_is_legacy(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    mg, prefix = resolve_active_project(config_path="/some/markgate.yaml")
    assert mg == "/some/markgate.yaml" and prefix is None


def test_resolve_default_prefix(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    _write_central(tmp_path, "default_prefix: docs\nprojects:\n  docs:\n    markgate: ~/d/markgate.yaml\n")
    mg, prefix = resolve_active_project()
    assert prefix == "docs"
    assert mg == os.path.expanduser("~/d/markgate.yaml")


def test_resolve_explicit_prefix_wins(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    _write_central(
        tmp_path,
        "default_prefix: a\nprojects:\n  a:\n    markgate: /a/markgate.yaml\n  b:\n    markgate: /b/markgate.yaml\n",
    )
    mg, prefix = resolve_active_project(prefix="b")
    assert prefix == "b" and mg == "/b/markgate.yaml"


def test_resolve_env_prefix(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    _write_central(tmp_path, "projects:\n  b:\n    markgate: /b/markgate.yaml\n")
    monkeypatch.setenv("DOCSPAN_PREFIX", "b")
    mg, prefix = resolve_active_project()
    assert prefix == "b" and mg == "/b/markgate.yaml"


def test_resolve_cwd_match(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    proj = tmp_path / "myproj"
    proj.mkdir()
    _write_central(tmp_path, f"projects:\n  mp:\n    markgate: {proj}/markgate.yaml\n")
    mg, prefix = resolve_active_project(cwd=str(proj))
    assert prefix == "mp" and mg == f"{proj}/markgate.yaml"


def test_resolve_none_when_no_match(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    mg, prefix = resolve_active_project(cwd=str(tmp_path))
    assert mg is None and prefix is None


# ─────────────────────────────────────────────────────────────────────────────
# State-dir routing
# ─────────────────────────────────────────────────────────────────────────────

def test_state_dir_prefix_uses_xdg(monkeypatch, tmp_path):
    _use_xdg(monkeypatch, tmp_path)
    assert get_state_dir(None, "proj") == str(tmp_path / "state" / "docspan" / "proj")


def test_state_dir_legacy_beside_config(tmp_path):
    cfg = tmp_path / "sub" / "markgate.yaml"
    assert get_state_dir(str(cfg), None) == str(tmp_path / "sub")


def test_state_dir_legacy_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert get_state_dir(None, None) == str(tmp_path)
