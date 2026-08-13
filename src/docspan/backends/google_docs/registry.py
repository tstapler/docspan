"""Registries for Google Docs markdown node dispatch (push and pull directions).

Mirrors the Confluence backend's TypedNodeConverter/NodeVisitorRegistry pattern
(docspan.backends.confluence.adf.converters / visitors), adapted to this
backend's two node shapes rather than sharing a genericized base: the push
direction dispatches on mistune's raw dict token["type"] (already a clean
string), while the pull direction's DocsParagraphNode/DocsTableNode have no
discriminant field at all, so callers synthesize a dispatch key (see
nodes_to_markdown._dispatch_key) before looking it up here.
"""
from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional


class MarkdownTokenConverter(abc.ABC):
    """Converts one mistune AST token into zero or more push-direction nodes."""

    token_type: str

    @abc.abstractmethod
    def convert(self, token: dict) -> List[Any]:
        """Convert a mistune token into DocsParagraphNode/DocsTableNode instances."""
        raise NotImplementedError


class MarkdownTokenRegistry:
    """Maps a mistune token_type string to the converter that handles it."""

    def __init__(self) -> None:
        self._converters: Dict[str, MarkdownTokenConverter] = {}

    def register(self, token_type: str, converter: MarkdownTokenConverter) -> None:
        self._converters[token_type] = converter

    def get(self, token_type: str) -> Optional[MarkdownTokenConverter]:
        return self._converters.get(token_type)

    def has(self, token_type: str) -> bool:
        return token_type in self._converters


class MarkdownNodeRenderer(abc.ABC):
    """Renders one pull-direction node (DocsParagraphNode/DocsTableNode) to a markdown line."""

    node_key: str

    @abc.abstractmethod
    def render(self, node: Any) -> str:
        raise NotImplementedError


class MarkdownRenderRegistry:
    """Maps a synthesized dispatch key (see _dispatch_key) to the renderer that handles it."""

    def __init__(self) -> None:
        self._renderers: Dict[str, MarkdownNodeRenderer] = {}

    def register(self, node_key: str, renderer: MarkdownNodeRenderer) -> None:
        self._renderers[node_key] = renderer

    def get(self, node_key: str) -> Optional[MarkdownNodeRenderer]:
        return self._renderers.get(node_key)

    def has(self, node_key: str) -> bool:
        return node_key in self._renderers
