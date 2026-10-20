"""Tests for migration's Epic 1 (`_run_git`/dirty-tree gate), Epic 2
(live/local zip + split), and Epic 3 (stage/swap/commit).

Epic 1's tests are real `git` subprocess calls against a `tmp_path` fixture
repo, not mocks -- per plan.md's Testing Strategy, the dirty-tree gate's edge
cases (untracked, no HEAD, unrelated dirty file elsewhere in the repo) are
exactly the kind of behavior that needs to be proven against real git, not
asserted from a stubbed subprocess.

Epic 2's tests are pure unit tests over `Section`/node fixtures, plus one
stubbed-Docs-client integration test proving `_split_live` end to end.

Epic 3's `_commit_swap` test is likewise a real fixture git repo (Task 3.5) --
history-continuity via `git log --follow` is exactly the kind of git-internal
behavior (rename/copy detection heuristics) that must be proven against real
git, not mocked.
"""

from __future__ import annotations

import copy
import subprocess

import pytest
from pydantic import ValidationError

from docspan.backends.google_docs import migration as migration_module
from docspan.backends.google_docs.manifest import PREAMBLE_HEADING_ID, ManifestStore
from docspan.backends.google_docs.markdown_to_paragraph_parser import (
    MarkdownToParagraphParser,
)
from docspan.backends.google_docs.migration import (
    MigrationError,
    MigrationPreview,
    _acquire_migration_lock,
    _apply_config_update,
    _check_clean_tree,
    _commit_swap,
    _guard_against_single_preamble_only,
    _record_migrated_state,
    _release_migration_lock,
    _rollback,
    _run_git,
    _split_live,
    _split_local,
    _stage_sections,
    _zip_sections,
)
from docspan.backends.google_docs.nodes_to_markdown import render_nodes_to_markdown
from docspan.backends.google_docs.section_splitter import PREAMBLE_SLUG, Section, split_nodes
from docspan.config import ConfigConflictError, Mapping, MarkgateConfig, load_config
from docspan.core.state import MappingState, SyncState


