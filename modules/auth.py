"""
Google Drive Authentication Module

Handles authentication for two separate Google accounts:
- Account A: Google Docs source
- Account B: Obsidian vault storage
"""

import os
import json
import logging
from google.oauth2 import service_account
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

# Google Drive API scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets.readonly',
]


class GoogleAuthenticator:
    """Handles Google API authentication"""

    def __init__(self, credentials_json=None, credentials_path=None):
        """
        Initialize authenticator with service account credentials

        Args:
            credentials_json: JSON string of service account credentials
            credentials_path: Path to service account JSON file
        """
        self.credentials = None

        if credentials_json:
            # Load from JSON string (for Railway env vars)
            creds_dict = json.loads(credentials_json)
            self.credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=SCOPES
            )
            logger.info("Loaded credentials from JSON string")
        elif credentials_path:
            # Load from file (for local development)
            self.credentials = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=SCOPES
            )
            logger.info(f"Loaded credentials from file: {credentials_path}")
        else:
            raise ValueError("Either credentials_json or credentials_path must be provided")

    def get_credentials(self):
        """
        Get valid credentials

        Returns:
            google.auth.credentials.Credentials
        """
        if not self.credentials:
            raise ValueError("Credentials not initialized")

        # Refresh if needed
        if not self.credentials.valid:
            logger.info("Refreshing credentials")
            self.credentials.refresh(Request())

        return self.credentials


class DualAccountAuth:
    """Manages authentication for both Google accounts"""

    def __init__(self):
        """Initialize dual account authentication from environment variables"""
        self.account_a_auth = None
        self.account_b_auth = None
        self._load_from_env()

    def _load_from_env(self):
        """Load credentials from environment variables"""
        # Account A (Google Docs)
        account_a_json = os.getenv('ACCOUNT_A_CREDENTIALS')
        account_a_path = os.getenv('ACCOUNT_A_CREDENTIALS_PATH')

        if account_a_json:
            self.account_a_auth = GoogleAuthenticator(credentials_json=account_a_json)
        elif account_a_path:
            self.account_a_auth = GoogleAuthenticator(credentials_path=account_a_path)
        else:
            logger.warning("Account A credentials not found in environment")

        # Account B (Obsidian Vault)
        account_b_json = os.getenv('ACCOUNT_B_CREDENTIALS')
        account_b_path = os.getenv('ACCOUNT_B_CREDENTIALS_PATH')

        if account_b_json:
            self.account_b_auth = GoogleAuthenticator(credentials_json=account_b_json)
        elif account_b_path:
            self.account_b_auth = GoogleAuthenticator(credentials_path=account_b_path)
        else:
            logger.warning("Account B credentials not found in environment")

    def get_account_a_credentials(self):
        """
        Get credentials for Account A (Google Docs)

        Returns:
            google.auth.credentials.Credentials
        """
        if not self.account_a_auth:
            raise ValueError("Account A not authenticated. Check ACCOUNT_A_CREDENTIALS env var")
        return self.account_a_auth.get_credentials()

    def get_account_b_credentials(self):
        """
        Get credentials for Account B (Obsidian Vault)

        Returns:
            google.auth.credentials.Credentials
        """
        if not self.account_b_auth:
            raise ValueError("Account B not authenticated. Check ACCOUNT_B_CREDENTIALS env var")
        return self.account_b_auth.get_credentials()

    def is_authenticated(self):
        """
        Check if both accounts are authenticated

        Returns:
            bool: True if both accounts are authenticated
        """
        return self.account_a_auth is not None and self.account_b_auth is not None
