"""Unit tests for the Google Docs comments reader / sidecar (no network)."""

import copy

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.backends.google_docs.comments import format_comments_markdown, parse_reply_directives
from docspan.config import GoogleDocsConfig

SAMPLE = [
    {
        "id": "c1",
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
        "id": "c2",
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


def test_formatter_open_comment_has_id_marker_and_directive_block() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    assert "<!-- id:c1 -->" in md
    assert "Reply:" in md
    assert "Resolve: no" in md


def test_formatter_resolved_comment_has_no_directive_block() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    resolved_section = md[md.index("## Resolved"):]
    assert "Reply:" not in resolved_section
    assert "Resolve:" not in resolved_section
    assert "<!-- id:c2 -->" in resolved_section


# ─────────────────────────────────────────────────────────────────────────────
# Reply/resolve directive parsing
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_reply_directives_ignores_untouched_comment() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    assert parse_reply_directives(md) == []


def test_parse_reply_directives_picks_up_reply_text() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    md = md.replace("\nReply:\n", "\nReply: Sounds good, will follow up.\n", 1)
    directives = parse_reply_directives(md)
    assert len(directives) == 1
    assert directives[0].comment_id == "c1"
    assert directives[0].reply == "Sounds good, will follow up."
    assert directives[0].resolve is False


def test_parse_reply_directives_picks_up_resolve_flag() -> None:
    md = format_comments_markdown("My Doc", SAMPLE)
    md = md.replace("\nResolve: no\n", "\nResolve: yes\n", 1)
    directives = parse_reply_directives(md)
    assert len(directives) == 1
    assert directives[0].comment_id == "c1"
    assert directives[0].reply == ""
    assert directives[0].resolve is True


def test_parse_reply_directives_matches_id_to_correct_comment_block() -> None:
    multi = [
        {"id": "a", "author": {"displayName": "A"}, "resolved": False, "content": "first"},
        {"id": "b", "author": {"displayName": "B"}, "resolved": False, "content": "second"},
    ]
    md = format_comments_markdown("Doc", multi)
    md = md.replace("\nResolve: no\n", "\nResolve: yes\n", 1)  # only the first comment's block
    directives = parse_reply_directives(md)
    assert len(directives) == 1
    assert directives[0].comment_id == "a"


# ─────────────────────────────────────────────────────────────────────────────
# Sidecar behavior
# ─────────────────────────────────────────────────────────────────────────────

class _StubClient:
    def __init__(self, comments, name="Doc Title", raises=False):
        self._comments = comments
        self._name = name
        self._raises = raises
        self.replies = []

    def get_comments(self, doc_id):
        if self._raises:
            raise RuntimeError("boom")
        return self._comments

    def get_doc_info(self, doc_id):
        return {"name": self._name}

    def create_reply(self, doc_id, comment_id, content="", resolve=False):
        self.replies.append((doc_id, comment_id, content, resolve))
        if resolve:
            for c in self._comments:
                if c.get("id") == comment_id:
                    c["resolved"] = True
        return {"id": f"reply-{len(self.replies)}", "content": content}


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


# ─────────────────────────────────────────────────────────────────────────────
# respond_to_comments
# ─────────────────────────────────────────────────────────────────────────────

def test_respond_to_comments_posts_reply_and_refreshes_sidecar(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    sample = copy.deepcopy(SAMPLE)
    client = _StubClient(sample, name="Oversub Doc")
    b = _backend(client)
    b._write_comment_sidecar("doc123", str(local))

    sidecar = tmp_path / "doc.md.comments.md"
    text = sidecar.read_text().replace("\nReply:\n", "\nReply: Sounds good.\n", 1)
    sidecar.write_text(text)

    result = b.respond_to_comments("doc123", str(local))
    assert result.posted == 1
    assert result.resolved == 0
    assert client.replies == [("doc123", "c1", "Sounds good.", False)]


def test_respond_to_comments_resolves_and_moves_comment_to_resolved_section(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    sample = copy.deepcopy(SAMPLE)
    client = _StubClient(sample, name="Oversub Doc")
    b = _backend(client)
    b._write_comment_sidecar("doc123", str(local))

    sidecar = tmp_path / "doc.md.comments.md"
    text = sidecar.read_text().replace("\nResolve: no\n", "\nResolve: yes\n", 1)
    sidecar.write_text(text)

    result = b.respond_to_comments("doc123", str(local))
    assert result.posted == 0
    assert result.resolved == 1
    assert client.replies == [("doc123", "c1", "", True)]

    refreshed = sidecar.read_text()
    assert refreshed.index("c1") > refreshed.index("## Resolved")


def test_respond_to_comments_no_directives_is_noop(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    sample = copy.deepcopy(SAMPLE)
    client = _StubClient(sample, name="Oversub Doc")
    b = _backend(client)
    b._write_comment_sidecar("doc123", str(local))

    result = b.respond_to_comments("doc123", str(local))
    assert result.posted == 0
    assert result.resolved == 0
    assert client.replies == []


def test_respond_to_comments_missing_sidecar_is_noop(tmp_path) -> None:
    local = tmp_path / "doc.md"
    local.write_text("body")
    client = _StubClient(copy.deepcopy(SAMPLE))
    b = _backend(client)

    result = b.respond_to_comments("doc123", str(local))
    assert result.posted == 0
    assert result.resolved == 0
    assert client.replies == []
