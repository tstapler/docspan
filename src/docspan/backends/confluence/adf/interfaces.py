"""
Interfaces for ADF conversion components.

This module defines the interfaces and base classes for the ADF converter
components, enabling a clean separation of concerns and extensibility.
"""

import abc
from typing import Any, Dict, List, Optional, Protocol, TypeVar, runtime_checkable

from docspan.backends.confluence.adf.nodes import AdfNode
from docspan.backends.confluence.markdown.ast import MarkdownNode


@runtime_checkable
class NodeVisitor(Protocol):
    """
    Protocol for node visitors that process Markdown nodes.
    """
    
    def visit(self, node: MarkdownNode) -> AdfNode:
        """
        Visit a markdown node and convert it to an ADF node.
        
        Args:
            node: Markdown node to convert
            
        Returns:
            Converted ADF node
        """
        ...


class BaseNodeVisitor(abc.ABC):
    """
    Base class for node visitors.
    
    Attributes:
        node_type: The type of node this visitor can process
    """
    
    node_type: str
    
    @abc.abstractmethod
    def visit(self, node: MarkdownNode) -> AdfNode:
        """
        Visit a markdown node and convert it to an ADF node.
        
        Args:
            node: Markdown node to convert
            
        Returns:
            Converted ADF node
        """
        pass


class NodeConverter(abc.ABC):
    """
    Base class for node converters.
    
    A node converter is responsible for converting a specific type of Markdown node
    to its ADF representation.
    """
    
    @abc.abstractmethod
    def convert(self, node: MarkdownNode) -> AdfNode:
        """
        Convert a markdown node to an ADF node.
        
        Args:
            node: Markdown node to convert
            
        Returns:
            Converted ADF node
        """
        pass


T = TypeVar('T', bound=MarkdownNode)


class TypedNodeConverter(NodeConverter, abc.ABC):
    """
    Base class for type-specific node converters.
    
    This class provides a type-safe interface for converting nodes of a specific type.
    
    Attributes:
        node_type: The type of node this converter can process
    """
    
    node_type: str
    
    @abc.abstractmethod
    def convert_typed(self, node: T) -> AdfNode:
        """
        Convert a typed markdown node to an ADF node.
        
        Args:
            node: Typed markdown node to convert
            
        Returns:
            Converted ADF node
        """
        pass
    
    def convert(self, node: MarkdownNode) -> AdfNode:
        """
        Convert a markdown node to an ADF node.
        
        Args:
            node: Markdown node to convert
            
        Returns:
            Converted ADF node
            
        Raises:
            TypeError: If the node type doesn't match the expected type
        """
        if node.type != self.node_type:
            raise TypeError(f"Expected node of type {self.node_type}, got {node.type}")
        return self.convert_typed(node)


class NodeRegistry(abc.ABC):
    """
    Interface for node converter registries.
    
    A registry maintains a collection of node converters and provides
    access to them by node type.
    """
    
    @abc.abstractmethod
    def register(self, node_type: str, converter: NodeConverter) -> None:
        """
        Register a converter for a specific node type.
        
        Args:
            node_type: Node type
            converter: Converter for the node type
        """
        pass
    
    @abc.abstractmethod
    def get(self, node_type: str) -> Optional[NodeConverter]:
        """
        Get the converter for a specific node type.
        
        Args:
            node_type: Node type
            
        Returns:
            Converter for the node type, or None if not found
        """
        pass
    
    @abc.abstractmethod
    def has(self, node_type: str) -> bool:
        """
        Check if a converter exists for a specific node type.
        
        Args:
            node_type: Node type
            
        Returns:
            True if a converter exists, False otherwise
        """
        pass


class AdfDocumentBuilder(abc.ABC):
    """
    Interface for building ADF documents.
    
    An ADF document builder is responsible for assembling ADF nodes into
    a complete document.
    """
    
    @abc.abstractmethod
    def build_document(self, nodes: List[AdfNode]) -> Dict[str, Any]:
        """
        Build an ADF document from a list of ADF nodes.
        
        Args:
            nodes: List of ADF nodes
            
        Returns:
            ADF document as a dictionary
        """
        pass