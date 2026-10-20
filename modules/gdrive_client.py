"""
Google Drive Client Module

Provides clients for interacting with Google Drive API:
- GoogleDocsClient: For Google Docs operations (Account A)
- VaultDriveClient: For Markdown files in Obsidian vault (Account B)
"""

import io
import logging
import time
from datetime import datetime
from typing import Optional, Dict, List
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
import socket

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 60  # seconds


class GoogleSheetsClient:
    """Client for reading mappings from Google Sheets"""

    def __init__(self, credentials):
        """
        Initialize Google Sheets client

        Args:
            credentials: Google OAuth2 credentials
        """
        self.credentials = credentials
        self.sheets_service = build('sheets', 'v4', credentials=credentials)

    def get_mappings(self, sheet_id: str, sheet_range: str) -> List[Dict[str, str]]:
        """
        Read mappings from Google Sheet and normalize

        Args:
            sheet_id: Spreadsheet ID
            sheet_range: Range to read (e.g., 'Sheet1!A:B')

        Returns:
            list: [{ 'doc_id': ..., 'vault_path': ... }, ...]
        """
        result = self.sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=sheet_range
        ).execute()
        values = result.get('values', [])

        if not values or len(values) < 2:
            logger.warning("Sheet has no data rows for mappings")
            return []

        headers = [h.strip().lower() for h in values[0]]
        try:
            doc_idx = headers.index('doc_id')
            vault_idx = headers.index('vault_path')
        except ValueError:
            raise ValueError("Sheet header must include 'doc_id' and 'vault_path'")

        mappings: List[Dict[str, str]] = []
        for row in values[1:]:
            # Guard missing columns
            doc_id = row[doc_idx].strip() if len(row) > doc_idx and row[doc_idx] else ''
            vault_path = row[vault_idx].strip() if len(row) > vault_idx and row[vault_idx] else ''

            if not doc_id or not vault_path:
                continue

            mappings.append({
                'doc_id': doc_id,
                'vault_path': vault_path
            })

        logger.info(f"Loaded {len(mappings)} mapping(s) from Google Sheet")
        return mappings


