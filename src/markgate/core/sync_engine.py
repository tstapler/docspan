"""
Sync Engine Module

Core synchronization logic for Google Docs -> Obsidian Vault (one-way sync)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional
from .gdrive_client import GoogleDocsClient, VaultDriveClient
from .converter import DocumentConverter
from .conflict_handler import ConflictHandler

logger = logging.getLogger(__name__)

# Store state file locally instead of in Google Drive to avoid service account quota issues
# Use /data volume on Railway (persistent), fall back to current directory for local dev
STATE_DIR = os.getenv('STATE_DIR', '/data' if os.path.exists('/data') else os.getcwd())
STATE_FILE_PATH = os.path.join(STATE_DIR, '.sync_state.json')


class SyncEngine:
    """Main synchronization engine"""

    def __init__(
        self,
        docs_client: GoogleDocsClient,
        vault_client: VaultDriveClient,
        mappings: List[Dict]
    ):
        """
        Initialize sync engine

        Args:
            docs_client: Google Docs client (Account A)
            vault_client: Vault Drive client (Account B)
            mappings: List of doc_id to vault_path mappings
        """
        self.docs_client = docs_client
        self.vault_client = vault_client
        self.mappings = mappings
        self.converter = DocumentConverter()
        self.conflict_handler = ConflictHandler()
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """
        Load sync state from local file

        Returns:
            dict: Sync state
        """
        try:
            if os.path.exists(STATE_FILE_PATH):
                with open(STATE_FILE_PATH, 'r') as f:
                    state = json.load(f)
                logger.info(f"Loaded sync state from {STATE_FILE_PATH}")
                return state
            else:
                logger.info("No existing sync state found, creating new")
                return self._create_initial_state()
        except Exception as e:
            logger.warning(f"Error loading state, creating new: {e}")
            return self._create_initial_state()

    def _create_initial_state(self) -> Dict:
        """
        Create initial sync state

        Returns:
            dict: Initial state structure
        """
        return {
            'last_run': None,
            'files': {}
        }

    def _save_state(self):
        """Save sync state to local file"""
        try:
            # Ensure the state directory exists
            os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)

            with open(STATE_FILE_PATH, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)
            logger.info(f"Saved sync state to {STATE_FILE_PATH}")
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def sync_all(self) -> Dict:
        """
        Synchronize all mapped documents

        Returns:
            dict: Sync results summary
        """
        logger.info(f"Starting sync for {len(self.mappings)} mappings")

        results = {
            'success': 0,
            'conflicts': 0,
            'errors': 0,
            'skipped': 0,
            'details': []
        }

        for mapping in self.mappings:
            doc_id = mapping['doc_id']
            vault_path = mapping['vault_path']

            try:
                result = self._sync_single(doc_id, vault_path)

                if result['status'] == 'success':
                    results['success'] += 1
                elif result['status'] == 'conflict':
                    results['conflicts'] += 1
                elif result['status'] == 'skipped':
                    results['skipped'] += 1
                else:
                    results['errors'] += 1

                results['details'].append({
                    'doc_id': doc_id,
                    'vault_path': vault_path,
                    'result': result
                })

            except Exception as e:
                logger.error(f"Error syncing {doc_id} <-> {vault_path}: {e}")
                results['errors'] += 1
                results['details'].append({
                    'doc_id': doc_id,
                    'vault_path': vault_path,
                    'result': {'status': 'error', 'error': str(e)}
                })

        # Update last run time
        self.state['last_run'] = datetime.now(timezone.utc).isoformat()
        self._save_state()

        logger.info(f"Sync completed: {results['success']} success, "
                   f"{results['conflicts']} conflicts, {results['errors']} errors, "
                   f"{results['skipped']} skipped")

        return results

    def _sync_single(self, doc_id: str, vault_path: str) -> Dict:
        """
        Synchronize a single document pair

        Args:
            doc_id: Google Doc ID
            vault_path: Path in vault

        Returns:
            dict: Sync result
        """
        logger.info(f"Syncing: {doc_id} <-> {vault_path}")

        # Get modification times
        try:
            doc_modified = self.docs_client.get_modified_time(doc_id)
        except Exception as e:
            logger.error(f"Error getting doc modified time: {e}")
            return {'status': 'error', 'error': f'Cannot access doc: {e}'}

        vault_modified = self.vault_client.get_modified_time(vault_path)

        # Check if markdown file exists
        markdown_exists = vault_modified is not None

        # Get last sync info
        file_state = self.state['files'].get(doc_id, {})
        last_synced = file_state.get('last_synced_at')
        if last_synced:
            last_synced = datetime.fromisoformat(last_synced)

        # Determine sync direction
        sync_decision = self._determine_sync_direction(
            doc_modified, vault_modified, last_synced, markdown_exists
        )

        logger.info(f"Sync decision: {sync_decision['action']} - {sync_decision['reason']}")

        # Skip if no changes
        if sync_decision['action'] == 'skip':
            return {'status': 'skipped', 'reason': sync_decision['reason']}

        # Perform sync (ONE-WAY only: doc -> vault)
        try:
            if sync_decision['action'] == 'doc_to_vault':
                self._sync_doc_to_vault(doc_id, vault_path)
                direction = 'doc_to_vault'
            else:
                return {'status': 'error', 'error': f"Unknown action: {sync_decision['action']}"}

            # Update state
            self.state['files'][doc_id] = {
                'last_synced_at': datetime.now(timezone.utc).isoformat(),
                'doc_modified_at': doc_modified.isoformat(),
                'vault_modified_at': vault_modified.isoformat() if vault_modified else None,
                'direction': direction,
                'vault_path': vault_path
            }

            return {
                'status': 'success',
                'direction': direction,
                'doc_modified': doc_modified,
                'vault_modified': vault_modified
            }

        except Exception as e:
            logger.error(f"Error during sync: {e}")
            return {'status': 'error', 'error': str(e)}

    def _determine_sync_direction(
        self,
        doc_modified: datetime,
        vault_modified: Optional[datetime],
        last_synced: Optional[datetime],
        markdown_exists: bool
    ) -> Dict:
        """
        Determine sync direction (ONE-WAY: Google Docs -> Vault only)

        Args:
            doc_modified: Google Doc modification time
            vault_modified: Vault file modification time (None if doesn't exist)
            last_synced: Last sync time (None if never synced)
            markdown_exists: Whether markdown file exists

        Returns:
            dict: Decision with 'action' and 'reason'
        """
        # First sync: always doc to vault
        if not last_synced:
            return {
                'action': 'doc_to_vault',
                'reason': 'First sync, initializing from doc'
            }

        # Markdown doesn't exist - sync from doc
        if not markdown_exists:
            return {
                'action': 'doc_to_vault',
                'reason': 'Markdown file does not exist'
            }

        # Check if doc was modified since last sync
        doc_changed = doc_modified > last_synced
        vault_changed = vault_modified > last_synced if vault_modified else False

        # Warn if vault was modified (but always sync from doc)
        if vault_changed:
            logger.warning(
                f"Vault file was modified since last sync, but ONE-WAY sync is enabled. "
                f"Changes in vault will be overwritten by Google Doc content."
            )

        # Only sync if doc changed
        if doc_changed:
            return {
                'action': 'doc_to_vault',
                'reason': 'Doc modified since last sync'
            }

        # Neither changed
        return {
            'action': 'skip',
            'reason': 'No changes since last sync'
        }

    def _sync_doc_to_vault(self, doc_id: str, vault_path: str):
        """
        Sync Google Doc to Markdown in vault

        Args:
            doc_id: Google Doc ID
            vault_path: Vault file path
        """
        logger.info(f"Syncing doc -> vault: {doc_id} -> {vault_path}")

        # Get doc content as HTML
        html_content = self.docs_client.get_doc_content(doc_id)

        # Convert to markdown
        markdown = self.converter.html_to_markdown(html_content)

        # Write to vault
        self.vault_client.write_file(vault_path, markdown)

        logger.info(f"Successfully synced doc to vault: {vault_path}")

    def get_sync_status(self) -> Dict:
        """
        Get current sync status

        Returns:
            dict: Status information
        """
        return {
            'last_run': self.state.get('last_run'),
            'total_files': len(self.state.get('files', {})),
            'files': self.state.get('files', {}),
            'pending_conflicts': self.conflict_handler.get_conflicts_count()
        }
