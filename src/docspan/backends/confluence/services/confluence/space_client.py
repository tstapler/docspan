"""
Client for managing Confluence spaces.

This module provides specialized functionality for working with Confluence spaces,
including retrieving space information and content.
"""

from typing import Any, Dict, List, Optional

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.services.confluence.base_client import BaseConfluenceClient


class SpaceClient(BaseConfluenceClient):
    """
    Client for managing Confluence spaces.
    
    Provides methods for retrieving space information and content.
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the space client.
        
        Args:
            config: Confluence configuration
        """
        super().__init__(config)
    
    def get_space(self, space_key: str) -> Dict[str, Any]:
        """
        Get information about a space.
        
        Args:
            space_key: Space key
            
        Returns:
            Space data
        """
        endpoint = f"space/{space_key}"
        params = {"expand": "description,homepage"}
        
        return self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )
    
    def get_spaces(
        self, 
        start: int = 0, 
        limit: int = 25,
        type: Optional[str] = None,
        status: Optional[str] = None,
        expand: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all spaces or filtered spaces.
        
        Args:
            start: Start index for pagination
            limit: Maximum number of results to return
            type: Space type filter (global, personal)
            status: Space status filter (current, archived)
            expand: Additional properties to expand in the response
            
        Returns:
            List of space data
        """
        endpoint = "space"
        params = {
            "start": start,
            "limit": limit
        }
        
        if type:
            params["type"] = type
        
        if status:
            params["status"] = status
            
        if expand:
            params["expand"] = expand
        
        response = self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )
        
        # Return the results
        if isinstance(response, dict):
            return response.get("results", [])
        
        return []
    
    def get_space_content(
        self,
        space_key: str,
        content_type: str = "page",
        start: int = 0,
        limit: int = 25,
        expand: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get content in a space.
        
        Args:
            space_key: Space key
            content_type: Content type (page, blogpost, comment)
            start: Start index for pagination
            limit: Maximum number of results to return
            expand: Additional properties to expand in the response
            
        Returns:
            List of content data
        """
        endpoint = f"space/{space_key}/content/{content_type}"
        params = {
            "start": start,
            "limit": limit
        }
        
        if expand:
            params["expand"] = expand
        
        response = self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )
        
        # Return the results
        if isinstance(response, dict):
            return response.get("results", [])
        
        return []
    
    def search_space_content(
        self,
        space_key: str,
        query: str,
        start: int = 0,
        limit: int = 25
    ) -> List[Dict[str, Any]]:
        """
        Search for content in a space.
        
        Args:
            space_key: Space key
            query: Search query
            start: Start index for pagination
            limit: Maximum number of results to return
            
        Returns:
            List of content data matching the search query
        """
        # Use CQL (Confluence Query Language)
        cql = f"space = {space_key} AND text ~ \"{query}\""
        
        endpoint = "content/search"
        params = {
            "cql": cql,
            "start": start,
            "limit": limit,
            "expand": "space,version"
        }
        
        response = self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )
        
        # Return the results
        if isinstance(response, dict):
            return response.get("results", [])
        
        return []