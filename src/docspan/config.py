"""markgate.yaml loader and config model."""

from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Literal, Optional

import yaml
from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

CONFIG_FILENAME = "markgate.yaml"

# Round-trip YAML: preserves comments/formatting across load→mutate→save so
# save_config() doesn't have to blow away a hand-annotated markgate.yaml just
# to persist one changed field (e.g. a newly-created remote_id).
_ryaml = YAML()
_ryaml.indent(mapping=2, sequence=4, offset=2)
_ryaml.preserve_quotes = True


def _merge_into(raw: object, new: object) -> object:
    """Recursively apply ``new`` onto ``raw`` in place, preserving ``raw``'s
    comments/order for keys and list items that are unchanged."""
    if isinstance(new, dict):
        if not isinstance(raw, dict):
            return new
        for key in list(raw.keys()):
            if key not in new:
                del raw[key]
        for key, value in new.items():
            raw[key] = _merge_into(raw[key], value) if key in raw else value
        return raw
    if isinstance(new, list):
        if not isinstance(raw, list):
            return new
        for i, value in enumerate(new):
            if i < len(raw):
                raw[i] = _merge_into(raw[i], value)
            else:
                raw.append(value)
        del raw[len(new):]
        return raw
    return new


class ConfigConflictError(Exception):
    """Raised when markgate.yaml changed on disk since it was loaded."""


class GoogleDocsConfig(BaseModel):
    # Service-account auth (app / non-user).
    credentials_path: Optional[str] = None
    # Per-user OAuth auth: path to an OAuth client secret JSON (Desktop app).
    oauth_client_secret_path: Optional[str] = None
    # Where the cached OAuth user token is stored/refreshed.
    # None → XDG default ($XDG_CONFIG_HOME/docspan/google_token.json), kept out of the repo.
    token_path: Optional[str] = None
    # On pull, also write a {file}.comments.md sidecar of the doc's comments.
    pull_comments: bool = True


class ConfluenceConfig(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None
    space_key: Optional[str] = None


class BackendsConfig(BaseModel):
    google_docs: Optional[GoogleDocsConfig] = None
    confluence: Optional[ConfluenceConfig] = None


class Mapping(BaseModel):
    local: str       # relative path to local markdown file
    backend: str     # "google_docs" or "confluence"
    # Google Doc ID or Confluence page ID. None means "not yet created" —
    # push treats this as a request to create the remote doc (interactively only).
    remote_id: Optional[str] = None
    direction: Literal["push", "pull", "both"] = "both"
    # Google Docs tab id (e.g. "t.moqlkhpwn82e") to target on a multi-tab doc.
    # None (default) targets the doc's first/default tab — preserves pre-tabs
    # behavior. Ignored by backends that don't support tabs (e.g. Confluence).
    tab_id: Optional[str] = None


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


def config_mtime(path: Optional[str] = None) -> Optional[float]:
    """Return markgate.yaml's current mtime, or None if it doesn't exist yet."""
    config_path = pathlib.Path(path or CONFIG_FILENAME)
    if not config_path.exists():
        return None
    return config_path.stat().st_mtime


def save_config(
    config: MarkgateConfig,
    path: Optional[str] = None,
    expected_mtime: Optional[float] = None,
) -> None:
    """Atomically write markgate.yaml (temp file + os.replace).

    If ``expected_mtime`` is given, aborts with ConfigConflictError when the
    file's mtime no longer matches — i.e. it was edited since it was loaded —
    rather than silently clobbering a concurrent edit.
    """
    config_path = pathlib.Path(path or CONFIG_FILENAME)

    if expected_mtime is not None and config_path.exists():
        current_mtime = config_path.stat().st_mtime
        if current_mtime != expected_mtime:
            raise ConfigConflictError(
                f"{config_path} was modified since it was loaded — reload and retry "
                "to avoid overwriting a concurrent edit."
            )

    new_raw = config.model_dump(exclude_none=True)

    doc: object = CommentedMap()
    if config_path.exists():
        with open(config_path) as f:
            loaded = _ryaml.load(f)
        if loaded is not None:
            doc = loaded
    doc = _merge_into(doc, new_raw)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_path.parent), prefix=f".{config_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            _ryaml.dump(doc, f)
        os.replace(tmp_path, config_path)
    except Exception:
        os.unlink(tmp_path)
        raise


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