class GoogleDocsClient:
    """Client for Google Docs operations (Account A)"""

    def __init__(self, credentials):
        """
        Initialize Google Docs client

        Args:
            credentials: Google OAuth2 credentials
        """
        self.credentials = credentials
        # Set timeout for HTTP requests
        self.drive_service = build('drive', 'v3', credentials=credentials)
        self.docs_service = build('docs', 'v1', credentials=credentials)

        # Set default socket timeout
        socket.setdefaulttimeout(TIMEOUT)

    def get_doc_content(self, doc_id: str) -> str:
        """
        Get Google Doc content as HTML with retry mechanism

        Args:
            doc_id: Google Doc ID

        Returns:
            str: Document content in HTML format
        """
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                # Export as HTML
                request = self.drive_service.files().export_media(
                    fileId=doc_id,
                    mimeType='text/html'
                )
                content = request.execute()
                logger.info(f"Retrieved content from Google Doc: {doc_id}")
                return content.decode('utf-8')

            except socket.timeout as e:
                last_error = e
                logger.warning(f"Timeout retrieving doc {doc_id} (attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))  # Exponential backoff
                    continue

            except HttpError as e:
                # Don't retry on 404 or permission errors
                if e.resp.status in [404, 403]:
                    logger.error(f"Error retrieving doc {doc_id}: {e}")
                    raise
                # Retry on other HTTP errors
                last_error = e
                logger.warning(f"HTTP error retrieving doc {doc_id} (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue

            except Exception as e:
                last_error = e
                logger.warning(f"Error retrieving doc {doc_id} (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue

        # All retries failed
        logger.error(f"Failed to retrieve doc {doc_id} after {MAX_RETRIES} attempts")
        raise last_error

    def get_doc_plain_text(self, doc_id: str) -> str:
        """
        Get Google Doc content as plain text

        Args:
            doc_id: Google Doc ID

        Returns:
            str: Document content in plain text
        """
        try:
            request = self.drive_service.files().export_media(
                fileId=doc_id,
                mimeType='text/plain'
            )
            content = request.execute()
            logger.info(f"Retrieved plain text from Google Doc: {doc_id}")
            return content.decode('utf-8')
        except HttpError as e:
            logger.error(f"Error retrieving doc {doc_id}: {e}")
            raise

    def update_doc_content(self, doc_id: str, content: str) -> bool:
        """
        Update Google Doc content from plain text

        Args:
            doc_id: Google Doc ID
            content: New content as plain text

        Returns:
            bool: True if successful
        """
        try:
            # Get current document to find the end index
            doc = self.docs_service.documents().get(documentId=doc_id).execute()
            doc_content = doc.get('body').get('content')
            end_index = doc_content[-1].get('endIndex') - 1

            # Delete all content first
            requests = [
                {
                    'deleteContentRange': {
                        'range': {
                            'startIndex': 1,
                            'endIndex': end_index
                        }
                    }
                },
                {
                    'insertText': {
                        'location': {
                            'index': 1
                        },
                        'text': content
                    }
                }
            ]

            self.docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()

            logger.info(f"Updated Google Doc: {doc_id}")
            return True
        except HttpError as e:
            logger.error(f"Error updating doc {doc_id}: {e}")
            raise

    def get_modified_time(self, doc_id: str) -> datetime:
        """
        Get last modified time of Google Doc

        Args:
            doc_id: Google Doc ID

        Returns:
            datetime: Last modified timestamp
        """
        try:
            file = self.drive_service.files().get(
                fileId=doc_id,
                fields='modifiedTime'
            ).execute()
            modified_time_str = file.get('modifiedTime')
            modified_time = datetime.fromisoformat(modified_time_str.replace('Z', '+00:00'))
            logger.debug(f"Doc {doc_id} modified at: {modified_time}")
            return modified_time
        except HttpError as e:
            if e.resp.status == 404:
                logger.error(
                    f"Google Doc not found: {doc_id}\n"
                    f"  → Please share this document with: "
                    f"obsidian-sync-account-aw@obsidian-sync-vault.iam.gserviceaccount.com\n"
                    f"  → Document URL: https://docs.google.com/document/d/{doc_id}/edit"
                )
            else:
                logger.error(f"Error getting modified time for doc {doc_id}: {e}")
            raise

    def get_doc_info(self, doc_id: str) -> Dict:
        """
        Get Google Doc metadata

        Args:
            doc_id: Google Doc ID

        Returns:
            dict: Document metadata
        """
        try:
            file = self.drive_service.files().get(
                fileId=doc_id,
                fields='id,name,modifiedTime,mimeType'
            ).execute()
            return file
        except HttpError as e:
            logger.error(f"Error getting info for doc {doc_id}: {e}")
            raise


class VaultDriveClient:
    """Client for Obsidian vault operations in Google Drive (Account B)"""

    def __init__(self, credentials, vault_folder_id: str):
        """
        Initialize Vault Drive client

        Args:
            credentials: Google OAuth2 credentials
            vault_folder_id: Google Drive folder ID containing the vault
        """
        self.credentials = credentials
        self.drive_service = build('drive', 'v3', credentials=credentials)
        self.vault_folder_id = vault_folder_id

    def _get_file_id_by_name(self, filename: str) -> Optional[str]:
        """
        Search for file by name in entire vault (recursively)

        Args:
            filename: File name to search for (e.g., "SignalPlus Log.md")

        Returns:
            str: File ID or None if not found
        """
        # Search in entire vault folder and subfolders
        # Use 'in parents' to search within vault and its descendants
        query = f"name='{filename}' and '{self.vault_folder_id}' in parents and trashed=false"

        try:
            # First try: search directly in vault root
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, parents)',
                supportsAllDrives=True
            ).execute()
            files = results.get('files', [])

            if files:
                logger.info(f"Found file '{filename}' with ID: {files[0]['id']}")
                return files[0]['id']

            # Second try: recursive search in all subfolders
            query = f"name='{filename}' and trashed=false"
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, parents)',
                supportsAllDrives=True
            ).execute()
            files = results.get('files', [])

            # Filter files that are within vault folder tree
            for file in files:
                if self._is_file_in_vault(file):
                    logger.info(f"Found file '{filename}' in subfolder with ID: {file['id']}")
                    return file['id']

            logger.warning(f"File not found in vault: {filename}")
            return None

        except HttpError as e:
            logger.error(f"Error searching for file {filename}: {e}")
            return None

    def _is_file_in_vault(self, file: Dict) -> bool:
        """
        Check if file is within vault folder tree

        Args:
            file: File metadata dict with 'parents' field

        Returns:
            bool: True if file is in vault tree
        """
        if 'parents' not in file:
            return False

        parents = file['parents']
        current_id = parents[0] if parents else None

        # Traverse up to check if we reach vault folder
        max_depth = 20  # Prevent infinite loop
        depth = 0

        while current_id and depth < max_depth:
            if current_id == self.vault_folder_id:
                return True

            try:
                parent = self.drive_service.files().get(
                    fileId=current_id,
                    fields='parents'
                ).execute()

                if 'parents' in parent and parent['parents']:
                    current_id = parent['parents'][0]
                else:
                    break

            except HttpError:
                break

            depth += 1

        return False

    def _get_file_id_by_path(self, relative_path: str) -> Optional[str]:
        """
        Get file ID by relative path within vault

        This method first tries to find by filename only (flexible),
        then falls back to exact path matching (strict)

        Args:
            relative_path: Path relative to vault root (e.g., "01. Inbox/note.md")

        Returns:
            str: File ID or None if not found
        """
        # Extract filename from path
        filename = relative_path.split('/')[-1]

        # Try flexible search by filename first
        file_id = self._get_file_id_by_name(filename)
        if file_id:
            return file_id

        # Fallback to exact path matching
        parts = relative_path.split('/')
        current_folder_id = self.vault_folder_id

        # Traverse folders
        for i, part in enumerate(parts[:-1]):
            query = f"name='{part}' and '{current_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            try:
                results = self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)'
                ).execute()
                folders = results.get('files', [])
                if not folders:
                    logger.warning(f"Folder not found: {part} in path {relative_path}")
                    return None
                current_folder_id = folders[0]['id']
            except HttpError as e:
                logger.error(f"Error finding folder {part}: {e}")
                return None

        # Find file in specific folder
        query = f"name='{filename}' and '{current_folder_id}' in parents and trashed=false"
        try:
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            files = results.get('files', [])
            if not files:
                logger.warning(f"File not found: {filename} in path {relative_path}")
                return None
            return files[0]['id']
        except HttpError as e:
            logger.error(f"Error finding file {filename}: {e}")
            return None

    def read_file(self, relative_path: str) -> Optional[str]:
        """
        Read markdown file content from vault

        Args:
            relative_path: Path relative to vault root

        Returns:
            str: File content or None if not found
        """
        file_id = self._get_file_id_by_path(relative_path)
        if not file_id:
            return None

        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

            content = fh.getvalue().decode('utf-8')
            logger.info(f"Read file from vault: {relative_path}")
            return content
        except HttpError as e:
            logger.error(f"Error reading file {relative_path}: {e}")
            raise

    def write_file(self, relative_path: str, content: str) -> bool:
        """
        Write or update markdown file in vault

        Args:
            relative_path: Path relative to vault root
            content: File content

        Returns:
            bool: True if successful
        """
        file_id = self._get_file_id_by_path(relative_path)

        try:
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode('utf-8')),
                mimetype='text/markdown',
                resumable=True
            )

            if file_id:
                # Update existing file
                self.drive_service.files().update(
                    fileId=file_id,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()
                logger.info(f"Updated file in vault: {relative_path}")
            else:
                # Create new file
                parts = relative_path.split('/')
                filename = parts[-1]

                # Ensure parent folders exist
                parent_id = self._ensure_folders_exist('/'.join(parts[:-1]))

                file_metadata = {
                    'name': filename,
                    'parents': [parent_id],
                    'mimeType': 'text/markdown'
                }

                self.drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id',
                    supportsAllDrives=True
                ).execute()
                logger.info(f"Created new file in vault: {relative_path}")

            return True
        except HttpError as e:
            logger.error(f"Error writing file {relative_path}: {e}")
            raise

    def _ensure_folders_exist(self, folder_path: str) -> str:
        """
        Ensure folder path exists, create if necessary

        Args:
            folder_path: Folder path relative to vault root

        Returns:
            str: Final folder ID
        """
        if not folder_path:
            return self.vault_folder_id

        parts = folder_path.split('/')
        current_folder_id = self.vault_folder_id

        for part in parts:
            if not part:
                continue

            # Check if folder exists
            query = f"name='{part}' and '{current_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            folders = results.get('files', [])

            if folders:
                current_folder_id = folders[0]['id']
            else:
                # Create folder
                file_metadata = {
                    'name': part,
                    'parents': [current_folder_id],
                    'mimeType': 'application/vnd.google-apps.folder'
                }
                folder = self.drive_service.files().create(
                    body=file_metadata,
                    fields='id',
                    supportsAllDrives=True
                ).execute()
                current_folder_id = folder.get('id')
                logger.info(f"Created folder: {part}")

        return current_folder_id

    def get_modified_time(self, relative_path: str) -> Optional[datetime]:
        """
        Get last modified time of file

        Args:
            relative_path: Path relative to vault root

        Returns:
            datetime: Last modified timestamp or None if not found
        """
        file_id = self._get_file_id_by_path(relative_path)
        if not file_id:
            return None

        try:
            file = self.drive_service.files().get(
                fileId=file_id,
                fields='modifiedTime'
            ).execute()
            modified_time_str = file.get('modifiedTime')
            modified_time = datetime.fromisoformat(modified_time_str.replace('Z', '+00:00'))
            logger.debug(f"File {relative_path} modified at: {modified_time}")
            return modified_time
        except HttpError as e:
            logger.error(f"Error getting modified time for {relative_path}: {e}")
            raise

    def file_exists(self, relative_path: str) -> bool:
        """
        Check if file exists in vault

        Args:
            relative_path: Path relative to vault root

        Returns:
            bool: True if file exists
        """
        return self._get_file_id_by_path(relative_path) is not None
