"""Unit tests for the Google Docs comments reader / sidecar (no network)."""


from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.comments import format_comments_markdown
from docspan.config import GoogleDocsConfig

SAMPLE = [
    {
        "author": {"displayName": "JP Phillips"},
        "createdTime": "2026-05-01T10:00:00Z",
        "resolved": False,
        "quotedFileContent": {"value": "oversubscribe CPU resources"},
        "content": "How will this work with autoscalers?",
        "replies": [
            {"author": {"displayName": "Greg Mann"}, "content": "Good question — see below."},
        ],
    },
    {
        "author": {"displayName": "Jose Fernandez"},
        "createdTime": "2026-05-02T09:00:00Z",
        "resolved": True,
        "content": "Why test with Stratum?",
        "replies": [],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Formatter
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_comments_render_empty_string() -> None:
    assert format_comments_markdown("Doc", []) == ""


def test_formatter_groups_open_and_resolved() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    assert md.startswith("# Comments: My Doc")
    assert "## Open" in md and "## Resolved" in md
    # Open comment: author, quoted selection, body, threaded reply.
    assert "JP Phillips" in md
    assert "> oversubscribe CPU resources" in md
    assert "How will this work with autoscalers?" in md
    assert "- **Greg Mann**: Good question — see below." in md
    # Resolved comment appears under Resolved.
    assert "Jose Fernandez" in md
    # Open section precedes Resolved section.
    assert md.index("## Open") < md.index("## Resolved")


def test_formatter_missing_author_is_unknown() -> None:
    md = format_comments_markdown("D", [{"content": "orphan", "resolved": False}])
    assert "Unknown" in md and "orphan" in md


# ─────────────────────────────────────────────────────────────────────────────
# Sidecar behavior
# ─────────────────────────────────────────────────────────────────────────────

class _StubClient:
    def __init__(self, comments, name="Doc Title", raises=False):
        self._comments = comments
        self._name = name
        self._raises = raises

    def get_comments(self, doc_id):
        if self._raises:
            raise RuntimeError("boom")
        return self._comments

    def get_doc_info(self, doc_id):
        return {"name": self._name}


def _backend(client, **cfg):
    b = GoogleDocsBackend(GoogleDocsConfig(**cfg))
    b._client = client
    return b


def test_sidecar_written_when_comments_exist(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    b = _backend(_StubClient(SAMPLE, name="Oversub Doc"))
    b._write_comment_sidecar("doc123", str(local))
    sidecar = tmp_path / "doc.md.comments.md"
    assert sidecar.exists()
    assert "# Comments: Oversub Doc" in sidecar.read_text()


def test_no_sidecar_when_no_comments_and_stale_removed(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    stale = tmp_path / "doc.md.comments.md"
    stale.write_text("# Comments: old")
    b = _backend(_StubClient([]))
    b._write_comment_sidecar("doc123", str(local))
    assert not stale.exists()


def test_pull_comments_disabled_skips_sidecar(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    b = _backend(_StubClient(SAMPLE), pull_comments=False)
    b._write_comment_sidecar("doc123", str(local))
    assert not (tmp_path / "doc.md.comments.md").exists()


def test_comment_fetch_failure_is_best_effort(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    b = _backend(_StubClient([], raises=True))
    # Must not raise, and must not create a sidecar.
    b._write_comment_sidecar("doc123", str(local))
    assert not (tmp_path / "doc.md.comments.md").exists()
