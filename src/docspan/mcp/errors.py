"""Anticipated tool failures, kept SDK-independent so core logic never imports ``mcp``."""
from __future__ import annotations

from typing import Any, Dict, Optional


class DocspanToolError(Exception):
    """A failure the caller can act on; ``code`` is machine-readable."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}
