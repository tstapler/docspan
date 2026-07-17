"""XDG Base Directory resolution for docspan's config, state, and credentials.

Split of concerns:
- config  ($XDG_CONFIG_HOME/docspan)  — central config.yaml + cached OAuth tokens
- state   ($XDG_STATE_HOME/docspan)   — sync state + content-addressed base store, per prefix

See https://specifications.freedesktop.org/basedir-spec/latest/
"""
from __future__ import annotations

import os
import pathlib

APP = "docspan"


def _home_dir(env_var: str, default_rel: str) -> pathlib.Path:
    """Resolve an XDG base dir from its env var, falling back to ~/<default_rel>."""
    raw = os.environ.get(env_var)
    if raw:
        return pathlib.Path(os.path.expanduser(raw))
    return pathlib.Path(os.path.expanduser("~")) / default_rel


def xdg_config_home() -> pathlib.Path:
    return _home_dir("XDG_CONFIG_HOME", ".config")


def xdg_state_home() -> pathlib.Path:
    return _home_dir("XDG_STATE_HOME", ".local/state")


def xdg_data_home() -> pathlib.Path:
    return _home_dir("XDG_DATA_HOME", ".local/share")


def config_home() -> pathlib.Path:
    """docspan's config dir: $XDG_CONFIG_HOME/docspan."""
    return xdg_config_home() / APP


def state_home() -> pathlib.Path:
    """docspan's state dir root: $XDG_STATE_HOME/docspan."""
    return xdg_state_home() / APP


def central_config_path() -> pathlib.Path:
    """Path to the central config: $XDG_CONFIG_HOME/docspan/config.yaml."""
    return config_home() / "config.yaml"


def state_dir_for_prefix(prefix: str) -> pathlib.Path:
    """Per-project state dir: $XDG_STATE_HOME/docspan/<prefix>."""
    return state_home() / prefix


def default_token_path(prefix: str) -> pathlib.Path:
    """Default cached-OAuth-token path for a project: $XDG_CONFIG_HOME/docspan/<prefix>/google_token.json."""
    return config_home() / prefix / "google_token.json"
