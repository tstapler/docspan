"""
Client for managing Confluence labels.

This module provides specialized functionality for working with Confluence labels,
including adding, retrieving, and removing labels.
"""

from typing import Any, Dict, List, Optional

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.services.confluence.base_client import BaseConfluenceClient


class LabelClient(BaseConfluenceClient):
    """
    Client for managing Confluence labels.
    
    Provides methods for adding, retrieving, and removing labels.
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the label client.
        
        Args:
            config: Confluence configuration
        """
        super().__init__(config)
    
    def add_label(self, content_id: str, label: str) -> Dict[str, Any]:
        """
        Add a label to a content item (page, blog post, etc.).
        
        Args:
            content_id: Content ID
            label: Label to add
            
        Returns:
            Created label data
        """
        endpoint = f"content/{content_id}/label"
        
        data = {
            "name": label,
            "prefix": "global"
        }
        
        self.logger.debug(f"Adding label '{label}' to content {content_id}")
        return self._make_request(
            method="POST",
            endpoint=endpoint,
            json_data=[data]  # The API expects an array of label objects
        )
    
    def add_labels(self, content_id: str, labels: List[str]) -> List[Dict[str, Any]]:
        """
        Add multiple labels to a content item (page, blog post, etc.).
        
        Args:
            content_id: Content ID
            labels: Labels to add
            
        Returns:
            List of created label data
        """
        if not labels:
            return []
            
        endpoint = f"content/{content_id}/label"
        
        data = [{"name": label, "prefix": "global"} for label in labels]
        
        self.logger.debug(f"Adding {len(labels)} labels to content {content_id}")
        return self._make_request(
            method="POST",
            endpoint=endpoint,
            json_data=data
        )
    
    def get_labels(self, content_id: str) -> List[Dict[str, Any]]:
        """
        Get all labels for a content item.
        
        Args:
            content_id: Content ID
            
        Returns:
            List of label data
        """
        endpoint = f"content/{content_id}/label"
        params = {"limit": 100}  # Set a reasonable limit
        
        response = self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )
        
        # Return the results
        if isinstance(response, dict):
            return response.get("results", [])
        
        return []
    
    def delete_label(self, content_id: str, label: str) -> bool:
        """
        Delete a label from a content item.
        
        Args:
            content_id: Content ID
            label: Label to delete
            
        Returns:
            True if successful, False otherwise
        """
        endpoint = f"content/{content_id}/label"
        params = {"name": label}
        
        self.logger.debug(f"Deleting label '{label}' from content {content_id}")
        
        try:
            self._make_request(
                method="DELETE",
                endpoint=endpoint,
                params=params
            )
            return True
        except Exception as e:
            self.logger.error(f"Error deleting label: {e}")
            return False