"""
Base client for interacting with Confluence API.

This module provides a base client class with common functionality
for Confluence API interactions, which specialized clients can extend.
"""

import abc
import json
import logging
from typing import Any, Dict, List, Optional, Union

import requests

from docspan.backends.confluence.config.models import ConfluenceConfig


class ConfluenceApiError(Exception):
    """Exception raised for Confluence API errors."""
    
    def __init__(
        self, 
        message: str, 
        status_code: Optional[int] = None, 
        error_type: Optional[str] = None, 
        response: Optional[requests.Response] = None
    ):
        """
        Initialize the exception.
        
        Args:
            message: Error message
            status_code: HTTP status code, if applicable
            error_type: Type of error, if known
            response: Original response object, if available
        """
        self.status_code = status_code
        self.error_type = error_type
        self.response = response
        super().__init__(message)


class ArchivedPageError(ConfluenceApiError):
    """Exception raised when attempting to access an archived page."""
    pass


class RestrictedPageError(ConfluenceApiError):
    """Exception raised when attempting to access a page without permissions."""
    pass


class PageNotFoundError(ConfluenceApiError):
    """Exception raised when a page cannot be found."""
    pass


class UnsupportedADFFeatureError(Exception):
    """
    Exception raised when markdown content requires an unimplemented ADF feature.

    This indicates that the content cannot be represented in pure ADF format
    and needs implementation work to support the feature.

    Attributes:
        message: Description of the unsupported feature
        source_file: Path to the markdown file containing the feature
        feature_name: Name of the unsupported feature (e.g., "mermaid_diagram")
    """

    def __init__(
        self,
        message: str,
        source_file: Optional[str] = None,
        feature_name: Optional[str] = None
    ):
        self.source_file = source_file
        self.feature_name = feature_name

        full_message = message
        if source_file:
            full_message += f"\nFile: {source_file}"
        if feature_name:
            full_message += f"\nFeature: {feature_name}"

        super().__init__(full_message)


class InvalidADFError(Exception):
    """
    Exception raised when generated ADF doesn't meet schema requirements.

    This indicates a bug in the ADF converter - the generated JSON doesn't
    conform to the Atlassian Document Format specification.

    Attributes:
        message: Description of what's invalid
        adf_content: The invalid ADF content (for debugging)
        missing_fields: List of missing required fields
    """

    def __init__(
        self,
        message: str,
        adf_content: Optional[Dict[str, Any]] = None,
        missing_fields: Optional[List[str]] = None
    ):
        self.adf_content = adf_content
        self.missing_fields = missing_fields or []

        full_message = f"Invalid ADF document: {message}"
        if missing_fields:
            full_message += f"\nMissing fields: {', '.join(missing_fields)}"

        super().__init__(full_message)


class ADFConversionError(Exception):
    """
    Exception raised when markdown cannot be converted to valid ADF.

    This is a general conversion failure that doesn't fit other categories.
    It may indicate corrupt markdown, unsupported syntax, or a converter bug.

    Attributes:
        message: Description of the conversion failure
        source_file: Path to the markdown file
        markdown_content: The problematic markdown (excerpt for debugging)
    """

    def __init__(
        self,
        message: str,
        source_file: Optional[str] = None,
        markdown_content: Optional[str] = None
    ):
        self.source_file = source_file
        self.markdown_content = markdown_content

        full_message = f"ADF conversion failed: {message}"
        if source_file:
            full_message += f"\nFile: {source_file}"
        if markdown_content:
            excerpt = markdown_content[:200] + "..." if len(markdown_content) > 200 else markdown_content
            full_message += f"\nMarkdown excerpt: {excerpt}"

        super().__init__(full_message)


