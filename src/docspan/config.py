"""markgate.yaml loader and config model."""

from __future__ import annotations

import os
import pathlib
from typing import Literal, Optional

import yaml
from pydantic import BaseModel

CONFIG_FILENAME = "markgate.yaml"


class GoogleDocsConfig(BaseModel):
    credentials_path: Optional[str] = None
    token_path: Optional[str] = ".markgate/google_token.json"


class ConfluenceConfig(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None


class BackendsConfig(BaseModel):
    google_docs: Optional[GoogleDocsConfig] = None
    confluence: Optional[ConfluenceConfig] = None


class Mapping(BaseModel):
    local: str       # relative path to local markdown file
    backend: str     # "google_docs" or "confluence"
    remote_id: str   # Google Doc ID or Confluence page ID
    direction: Literal["push", "pull", "both"] = "both"


class MarkgateConfig(BaseModel):
    backends: BackendsConfig = BackendsConfig()
    mappings: list[Mapping] = []


def load_config(path: Optional[str] = None) -> MarkgateConfig:
    """Load markgate.yaml, falling back to env vars for credentials."""
    config_path = pathlib.Path(path or CONFIG_FILENAME)

    raw: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    # Env var overrides for Confluence (backwards compat with markdown-confluence)
    if "backends" not in raw:
        raw["backends"] = {}
    if "confluence" not in raw["backends"]:
        raw["backends"]["confluence"] = {}
    cf = raw["backends"]["confluence"]
    cf.setdefault("base_url", os.getenv("CONFLUENCE_BASE_URL"))
    cf.setdefault("username", os.getenv("ATLASSIAN_USER_NAME"))
    cf.setdefault("api_token", os.getenv("CONFLUENCE_API_TOKEN"))

    return MarkgateConfig(**raw)


# ─────────────────────────────────────────────────────────────────────────────
# Central config — registry of projects by prefix, stored under XDG config home.
# ─────────────────────────────────────────────────────────────────────────────

class ProjectEntry(BaseModel):
    markgate: str  # path to this project's markgate.yaml (may contain ~)


class CentralConfig(BaseModel):
    default_prefix: Optional[str] = None
    projects: dict[str, ProjectEntry] = {}


def load_central_config() -> CentralConfig:
    """Load the central config from $XDG_CONFIG_HOME/docspan/config.yaml (empty if absent)."""
    from docspan.core.xdg import central_config_path

    path = central_config_path()
    if not path.exists():
        return CentralConfig()
    return CentralConfig(**(yaml.safe_load(path.read_text()) or {}))


def resolve_active_project(
    prefix: Optional[str] = None,
    config_path: Optional[str] = None,
    cwd: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve which markgate.yaml is active and its storage prefix.

    Returns ``(markgate_path, prefix)``:
    - An explicit ``--config`` path wins → legacy mode ``(config_path, None)`` (storage stays
      beside the file, back-compat).
    - Otherwise consult the central config, selecting a prefix by precedence:
      explicit ``prefix`` → ``DOCSPAN_PREFIX`` env → cwd inside a registered project → ``default_prefix``.
    - ``(None, None)`` means "no central config match" — caller falls back to a local ./markgate.yaml.
    """
    if config_path:
        return (config_path, None)

    central = load_central_config()
    name = prefix or os.getenv("DOCSPAN_PREFIX")

    if not name:
        here = os.path.abspath(cwd or os.getcwd())
        for pname, entry in central.projects.items():
            proj_dir = os.path.dirname(os.path.abspath(os.path.expanduser(entry.markgate)))
            if here == proj_dir or here.startswith(proj_dir + os.sep):
                name = pname
                break

    if not name:
        name = central.default_prefix

    if name and name in central.projects:
        return (os.path.expanduser(central.projects[name].markgate), name)
    return (None, name)
