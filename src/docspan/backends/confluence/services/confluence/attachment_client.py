"""
Client for managing Confluence attachments.

This module provides specialized functionality for working with Confluence attachments,
including uploading and retrieving attachments.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.services.confluence.base_client import BaseConfluenceClient


class AttachmentClient(BaseConfluenceClient):
    """
    Client for managing Confluence attachments.
    
    Provides methods for uploading and retrieving attachments.
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the attachment client.
        
        Args:
            config: Confluence configuration
        """
        super().__init__(config)
    
    def upload_attachment(
        self, 
        page_id: str, 
        file_path: Path, 
        comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload an attachment to a Confluence page.
        
        Args:
            page_id: Confluence page ID
            file_path: Path to the file to upload
            comment: Optional comment for the attachment
            
        Returns:
            Attachment data
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            ConfluenceApiError: For API errors
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Set up request details
        endpoint = f"content/{page_id}/child/attachment"
        headers = {"X-Atlassian-Token": "no-check"}
        data = {}
        
        if comment:
            data["comment"] = comment
        
        # Open file for upload
        with file_path.open("rb") as file_obj:
            files = {"file": (file_path.name, file_obj)}
            
            # Make the request
            response = self._make_request(
                method="POST",
                endpoint=endpoint,
                headers=headers,
                data=data,
                files=files
            )
            
            # Return the first result (there should only be one)
            if isinstance(response, dict) and "results" in response:
                return response["results"][0]
            
            return response
    
    def get_attachments(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Get all attachments for a page.
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            List of attachment data
        """
        endpoint = f"content/{page_id}/child/attachment"
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
    
    def get_attachment_content(self, attachment_id: str) -> bytes:
        """
        Get the content of an attachment.
        
        Args:
            attachment_id: Attachment ID
            
        Returns:
            Binary content of the attachment
        """
        endpoint = f"content/{attachment_id}/download"
        
        # Use session directly to get binary content
        url = f"{self.rest_api_url}/{endpoint.lstrip('/')}"
        response = self.session.get(url)
        response.raise_for_status()
        
        return response.content
    
    def delete_attachment(self, attachment_id: str) -> Dict[str, Any]:
        """
        Delete an attachment from Confluence.
        
        Args:
            attachment_id: Attachment ID
            
        Returns:
            Deletion result
        """
        endpoint = f"content/{attachment_id}"
        
        response = self._make_request(
            method="DELETE",
            endpoint=endpoint
        )
        
        # For DELETE operations with no response body
        if not response or (isinstance(response, dict) and not response):
            return {"status": "success", "id": attachment_id}
        
        return response