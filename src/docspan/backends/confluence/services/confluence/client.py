"""
Facade client for interacting with Confluence API.

This module provides a facade over specialized Confluence clients,
maintaining backward compatibility with the original monolithic client.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.models.page import ConfluencePage
from docspan.backends.confluence.services.confluence.base_client import (
    ConfluenceApiError, 
    ArchivedPageError,
    PageNotFoundError,
    RestrictedPageError
)
from docspan.backends.confluence.services.confluence.page_client import PageClient
from docspan.backends.confluence.services.confluence.attachment_client import AttachmentClient
from docspan.backends.confluence.services.confluence.comment_client import ConfluenceCommentClient
from docspan.backends.confluence.services.confluence.label_client import LabelClient
from docspan.backends.confluence.services.confluence.space_client import SpaceClient


class ConfluenceClient:
    """
    Facade client for interacting with Confluence REST API.
    
    This class delegates to specialized client implementations while
    maintaining backward compatibility with the original client.
    
    Attributes:
        config: Confluence configuration
        page_client: Client for page operations
        attachment_client: Client for attachment operations
        label_client: Client for label operations
        space_client: Client for space operations
        base_url: Base URL for the Confluence instance
        rest_api_url: URL for the REST API
        logger: Logger instance
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the Confluence client facade.
        
        Args:
            config: Confluence configuration
        """
        self.config = config
        
        # Initialize specialized clients
        self.page_client = PageClient(config)
        self.attachment_client = AttachmentClient(config)
        self.comment_client = ConfluenceCommentClient(config)
        self.label_client = LabelClient(config)
        self.space_client = SpaceClient(config)
        
        # Set up common attributes for backward compatibility
        self.session = self.page_client.session
        self.base_url = config.base_url.rstrip("/")
        
        if "/wiki" not in self.base_url:
            self.rest_api_url = f"{self.base_url}/wiki/rest/api"
        else:
            self.rest_api_url = f"{self.base_url}/rest/api"
            
        self.logger = logging.getLogger(__name__)
        
        # Cache for parent page information
        self._parent_page_info = None
    
    # Page operations
    
    def get_page(self, page_id: str) -> Dict[str, Any]:
        """
        Get a page from Confluence by ID.
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            Page data
            
        Raises:
            requests.RequestException: If the API call fails
            ValueError: If the page does not exist
        """
        try:
            return self.page_client.get_page(page_id)
        except PageNotFoundError as e:
            # Convert to requests.HTTPError for backward compatibility
            raise requests.HTTPError(str(e), response=e.response)
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    def find_page_by_title(self, space_key: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a page by title within a space.
        
        Args:
            space_key: Confluence space key
            title: Page title to find
            
        Returns:
            Page data or None if not found
        """
        return self.page_client.find_page_by_title(space_key, title)
    
    def create_page(self, page: ConfluencePage) -> Dict[str, Any]:
        """
        Create a new page in Confluence.
        
        Args:
            page: Page data to create
            
        Returns:
            Created page data
        """
        try:
            result = self.page_client.create_page(page)
            
            # Add labels if specified (maintain backward compatibility)
            if page.labels and result.get("id"):
                self.add_labels(result["id"], page.labels)
                
            return result
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    def update_page(self, page: ConfluencePage) -> Dict[str, Any]:
        """
        Update an existing page in Confluence.
        
        Args:
            page: Page data to update
            
        Returns:
            Updated page data or error information
        """
        try:
            result = self.page_client.update_page(page)
            
            # Add labels if specified (maintain backward compatibility)
            if page.labels and isinstance(result, dict) and result.get("id"):
                self.add_labels(result["id"], page.labels)
                
            return result
        except ArchivedPageError as e:
            # Return structured error for archived pages (backward compatibility)
            return self._create_archived_page_error(page, str(e))
        except PageNotFoundError as e:
            # Return structured error for not found pages (backward compatibility)
            return {
                "status": "error",
                "error_type": "page_not_found",
                "message": f"Page {page.id} not found",
                "page_id": page.id,
                "title": page.title,
                "original_error": str(e)
            }
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    def unarchive_page(self, page_id: str) -> Dict[str, Any]:
        """
        Unarchive a page in Confluence.
        
        Args:
            page_id: Confluence page ID to unarchive
            
        Returns:
            API response
            
        Raises:
            requests.RequestException: If the unarchive operation fails
        """
        try:
            return self.page_client.unarchive_page(page_id)
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    def delete_page(self, page_id: str, trash: bool = True, current_status: str = "current") -> Dict[str, Any]:
        """
        Delete a page from Confluence.
        
        Args:
            page_id: Confluence page ID to delete
            trash: Whether to move the page to trash or permanently delete it
            current_status: The current status of the page ("current", "archived", "draft", etc.)
                           This is important for the Confluence API to properly handle deletion
            
        Returns:
            API response
            
        Notes:
            Confluence DELETE API requires different handling based on page status:
            - If page is "current", it will be trashed
            - If page is "archived" or "trashed", it needs different treatment
        """
        try:
            # If the page is archived, try to unarchive it first
            if current_status == "archived":
                try:
                    self.logger.info(f"Page {page_id} is archived. Attempting to unarchive first...")
                    self.unarchive_page(page_id)
                    current_status = "current"  # Update status after unarchiving
                    self.logger.info(f"Page {page_id} successfully unarchived. Proceeding with deletion...")
                except Exception as e:
                    self.logger.warning(f"Failed to unarchive page {page_id}: {e}. Proceeding with direct deletion...")
            
            return self.page_client.delete_page(page_id, trash, current_status)
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    # Attachment operations
    
    def upload_attachment(
        self, page_id: str, file_path: Path, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload an attachment to a Confluence page.
        
        Args:
            page_id: Confluence page ID
            file_path: Path to the file to upload
            comment: Optional comment for the attachment
            
        Returns:
            Attachment data
        """
        try:
            return self.attachment_client.upload_attachment(page_id, file_path, comment)
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    # Label operations
    
    def add_label(self, page_id: str, label: str) -> Dict[str, Any]:
        """
        Add a label to a page.
        
        Args:
            page_id: Page ID
            label: Label to add
            
        Returns:
            API response
        """
        try:
            return self.label_client.add_label(page_id, label)
        except ConfluenceApiError as e:
            # Convert to requests.HTTPError for backward compatibility
            if e.response:
                raise requests.HTTPError(str(e), response=e.response)
            else:
                raise requests.RequestException(str(e))
    
    def add_labels(self, page_id: str, labels: List[str]) -> None:
        """
        Add multiple labels to a page.
        
        Args:
            page_id: Page ID
            labels: Labels to add
        """
        for label in labels:
            self.add_label(page_id, label)
    
    # Space key lookup
    
    def get_space_key_from_parent(self, parent_id: str) -> Optional[str]:
        """
        Retrieve the space key from a parent page ID.
        
        Args:
            parent_id: Parent page ID
            
        Returns:
            Space key if found, None otherwise
        """
        return self.page_client.get_space_key_from_parent(parent_id)
    
    # Helper methods for backward compatibility
    
    def _extract_error_from_response(self, response) -> Dict[str, Any]:
        """
        Extract error details from a response.
        
        Args:
            response: HTTP response
            
        Returns:
            Dictionary with error details
        """
        try:
            return response.json()
        except Exception:
            try:
                return {"message": response.text}
            except Exception:
                self.logger.debug("Could not extract error body from response", exc_info=True)
                return {"message": "Unknown error"}
    
    def _is_archived_page_error(self, response, error_message: str) -> bool:
        """
        Check if an error indicates an archived page.
        
        Args:
            response: HTTP response
            error_message: Error message string
            
        Returns:
            True if error indicates archived page, False otherwise
        """
        # Delegate to the page client's implementation
        if isinstance(self.page_client, PageClient) and hasattr(self.page_client, "_is_archived_page_error"):
            # Access protected method for backward compatibility
            return self.page_client._is_archived_page_error(response, error_message)
        
        # Fallback implementation
        if "PermissionException" in error_message and any([
            "Could not update Content" in error_message,
            "Parent Content doesn't exist" in error_message,
            "No parent content exists" in error_message
        ]):
            return True
            
        return False
    
    def _create_archived_page_error(self, page: ConfluencePage, error_message: str) -> Dict[str, Any]:
        """
        Create a standardized error response for archived pages.
        
        Args:
            page: Page that was being updated
            error_message: Error message from the API
            
        Returns:
            Dictionary with error information
        """
        return {
            "status": "error",
            "error_type": "archived_page",
            "message": "Page appears to be archived or restricted",
            "page_id": page.id,
            "title": page.title,
            "original_error": error_message
        }