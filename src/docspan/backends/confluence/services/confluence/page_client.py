"""
Client for managing Confluence pages.

This module provides specialized functionality for working with Confluence pages,
including creating, updating, getting, and deleting pages.
"""

import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Literal
from copy import deepcopy

from docspan.backends.confluence.config.models import ConfluenceConfig
from docspan.backends.confluence.models.page import ConfluencePage
from docspan.backends.confluence.services.confluence.base_client import (
    BaseConfluenceClient,
    ConfluenceApiError,
    ArchivedPageError,
    PageNotFoundError
)


class PageClient(BaseConfluenceClient):
    """
    Client for managing Confluence pages.
    
    Provides methods for creating, updating, retrieving, and deleting pages.
    """
    
    def __init__(self, config: ConfluenceConfig):
        """
        Initialize the page client.
        
        Args:
            config: Confluence configuration
        """
        super().__init__(config)
        
        # Cache for parent page information
        self._parent_page_info = None
    
    def get_page(self, page_id: str, detect_editor: bool = True) -> Dict[str, Any]:
        """
        Get a page from Confluence by ID.

        Args:
            page_id: Confluence page ID
            detect_editor: Whether to detect and include editor type information

        Returns:
            Page data with optional editor type detection

        Raises:
            PageNotFoundError: If the page doesn't exist
            ConfluenceApiError: For other API errors
        """
        # Expand metadata.properties to detect editor type
        expand_params = "body.atlas_doc_format,body.storage,version,ancestors,space"
        if detect_editor:
            expand_params += ",metadata.properties"

        page_data = self._make_request(
            method="GET",
            endpoint=f"content/{page_id}",
            params={"expand": expand_params}
        )

        # Detect and annotate editor type
        if detect_editor:
            page_data['_editor_type'] = self._detect_editor_type(page_data)

        return page_data
        
    def get_page_by_id(self, page_id: str) -> Dict[str, Any]:
        """
        Alternative method to get a page by ID with more detailed error handling.
        
        This method is optimized for page validation and provides more graceful error handling.
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            Page data or empty dict if page not found
        """
        try:
            # Try standard get_page first
            return self.get_page(page_id)
        except (PageNotFoundError, ArchivedPageError):
            # Try with alternate parameters that might work for archived pages
            try:
                return self._make_request(
                    method="GET",
                    endpoint=f"content/{page_id}",
                    params={"expand": "version,ancestors,space", "status": "any"},
                    handle_errors=False
                )
            except Exception:
                # Final attempt with minimal parameters
                try:
                    return self._make_request(
                        method="GET",
                        endpoint=f"content/{page_id}",
                        params={"status": "any"},
                        handle_errors=False
                    )
                except Exception:
                    return {}
        except Exception:
            # Return empty dict for any other errors
            return {}
    
    def get_page_version(self, page_id: str, version_number: int) -> Dict[str, Any]:
        """
        Get a specific version of a page from Confluence.

        Args:
            page_id: Confluence page ID
            version_number: Version number to retrieve

        Returns:
            Page data for the specified version

        Raises:
            PageNotFoundError: If the page or version doesn't exist
            ConfluenceApiError: For other API errors
        """
        page_data = self._make_request(
            method="GET",
            endpoint=f"content/{page_id}",
            params={
                "status": "historical",
                "version": version_number,
                "expand": "body.atlas_doc_format,body.storage,version,ancestors,space"
            }
        )
        return page_data

    def get_all_page_versions(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Get all versions of a page from Confluence.

        Args:
            page_id: Confluence page ID

        Returns:
            List of all page versions

        Raises:
            PageNotFoundError: If the page doesn't exist
            ConfluenceApiError: For other API errors
        """
        response = self._make_request(
            method="GET",
            endpoint=f"content/{page_id}/version"
        )
        return response.get("results", [])

    def find_page_by_title(self, space_key: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a page by title within a space.

        Args:
            space_key: Confluence space key
            title: Page title to find

        Returns:
            Page data or None if not found
        """
        response = self._make_request(
            method="GET",
            endpoint="content",
            params={"spaceKey": space_key, "title": title, "expand": "version"}
        )
        
        results = response.get("results", [])
        return results[0] if results else None
    
    def create_page(self, page: ConfluencePage) -> Dict[str, Any]:
        """
        Create a new page in Confluence.
        
        Args:
            page: Page data to create
            
        Returns:
            Created page data
            
        Raises:
            ValueError: If required fields are missing
            ConfluenceApiError: For API errors
        """
        # Try to get space key from different sources in priority order:
        # 1. Page's space_key if set
        # 2. Parent page if parent_id is set
        # 3. Config's space_key as default fallback
        
        space_key = page.space_key
        parent_id = page.parent_id
        
        # Validate parent page exists if specified
        if parent_id:
            try:
                # Try to get the parent page to verify it exists
                parent_exists = False
                try:
                    parent_page = self.get_page(parent_id)
                    parent_exists = True
                    self.logger.debug(f"Parent page exists: {parent_id}")
                    
                    # If we don't have a space key yet, get it from the parent
                    if not space_key and parent_page and "space" in parent_page:
                        space_key = parent_page.get("space", {}).get("key")
                        if space_key:
                            self.logger.debug(f"Using space key '{space_key}' from parent page {parent_id}")
                except Exception as e:
                    self.logger.warning(f"Failed to verify parent page {parent_id}: {e}")
                    parent_exists = False
                
                # If parent doesn't exist, raise a specific exception to handle this case properly
                # rather than silently creating at root level
                if not parent_exists:
                    self.logger.warning(f"Parent page {parent_id} does not exist or is not accessible.")
                    # We'll raise an error but keep the parent_id value in the file
                    # This allows the hierarchical structure to be preserved even when parent pages don't exist yet
                    raise ValueError(f"Parent page {parent_id} does not exist or is not accessible. Hierarchical publishing may require processing parent pages first.")
                    
            except Exception as e:
                # Any error here means the parent page likely doesn't exist
                self.logger.warning(f"Error checking parent page {parent_id}, assuming not available: {e}")
                parent_id = None
        
        # Try parent if no space_key specified (as a backup if direct check failed)
        if not space_key and parent_id:
            space_key = self.get_space_key_from_parent(parent_id)
            if space_key:
                self.logger.debug(f"Using space key '{space_key}' from parent page {parent_id}")
        
        # Fall back to config's default space key if available
        if not space_key and hasattr(self.config, 'space_key') and self.config.space_key:
            space_key = self.config.space_key
            self.logger.debug(f"Using default space key '{space_key}' from configuration")
            
        # If we still don't have a space key, we can't create the page
        if not space_key:
            raise ValueError("Space key is required for creating pages. Either specify it directly, provide a valid parent page ID, or set a default space key in configuration.")
            
        # Build request data
        data = {
            "type": "page",
            "title": page.title,
            "space": {"key": space_key},
            "ancestors": [{"id": parent_id}] if parent_id else [],
        }

        # SECURITY: If restrictions will be applied, create as draft first to avoid public exposure
        # Confluence Cloud doesn't support restrictions in create payload, so we use draft status
        # to keep the page hidden until restrictions are set
        create_as_draft = bool(page.restrictions)
        if create_as_draft:
            data["status"] = "draft"
            self.logger.info(f"Creating page '{page.title}' as draft (restrictions will be applied before publishing)")

        # Add content based on type
        data["body"] = self._format_page_content(page.content)

        # Create the page
        self.logger.debug(f"Creating page '{page.title}' in space {space_key} with parent ID {parent_id}")
        result = self._make_request(
            method="POST",
            endpoint="content",
            json_data=data
        )

        # Set restrictions after page creation if provided
        if page.restrictions and result.get("id"):
            page_id = result["id"]
            self.logger.info(f"Setting restrictions on newly created page {page_id}")
            self.set_page_restrictions(page_id, page.restrictions)

            # Now publish the page (transition from draft to current)
            if create_as_draft:
                self.logger.info(f"Publishing page {page_id} after restrictions applied")
                self._publish_draft_page(page_id, result.get("version", {}).get("number", 1))

        return result
    
    def update_page(self, page: ConfluencePage) -> Dict[str, Any]:
        """
        Update an existing page in Confluence.
        
        Args:
            page: Page data to update
            
        Returns:
            Updated page data
            
        Raises:
            ValueError: If page ID is missing
            ArchivedPageError: If the page is archived
            PageNotFoundError: If the page doesn't exist
            ConfluenceApiError: For other API errors
        """
        if not page.id:
            raise ValueError("Page ID is required for update")
            
        # Get current version and parent if not specified
        page_data = None
        if page.version is None or page.parent_id:
            try:
                page_data = self.get_page(page.id)
                page.version = page_data.get("version", {}).get("number", 0)
            except (PageNotFoundError, ArchivedPageError) as e:
                # These errors should propagate up
                raise
            except Exception as e:
                self.logger.warning(f"Couldn't get current page version: {e}")
                raise

        # Check if parent needs to be changed
        if page.parent_id and page_data:
            current_parent_id = None
            if "ancestors" in page_data and page_data["ancestors"]:
                current_parent_id = page_data["ancestors"][-1]["id"]

            # If parent is different, move the page first
            if current_parent_id != page.parent_id:
                # Guard: a page cannot be its own parent (e.g. space landing pages whose
                # connie-parent-id matches their own page ID).  Skip the move silently.
                if page.id == page.parent_id:
                    self.logger.debug(
                        f"Skipping move for page {page.id}: target parent is the page itself "
                        "(expected for root/landing pages)"
                    )
                else:
                    self.logger.info(
                        f"Parent changed from {current_parent_id} to {page.parent_id} for page {page.id}. "
                        "Moving page before updating content."
                    )
                    try:
                        self.move_page(page.id, page.parent_id, position='append')
                        self.logger.info(f"Successfully moved page {page.id} to new parent {page.parent_id}")
                    except Exception as move_error:
                        self.logger.error(f"Failed to move page to new parent: {move_error}")
                        # Continue with content update even if move fails

        # Build request data
        data = {
            "type": "page",
            "title": page.title,
            "version": {"number": page.version + 1},
        }
        
        # Add space key if provided
        if page.space_key:
            data["space"] = {"key": page.space_key}

        # Note: Don't add restrictions to update payload - will be set separately after update

        # Add comment if force update is enabled
        if hasattr(page, 'force_update') and page.force_update:
            import time
            timestamp = str(int(time.time()))
            data["metadata"] = {
                "comment": f"Forced update at {timestamp}"
            }
            
        # Add content
        data["body"] = self._format_page_content(page.content)
        
        # Update the page
        self.logger.debug(f"Updating page '{page.title}' (ID: {page.id}, version: {page.version})")
        
        try:
            result = self._make_request(
                method="PUT",
                endpoint=f"content/{page.id}",
                json_data=data
            )
            
            # Check if version actually incremented
            new_version = result.get('version', {}).get('number')
            if new_version <= page.version:
                self.logger.warning(
                    f"Version didn't increment (was: {page.version}, now: {new_version}). "  
                    f"Content may not have changed enough for Confluence to create a new version."
                )
            else:
                self.logger.debug(f"Version successfully incremented to {new_version}")

            # Set restrictions after page update if provided
            if page.restrictions and page.id:
                self.logger.debug(f"Setting restrictions on updated page {page.id}")
                self.set_page_restrictions(page.id, page.restrictions)

            return result
        except ArchivedPageError:
            # Return structured error info for archived pages
            return self._create_archived_page_error(page.id, page.title)
        except Exception as e:
            # No fallback format - ADF is the only supported format
            # Any errors should be raised directly for proper handling
            self.logger.error(f"Failed to update page {page.id}: {e}")
            raise

    def set_page_restrictions(self, page_id: str, restrictions: List[Dict[str, Any]]) -> None:
        """
        Set restrictions on a Confluence page.

        This must be called AFTER page creation, as Confluence Cloud doesn't support
        setting restrictions during page creation.

        Args:
            page_id: Confluence page ID
            restrictions: List of restriction objects with format:
                [
                    {
                        "operation": "read",
                        "restrictions": {
                            "user": [{"accountId": "123"}],
                            "group": [{"name": "developers"}]
                        }
                    },
                    {
                        "operation": "update",
                        "restrictions": {
                            "user": [{"accountId": "123"}]
                        }
                    }
                ]

        Raises:
            ConfluenceApiError: If the API request fails
        """
        if not restrictions:
            return

        self.logger.debug(f"Setting restrictions on page {page_id}: {restrictions}")

        # Update restrictions for each operation type
        for restriction in restrictions:
            operation = restriction.get("operation")
            restriction_data = restriction.get("restrictions", {})

            if not operation or not restriction_data:
                self.logger.warning(f"Invalid restriction format, skipping: {restriction}")
                continue

            # PUT to /rest/api/content/{id}/restriction/{operation}
            endpoint = f"content/{page_id}/restriction/{operation}"

            try:
                # The API expects the restriction data directly (not wrapped in array)
                payload = restriction_data

                self.logger.debug(f"Setting {operation} restriction on page {page_id}")
                self._make_request(
                    method="PUT",
                    endpoint=endpoint,
                    json_data=payload
                )
                self.logger.info(f"Successfully set {operation} restriction on page {page_id}")
            except Exception as e:
                self.logger.error(f"Failed to set {operation} restriction on page {page_id}: {e}")
                # Raise exception - restrictions are security-critical when explicitly requested
                raise ConfluenceApiError(f"Failed to set {operation} restriction on page {page_id}: {e}")

    def _publish_draft_page(self, page_id: str, current_version: int) -> Dict[str, Any]:
        """
        Publish a draft page (transition from draft to current status).

        Args:
            page_id: Confluence page ID
            current_version: Current version number of the draft page

        Returns:
            Updated page result

        Raises:
            ConfluenceApiError: If the API request fails
        """
        self.logger.debug(f"Publishing draft page {page_id} (version {current_version})")

        # Get the current page to retrieve its content and other properties
        current_page = self._make_request(
            method="GET",
            endpoint=f"content/{page_id}",
            params={"expand": "body.storage,version"}
        )

        # Update the page with status="current" to publish it
        update_data = {
            "version": {
                "number": current_version + 1
            },
            "title": current_page["title"],
            "type": "page",
            "status": "current",
            "body": current_page["body"]
        }

        result = self._make_request(
            method="PUT",
            endpoint=f"content/{page_id}",
            json_data=update_data
        )

        self.logger.info(f"Successfully published draft page {page_id}")
        return result

    def delete_page(self, page_id: str, trash: bool = True, current_status: str = "current") -> Dict[str, Any]:
        """
        Delete a page from Confluence.
        
        Args:
            page_id: Confluence page ID to delete
            trash: Whether to move the page to trash or permanently delete it
            current_status: The current status of the page ("current", "archived", "draft", etc.)
            
        Returns:
            Deletion result
            
        Notes:
            Confluence API deletion rules:
            - If page status is "current" -> use status=trashed to move to trash
            - If page status is "trashed" -> use status=deleted to permanently delete
            - If page status is "archived" -> page must be unarchived first
        """
        try:
            if current_status == "archived":
                # For archived pages, we need to unarchive first, then delete
                self.logger.info(f"Handling archived page deletion for {page_id}")
                
                # Step 1: Unarchive the page
                try:
                    self.unarchive_page(page_id)
                    self.logger.info(f"Successfully unarchived page {page_id}")
                    # Update status for next steps
                    current_status = "current"
                except Exception as e:
                    self.logger.error(f"Failed to unarchive page {page_id}: {e}")
                    # We can't proceed with standard deletion if unarchiving failed
                    raise ConfluenceApiError(
                        message=f"Cannot delete archived page {page_id}: Failed to unarchive first: {e}",
                        status_code=400
                    )
            
            # Step 2: Delete the page using two-step process per Confluence API v1
            # Only "current", "draft", or "trashed" are valid statuses for deletion
            if current_status not in ["current", "draft", "trashed"]:
                current_status = "current"  # Default to current if unknown status

            # Two-step deletion process:
            # Step 2a: Move to trash (if not already trashed)
            if current_status != "trashed":
                self.logger.info(f"Step 1: Moving page {page_id} to trash (current status: {current_status})")
                trash_result = self._make_request(
                    method="DELETE",
                    endpoint=f"content/{page_id}",
                    params={"status": "current"},  # Use "current" to move to trash
                    error_handlers={
                        400: lambda r: self._handle_delete_error(r, page_id),
                        403: lambda r: self._handle_delete_error(r, page_id)
                    }
                )
                self.logger.info(f"Successfully moved page {page_id} to trash")

                # If only moving to trash (not permanent delete), return now
                if trash:
                    if not trash_result:
                        trash_result = {"status": "success", "id": page_id, "trashed": True}
                    return trash_result

            # Step 2b: Purge from trash (permanent deletion)
            if not trash:
                self.logger.info(f"Step 2: Purging page {page_id} from trash")
                purge_result = self._make_request(
                    method="DELETE",
                    endpoint=f"content/{page_id}",
                    params={"status": "trashed"},  # Use "trashed" to purge
                    error_handlers={
                        400: lambda r: self._handle_delete_error(r, page_id),
                        403: lambda r: self._handle_delete_error(r, page_id)
                    }
                )
                if not purge_result:
                    purge_result = {"status": "success", "id": page_id, "deleted": True}
                self.logger.info(f"Successfully purged page {page_id}")
                return purge_result

            # Should not reach here
            return {"status": "success", "id": page_id, "deleted": True}
                
        except Exception as e:
            self.logger.error(f"Error deleting page {page_id}: {e}")
            # Don't mask the error, propagate it
            raise
    
    def unarchive_page(self, page_id: str) -> Dict[str, Any]:
        """
        Unarchive a page in Confluence by updating its status.
        
        Args:
            page_id: Confluence page ID to unarchive
            
        Returns:
            API response
            
        Raises:
            ConfluenceApiError: If the unarchive operation fails
        """
        self.logger.info(f"Attempting to unarchive page {page_id}")
        
        # First, get the current page info to retrieve its version and content
        try:
            # Get current page info with archived status
            page_info = None
            try:
                # Try with standard endpoint first
                page_info = self._make_request(
                    method="GET",
                    endpoint=f"content/{page_id}",
                    params={"expand": "body.atlas_doc_format,version,ancestors,space,status", "status": "archived"},
                    handle_errors=False
                )
            except Exception as e:
                self.logger.warning(f"Failed to get archived page with standard parameters: {e}")
                # Try other status parameters
                try:
                    page_info = self._make_request(
                        method="GET",
                        endpoint=f"content/{page_id}",
                        params={"expand": "body.atlas_doc_format,version,ancestors,space,status"},
                        handle_errors=False
                    )
                except Exception as e2:
                    self.logger.error(f"Failed to get page info: {e2}")
                    raise ConfluenceApiError(
                        message=f"Cannot retrieve page information for {page_id}",
                        status_code=404
                    )
            
            if not page_info:
                raise ConfluenceApiError(
                    message=f"Failed to retrieve page information for {page_id}",
                    status_code=404
                )
                
            # Extract current version and content (ADF format)
            current_version = page_info.get("version", {}).get("number", 0)
            space_key = page_info.get("space", {}).get("key")
            title = page_info.get("title", "")

            # Get ADF content - may be nested in "value" field as JSON string
            adf_body_raw = page_info.get("body", {}).get("atlas_doc_format", {})
            if isinstance(adf_body_raw, dict) and "value" in adf_body_raw:
                # Parse nested JSON string
                try:
                    adf_content = json.loads(adf_body_raw["value"])
                except json.JSONDecodeError:
                    # If parsing fails, use the raw value
                    adf_content = adf_body_raw
            else:
                adf_content = adf_body_raw

            self.logger.info(f"Retrieved page info: version={current_version}, space={space_key}, title={title}")

            # Create update payload with status=current
            update_data = {
                "id": page_id,
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "version": {"number": current_version + 1},
                "body": {
                    "editor": {
                        "value": json.dumps(adf_content),
                        "representation": "atlas_doc_format"
                    }
                },
                "status": "current"
            }
            
            # Update the page to change its status
            self.logger.info(f"Updating page {page_id} with status=current")
            result = self._make_request(
                method="PUT",
                endpoint=f"content/{page_id}",
                json_data=update_data,
                handle_errors=True
            )
            
            self.logger.info(f"Successfully unarchived page {page_id} by updating its status")
            return {"status": "success", "id": page_id, "unarchived": True, "data": result}
            
        except Exception as e:
            self.logger.error(f"Failed to unarchive page {page_id}: {e}")
            raise ConfluenceApiError(
                message=f"Failed to unarchive page {page_id}: {e}",
                response=getattr(e, "response", None)
            )

    def _handle_delete_error(self, response, page_id: str) -> Dict[str, Any]:
        """
        Handle errors during page deletion by trying alternative methods.

        Args:
            response: Error response
            page_id: ID of page being deleted

        Returns:
            Result of alternative deletion attempt
        """
        # Log the detailed error information
        try:
            error_body = response.json() if response.content else None
            self.logger.error(f"Delete failed for page {page_id}: status={response.status_code}, body={error_body}")
        except:
            self.logger.error(f"Delete failed for page {page_id}: status={response.status_code}, text={response.text[:200]}")

        # Attempt to use archive endpoint for archived pages
        self.logger.info(f"Standard deletion failed for page {page_id}, trying archive endpoint")

        try:
            # Try to use the archive endpoint instead
            archive_response = self.session.post(f"{self.rest_api_url}/content/{page_id}/archive")
            archive_response.raise_for_status()
            return {"status": "success", "id": page_id, "archived": True}
        except Exception as e:
            self.logger.warning(f"Archive endpoint also failed for page {page_id}: {e}")
            if hasattr(archive_response, 'text'):
                self.logger.warning(f"Archive response: {archive_response.text[:200]}")
            # Re-raise the original error by raising a new exception with the original status code
            raise ConfluenceApiError(
                message=f"Failed to delete page {page_id}",
                status_code=response.status_code,
                response=response
            )
    
    def get_space_key_from_parent(self, parent_id: str) -> Optional[str]:
        """
        Retrieve the space key from a parent page ID.
        
        Args:
            parent_id: Parent page ID
            
        Returns:
            Space key if found, None otherwise
        """
        # Check cache first
        if self._parent_page_info and self._parent_page_info.get("id") == parent_id:
            return self._parent_page_info.get("space", {}).get("key")
            
        try:
            # Get parent page information
            parent_data = self.get_page(parent_id)
            self._parent_page_info = parent_data
            
            # Extract space key
            space = parent_data.get("space", {})
            return space.get("key")
        except Exception as e:
            self.logger.warning(f"Failed to retrieve space key from parent page {parent_id}: {e}")
            
            # Try to infer space key from parent ID if it might contain space info
            if parent_id and any(space_id in parent_id for space_id in ["BYOADMIN", "DEV", "TEAM", "DOC"]):
                for space_id in ["BYOADMIN", "DEV", "TEAM", "DOC"]:
                    if space_id in parent_id:
                        self.logger.info(f"Using inferred space key '{space_id}' based on parent ID")
                        return space_id
            return None
    
    def move_page(self, page_id: str, target_id: str, position: Literal['before', 'after', 'append'] = 'append') -> Dict[str, Any]:
        """
        Move a page to a new location relative to a target page.
        
        Args:
            page_id: ID of the page to move
            target_id: ID of the target page
            position: Position relative to the target page:
                - 'before': Move page before target page (same level)
                - 'after': Move page after target page (same level)
                - 'append': Move page as a child of target page (recommended)
                
        Note:
            This method uses the dedicated Move Page API endpoint for moving pages.
            https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content---children-and-descendants/#api-content-id-move-position-targetid-put
            
        Returns:
            API response
            
        Raises:
            ConfluenceApiError: For API errors
        """
        self.logger.info(f"Moving page {page_id} to parent {target_id}")
        
        try:
            # First, make sure the target page exists
            try:
                target_page = self.get_page(target_id)
                target_title = target_page.get("title", "Unknown")
                self.logger.info(f"Target parent page {target_id} exists: '{target_title}'")
            except Exception as e:
                self.logger.error(f"Target parent page {target_id} does not exist: {e}")
                raise ValueError(f"Target parent page {target_id} does not exist or is not accessible")
            
            # Then, get the current page to obtain its version and other details
            try:
                page_data = self.get_page(page_id)
            except Exception as e:
                self.logger.error(f"Source page {page_id} does not exist: {e}")
                raise ValueError(f"Source page {page_id} does not exist or is not accessible")
                
            current_version = page_data.get("version", {}).get("number", 0)
            space_key = page_data.get("space", {}).get("key")
            title = page_data.get("title")
            
            # Check if page already has the correct parent
            current_parent_id = None
            if "ancestors" in page_data and page_data["ancestors"]:
                current_parent_id = page_data["ancestors"][-1]["id"]
                self.logger.info(f"Current parent of page {page_id} is {current_parent_id}")
                
            if current_parent_id == target_id:
                self.logger.info(f"Page {page_id} already has parent {target_id}, no update needed")
                return {"id": page_id, "title": title, "status": "current", "no_change": True}
            
            # Use the dedicated move page API endpoint
            self.logger.info(f"📄➡️ Moving page '{title}' (ID: {page_id}) to parent '{target_title}' (ID: {target_id}) with position {position}")
            
            try:
                # Call the dedicated move page endpoint
                # Reference: https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content---children-and-descendants/#api-content-id-move-position-targetid-put
                result = self._make_request(
                    method="PUT", 
                    endpoint=f"content/{page_id}/move/{position}/{target_id}",
                    params={"expand": "ancestors,version"}
                )
                
                # Log more detailed information about the response
                resp_id = result.get("id", "unknown")
                resp_title = result.get("title", "unknown")
                resp_version = result.get("version", {}).get("number", "unknown")
                resp_type = result.get("type", "unknown")
                self.logger.info(f"✅ Move API successful: {resp_title} (ID: {resp_id}, ver: {resp_version}, type: {resp_type})")
            except Exception as move_error:
                self.logger.error(f"❌ Move API failed: {move_error}")
                # Re-raise to be handled by the outer exception handler
                raise
            
            # 7. Verify the parent was updated correctly
            try:
                verification = self.get_page(page_id)
                final_parent_id = None
                if "ancestors" in verification and verification["ancestors"]:
                    final_parent_id = verification["ancestors"][-1]["id"]
                    
                # Always log the verification result
                self.logger.info(f"Verification - Page '{title}' (ID: {page_id}) current parent: {final_parent_id}")
                
                if final_parent_id == target_id:
                    self.logger.info(f"✅ Successfully moved page '{title}' (ID: {page_id}) to parent '{target_title}' (ID: {target_id})")
                else:
                    self.logger.warning(f"⚠️ Page move may have failed. Page '{title}' (ID: {page_id}) has parent: {final_parent_id}, Expected parent: {target_id}")
            except Exception as e:
                self.logger.warning(f"Could not verify page move: {e}")
            
            return result
        except ValueError as ve:
            # Re-raise value errors for better handling
            self.logger.error(f"Value error moving page: {ve}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to move page {page_id} to parent {target_id}: {e}")
            raise

    def _detect_editor_type(self, page_data: Dict[str, Any]) -> str:
        """
        Detect which editor was used to create/edit a page.

        Args:
            page_data: Page data from Confluence API

        Returns:
            Editor type: 'v2' (new editor/ADF), 'v1' (legacy editor/storage), or 'unknown'
        """
        # Check metadata.properties.editor first (most reliable)
        metadata = page_data.get("metadata", {})
        properties = metadata.get("properties", {})

        # The editor property might be nested
        if "editor" in properties:
            editor_info = properties["editor"]
            if isinstance(editor_info, dict):
                editor_value = editor_info.get("value", "")
            else:
                editor_value = str(editor_info)

            if editor_value == "v2":
                return "v2"
            elif editor_value == "v1":
                return "v1"

        # Fallback: Check body format
        body = page_data.get("body", {})

        # If has atlas_doc_format but no storage, it's new editor
        has_adf = "atlas_doc_format" in body and body["atlas_doc_format"]
        has_storage = "storage" in body and body["storage"]

        if has_adf and not has_storage:
            return "v2"
        elif has_storage and not has_adf:
            return "v1"
        elif has_storage and has_adf:
            # Both formats present - prefer the one with actual content
            storage_value = body.get("storage", {}).get("value", "")
            adf_value = body.get("atlas_doc_format", {})

            if adf_value and isinstance(adf_value, dict) and adf_value.get("version") == 1:
                return "v2"
            elif storage_value:
                return "v1"

        return "unknown"

    def migrate_to_new_editor(self, page_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Migrate a legacy editor page to the new editor (ADF format).

        This method:
        1. Fetches the page and detects its editor type
        2. If it's using the legacy editor, converts storage format to ADF
        3. Updates the page with ADF content and sets editor metadata to v2

        Args:
            page_id: Confluence page ID to migrate
            dry_run: If True, only detect and report without making changes

        Returns:
            Dictionary with migration results including:
            - current_editor: The detected editor type
            - needs_migration: Whether migration is needed
            - migrated: Whether migration was performed (False for dry_run)
            - page_data: Updated page data if migrated

        Raises:
            ConfluenceApiError: For API errors
        """
        self.logger.info(f"{'[DRY RUN] ' if dry_run else ''}Checking page {page_id} for legacy editor migration")

        # Get page with editor detection
        page_data = self.get_page(page_id, detect_editor=True)
        editor_type = page_data.get("_editor_type", "unknown")
        title = page_data.get("title", "Unknown")

        result = {
            "page_id": page_id,
            "title": title,
            "current_editor": editor_type,
            "needs_migration": editor_type == "v1",
            "migrated": False
        }

        if editor_type == "v2":
            self.logger.info(f"Page '{title}' (ID: {page_id}) is already using new editor (v2)")
            return result

        if editor_type == "unknown":
            self.logger.debug(f"Page '{title}' (ID: {page_id}) has unknown editor type (likely transitional state with both formats but no metadata)")
            self.logger.debug(f"Treating as legacy page and attempting migration...")

        self.logger.debug(f"Page '{title}' (ID: {page_id}) is using legacy editor — attempting auto-migration")

        if dry_run:
            self.logger.info(f"[DRY RUN] Would migrate page '{title}' from legacy editor to new editor")
            return result

        # Perform migration
        try:
            body = page_data.get("body", {})

            # Check if page already has ADF content (transitional state)
            existing_adf = body.get("atlas_doc_format")
            if existing_adf and isinstance(existing_adf, dict):
                self.logger.info(f"Page already has ADF content, will update metadata only")
                adf_content = existing_adf
            else:
                # Get storage format content and convert to ADF
                storage_content = body.get("storage", {}).get("value", "")

                if not storage_content:
                    raise ValueError("No storage content found in legacy page")

                # Convert storage format HTML to ADF
                # Note: This is a simplified conversion - in reality, you may need
                # a more sophisticated HTML to ADF converter
                try:
                    from docspan.backends.confluence.adf.converter import storage_to_adf
                    adf_content = storage_to_adf(storage_content)
                except (ImportError, AttributeError):
                    # Fallback: Create a legacy content macro wrapper
                    self.logger.info("storage_to_adf not available, using legacy content wrapper")
                    adf_content = self._wrap_legacy_content_in_adf(storage_content)

            # Get current version
            current_version = page_data.get("version", {}).get("number", 0)
            space_key = page_data.get("space", {}).get("key")

            # Update page with ADF content
            update_data = {
                "id": page_id,
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "version": {"number": current_version + 1},
                "body": {
                    "editor": {
                        "value": json.dumps(adf_content),
                        "representation": "atlas_doc_format"
                    }
                }
            }

            self.logger.info(f"Migrating page '{title}' (ID: {page_id}) to new editor...")
            updated_page = self._make_request(
                method="PUT",
                endpoint=f"content/{page_id}",
                json_data=update_data
            )

            # Set editor property via separate API endpoint (properties must be set this way)
            self.logger.info(f"Setting editor property to v2 for page '{title}'...")
            property_data = {
                "key": "editor",
                "value": "v2"
            }

            # Try to update existing property first, if that fails, create it
            try:
                self._make_request(
                    method="PUT",
                    endpoint=f"content/{page_id}/property/editor",
                    json_data=property_data
                )
                self.logger.info(f"✅ Updated editor property to v2")
            except Exception as prop_error:
                # Property doesn't exist, create it
                self.logger.debug(f"Property doesn't exist, creating it: {prop_error}")
                self._make_request(
                    method="POST",
                    endpoint=f"content/{page_id}/property",
                    json_data=property_data
                )
                self.logger.info(f"✅ Created editor property with value v2")

            result["migrated"] = True
            result["page_data"] = updated_page
            self.logger.info(f"✅ Successfully migrated page '{title}' to new editor (ADF format)")

            return result

        except Exception as e:
            # Log at DEBUG: migration failure is expected for many legacy pages
            # (e.g. 400 from the ADF-conversion endpoint). Callers that want to
            # surface this to the user can do so at a higher level.
            self.logger.debug(f"Failed to migrate page {page_id}: {e}")
            result["error"] = str(e)
            raise

    def _wrap_legacy_content_in_adf(self, storage_html: str) -> Dict[str, Any]:
        """
        Wrap legacy storage format HTML in an ADF legacy content macro.

        This is a fallback method when proper HTML->ADF conversion is not available.
        It wraps the legacy content in a legacy content macro so it renders correctly.

        Args:
            storage_html: Legacy storage format HTML

        Returns:
            ADF document with legacy content macro
        """
        return {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "⚠️ This page was migrated from the legacy editor. Some formatting may appear in a legacy content block below."
                        }
                    ]
                },
                {
                    "type": "bodiedExtension",
                    "attrs": {
                        "extensionType": "com.atlassian.confluence.macro.core",
                        "extensionKey": "legacy-content",
                        "parameters": {
                            "macroParams": {},
                            "macroMetadata": {
                                "macroId": {"value": "legacy-content"},
                                "schemaVersion": {"value": "1"},
                                "title": "Legacy Content"
                            }
                        },
                        "layout": "default"
                    },
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": storage_html,
                                    "marks": [{"type": "code"}]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    def _format_page_content(self, content: Any) -> Dict[str, Any]:
        """
        Format page content for the Confluence API using ADF format.

        This method enforces ADF (Atlassian Document Format) as the exclusive format.
        Storage format is not supported and will raise errors.

        For REST API v1 (/rest/api/content), the body must use the "editor" format with
        the ADF content as a JSON-encoded string.

        References:
        - https://community.developer.atlassian.com/t/can-i-create-content-in-confluence-cloud-using-atlassian-document-format-adf-rather-than-storage-format/30720
        - https://community.developer.atlassian.com/t/confluence-rest-api-v2-create-page-with-atlas-doc-format-representation/67565

        Args:
            content: Page content (must be ADF dict)

        Returns:
            Formatted content dictionary with editor format for REST API v1:
            {
                "editor": {
                    "value": "<JSON-encoded ADF string>",
                    "representation": "atlas_doc_format"
                }
            }

        Raises:
            UnsupportedADFFeatureError: If content contains unsupported features
            ADFConversionError: If content cannot be converted to valid ADF
        """
        from docspan.backends.confluence.services.confluence.base_client import (
            UnsupportedADFFeatureError,
            ADFConversionError,
            InvalidADFError
        )

        if isinstance(content, dict):
            # Content is already ADF document
            self._validate_adf_content(content)
            # For REST API v1, we need to use "editor" with a JSON-encoded string
            return {
                "editor": {
                    "value": json.dumps(content),
                    "representation": "atlas_doc_format"
                }
            }

        elif isinstance(content, str) and content.startswith("{") and content.endswith("}"):
            # Content looks like JSON string - parse and validate
            try:
                adf_content = json.loads(content)
                self._validate_adf_content(adf_content)
                # For REST API v1, we need to use "editor" with a JSON-encoded string
                return {
                    "editor": {
                        "value": json.dumps(adf_content),
                        "representation": "atlas_doc_format"
                    }
                }
            except json.JSONDecodeError as e:
                raise ADFConversionError(
                    f"Failed to parse JSON content: {e}",
                    markdown_content=content[:200]
                )
        else:
            # String content is not supported - must be ADF dict
            raise ADFConversionError(
                "Content must be ADF dictionary format. String/storage format is not supported.",
                markdown_content=str(content)[:200] if content else None
            )

    def _validate_adf_content(self, adf_content: Dict[str, Any]) -> None:
        """
        Validate ADF content for unsupported features.

        Args:
            adf_content: ADF content to validate

        Raises:
            UnsupportedADFFeatureError: If content contains unsupported features
            InvalidADFError: If ADF structure is invalid
        """
        from docspan.backends.confluence.services.confluence.base_client import (
            UnsupportedADFFeatureError,
            InvalidADFError
        )

        # Check basic structure
        if not isinstance(adf_content, dict):
            raise InvalidADFError(
                "ADF content must be a dictionary",
                adf_content=adf_content
            )

        # Check for storage_format_html (indicates legacy format)
        # NOTE: Disabled this validation because storage_format_html is legitimately
        # used by the Mermaid plugin to embed rendered diagrams.
        # The original validation was too strict and rejected valid ADF content.
        # if self._has_storage_format_html(adf_content):
        #     raise UnsupportedADFFeatureError(
        #         "Content contains 'storage_format_html' attribute. "
        #         "This indicates legacy storage format which is no longer supported. "
        #         "Content must be converted to pure ADF format.",
        #         feature_name="storage_format_html"
        #     )

        # Validate node types are supported
        self._validate_node_types(adf_content)

    def _has_storage_format_html(self, adf_content: Dict[str, Any]) -> bool:
        """
        Check if any node in the ADF content has storage_format_html.

        Args:
            adf_content: ADF content to check

        Returns:
            True if any node has storage_format_html, False otherwise
        """
        # Check current node
        if "attrs" in adf_content and "storage_format_html" in adf_content["attrs"]:
            return True

        # Check content nodes recursively
        if "content" in adf_content and isinstance(adf_content["content"], list):
            for node in adf_content["content"]:
                if isinstance(node, dict) and self._has_storage_format_html(node):
                    return True

        return False

    def _validate_node_types(self, node: Dict[str, Any]) -> None:
        """
        Recursively validate that all node types are supported.

        Args:
            node: ADF node to validate

        Raises:
            UnsupportedADFFeatureError: If node contains unsupported types
        """
        from docspan.backends.confluence.services.confluence.base_client import UnsupportedADFFeatureError

        node_type = node.get("type")

        # List of known unsupported features (expand as we discover them)
        # Note: This is intentionally minimal - we discover unsupported features through errors
        unsupported_types = {
            # Add types here as we discover them
            # Example: "customPanel", "unknownExtension"
        }

        if node_type in unsupported_types:
            raise UnsupportedADFFeatureError(
                f"ADF node type '{node_type}' is not yet supported. "
                f"Please implement ADF conversion for this type.",
                feature_name=node_type
            )

        # Recursively check children
        if "content" in node and isinstance(node["content"], list):
            for child in node["content"]:
                if isinstance(child, dict):
                    self._validate_node_types(child)