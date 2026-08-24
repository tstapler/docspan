"""Unit tests for mermaid_appendix.py (in-doc mermaid source recovery).

See mermaid_appendix.py's module docstring for why this exists: an
independent, Doc-only recovery path (no git checkout required) alongside
mermaid_cache_sidecar.py's committed sidecar.
"""

from docspan.backends.google_docs.docs_structure_parser import DocsParagraphNode
from docspan.backends.google_docs.mermaid_appendix import (
    APPENDIX_HEADING,
    build_appendix_nodes,
    extract_appendix_entries,
    find_appendix_boundary,
    strip_appendix_from_markdown,
)


def test_empty_entries_produces_no_appendix() -> None:
    assert build_appendix_nodes([]) == []


def test_build_then_extract_round_trips_entries() -> None:
    entries = [
        ("abc123", "graph TD\n  A --> B"),
        ("def456", "sequenceDiagram\n  Caller->>API: request"),
    ]

    nodes = build_appendix_nodes(entries)
    extracted = extract_appendix_entries(nodes)

    assert extracted == dict(entries)


def test_appendix_code_block_uses_text_language_not_mermaid() -> None:
    nodes = build_appendix_nodes([("abc123", "graph TD\n  A --> B")])

    fence_marker_texts = [n.text for n in nodes if n.text.startswith("```")]
    assert "```text" in fence_marker_texts
    assert "```mermaid" not in fence_marker_texts


def test_appendix_starts_with_heading_2() -> None:
    nodes = build_appendix_nodes([("abc123", "graph TD\n  A --> B")])

    assert nodes[0].style == "HEADING_2"
    assert nodes[0].text == APPENDIX_HEADING


def test_find_appendix_boundary_locates_heading() -> None:
    body_node = DocsParagraphNode(
        style="NORMAL_TEXT", text="some body text", start_index=0, end_index=0, spans=[]
    )
    appendix_nodes = build_appendix_nodes([("abc123", "graph TD\n  A --> B")])
    nodes = [body_node] + appendix_nodes

    assert find_appendix_boundary(nodes) == 1


def test_find_appendix_boundary_absent_returns_none() -> None:
    body_node = DocsParagraphNode(
        style="NORMAL_TEXT", text="some body text", start_index=0, end_index=0, spans=[]
    )

    assert find_appendix_boundary([body_node]) is None


def test_extract_appendix_entries_skips_malformed_shapes() -> None:
    malformed = [
        DocsParagraphNode(
            style="HEADING_3", text="Diagram abc123", start_index=0, end_index=0, spans=[]
        ),
        DocsParagraphNode(
            style="NORMAL_TEXT", text="not a fence marker", start_index=0, end_index=0, spans=[]
        ),
    ]

    assert extract_appendix_entries(malformed) == {}


def test_strip_appendix_from_markdown_truncates_at_heading() -> None:
    markdown = (
        "# Doc Title\n\nSome content.\n\n"
        f"## {APPENDIX_HEADING}\n\n### Diagram abc123\n\n```text\ngraph TD\n```\n"
    )

    result = strip_appendix_from_markdown(markdown)

    assert result == "# Doc Title\n\nSome content.\n"
    assert APPENDIX_HEADING not in result


def test_strip_appendix_from_markdown_is_a_no_op_without_heading() -> None:
    markdown = "# Doc Title\n\nSome content.\n"

    assert strip_appendix_from_markdown(markdown) == markdown
