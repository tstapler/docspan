"""End-to-end coverage of cross-document link resolution through
GoogleDocsBackend.push(), threading a Mapping list in via the `mappings`
kwarg the way orchestrator.orchestrate_push()/cli/main.py do.

Complements tests/test_cross_doc_links.py (which covers the resolver in
isolation) by exercising the actual wiring: constructing the resolver in
push(), fetching a *different* document for heading resolution, and
reporting unresolved cross-doc links the same way dead same-document
anchors are reported.
"""
from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

from docspan.backends.google_docs.backend import GoogleDocsBackend
from docspan.config import Mapping


def _paragraph(
    text: str,
    start: int,
    style: str = "NORMAL_TEXT",
    heading_id: str | None = None,
    runs: list[dict] | None = None,
) -> dict:
    paragraph_style: dict = {"namedStyleType": style}
    if heading_id:
        paragraph_style["headingId"] = heading_id
    elements = runs if runs is not None else [
        {"textRun": {"content": text + "\n", "textStyle": {}}}
    ]
    return {
        "startIndex": start,
        "endIndex": start + len(text) + 1,
        "paragraph": {"paragraphStyle": paragraph_style, "elements": elements},
    }


def _doc(*paragraphs: dict, revision_id: str = "rev-1") -> dict:
    return {"revisionId": revision_id, "body": {"content": list(paragraphs)}}


def _link_requests(fake_client: MagicMock) -> list[dict]:
    return [
        request["updateTextStyle"]["textStyle"]["link"]
        for call in fake_client.batch_update.call_args_list
        for request in call.args[1]
        if "updateTextStyle" in request
        and "link" in request["updateTextStyle"].get("textStyle", {})
    ]


