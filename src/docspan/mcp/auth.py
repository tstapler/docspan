"""Map credential failures onto structured tool errors so a long-lived server never dies on one."""
from __future__ import annotations

from typing import Optional

from google.auth.exceptions import RefreshError, TransportError

from docspan.backends.google_docs.tabs import TabNotFoundError
from docspan.mcp.errors import DocspanToolError

AUTH_EXPIRED_MESSAGE = "Auth expired — run 'docspan auth setup google_docs'"


def translate_exception(exc: BaseException) -> Optional[DocspanToolError]:
    """The structured form of ``exc``, or None if it is a genuine crash to let propagate."""
    if isinstance(exc, DocspanToolError):
        return exc
    if isinstance(exc, (RefreshError, TransportError)):
        return DocspanToolError("auth_expired", AUTH_EXPIRED_MESSAGE, {"cause": type(exc).__name__})
    if isinstance(exc, TabNotFoundError):
        return DocspanToolError("tab_not_found", str(exc))
    return None
