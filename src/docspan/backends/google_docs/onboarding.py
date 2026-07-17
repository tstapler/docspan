"""Interactive-onboarding helpers for the Google Docs backend (auth setup UX)."""
from __future__ import annotations

import glob
import json
import os
import pathlib
import sys
from typing import List, Optional, Tuple

import yaml

from docspan.config import CONFIG_FILENAME

OAUTH_HELP = """\
Create an OAuth client — about 2 minutes:
  • Open   https://console.cloud.google.com/apis/credentials
  • Create Credentials → OAuth client ID → Application type: Desktop app
  • Download the JSON
  • Enable the Docs + Drive APIs: https://console.cloud.google.com/apis/library
"""


def is_interactive() -> bool:
    """True only when we can safely prompt (real TTY, not CI)."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.getenv("CI")


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt with a default (Enter accepts the default)."""
    ans = input(prompt).strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def autodetect_client_secret(search_dirs: Optional[List[str]] = None) -> Optional[str]:
    """Find a likely OAuth client_secret*.json in common locations."""
    dirs = search_dirs or [".", ".markgate", os.path.expanduser("~/Downloads")]
    patterns = ("client_secret*.json", "*apps.googleusercontent.com.json")
    for directory in dirs:
        for pattern in patterns:
            hits = sorted(glob.glob(os.path.join(directory, pattern)))
            if hits:
                return hits[0]
    return None


def validate_client_secret(path: str) -> Tuple[bool, str]:
    """Validate that `path` is a Desktop-app OAuth client secret JSON."""
    p = pathlib.Path(os.path.expanduser(str(path)))
    if not p.is_file():
        return False, f"no file at '{path}'."
    try:
        data = json.loads(p.read_text())
    except Exception:
        return False, "that file isn't valid JSON."
    if "installed" in data or "web" in data:
        return True, ""
    if data.get("type") == "service_account":
        return False, "that's a service-account key, not an OAuth client (choose method 2 for that)."
    return False, "that doesn't look like an OAuth client file (expected a Desktop-app client_secret.json)."


def validate_service_account(path: str) -> Tuple[bool, str]:
    """Validate a service-account key JSON; on success the message is the client_email."""
    p = pathlib.Path(os.path.expanduser(str(path)))
    if not p.is_file():
        return False, f"no file at '{path}'."
    try:
        data = json.loads(p.read_text())
    except Exception:
        return False, "that file isn't valid JSON."
    if data.get("type") != "service_account":
        return False, "that doesn't look like a service-account key (expected type: service_account)."
    return True, data.get("client_email", "")


def persist_google_docs_config(config_path: Optional[str], updates: dict) -> str:
    """
    Merge `updates` into backends.google_docs in markgate.yaml, preserving other keys/mappings.

    Returns the path written. (Comments are not preserved — round-trips via PyYAML.)
    """
    path = pathlib.Path(config_path or CONFIG_FILENAME)
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    raw.setdefault("backends", {}).setdefault("google_docs", {})
    for key, value in updates.items():
        if value is not None:
            raw["backends"]["google_docs"][key] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))
    return str(path)