class TestCrossDocLinkResolutionThroughPush:
    def _local(self, tmp_path, name: str, text: str) -> str:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_relative_link_with_no_fragment_resolves_to_target_edit_url(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
        }[doc_id]
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status in ("ok", "warning"), result.message
        links = _link_requests(fake_client)
        assert {"url": "https://docs.google.com/document/d/doc-2/edit"} in links, links

    def test_relative_link_with_fragment_resolves_against_targets_live_headings(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md#some-heading)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        target_doc = _doc(
            _paragraph("Some Heading", 1, "HEADING_2", "h.target"),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
            "doc-2": target_doc,
        }[doc_id]
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status in ("ok", "warning"), result.message
        links = _link_requests(fake_client)
        assert {
            "url": "https://docs.google.com/document/d/doc-2/edit#heading=h.target"
        } in links, links

    def test_link_to_unmapped_file_is_left_untouched(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](../not-mapped.md)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
        }[doc_id]
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status in ("ok", "warning", "skipped"), result.message
        links = _link_requests(fake_client)
        assert {"url": "../not-mapped.md"} in links, links

    def test_fragment_matching_no_heading_in_target_is_reported_not_silently_written(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md#missing-heading)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        target_doc = _doc(
            _paragraph("Some Heading", 1, "HEADING_2", "h.target"),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
            "doc-2": target_doc,
        }[doc_id]
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status == "warning"
        assert "missing-heading" in result.message
        links = _link_requests(fake_client)
        assert not any("missing-heading" in str(link) for link in links)

    def test_target_fetch_failure_reports_but_source_push_still_completes(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md#some-heading)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )

        def get_document(doc_id, **_):
            if doc_id == "doc-1":
                return source_doc
            raise RuntimeError("403 Forbidden")

        fake_client.get_document.side_effect = get_document
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status == "warning"
        assert "target.md" in result.message or "doc-2" in result.message
        links = _link_requests(fake_client)
        assert not any("some-heading" in str(link) for link in links)

    def test_ambiguous_mapping_is_reported_not_silently_resolved(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # Criterion 8: two mapping entries that normalize to the same local
        # path must produce a loud ambiguous-match failure through the real
        # push() path, not just in the resolver unit tests
        # (test_cross_doc_links.py's test_ambiguous_mapping_is_reported).
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
        }[doc_id]
        target_local = str(tmp_path / "target.md")
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=target_local, backend="google_docs", remote_id="doc-2"),
            Mapping(local=target_local, backend="google_docs", remote_id="doc-3"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status == "warning"
        assert target_local in result.message or "target.md" in result.message
        links = _link_requests(fake_client)
        assert not any(
            link.get("url", "").endswith(("doc-2/edit", "doc-3/edit")) for link in links
        )

    def test_multiple_links_to_same_target_fetch_it_only_once(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path,
            "source.md",
            "see [one](target.md#heading-one) and [two](target.md#heading-two)\n",
        )
        source_doc = _doc(
            _paragraph("see one and two", 1, runs=[
                {"textRun": {"content": "see one and two\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        target_doc = _doc(
            _paragraph("Heading One", 1, "HEADING_2", "h.one"),
            _paragraph("Heading Two", 14, "HEADING_2", "h.two"),
            revision_id="rev-1",
        )
        calls = []

        def get_document(doc_id, **_):
            calls.append(doc_id)
            return {"doc-1": source_doc, "doc-2": target_doc}[doc_id]

        fake_client.get_document.side_effect = get_document
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status in ("ok", "warning"), result.message
        assert calls.count("doc-2") == 1, calls

    def test_shared_cross_doc_cache_bounds_fetches_across_multiple_push_calls(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        """Criterion 11: a `push --all` run gets a fresh backend instance (and
        thus a fresh CrossDocLinkResolver) per mapping — see cli/main.py's
        per-mapping loop. Without a `cross_doc_cache` dict shared across those
        push() calls, N source docs linking to the same target would each
        pay their own fetch of it. Passing the same dict into every push()
        call (as orchestrate_push()/cli/main.py now do) keeps the target
        fetched only once for the whole run, not once per pushing document.
        """
        target_doc = _doc(
            _paragraph("Some Heading", 1, "HEADING_2", "h.target"),
            revision_id="rev-1",
        )
        source_a_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        source_b_doc = _doc(
            _paragraph("see it too", 1, runs=[
                {"textRun": {"content": "see it too\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )

        source_a_local = self._local(
            tmp_path, "source-a.md", "see [it](target.md#some-heading)\n"
        )
        source_b_local = self._local(
            tmp_path, "source-b.md", "see [it too](target.md#some-heading)\n"
        )
        mappings = [
            Mapping(local=source_a_local, backend="google_docs", remote_id="doc-a"),
            Mapping(local=source_b_local, backend="google_docs", remote_id="doc-b"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        calls: list[str] = []
        docs_by_id = {"doc-a": source_a_doc, "doc-b": source_b_doc, "doc-2": target_doc}

        # Two backend instances — matching cli/main.py's push loop, which
        # constructs a fresh backend per mapping via _get_backend().
        backend_a, fake_client_a = make_backend()
        backend_b, fake_client_b = make_backend()
        for fake_client in (fake_client_a, fake_client_b):
            def get_document(doc_id, _calls=calls, _docs=docs_by_id, **_):
                _calls.append(doc_id)
                return _docs[doc_id]

            fake_client.get_document.side_effect = get_document

        cross_doc_cache: dict = {}
        result_a = backend_a.push(
            source_a_local, "doc-a", mappings=mappings, cross_doc_cache=cross_doc_cache
        )
        result_b = backend_b.push(
            source_b_local, "doc-b", mappings=mappings, cross_doc_cache=cross_doc_cache
        )

        assert result_a.status in ("ok", "warning"), result_a.message
        assert result_b.status in ("ok", "warning"), result_b.message
        assert calls.count("doc-2") == 1, calls

    def test_untouched_broken_link_is_never_claimed_as_fixed(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # Criterion 9: a paragraph pass 1 finds no diff for (the local
        # markdown text below is byte-for-byte what the live doc already
        # has) still has to run through pass 2 whenever anything elsewhere
        # in the document needs it, and pass 2 re-emits styling for every
        # aligned paragraph regardless of whether pass 1 touched it — see
        # docs_request_builder.py's build_span_style_requests. That is fine
        # as long as the result never *claims* the already-broken link got
        # fixed. It should be reported as unresolved (same as any other
        # push, criterion 4) and nothing in the message should read as a
        # success/fix claim.
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "see [it](target.md#missing-heading)\n"
        )
        source_doc = _doc(
            _paragraph("see it", 1, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        target_doc = _doc(
            _paragraph("Some Heading", 1, "HEADING_2", "h.target"),
            revision_id="rev-1",
        )
        fake_client.get_document.side_effect = lambda doc_id, **_: {
            "doc-1": source_doc,
            "doc-2": target_doc,
        }[doc_id]
        mappings = [
            Mapping(local=source_local, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
        ]

        result = backend.push(source_local, "doc-1", mappings=mappings)

        assert result.status == "warning"
        message = result.message or ""
        assert "missing-heading" in message
        assert "fixed" not in message.lower()
        assert "resolved" not in message.lower()

    def test_no_mappings_kwarg_preserves_old_same_document_anchor_behavior(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # Criterion 2 regression guard: pushing with no `mappings` at all
        # (old call sites / old behavior) must not raise or change how a
        # same-document `#fragment` anchor resolves.
        backend, fake_client = make_backend()
        source_local = self._local(
            tmp_path, "source.md", "## Current state\n\nsee [it](#current-state)\n"
        )
        source_doc = _doc(
            _paragraph("Current state", 1, "HEADING_2", "h.cur"),
            _paragraph("see it", 16, runs=[
                {"textRun": {"content": "see it\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        fake_client.get_document.return_value = source_doc

        result = backend.push(source_local, "doc-1")

        assert result.status in ("ok", "warning"), result.message
        links = _link_requests(fake_client)
        assert {"headingId": "h.cur"} in links, links

    def test_all_run_across_multiple_sources_fetches_shared_target_once_per_source(
        self, tmp_path, make_backend: Callable[[], tuple[GoogleDocsBackend, MagicMock]]
    ) -> None:
        # Criterion 10: a `push --all`-style run pushes several source
        # documents, each with multiple links to the *same* target. Fetch
        # calls for that target must be bound by the number of distinct
        # source pushes that reference it (one get_document() per push, via
        # the per-run cache already proven in
        # test_multiple_links_to_same_target_fetch_it_only_once) — not by
        # the total number of links across the whole run, which would be
        # the O(docs^2)-shaped blowup this criterion rules out.
        backend, fake_client = make_backend()
        source_one = self._local(
            tmp_path,
            "source-one.md",
            "see [a](target.md#heading-one) and [b](target.md#heading-two)\n",
        )
        source_two = self._local(
            tmp_path,
            "source-two.md",
            "see [c](target.md#heading-one) and [d](target.md#heading-two)\n",
        )
        source_one_doc = _doc(
            _paragraph("see a and b", 1, runs=[
                {"textRun": {"content": "see a and b\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        source_two_doc = _doc(
            _paragraph("see c and d", 1, runs=[
                {"textRun": {"content": "see c and d\n", "textStyle": {}}}
            ]),
            revision_id="rev-1",
        )
        target_doc = _doc(
            _paragraph("Heading One", 1, "HEADING_2", "h.one"),
            _paragraph("Heading Two", 14, "HEADING_2", "h.two"),
            revision_id="rev-1",
        )
        calls = []

        def get_document(doc_id, **_):
            calls.append(doc_id)
            return {
                "doc-1": source_one_doc,
                "doc-2": target_doc,
                "doc-3": source_two_doc,
            }[doc_id]

        fake_client.get_document.side_effect = get_document
        mappings = [
            Mapping(local=source_one, backend="google_docs", remote_id="doc-1"),
            Mapping(local=str(tmp_path / "target.md"), backend="google_docs", remote_id="doc-2"),
            Mapping(local=source_two, backend="google_docs", remote_id="doc-3"),
        ]

        result_one = backend.push(source_one, "doc-1", mappings=mappings)
        result_two = backend.push(source_two, "doc-3", mappings=mappings)

        assert result_one.status in ("ok", "warning"), result_one.message
        assert result_two.status in ("ok", "warning"), result_two.message
        assert calls.count("doc-2") == 2, calls
