"""Tests for the MCP query layer: outline, read_section, index, links, crawl, diff, server."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from docspan.mcp.crawl import crawl
from docspan.mcp.errors import DocspanToolError
from docspan.mcp.index import DocspanIndex
from docspan.mcp.links import extract_doc_id, parse_doc_url, resolve_links
from docspan.mcp.sections import find_section_by_path, parse_tabs
from docspan.mcp.service import DocService


def _para(text, style="NORMAL_TEXT", heading_id=None, link=None, pos=[1]):
    start = pos[0]
    end = start + len(text) + 1
    pos[0] = end
    style_dict = {"namedStyleType": style}
    if heading_id:
        style_dict["headingId"] = heading_id
    text_style = {"link": {"url": link}} if link else {}
    return {"startIndex": start, "endIndex": end, "paragraph": {
        "paragraphStyle": style_dict,
        "elements": [{"startIndex": start, "endIndex": end,
                      "textRun": {"content": text + "\n", "textStyle": text_style}}]}}


def u(doc_id):
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def make_doc(doc_id="DOC1", revision="rev1", links=()):
    plan = [
        _para("Intro text before any heading."),
        _para("Overview", "HEADING_1", "h.over"),
        _para("Overview body about widgets."),
        _para("Details", "HEADING_2", "h.det"),
        _para("Details body about gadgets and sprockets."),
        _para("Notes", "HEADING_2", "h.n1"),
        _para("First notes."),
    ]
    weekly = [
        _para("Notes", "HEADING_1", "h.wk"),
        _para("Weekly notes body."),
        _para("Notes", "HEADING_2", "h.wk2"),
        _para("Dup heading body."),
    ] + [_para(f"see {u}", link=u) for u in links]
    return {
        "documentId": doc_id, "title": f"Title {doc_id}", "revisionId": revision,
        "tabs": [
            {"tabProperties": {"tabId": "t.0", "title": "Plan"},
             "documentTab": {"body": {"content": plan}}},
            {"tabProperties": {"tabId": "t.1", "title": "Weekly"},
             "documentTab": {"body": {"content": weekly}}},
        ],
    }


class FakeClient:
    def __init__(self, docs, comments=None):
        self.docs = docs
        self._comments = comments or {}
        self.calls = 0

    def get_document(self, doc_id, include_tabs_content=True):
        self.calls += 1
        return copy.deepcopy(self.docs[doc_id])

    def get_comments(self, doc_id):
        return self._comments.get(doc_id, [])

    def get_modified_time(self, doc_id):
        import datetime
        return datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)


@pytest.fixture
def svc(tmp_path):
    client = FakeClient({"DOC1": make_doc()})
    return DocService(lambda: client, tmp_path / "scratch", index=DocspanIndex(":memory:")), client


def test_outline_has_tabs_counts_and_no_body(svc):
    s, _ = svc
    out = s.outline("DOC1")
    assert [t.tab_name for t in out.tabs] == ["Plan", "Weekly"]
    assert out.last_modified.startswith("2026-01-02")
    paths = [h.heading_path for h in out.headings]
    assert "Plan > Overview > Details" in paths
    over = next(h for h in out.headings if h.heading_path == "Plan > Overview")
    assert over.char_count > next(h for h in out.headings if h.heading_path == "Plan > Overview > Details").char_count
    assert over.url == "https://docs.google.com/document/d/DOC1/edit?tab=t.0#heading=h.over"
    assert "gadgets" not in out.model_dump_json()


def test_read_section_compound_path_and_subsections(svc):
    s, _ = svc
    res = s.read_section("DOC1", "Plan > Overview")
    assert "widgets" in res.text and "gadgets" in res.text  # includes subsection
    assert "Weekly notes body" not in res.text
    res = s.read_section("DOC1", "plan > overview > details")
    assert "gadgets" in res.text and "widgets" not in res.text
    assert "Weekly notes body" not in res.text
    assert res.url.endswith("?tab=t.0#heading=h.det")


def test_read_section_max_chars_truncates(svc):
    s, _ = svc
    res = s.read_section("DOC1", "Plan > Overview", max_chars=10)
    assert len(res.text) == 10 and res.truncated and res.char_count > 10


def test_read_section_ambiguous_lists_matches():
    tabs = parse_tabs(make_doc())
    with pytest.raises(DocspanToolError) as e:
        find_section_by_path(tabs, "Weekly > Notes", "DOC1")
    assert e.value.code == "ambiguous_heading_path"
    ids = {m["heading_id"] for m in e.value.details["matches"]}
    assert ids == {"h.wk", "h.wk2"}
    # A qualified path disambiguates.
    _, sec = find_section_by_path(tabs, "Weekly > Notes > Notes", "DOC1")
    assert sec.heading_id == "h.wk2"


def test_read_section_not_found():
    with pytest.raises(DocspanToolError) as e:
        find_section_by_path(parse_tabs(make_doc()), "Plan > Nope", "DOC1")
    assert e.value.code == "section_not_found"


def test_resolve_links_walks_raw_json_with_tab_fragments():
    urls = [
        "https://docs.google.com/document/d/ABC123/edit?tab=t.5#heading=h.xyz",
        "https://docs.google.com/document/d/ABC123/edit?tab=t.5#heading=h.xyz",  # dup
        "https://docs.google.com/document/d/DEF456/edit",
        "https://example.com/not-a-doc",
        "https://docs.google.com/spreadsheets/d/SHEET/edit",
    ]
    links = resolve_links(make_doc(links=urls))
    assert [(k.target_doc_id, k.target_tab, k.target_heading_id) for k in links] == [
        ("ABC123", "t.5", "h.xyz"), ("DEF456", None, None)]
    assert links[0].url == "https://docs.google.com/document/d/ABC123/edit?tab=t.5#heading=h.xyz"


def test_extract_doc_id():
    assert extract_doc_id("https://docs.google.com/document/d/ABC-1_2/edit?tab=t.0") == "ABC-1_2"
    assert extract_doc_id("ABC") == "ABC"
    assert parse_doc_url("https://evil.example/document/d/ABC") is None


def test_index_search_snippet_url_and_incremental(svc):
    s, client = svc
    assert s.index_doc("DOC1") is True
    assert s.index_doc("DOC1") is False  # same revisionId: skipped
    res = s.search_docs("sprocket")  # porter stemming: sprockets
    assert res.hits and res.hits[0].heading_path == "Plan > Overview > Details"
    assert "[" in res.hits[0].snippet
    assert res.hits[0].url == "https://docs.google.com/document/d/DOC1/edit?tab=t.0#heading=h.det"
    client.docs["DOC1"]["revisionId"] = "rev2"
    assert s.index_doc("DOC1") is True


def test_search_is_safe_against_fts_syntax_and_scope(svc):
    s, _ = svc
    s.index_doc("DOC1")
    # FTS5 operators/quotes in user input are quoted away: no syntax error, just no match.
    assert s.search_docs('widgets" OR NEAR(').hits == []
    assert s.search_docs("widgets", scope=["OTHER"]).hits == []
    assert s.search_docs("widgets", scope=["DOC1"]).hits


def test_empty_index_warns(svc):
    s, _ = svc
    assert "empty" in s.search_docs("x").warning


def test_index_keyed_by_doc_tab_heading(tmp_path):
    idx = DocspanIndex(tmp_path / "i.db")
    s = DocService(lambda: FakeClient({"DOC1": make_doc()}), tmp_path / "sc", index=idx)
    s.index_doc("DOC1")
    rows = sqlite3.connect(tmp_path / "i.db").execute(
        "select doc_id, tab_id, heading_path from doc_sections").fetchall()
    assert ("DOC1", "t.1", "Weekly > Notes > Notes") in rows


def test_fetch_writes_files_not_body_and_diff_since_last_pull(svc, tmp_path):
    s, client = svc
    res = s.fetch_doc("DOC1")
    assert {f.tab_name for f in res.files} == {"Plan", "Weekly"}
    assert "gadgets" not in res.model_dump_json()
    assert "gadgets" in open(res.files[0].local_path).read()
    assert s.diff_since("DOC1").unchanged is True
    client.docs["DOC1"]["tabs"][0]["documentTab"]["body"]["content"].append(
        _para("Fresh", "HEADING_2", "h.new"))
    client.docs["DOC1"]["revisionId"] = "rev2"
    d = s.diff_since("DOC1")
    assert not d.unchanged and d.last_pulled_revision == "rev1" and d.current_revision == "rev2"
    assert any("Fresh" in a for a in d.added)


def test_diff_without_pull_is_structured_error(svc):
    with pytest.raises(DocspanToolError) as e:
        svc[0].diff_since("DOC1")
    assert e.value.code == "no_pulled_copy"


def test_fetch_single_tab_and_unknown_tab(svc):
    s, _ = svc
    assert [f.tab_name for f in s.fetch_doc("DOC1", tab="Weekly").files] == ["Weekly"]
    with pytest.raises(DocspanToolError):
        s.fetch_doc("DOC1", tab="Nope")


def test_fetch_to_path_outside_allowed_roots_rejected(svc):
    with pytest.raises(DocspanToolError) as e:
        svc[0].fetch_doc("DOC1", tab="Plan", to_path="/etc/docspan-out.md")
    assert e.value.code == "path_not_allowed"


def test_list_comments_buckets_and_filters(tmp_path):
    comments = [
        {"id": "c1", "content": "fix", "resolved": False, "author": {"displayName": "A"},
         "quotedFileContent": {"value": "Details body about gadgets"}},
        {"id": "c2", "content": "old", "resolved": True, "author": {"displayName": "B"},
         "quotedFileContent": {"value": "Weekly notes body"}},
    ]
    client = FakeClient({"DOC1": make_doc()}, {"DOC1": comments})
    s = DocService(lambda: client, tmp_path)
    allc = s.list_comments("DOC1").comments
    assert {c.comment_id: c.heading_path for c in allc}["c1"] == "Plan > Overview > Details"
    assert [c.comment_id for c in s.list_comments("DOC1", resolved=True).comments] == ["c2"]
    assert all(c.url for c in allc)


def test_crawl_depth_max_docs_cycles_and_edges(tmp_path):
    docs = {
        "A": make_doc("A", links=[u("B"), u("C")]),
        "B": make_doc("B", links=[u("A"), u("D")]),  # cycle back to A
        "C": make_doc("C"),
        "D": make_doc("D", links=[u("E")]),
        "E": make_doc("E"),
    }
    idx = DocspanIndex(":memory:")
    s = DocService(lambda: FakeClient(docs), tmp_path, index=idx)
    r = crawl(s, "A", depth=2, delay=0, sleep=lambda _: None)
    assert r.fetched == ["A", "B", "C", "D"]  # E is depth 3
    assert ("A", "B") in r.edges and ("B", "A") in r.edges and ("D", "E") in r.edges
    assert ("D", "E") in idx.edges()
    assert crawl(s, "A", depth=0, sleep=lambda _: None).fetched == ["A"]
    r = crawl(s, "A", depth=2, max_docs=2, sleep=lambda _: None)
    assert r.fetched == ["A", "B"] and r.truncated
    r = crawl(s, "A", depth=2, allowed_ids={"B"}, sleep=lambda _: None)
    assert r.fetched == ["A", "B"] and "C" in r.skipped_scope


def test_crawl_continues_after_http_error(tmp_path):
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    docs = {"A": make_doc("A", links=[u("B"), u("C")]), "C": make_doc("C")}

    class Flaky(FakeClient):
        def get_document(self, doc_id, include_tabs_content=True):
            if doc_id == "B":
                raise HttpError(Response({"status": 404}), b"nope")
            return super().get_document(doc_id)

    r = crawl(DocService(lambda: Flaky(docs), tmp_path), "A", depth=1, sleep=lambda _: None)
    assert r.fetched == ["A", "C"] and "B" in r.failed


def test_list_mappings(tmp_path):
    from docspan.config import Mapping, MarkgateConfig
    cfg = MarkgateConfig(mappings=[
        Mapping(local="a.md", backend="google_docs", remote_id="X", tab_id="t.1"),
        Mapping(local="b.md", backend="google_docs"),
    ])
    out = DocService(lambda: None, tmp_path, config=cfg).list_mappings()
    assert [m.remote_id for m in out.mappings] == ["X"]
    assert out.mappings[0].url.endswith("X/edit?tab=t.1")


def test_cli_registers_new_commands_and_keeps_pull_push():
    from typer.testing import CliRunner

    from docspan.cli.main import app
    runner = CliRunner()
    for cmd in ("mcp", "index", "crawl"):
        assert runner.invoke(app, [cmd, "--help"]).exit_code == 0
    # Existing commands are untouched.
    assert runner.invoke(app, ["pull", "--help"]).exit_code == 0
    assert runner.invoke(app, ["push", "--help"]).exit_code == 0


def test_extract_doc_id_rejects_path_traversal():
    for bad in ("../../etc", "a/b", "..", ""):
        with pytest.raises(DocspanToolError) as e:
            extract_doc_id(bad)
        assert e.value.code == "invalid_doc_id"


def test_same_named_tabs_get_distinct_files(tmp_path):
    doc = make_doc()
    doc["tabs"][1]["tabProperties"]["title"] = "Plan"
    s = DocService(lambda: FakeClient({"DOC1": doc}), tmp_path)
    paths = {f.local_path for f in s.fetch_doc("DOC1").files}
    assert len(paths) == 2


def _doc_with(paragraphs):
    return {"documentId": "D", "title": "T", "revisionId": "r", "tabs": [
        {"tabProperties": {"tabId": "t.0", "title": "Tab"},
         "documentTab": {"body": {"content": paragraphs}}}]}


def test_empty_heading_is_skipped_not_ambiguous():
    tabs = parse_tabs(_doc_with([
        _para("Intro", "HEADING_1", "h.1", pos=[1]), _para("", "HEADING_2", "h.2", pos=[50]),
        _para("body", pos=[60])]))
    _, sec = find_section_by_path(tabs, "Tab > Intro", "D")
    assert sec.heading_id == "h.1"


def test_duplicate_sibling_headings_get_ordinals_and_distinct_snapshots(tmp_path):
    doc = _doc_with([
        _para("Parent", "HEADING_1", "h.p", pos=[1]),
        _para("Notes", "HEADING_2", "h.a", pos=[20]), _para("first body", pos=[40]),
        _para("Notes", "HEADING_2", "h.b", pos=[60]), _para("second body", pos=[80])])
    tabs = parse_tabs(doc)
    _, a = find_section_by_path(tabs, "Tab > Parent > Notes", "D")
    _, b = find_section_by_path(tabs, "Tab > Parent > Notes [2]", "D")
    assert (a.heading_id, b.heading_id) == ("h.a", "h.b")
    s = DocService(lambda: FakeClient({"D": doc}), tmp_path)
    s.fetch_doc("D")
    snap = json.loads((tmp_path / "D" / "snapshot.json").read_text())["sections"]
    assert len(snap) == 4  # tab + 3 headings, no key collisions


def test_level_skip_and_title_levels():
    tabs = parse_tabs(_doc_with([
        _para("Doc Title", "TITLE", "h.t", pos=[1]), _para("Deep", "HEADING_3", "h.d", pos=[20]),
        _para("deep body", pos=[40])]))
    paths = [s.path for s in tabs[0].sections]
    assert "Tab > Doc Title > Deep" in paths
    assert find_section_by_path(tabs, "Tab > Deep", "D")[1].heading_id == "h.d"  # ancestor skipped


def test_to_path_rejects_dot_directories_and_non_md(svc, tmp_path, monkeypatch):
    s, _ = svc
    monkeypatch.chdir(tmp_path)
    for bad in (".git/hooks/pre-commit.md", "notes.txt"):
        with pytest.raises(DocspanToolError) as e:
            s.fetch_doc("DOC1", tab="Plan", to_path=str(tmp_path / bad))
        assert e.value.code == "path_not_allowed"
    ok = s.fetch_doc("DOC1", tab="Plan", to_path=str(tmp_path / "out" / "plan.md"))
    assert (tmp_path / "out" / "plan.md").read_text() and ok.files[0].local_path.endswith("plan.md")


def test_crawl_failures_count_toward_max_docs_and_bad_start_is_recorded(tmp_path):
    docs = {"A": make_doc("A", links=[u("X"), u("Y"), u("Z")])}
    s = DocService(lambda: FakeClient(docs), tmp_path)

    class Missing(FakeClient):
        def get_document(self, doc_id, include_tabs_content=True):
            if doc_id in docs:
                return super().get_document(doc_id)
            raise DocspanToolError("gone", "deleted")

    s = DocService(lambda: Missing(docs), tmp_path)
    r = crawl(s, "A", depth=1, max_docs=2, sleep=lambda _: None)
    assert r.fetched == ["A"] and set(r.failed) == {"X"} and r.truncated
    r = crawl(s, "NOPE", sleep=lambda _: None)
    assert r.fetched == [] and "NOPE" in r.failed


def test_empty_revision_is_always_stale():
    idx = DocspanIndex(":memory:")
    idx.ingest("D", "", "t", [])
    assert idx.is_stale("D", "") is True