class BaseConfluenceClient(abc.ABC):
    """
    Base client for Confluence REST API interactions.
    
    Provides common functionality for authentication, session management,
    URL construction, and error handling.
    
    Attributes:
        config: Confluence configuration
        session: Requests session for API calls
        base_url: Base URL for the Confluence instance
        rest_api_url: URL for the REST API
        logger: Logger instance
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the base client.
        
        Args:
            config: Confluence configuration
        """
        self.config = config
        self.session = requests.Session()
        
        # Configure authentication
        if config.api_token:
            self.session.auth = (config.username, config.api_token)
            
        # Set base URL
        self.base_url = config.base_url.rstrip("/")
        
        # Determine REST API URL based on Confluence Cloud patterns
        if "/wiki" not in self.base_url:
            self.rest_api_url = f"{self.base_url}/wiki/rest/api"
        else:
            self.rest_api_url = f"{self.base_url}/rest/api"
            
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None, 
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        error_handlers: Optional[Dict[int, callable]] = None,
        handle_errors: bool = True
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Make a request to the Confluence API with error handling.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (will be appended to rest_api_url)
            params: Query parameters
            data: Form data
            json_data: JSON data
            files: Files to upload
            headers: Request headers
            error_handlers: Mapping of status codes to handler functions
            
        Returns:
            API response parsed as JSON
            
        Raises:
            ConfluenceApiError: For API errors
            requests.RequestException: For request errors
        """
        url = f"{self.rest_api_url}/{endpoint.lstrip('/')}"
        
        # Set up default headers
        all_headers = {"Accept": "application/json"}
        if headers:
            all_headers.update(headers)
            
        # Log request details at debug level
        self.logger.debug(f"API Request: {method} {url}")
        if params:
            self.logger.debug(f"Params: {params}")
        if json_data:
            # Log the structure of json_data to help debug
            if isinstance(json_data, dict):
                self.logger.debug(f"JSON Data keys: {list(json_data.keys())}")
                if "body" in json_data:
                    body = json_data["body"]
                    self.logger.debug(f"Body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}")
                    if isinstance(body, dict) and "atlas_doc_format" in body:
                        adf = body["atlas_doc_format"]
                        if isinstance(adf, dict):
                            content_len = len(adf.get("content", [])) if "content" in adf else 0
                            self.logger.debug(f"ADF document has {content_len} content nodes")
                            # Log the actual ADF structure (first 500 chars) to verify it's correct
                            adf_str = json.dumps(adf)
                            self.logger.debug(f"ADF structure (first 500 chars): {adf_str[:500]}")
            else:
                self.logger.debug(f"JSON Data type: {type(json_data)}")

        # Add comprehensive logging to debug empty page issue
        if json_data:
            full_json = json.dumps(json_data, indent=2)
            self.logger.debug(f"FULL REQUEST PAYLOAD (first 2000 chars):\n{full_json[:2000]}")
            self.logger.debug(f"Total payload length: {len(full_json)} characters")

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
                files=files,
                headers=all_headers,
                timeout=(10, 30)
            )
            
            # Try to handle specific error codes with custom handlers
            if error_handlers and response.status_code in error_handlers:
                return error_handlers[response.status_code](response)
                
            # Otherwise, raise for status and handle common errors
            if handle_errors:
                response.raise_for_status()

            # Log response details
            if response.content:
                response_data = response.json()
                response_str = json.dumps(response_data, indent=2)
                self.logger.debug(f"API RESPONSE (first 2000 chars):\n{response_str[:2000]}")
                return response_data
            else:
                return {"status": "success"}
                
        except requests.exceptions.HTTPError as e:
            self._handle_http_error(e)
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request error: {e}")
            raise ConfluenceApiError(f"Request error: {e}")
    
    def _handle_http_error(self, error: requests.exceptions.HTTPError):
        """
        Handle HTTP errors from the API.
        
        Args:
            error: HTTP error from requests
            
        Raises:
            ArchivedPageError: For archived page errors
            RestrictedPageError: For permission errors
            PageNotFoundError: For not found errors
            ConfluenceApiError: For other API errors
        """
        status_code = error.response.status_code
        error_details = self._extract_error_from_response(error.response)
        
        if status_code == 403:
            # Check if this is an archived page
            if self._is_archived_page_error(error.response, error_details.get("message", "")):
                raise ArchivedPageError(
                    message="Page appears to be archived or restricted",
                    status_code=403,
                    error_type="archived_page",
                    response=error.response
                )
            else:
                raise RestrictedPageError(
                    message=f"Permission denied: {error_details.get('message', 'Access restricted')}",
                    status_code=403,
                    response=error.response
                )
        elif status_code == 404:
            raise PageNotFoundError(
                message="Page not found or doesn't exist",
                status_code=404,
                response=error.response
            )
        else:
            # For other error types
            message = error_details.get("message", str(error))
            raise ConfluenceApiError(
                message=f"API error ({status_code}): {message}",
                status_code=status_code,
                response=error.response
            )
    
    def _extract_error_from_response(self, response) -> Dict[str, Any]:
        """
        Extract error details from a response.
        
        Args:
            response: HTTP response
            
        Returns:
            Dictionary with error details
        """
        try:
            error_data = response.json()
            # Log the complete error response
            self.logger.debug(f"Raw API error response: {json.dumps(error_data, indent=2)}")
            return error_data
        except (ValueError, json.JSONDecodeError):
            try:
                # Try to parse as text if JSON parsing fails
                error_text = response.text
                self.logger.debug(f"Raw API error text: {error_text[:1000]}")
                return {"message": error_text}
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
        # Several checks for different archived page indicators
        if "PermissionException" in error_message and any([
            "Could not update Content" in error_message,
            "Parent Content doesn't exist" in error_message,
            "No parent content exists" in error_message,
            "The parent ID specified does not exist" in error_message,
            "No parent content found" in error_message
        ]):
            return True
            
        # Also check for specific error code patterns
        try:
            error_data = self._extract_error_from_response(response)
            if error_data:
                # Check for Confluence-specific error codes that indicate archived pages
                if error_data.get("statusCode") == 403 and any([
                    "CONTENT_ARCHIVE" in str(error_data),
                    "contentArchivedException" in str(error_data),
                    "parentNotFoundException" in str(error_data)
                ]):
                    return True
        except Exception:
            self.logger.debug("Could not inspect response for archived-page error", exc_info=True)

        return False
    
    def _create_archived_page_error(self, page_id: str, title: Optional[str] = None, 
                                   error_message: str = "") -> Dict[str, Any]:
        """
        Create a standardized error response for archived pages.
        
        Args:
            page_id: Page ID
            title: Page title, if known
            error_message: Error message from the API
            
        Returns:
            Dictionary with error information
        """
        return {
            "status": "error",
            "error_type": "archived_page",
            "message": "Page appears to be archived or restricted",
            "page_id": page_id,
            "title": title or "",
            "original_error": error_message
        }