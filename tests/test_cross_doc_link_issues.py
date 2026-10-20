"""Direct tests for DocsRequestBuilder.cross_doc_link_issues() (docs_request_builder.py).

Parallels test_heading_anchors.py's TestUnresolvedAnchorLinks but for the
cross-document reporting path — criterion 4 (unresolved cross-doc anchor is
reported, never silently dropped) exercised directly against the request
builder rather than through a full backend.push().
"""
from __future__ import annotations

from docspan.backends.google_docs.cross_doc_links import CrossDocLinkResolver
from docspan.backends.google_docs.docs_request_builder import DocsRequestBuilder
from docspan.backends.google_docs.docs_structure_parser import (
    DocsParagraphNode,
    DocsTableNode,
    TableCell,
    TextSpan,
)
from docspan.config import Mapping

builder = DocsRequestBuilder()


def _doc(*paragraphs: dict, revision_id: str = "rev-1") -> dict:
    return {"revisionId": revision_id, "body": {"content": list(paragraphs)}}


def _paragraph(text: str, start: int, style: str = "NORMAL_TEXT") -> dict:
    return {
        "startIndex": start,
        "endIndex": start + len(text) + 1,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
        },
    }


def make_mapping(local, remote_id="TARGETID", backend="google_docs"):
    return Mapping(local=local, backend=backend, remote_id=remote_id)


class TestCrossDocLinkIssues:
    def test_no_resolver_returns_empty(self) -> None:
        doc = _doc(_paragraph("see it", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="target.md#missing"),
            ]),
        ]
        assert builder.cross_doc_link_issues(doc, target, resolver=None, local_path="source.md") == []

    def test_no_local_path_returns_empty(self) -> None:
        doc = _doc(_paragraph("see it", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="target.md#missing"),
            ]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        assert builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path=None) == []

    def test_unresolvable_fragment_is_reported(self) -> None:
        doc = _doc(_paragraph("see it", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="target.md#missing"),
            ]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        issues = builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md")
        assert len(issues) == 1
        assert "missing" in issues[0]

    def test_resolvable_link_is_not_reported(self) -> None:
        doc = _doc(_paragraph("see it", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="target.md"),
            ]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        assert builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md") == []

    def test_unmapped_link_is_not_reported(self) -> None:
        # Criterion 3: a link to a file with no mapping entry is left
        # untouched, not treated as a failure worth reporting.
        doc = _doc(_paragraph("see it", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="../not-mapped.md"),
            ]),
        ]
        resolver = CrossDocLinkResolver([], lambda d, t: [])
        assert builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md") == []

    def test_same_document_anchor_is_not_reported_here(self) -> None:
        # Same-document anchors are unresolved_anchor_links()'s job, not
        # cross_doc_link_issues()'s -- this asserts the two paths stay
        # disjoint (criterion 2 isolation).
        doc = _doc(_paragraph("Intro", 1, "HEADING_1"), _paragraph("see it", 8))
        target = [
            DocsParagraphNode(style="HEADING_1", text="Intro"),
            DocsParagraphNode(style="NORMAL_TEXT", text="see it", spans=[
                TextSpan(text="see it", link="#missing-anchor"),
            ]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        assert builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md") == []

    def test_unresolvable_link_in_table_cell_is_reported(self) -> None:
        doc = _doc(_paragraph("unrelated", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="unrelated"),
            DocsTableNode(rows=[[
                TableCell(text="see it", spans=[
                    TextSpan(text="see it", link="target.md#missing"),
                ]),
            ]]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        issues = builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md")
        assert len(issues) == 1
        assert "missing" in issues[0]

    def test_duplicate_issue_detail_is_reported_once(self) -> None:
        doc = _doc(_paragraph("see one and two", 1))
        target = [
            DocsParagraphNode(style="NORMAL_TEXT", text="see one and two", spans=[
                TextSpan(text="see one", link="target.md#missing"),
                TextSpan(text=" and two", link="target.md#missing"),
            ]),
        ]
        resolver = CrossDocLinkResolver([make_mapping("target.md")], lambda d, t: [])
        issues = builder.cross_doc_link_issues(doc, target, resolver=resolver, local_path="source.md")
        assert len(issues) == 1
