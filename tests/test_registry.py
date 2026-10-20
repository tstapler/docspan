"""Unit tests for the push/pull dispatch registries themselves."""

from docspan.backends.google_docs.markdown_to_paragraph_parser import _PUSH_REGISTRY
from docspan.backends.google_docs.nodes_to_markdown import _PULL_REGISTRY
from docspan.backends.google_docs.registry import (
    MarkdownNodeRenderer,
    MarkdownRenderRegistry,
    MarkdownTokenConverter,
    MarkdownTokenRegistry,
)

# ─────────────────────────────────────────────────────────────────────────────
# MarkdownTokenRegistry (push direction)
# ─────────────────────────────────────────────────────────────────────────────

class _StubConverter(MarkdownTokenConverter):
    token_type = "stub"

    def convert(self, token: dict):
        return [token]


def test_token_registry_register_and_get() -> None:
    registry = MarkdownTokenRegistry()
    converter = _StubConverter()
    registry.register("stub", converter)
    assert registry.get("stub") is converter


def test_token_registry_unregistered_type_returns_none() -> None:
    registry = MarkdownTokenRegistry()
    assert registry.get("thematic_break") is None


def test_token_registry_has() -> None:
    registry = MarkdownTokenRegistry()
    registry.register("stub", _StubConverter())
    assert registry.has("stub") is True
    assert registry.has("html") is False


def test_push_registry_covers_all_seven_node_types() -> None:
    for token_type in (
        "heading", "paragraph", "list", "block_code", "code", "table",
        "block_quote", "blank_line",
    ):
        assert _PUSH_REGISTRY.has(token_type)


def test_push_registry_registers_new_type_without_touching_parse() -> None:
    """A future node type (e.g. a mermaid fence) is addable with a single
    registry.register(...) call — parse()'s dispatch loop needs no edits."""
    registry = MarkdownTokenRegistry()
    registry.register("mermaid", _StubConverter())
    token = {"type": "mermaid", "raw": "graph TD"}
    converter = registry.get(token["type"])
    assert converter is not None
    assert converter.convert(token) == [token]


# ─────────────────────────────────────────────────────────────────────────────
# MarkdownRenderRegistry (pull direction)
# ─────────────────────────────────────────────────────────────────────────────

class _StubRenderer(MarkdownNodeRenderer):
    node_key = "stub"

    def render(self, node) -> str:
        return "stub"


def test_render_registry_register_and_get() -> None:
    registry = MarkdownRenderRegistry()
    renderer = _StubRenderer()
    registry.register("stub", renderer)
    assert registry.get("stub") is renderer


def test_render_registry_unregistered_key_returns_none() -> None:
    registry = MarkdownRenderRegistry()
    assert registry.get("mermaid") is None


def test_render_registry_has() -> None:
    registry = MarkdownRenderRegistry()
    registry.register("stub", _StubRenderer())
    assert registry.has("stub") is True
    assert registry.has("table") is False


def test_pull_registry_covers_all_four_dispatch_keys() -> None:
    for node_key in ("table", "heading", "list_item", "paragraph"):
        assert _PULL_REGISTRY.has(node_key)
