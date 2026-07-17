"""
Google Drive Authentication Module

Handles authentication for two separate Google accounts:
- Account A: Google Docs source
- Account B: Obsidian vault storage
"""

import json
import logging
import os
import pathlib

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials

logger = logging.getLogger(__name__)

# Default cache location for the per-user OAuth token.
DEFAULT_TOKEN_PATH = ".markgate/google_token.json"

# Google Drive API scopes
PULL_SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

PUSH_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Default to read-write scopes so push works out of the box.
# Note: existing tokens created with documents.readonly are insufficient for push.
DEFAULT_SCOPES = PUSH_SCOPES

# Legacy alias — kept so any code that references SCOPES still works.
SCOPES = DEFAULT_SCOPES


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


class OAuthAuthenticator:
    """
    Per-user OAuth authentication (browser consent flow + cached refreshable token).

    Mirrors how `gws` and the google-docs plugin authenticate: the user signs in once,
    the token is cached on disk, and it refreshes silently thereafter. Acts as the user,
    so it can reach their own Docs plus anything shared with them.
    """

    def __init__(self, client_secret_path=None, token_path=None, scopes=None):
        """
        Args:
            client_secret_path: Path to an OAuth client secret JSON (Desktop app).
                Only needed for the first (interactive) authorization.
            token_path: Where the cached user token is stored/refreshed.
            scopes: OAuth scopes (defaults to DEFAULT_SCOPES = read/write).
        """
        self.client_secret_path = client_secret_path
        self.token_path = token_path or DEFAULT_TOKEN_PATH
        self.scopes = scopes or DEFAULT_SCOPES
        self.credentials = None

    def _token_file(self) -> pathlib.Path:
        return pathlib.Path(os.path.expanduser(self.token_path))

    def _load_cached(self):
        path = self._token_file()
        if path.exists():
            return UserCredentials.from_authorized_user_file(str(path), self.scopes)
        return None

    def _save(self, creds) -> None:
        path = self._token_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(creds.to_json())
        logger.info(f"Cached OAuth token at {path}")

    def has_valid_credentials(self) -> bool:
        """True if a usable (valid or refreshable) token is already cached — no browser needed."""
        try:
            creds = self.credentials or self._load_cached()
        except Exception:
            return False
        return bool(creds and (creds.valid or (creds.expired and creds.refresh_token)))

    def get_credentials(self, allow_interactive: bool = True):
        """
        Return valid OAuth credentials, refreshing or launching the consent flow as needed.

        Args:
            allow_interactive: if False, never open a browser — raise if no usable token exists.
        """
        creds = self.credentials or self._load_cached()

        if creds and creds.valid:
            self.credentials = creds
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save(creds)
            self.credentials = creds
            return creds

        if not allow_interactive:
            raise ValueError(
                f"No valid cached OAuth token at {self.token_path} and interactive auth is disabled. "
                "Run: docspan auth setup google_docs --oauth --client-secret <path>"
            )

        if not self.client_secret_path:
            raise ValueError(
                "OAuth client secret not configured. Set oauth_client_secret_path in markgate.yaml "
                "(backends.google_docs) or pass --client-secret to `docspan auth setup google_docs --oauth`."
            )

        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            os.path.expanduser(self.client_secret_path), self.scopes
        )
        creds = flow.run_local_server(port=0)
        self._save(creds)
        self.credentials = creds
        return creds


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

    def is_authenticated(self) -> bool:
        """Return True if the primary Google Docs account (account A) is configured."""
        return self.account_a_auth is not None
