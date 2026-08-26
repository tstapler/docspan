"""Centralised path constants for docspan's local state files."""

import re
from typing import List

STATE_FILENAME = ".markgate-state.json"
BASE_STORE_DIR = ".markgate-base"
BASE_FILE_SUFFIX = ".base"
ORIG_SUFFIX = ".orig"
COMMENTS_SUFFIX = ".comments.md"
GOOGLE_TOKEN_PATH = ".markgate/google_token.json"

_DATA_URI = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=]{20,}")

# How much of an offending data: URI to keep in a warning message -- enough
# to identify the MIME type and confirm it's real, not the multi-hundred-KB
# payload that's the whole problem.
_DATA_URI_PREVIEW_CHARS = 60


def find_data_uris(content: str) -> List[str]:
    """Return a truncated preview of each `data:...;base64,...` URI in `content`.

    Every markdown/text write in docspan is a plain `pathlib.Path(...
    ).write_text(...)` -- there is no shared writer to hook centrally, so
    every call site is expected to call this immediately before writing and
    fold a non-empty result into that pull's warning message (never raise:
    the file is still worth writing, same as any other pull-time residue --
    see projection.py's describe_residue/docs_structure_parser.py's
    unreadable_links for the established pattern this follows).

    Returns an empty list for clean content -- the common case, and cheap
    (`no match found` short-circuits the regex scan).
    """
    return [
        (m.group(0)[:_DATA_URI_PREVIEW_CHARS] + "...")
        for m in _DATA_URI.finditer(content)
    ]