def _git(*args: str, cwd: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(repo_dir) -> None:  # type: ignore[no-untyped-def]
    repo_dir.mkdir(exist_ok=True)
    _git("init", cwd=str(repo_dir))
    _git("config", "user.email", "test@example.com", cwd=str(repo_dir))
    _git("config", "user.name", "Test", cwd=str(repo_dir))


def test_run_git_returns_completed_process_on_success(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)

    result = _run_git("rev-parse", "--show-toplevel", cwd=str(tmp_path))

    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_run_git_raises_migration_error_on_git_not_found(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _raise_not_found(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise_not_found)

    with pytest.raises(MigrationError):
        _run_git("status", cwd=str(tmp_path))


def test_run_git_raises_migration_error_on_timeout(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _raise_timeout(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    with pytest.raises(MigrationError):
        _run_git("status", cwd=str(tmp_path), timeout=1)


def test_run_git_raises_migration_error_on_unexpected_nonzero_exit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)

    with pytest.raises(MigrationError):
        _run_git("not-a-real-git-subcommand", cwd=str(tmp_path))


def test_check_clean_tree_passes_for_clean_tracked_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)
    tracked = tmp_path / "handbook.md"
    tracked.write_text("hello\n", encoding="utf-8")
    _git("add", "handbook.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))

    _check_clean_tree(str(tracked))  # must not raise


def test_check_clean_tree_refuses_dirty_tracked_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)
    tracked = tmp_path / "handbook.md"
    tracked.write_text("hello\n", encoding="utf-8")
    _git("add", "handbook.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))
    tracked.write_text("hello, modified\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="uncommitted changes"):
        _check_clean_tree(str(tracked))


def test_check_clean_tree_refuses_untracked_file_with_distinct_message(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)
    # A repo needs at least one commit for `git rev-parse HEAD` to succeed,
    # so this case is exercised via an unrelated committed file, leaving the
    # file under test itself untracked.
    (tmp_path / "committed.md").write_text("committed\n", encoding="utf-8")
    _git("add", "committed.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))

    untracked = tmp_path / "handbook.md"
    untracked.write_text("hello\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="not yet tracked by git"):
        _check_clean_tree(str(untracked))


def test_check_clean_tree_refuses_when_repo_has_no_head(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)
    tracked = tmp_path / "handbook.md"
    tracked.write_text("hello\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="no commits yet"):
        _check_clean_tree(str(tracked))


def test_check_clean_tree_passes_when_unrelated_file_elsewhere_in_repo_is_dirty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _init_repo(tmp_path)
    tracked = tmp_path / "handbook.md"
    tracked.write_text("hello\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("other\n", encoding="utf-8")
    _git("add", "handbook.md", "other.md", cwd=str(tmp_path))
    _git("commit", "-m", "initial", cwd=str(tmp_path))

    # Dirty a completely unrelated file in the same repo -- the gate is
    # scoped to `tracked`'s own pathspec, so this must not trip it.
    other.write_text("other, modified\n", encoding="utf-8")

    _check_clean_tree(str(tracked))  # must not raise


def test_check_clean_tree_refuses_when_not_inside_a_git_repository(tmp_path) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path / "not-a-repo.md"
    outside.write_text("hello\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="not inside a git repository"):
        _check_clean_tree(str(outside))


# ─────────────────────────────────────────────────────────────────────────────
# Epic 2: live/local zip + split
# ─────────────────────────────────────────────────────────────────────────────


def _section(heading_id: str, title: str, slug: str) -> Section:
    return Section(heading_id=heading_id, title=title, nodes=[], slug=slug)


def test_zip_sections_assigns_live_heading_id_when_counts_and_titles_match() -> None:  # type: ignore[no-untyped-def]
    live = [
        _section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG),
        _section("h.abc123", "First", "first"),
        _section("h.def456", "Second", "second"),
    ]
    local = [
        _section("", "", PREAMBLE_SLUG),
        _section("", "First", "first"),
        _section("", "Second", "second"),
    ]

    zipped = _zip_sections(live, local)

    assert [s.heading_id for s in zipped] == [
        PREAMBLE_HEADING_ID,
        "h.abc123",
        "h.def456",
    ]
    # Content identity (nodes/slug) comes from the local side, not live.
    assert zipped[1].slug == "first"


def test_zip_sections_raises_remote_diverged_error_on_count_mismatch() -> None:  # type: ignore[no-untyped-def]
    live = [
        _section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG),
        _section("h.abc123", "First", "first"),
        _section("h.def456", "Second", "second"),
    ]
    local = [
        _section("", "", PREAMBLE_SLUG),
        _section("", "First", "first"),
    ]

    with pytest.raises(MigrationError, match="remote has diverged"):
        _zip_sections(live, local)


def test_zip_sections_raises_remote_diverged_error_on_title_mismatch_at_same_position() -> None:  # type: ignore[no-untyped-def]
    live = [
        _section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG),
        _section("h.abc123", "First", "first"),
    ]
    local = [
        _section("", "", PREAMBLE_SLUG),
        _section("", "Renamed First", "renamed-first"),
    ]

    with pytest.raises(MigrationError, match="remote has diverged"):
        _zip_sections(live, local)


def test_zip_sections_assigns_placeholder_heading_id_when_live_section_is_falsy() -> None:  # type: ignore[no-untyped-def]
    # A live section whose own heading_id is falsy but not the preamble
    # sentinel (e.g. a freshly written heading Docs hasn't assigned an id to
    # yet) falls back to the placeholder scheme.
    live = [
        _section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG),
        _section("", "First", "first"),
        _section("h.def456", "Second", "second"),
    ]
    local = [
        _section("", "", PREAMBLE_SLUG),
        _section("", "First", "first"),
        _section("", "Second", "second"),
    ]

    zipped = _zip_sections(live, local)

    assert zipped[0].heading_id == PREAMBLE_HEADING_ID
    assert zipped[1].heading_id == "__migrated-1__"
    assert zipped[2].heading_id == "h.def456"


def test_placeholder_heading_ids_never_collide_with_preamble_or_each_other() -> None:  # type: ignore[no-untyped-def]
    live = [_section("", f"Section {i}", f"section-{i}") for i in range(5)]
    local = [_section("", f"Section {i}", f"section-{i}") for i in range(5)]

    zipped = _zip_sections(live, local)

    heading_ids = [s.heading_id for s in zipped]
    assert heading_ids == [f"__migrated-{i}__" for i in range(5)]
    assert PREAMBLE_HEADING_ID not in heading_ids
    assert len(set(heading_ids)) == len(heading_ids)  # no collisions


def test_guard_against_single_preamble_only_refuses_when_zip_is_preamble_only(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    local_file = tmp_path / "doc.md"
    local_file.write_text("Just some plain paragraphs, no headings at all.\n", encoding="utf-8")
    zipped = [_section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG)]

    with pytest.raises(MigrationError):
        _guard_against_single_preamble_only(zipped, str(local_file), "HEADING_1")


def test_guard_against_single_preamble_only_names_deepest_heading_when_present(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    local_file = tmp_path / "doc.md"
    local_file.write_text("## A subheading\n\nSome text.\n", encoding="utf-8")
    zipped = [_section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG)]

    with pytest.raises(MigrationError, match="HEADING_2"):
        _guard_against_single_preamble_only(zipped, str(local_file), "HEADING_1")


def test_guard_against_single_preamble_only_passes_when_zip_has_real_sections() -> None:  # type: ignore[no-untyped-def]
    zipped = [
        _section(PREAMBLE_HEADING_ID, "", PREAMBLE_SLUG),
        _section("h.abc123", "First", "first"),
    ]

    _guard_against_single_preamble_only(zipped, "/does/not/matter.md", "HEADING_1")  # must not raise


def test_split_local_produces_falsy_heading_ids(tmp_path) -> None:  # type: ignore[no-untyped-def]
    local_file = tmp_path / "doc.md"
    local_file.write_text("# First\n\nBody text.\n\n# Second\n\nMore text.\n", encoding="utf-8")

    sections = _split_local(str(local_file), "HEADING_1")

    assert [s.title for s in sections] == ["", "First", "Second"]
    assert all(not s.heading_id or s.heading_id == PREAMBLE_HEADING_ID for s in sections)


def _make_para_element(text: str, style: str = "NORMAL_TEXT", heading_id: str | None = None) -> dict:
    paragraph_style: dict = {"namedStyleType": style}
    if heading_id is not None:
        paragraph_style["headingId"] = heading_id
    return {
        "startIndex": 1,
        "endIndex": 1 + len(text) + 1,
        "paragraph": {
            "paragraphStyle": paragraph_style,
            "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
        },
    }


class _StubDocsClient:
    def __init__(self, doc: dict) -> None:
        self._doc = doc

    def get_document(self, doc_id: str, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
        return self._doc


def test_split_live_harvests_real_heading_ids_from_stubbed_client() -> None:  # type: ignore[no-untyped-def]
    doc = {
        "body": {
            "content": [
                _make_para_element("Intro body.", style="NORMAL_TEXT"),
                _make_para_element("First", style="HEADING_1", heading_id="h.abc123"),
                _make_para_element("Body one.", style="NORMAL_TEXT"),
                _make_para_element("Second", style="HEADING_1", heading_id="h.def456"),
                _make_para_element("Body two.", style="NORMAL_TEXT"),
            ]
        }
    }
    client = _StubDocsClient(doc)

    sections = _split_live(client, "fake-doc-id", "HEADING_1")

    assert [s.title for s in sections] == ["", "First", "Second"]
    assert sections[0].heading_id == PREAMBLE_HEADING_ID
    assert sections[1].heading_id == "h.abc123"
    assert sections[2].heading_id == "h.def456"


def test_split_live_and_split_local_zip_end_to_end(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = {
        "body": {
            "content": [
                _make_para_element("Intro body.", style="NORMAL_TEXT"),
                _make_para_element("First", style="HEADING_1", heading_id="h.abc123"),
                _make_para_element("Body one.", style="NORMAL_TEXT"),
                _make_para_element("Second", style="HEADING_1", heading_id="h.def456"),
                _make_para_element("Body two.", style="NORMAL_TEXT"),
            ]
        }
    }
    client = _StubDocsClient(doc)
    local_file = tmp_path / "doc.md"
    local_file.write_text(
        "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two.\n",
        encoding="utf-8",
    )

    live_sections = _split_live(client, "fake-doc-id", "HEADING_1")
    local_sections = _split_local(str(local_file), "HEADING_1")
    zipped = _zip_sections(live_sections, local_sections)

    assert [s.heading_id for s in zipped] == [
        PREAMBLE_HEADING_ID,
        "h.abc123",
        "h.def456",
    ]
    assert [s.title for s in zipped] == ["", "First", "Second"]


# ─────────────────────────────────────────────────────────────────────────────
# Epic 3: stage sections, atomic swap, git commit
# ─────────────────────────────────────────────────────────────────────────────


def _zipped_from_markdown(markdown: str, split_level: str = "HEADING_1") -> list[Section]:
    """Build a zipped section list straight from local markdown, real heading_ids assigned."""
    nodes = MarkdownToParagraphParser().parse(markdown)
    local_sections = split_nodes(nodes, split_level)
    live_sections = [
        Section(
            heading_id=(PREAMBLE_HEADING_ID if section.heading_id == PREAMBLE_HEADING_ID else f"h.{index}"),
            title=section.title,
            nodes=[],
            slug=section.slug,
        )
        for index, section in enumerate(local_sections)
    ]
    return _zip_sections(live_sections, local_sections)


def test_stage_sections_writes_byte_identical_content_and_manifest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    zipped = _zipped_from_markdown(
        "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two.\n"
    )
    target_dir = tmp_path / "doc"

    staging_dir = _stage_sections(zipped, str(target_dir))

    try:
        files = sorted(p.name for p in staging_dir.glob("*.md"))
        assert files == ["00-preamble.md", "01-first.md", "02-second.md"]
        assert "Body one." in (staging_dir / "01-first.md").read_text(encoding="utf-8")
        assert "Body two." in (staging_dir / "02-second.md").read_text(encoding="utf-8")

        entries = ManifestStore.load(str(staging_dir / "_manifest.yaml"))
        assert [e.filename for e in entries] == files
        assert [e.heading_id for e in entries] == ["__preamble__", "h.1", "h.2"]
    finally:
        import shutil

        shutil.rmtree(staging_dir, ignore_errors=True)


def test_should_LeaveNoTargetDirChanges_when_StagingFailsBeforeSwap(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`_stage_sections` writes only into its own sibling scratch dir
    (`.{dirname}.migrate-{pid}.tmp/`) -- `target_dir` itself is never
    created or touched until `_commit_swap`'s atomic rename. A failure
    partway through staging (here: the second section's markdown rendering
    raises) must therefore leave the real, live target location -- the
    original tracked file and the directory `target_dir` would occupy --
    completely untouched: no partial files, no git status changes.
    """
    repo_root = tmp_path
    _init_repo(repo_root)
    old_file = repo_root / "handbook.md"
    original_content = "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two.\n"
    old_file.write_text(original_content, encoding="utf-8")
    _git("add", "handbook.md", cwd=str(repo_root))
    _git("commit", "-m", "initial", cwd=str(repo_root))

    zipped = _zipped_from_markdown(original_content)
    target_dir = repo_root / "handbook"

    real_render = migration_module.render_nodes_to_markdown
    call_count = {"n": 0}

    def _flaky_render(nodes):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom mid-staging")
        return real_render(nodes)

    monkeypatch.setattr(migration_module, "render_nodes_to_markdown", _flaky_render)

    scratch_dir = None
    try:
        with pytest.raises(RuntimeError, match="boom mid-staging"):
            _stage_sections(zipped, str(target_dir))

        # The real target directory was never created at all -- staging
        # writes exclusively to its own disposable scratch dir.
        assert not target_dir.exists()
        assert old_file.exists()
        assert old_file.read_text(encoding="utf-8") == original_content

        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "handbook.md", "handbook"],
            cwd=str(repo_root), capture_output=True, text=True, check=True,
        )
        assert status.stdout == ""
    finally:
        import shutil

        for candidate in repo_root.glob(".handbook.migrate-*.tmp"):
            scratch_dir = candidate
            shutil.rmtree(candidate, ignore_errors=True)
        assert scratch_dir is not None, "expected a scratch dir to have been created"


def test_commit_swap_preserves_history_via_git_log_follow(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path
    _init_repo(repo_root)
    old_file = repo_root / "handbook.md"
    original_content = (
        "Intro body.\n\n# First\n\nBody one paragraph with enough text to survive "
        "similarity detection across the split.\n\n# Second\n\nBody two paragraph, "
        "likewise long enough for -C20% rename detection to find a match.\n"
    )
    old_file.write_text(original_content, encoding="utf-8")
    _git("add", "handbook.md", cwd=str(repo_root))
    _git("commit", "-m", "initial", cwd=str(repo_root))
    pre_split_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True, check=True
    ).stdout.strip()

    zipped = _zipped_from_markdown(original_content)
    target_dir = repo_root / "handbook"
    staging_dir = _stage_sections(zipped, str(target_dir))

    pre_migration_head = _commit_swap(
        staging_dir, str(target_dir), str(old_file), str(repo_root)
    )

    assert pre_migration_head == pre_split_head
    assert not old_file.exists()
    assert (target_dir / "01-first.md").exists()

    log = subprocess.run(
        ["git", "log", "--follow", "-C20%", "--format=%H", "--", "handbook/01-first.md"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hashes = log.stdout.split()
    assert pre_split_head in commit_hashes

    # One new commit landed containing the delete + all adds.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True, check=True
    )
    assert status.stdout == ""
    show = subprocess.run(
        ["git", "show", "--stat", "--format=", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "handbook.md" in show.stdout
    assert "01-first.md" in show.stdout


def test_migration_preview_from_zipped_computes_rows_without_touching_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    zipped = _zipped_from_markdown(
        "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two three.\n"
    )

    preview = MigrationPreview.from_zipped(zipped)

    assert [row.filename for row in preview.rows] == [
        "00-preamble.md",
        "01-first.md",
        "02-second.md",
    ]
    assert [row.title for row in preview.rows] == ["", "First", "Second"]
    second_row = preview.rows[2]
    # Word/line counts are computed over the rendered markdown (heading line
    # included, per `render_nodes_to_markdown`) -- assert against that
    # rendering directly rather than guessing its exact word count.
    rendered = render_nodes_to_markdown(zipped[2].nodes)
    assert second_row.word_count == len(rendered.split())
    assert second_row.line_count == len(rendered.splitlines())

    table = preview.render()
    # Column headers match ux.md §2's exact dry-run table shape ("Target
    # file"/"Heading"/"Lines"/"Words"), not the Epic-3-placeholder
    # "File"/"Title" names -- reconciled in Epic 5 (Task 5.2's cross-surface
    # consistency requirement with `pull --to-sectioned --dry-run`).
    assert [column.header for column in table.columns] == [
        "Target file",
        "Heading",
        "Lines",
        "Words",
    ]
    assert table.row_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# Epic 4 — state + config update, rollback
# ─────────────────────────────────────────────────────────────────────────────


def _mapping_state(remote_version: str = "v0") -> MappingState:
    return MappingState(
        doc_id="doc123",
        backend="google_docs",
        last_synced_at="2024-01-01T00:00:00+00:00",
        base_hash="base",
        remote_version=remote_version,
        local_hash="local",
    )


def test_should_RecordOneStateEntryPerSection_when_MigrationSucceeds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    zipped = _zipped_from_markdown(
        "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two.\n"
    )
    state = SyncState()
    state.mappings["handbook.md"] = _mapping_state()
    mapping = Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")
    new_local_dir = str(tmp_path / "handbook")
    state_path = str(tmp_path / "state.json")
    state_dir = str(tmp_path / "state_dir")

    save_calls = {"n": 0}
    state.save = lambda path: save_calls.__setitem__("n", save_calls["n"] + 1)  # type: ignore[method-assign]

    created_paths: list = []
    _record_migrated_state(
        state, state_path, state_dir, mapping, zipped, "v1", new_local_dir, created_paths
    )

    section_paths = {
        str(tmp_path / "handbook" / boundary.filename)
        for boundary in migration_module._section_boundaries(zipped)
    }
    assert set(state.mappings.keys()) == section_paths
    assert "handbook.md" not in state.mappings
    assert save_calls["n"] == 1
    assert {str(p) for p in created_paths} == section_paths | {"handbook.md"}


def test_should_RestorePriorStateSnapshot_when_StateWriteFails(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    zipped = _zipped_from_markdown(
        "Intro body.\n\n# First\n\nBody one.\n\n# Second\n\nBody two.\n"
    )
    state = SyncState()
    state.mappings["handbook.md"] = _mapping_state()
    snapshot_before = copy.deepcopy(state.mappings)
    mapping = Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")

    calls = {"n": 0}

    def fake_record_state(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return True

    monkeypatch.setattr(migration_module, "record_state", fake_record_state)
    save_calls = {"n": 0}
    state.save = lambda path: save_calls.__setitem__("n", save_calls["n"] + 1)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="disk full"):
        _record_migrated_state(
            state,
            str(tmp_path / "state.json"),
            str(tmp_path / "state_dir"),
            mapping,
            zipped,
            "v1",
            str(tmp_path / "handbook"),
            [],
        )

    assert state.mappings == snapshot_before
    assert save_calls["n"] == 0


def test_should_ConstructFreshMapping_when_ApplyingConfigUpdate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    mapping = Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")
    config = MarkgateConfig(mappings=[mapping])
    config_path = tmp_path / "markgate.yaml"

    new_mapping = _apply_config_update(
        config, str(config_path), mapping, "HEADING_1", "sections/handbook"
    )

    assert new_mapping is not mapping
    assert new_mapping.sectioned is True
    assert new_mapping.split_level == "HEADING_1"
    assert new_mapping.local == "sections/handbook"
    assert config.mappings[0] is new_mapping

    reloaded = load_config(str(config_path))
    assert reloaded.mappings[0].sectioned is True
    assert reloaded.mappings[0].local == "sections/handbook"


def test_should_RaiseAtConstruction_when_SplitLevelInvalid(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mapping = Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")
    config = MarkgateConfig(mappings=[mapping])
    config_path = tmp_path / "markgate.yaml"

    save_calls = {"n": 0}

    def fake_save_config(*args, **kwargs):  # type: ignore[no-untyped-def]
        save_calls["n"] += 1

    monkeypatch.setattr(migration_module, "save_config", fake_save_config)

    with pytest.raises(ValidationError):
        _apply_config_update(
            config, str(config_path), mapping, "NOT_A_REAL_LEVEL", "sections/handbook"
        )

    assert save_calls["n"] == 0
    # config.mappings is untouched -- construction failed before any swap.
    assert config.mappings == [mapping]


def test_should_TriggerRollback_when_ExpectedMtimeMismatchOnConfigWrite(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mapping = Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")
    config = MarkgateConfig(mappings=[mapping])
    config_path = tmp_path / "markgate.yaml"

    def fake_save_config(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConfigConflictError("markgate.yaml was modified since it was loaded")

    monkeypatch.setattr(migration_module, "save_config", fake_save_config)

    with pytest.raises(ConfigConflictError):
        _apply_config_update(
            config, str(config_path), mapping, "HEADING_1", "sections/handbook"
        )

    # Not retried -- and the in-memory swap is unwound back to the original mapping.
    assert config.mappings == [mapping]
    assert config.mappings[0] is mapping


def test_should_DiscardStagingDir_when_FailureIsPreCommit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list = []
    monkeypatch.setattr(migration_module, "_run_git", lambda *a, **kw: calls.append(a))

    _rollback(
        repo_root=str(tmp_path),
        pre_migration_head=None,
        commit_landed=False,
        created_paths=[],
        prior_state_snapshot=None,
        prior_config_snapshot=None,
    )

    assert calls == []


def test_should_GitResetHardWithCorrectSha_when_FailureIsPostCommit(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list = []
    monkeypatch.setattr(migration_module, "_run_git", lambda *a, **kw: calls.append(a))

    state = SyncState()
    state.mappings["new/00-preamble.md"] = _mapping_state("v1")
    prior_state_snapshot = {"handbook.md": _mapping_state("v0")}
    save_calls = {"n": 0}
    state.save = lambda path: save_calls.__setitem__("n", save_calls["n"] + 1)  # type: ignore[method-assign]

    config = MarkgateConfig(
        mappings=[
            Mapping(
                local="new",
                backend="google_docs",
                remote_id="doc123",
                sectioned=True,
                split_level="HEADING_1",
            )
        ]
    )
    prior_config_snapshot = [Mapping(local="handbook.md", backend="google_docs", remote_id="doc123")]

    _rollback(
        repo_root=str(tmp_path),
        pre_migration_head="abc123",
        commit_landed=True,
        created_paths=[],
        prior_state_snapshot=prior_state_snapshot,
        prior_config_snapshot=prior_config_snapshot,
        state=state,
        state_path=str(tmp_path / "state.json"),
        config=config,
    )

    assert calls == [("reset", "--hard", "abc123")]
    assert state.mappings == prior_state_snapshot
    assert save_calls["n"] == 1
    assert config.mappings == prior_config_snapshot


def test_should_SkipGitResetHard_when_PreCommitHeadNotCaptured(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`commit_landed=True` never happens without a captured head in practice, but the
    reset call is gated on `pre_migration_head is not None` regardless of `commit_landed`
    -- this pins that the gate is on the head, not a separate branch."""
    calls: list = []
    monkeypatch.setattr(migration_module, "_run_git", lambda *a, **kw: calls.append(a))

    _rollback(
        repo_root=str(tmp_path),
        pre_migration_head=None,
        commit_landed=True,
        created_paths=[],
        prior_state_snapshot=None,
        prior_config_snapshot=None,
    )

    assert calls == []


def test_should_ReportRollbackFailed_when_GitResetHardErrors(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_run_git(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise MigrationError("git reset --hard failed (exit 1): fatal: ...")

    monkeypatch.setattr(migration_module, "_run_git", fake_run_git)

    with pytest.raises(MigrationError, match="Rollback FAILED"):
        _rollback(
            repo_root=str(tmp_path),
            pre_migration_head="abc123",
            commit_landed=True,
            created_paths=[],
        )


def test_should_RaiseMigrationError_when_LockAlreadyHeld(tmp_path) -> None:  # type: ignore[no-untyped-def]
    lock_path = _acquire_migration_lock(str(tmp_path), "handbook")
    try:
        with pytest.raises(MigrationError, match="already in progress"):
            _acquire_migration_lock(str(tmp_path), "handbook")
    finally:
        _release_migration_lock(lock_path)

    assert not lock_path.exists()


def test_release_migration_lock_removes_file_and_tolerates_already_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    lock_path = _acquire_migration_lock(str(tmp_path), "handbook")
    assert lock_path.exists()

    _release_migration_lock(lock_path)
    assert not lock_path.exists()

    # Releasing an already-removed lock must not raise.
    _release_migration_lock(lock_path)
