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
