"""Revision history CLI: list / show / diff / restore against a mocked Drive API."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from docspan.backends.google_docs.client import GoogleDocsClient
from docspan.cli.main import app
from docspan.config import Mapping, MarkgateConfig
from docspan.core.revisions import SectionNotFound, restore_section

runner = CliRunner()

REVS = {
    "r1": "# Doc\n\n## Intro\n\nold intro\n\n## Details\n\nold details\n",
    "r2": "# Doc\n\n## Intro\n\nnew intro\n",
}


def make_client(exports=None, pages=None):
    """Real GoogleDocsClient wired to a mocked Drive service."""
    client = object.__new__(GoogleDocsClient)
    drive = MagicMock()
    client.drive_service = drive
    pages = pages or [{"revisions": [
        {"id": "1", "modifiedTime": "2026-10-31T10:00:00Z", "lastModifyingUser": {"displayName": "Ann"}},
        {"id": "2", "modifiedTime": "2026-11-01T11:00:00Z", "lastModifyingUser": {"displayName": "Bob"}},
        {"id": "3", "modifiedTime": "2026-11-02T12:00:00Z", "lastModifyingUser": {"displayName": "Cy"}},
    ]}]
    drive.revisions.return_value.list.return_value.execute.side_effect = pages
    exports = exports if exports is not None else {"1": ("text/markdown", REVS["r1"])}

    def get(fileId, revisionId, fields):
        mime, _ = exports[revisionId]
        req = MagicMock()
        req.execute.return_value = {"id": revisionId, "exportLinks": {mime: f"https://x/{revisionId}"}}
        return req

    drive.revisions.return_value.get.side_effect = get

    def request(url):
        rid = url.rsplit("/", 1)[1]
        return SimpleNamespace(status=200), exports[rid][1].encode()

    drive._http.request.side_effect = request
    return client


@pytest.fixture
def env(tmp_path):
    local = tmp_path / "doc.md"
    local.write_text("# Doc\n\n## Intro\n\nlocal intro\n\n## Details\n\nlocal details\n", encoding="utf-8")
    cfg = tmp_path / "docspan.yaml"
    cfg.write_text("mappings: []\n", encoding="utf-8")
    mapping = Mapping(local=str(local), backend="google_docs", remote_id="DOC1")
    return local, str(cfg), MarkgateConfig(mappings=[mapping])


def invoke(env, client, *args):
    local, cfg, config = env
    backend = SimpleNamespace(_ensure_client=lambda: None, _client=client)
    with patch("docspan.cli.main.load_config", return_value=config), \
         patch("docspan.cli.main._get_backend", return_value=backend):
        return runner.invoke(app, [*args, "--config", cfg])


def test_revisions_list_command(env):
    page1 = {"revisions": [{"id": "1", "modifiedTime": "2026-10-31T10:00:00Z",
                            "lastModifyingUser": {"displayName": "Ann"}}], "nextPageToken": "t"}
    page2 = {"revisions": [{"id": "2", "modifiedTime": "2026-11-01T11:00:00Z",
                            "lastModifyingUser": {"displayName": "Bob"}}]}
    result = invoke(env, make_client(pages=[page1, page2]), "revisions", env[0].as_posix())
    assert result.exit_code == 0, result.output
    for expected in ("2026-10-31T10:00:00Z", "Ann", "2026-11-01T11:00:00Z", "Bob"):
        assert expected in result.output
    assert "2 revisions" in result.output


def test_revisions_list_empty(env):
    result = invoke(env, make_client(pages=[{"revisions": []}]), "revisions", env[0].as_posix())
    assert result.exit_code == 0
    assert "No revisions found" in result.output


def test_revision_show_command(env):
    result = invoke(env, make_client(), "revision-show", env[0].as_posix(), "--rev", "1")
    assert result.exit_code == 0, result.output
    assert "old details" in result.output
    assert "warning" not in result.output


def test_revision_show_falls_back_to_plain_text_with_warning(env):
    client = make_client(exports={"1": ("text/plain", "plain body\n")})
    result = invoke(env, client, "revision-show", env[0].as_posix(), "--rev", "1")
    assert result.exit_code == 0
    assert "plain body" in result.output
    assert "text/markdown is not in this revision's exportLinks" in result.output


def test_revision_show_tab_is_best_effort(env):
    result = invoke(env, make_client(), "revision-show", env[0].as_posix(), "--rev", "1", "--tab", "T")
    assert "best-effort" in result.output


def test_revision_diff_against_local(env):
    result = invoke(env, make_client(), "revision-diff", env[0].as_posix(), "--rev", "1")
    assert result.exit_code == 0
    assert "-old details" in result.output and "+local details" in result.output


def test_revision_diff_same_revision_is_empty(env):
    result = invoke(env, make_client(), "revision-diff", env[0].as_posix(), "--rev", "1", "--rev", "1")
    assert result.exit_code == 0
    assert "No differences" in result.output


def test_restore_section_merge(env):
    local = env[0]
    result = invoke(env, make_client(), "restore", local.as_posix(), "--rev", "1", "--section", "Details")
    assert result.exit_code == 0, result.output
    text = local.read_text(encoding="utf-8")
    assert "old details" in text and "local details" not in text
    assert "local intro" in text


def test_restore_missing_heading_leaves_file_unchanged(env):
    local = env[0]
    before = local.read_text(encoding="utf-8")
    client = make_client(exports={"2": ("text/markdown", REVS["r2"])})
    result = invoke(env, client, "restore", local.as_posix(), "--rev", "2", "--section", "Details")
    assert result.exit_code == 1
    assert "heading 'Details' not found in revision 2" in result.output
    assert local.read_text(encoding="utf-8") == before


def test_restore_without_section_replaces_everything(env):
    local = env[0]
    invoke(env, make_client(), "restore", local.as_posix(), "--rev", "1")
    assert local.read_text(encoding="utf-8") == REVS["r1"]


def test_restore_section_includes_subsections_and_ignores_fenced_headings():
    old = "## A\n\n```\n## Details\n```\n\n## Details\n\nold\n\n### Sub\n\nsub\n\n## Z\n\nz\n"
    local = "## A\n\nmine\n\n## Details\n\nlocal\n\n## Z\n\nlocal z\n"
    out = restore_section(local, old, "## Details", "9")
    assert out == "## A\n\nmine\n\n## Details\n\nold\n\n### Sub\n\nsub\n\n## Z\n\nlocal z\n"
    with pytest.raises(SectionNotFound):
        restore_section(local, old, "Nope", "9")


def test_find_section_fence_tracks_marker_char_and_length():
    from docspan.core.revisions import find_section
    md = "# A\n```\n# no\n~~~\n# B\n```\n# C\n"
    assert find_section(md, "C") is not None
    assert find_section(md, "B") is None
    assert find_section("# A\n````\n```\n# in\n````\n# D\n", "in") is None
    assert find_section("# A\n````\n```\n# in\n````\n# D\n", "D") is not None


def test_find_section_keeps_trailing_hash_without_space():
    from docspan.core.revisions import find_section
    assert find_section("# C#\nx\n", "C#") is not None
    assert find_section("# C#\nx\n", "C") is None
    assert find_section("# C ##\nx\n", "C") is not None


def test_restore_whole_file_writes_backup(env):
    local = env[0]
    before = local.read_text(encoding="utf-8")
    invoke(env, make_client(), "restore", local.as_posix(), "--rev", "1")
    assert (local.parent / "doc.md.bak").read_text(encoding="utf-8") == before


def test_restore_whole_file_never_overwrites_existing_backup(env):
    local = env[0]
    original = local.read_text(encoding="utf-8")
    invoke(env, make_client(), "restore", local.as_posix(), "--rev", "1")
    invoke(env, make_client(), "restore", local.as_posix(), "--rev", "1")
    assert (local.parent / "doc.md.bak").read_text(encoding="utf-8") == original
    assert (local.parent / "doc.md.bak.1").exists()


def test_headings_ignores_inline_backtick_and_indented_fences():
    from docspan.core.revisions import _headings
    assert [h[2] for h in _headings("```x``` inline\n# Real\n")] == ["Real"]
    assert [h[2] for h in _headings("    ```\n# Real\n")] == ["Real"]


def test_restore_and_local_diff_reject_sectioned_directory(tmp_path):
    d = tmp_path / "sections"
    d.mkdir()
    cfg = tmp_path / "docspan.yaml"
    cfg.write_text("mappings: []\n", encoding="utf-8")
    config = MarkgateConfig(mappings=[Mapping(local=str(d), backend="google_docs", remote_id="DOC1")])
    e = (d, str(cfg), config)
    for args in (("restore", d.as_posix(), "--rev", "1", "--section", "X"),
                 ("restore", d.as_posix(), "--rev", "1"),
                 ("revision-diff", d.as_posix(), "--rev", "1")):
        result = invoke(e, make_client(), *args)
        assert result.exit_code == 1
        assert "is a directory" in " ".join(result.output.split())


def test_drive_export_error_paths():
    client = make_client()
    client.drive_service._http.request.side_effect = None
    client.drive_service._http.request.return_value = (SimpleNamespace(status=403), b"")
    with pytest.raises(PermissionError):
        client.get_revision_export("DOC1", "1")
    client.drive_service._http.request.return_value = (SimpleNamespace(status=500), b"")
    with pytest.raises(RuntimeError, match="500"):
        client.get_revision_export("DOC1", "1")
    client.drive_service.revisions.return_value.get.side_effect = None
    client.drive_service.revisions.return_value.get.return_value.execute.return_value = {"id": "1"}
    with pytest.raises(ValueError, match="exportLink"):
        client.get_revision_export("DOC1", "1")


def test_list_revisions_does_not_request_email():
    client = make_client()
    client.list_revisions("DOC1")
    fields = client.drive_service.revisions.return_value.list.call_args.kwargs["fields"]
    assert "emailAddress" not in fields
