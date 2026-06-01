"""
Conflict Handler Module

Handles synchronization conflicts when both files are modified
"""

import logging
import json
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

CONFLICTS_LOG_FILE = 'conflicts.log'


class ConflictHandler:
    """Handles sync conflicts"""

    def __init__(self):
        """Initialize conflict handler"""
        self.conflicts = []

    def record_conflict(self, conflict_info: Dict):
        """
        Record a conflict

        Args:
            conflict_info: Dictionary containing conflict details
        """
        conflict = {
            'timestamp': datetime.now().isoformat(),
            'doc_id': conflict_info.get('doc_id'),
            'vault_path': conflict_info.get('vault_path'),
            'doc_modified': conflict_info.get('doc_modified').isoformat() if conflict_info.get('doc_modified') else None,
            'vault_modified': conflict_info.get('vault_modified').isoformat() if conflict_info.get('vault_modified') else None,
            'last_synced': conflict_info.get('last_synced').isoformat() if conflict_info.get('last_synced') else None,
        }

        self.conflicts.append(conflict)

        # Log conflict
        logger.warning(f"CONFLICT: {conflict['vault_path']} (Doc ID: {conflict['doc_id']})")
        logger.warning(f"  Doc modified: {conflict['doc_modified']}")
        logger.warning(f"  Vault modified: {conflict['vault_modified']}")
        logger.warning(f"  Last synced: {conflict['last_synced']}")

        # Write to log file
        self._write_to_log(conflict)

    def _write_to_log(self, conflict: Dict):
        """
        Write conflict to log file

        Args:
            conflict: Conflict information
        """
        try:
            with open(CONFLICTS_LOG_FILE, 'a') as f:
                f.write(json.dumps(conflict) + '\n')
            logger.debug(f"Wrote conflict to {CONFLICTS_LOG_FILE}")
        except Exception as e:
            logger.error(f"Error writing conflict to log: {e}")

    def get_conflicts(self) -> List[Dict]:
        """
        Get all recorded conflicts in this session

        Returns:
            list: List of conflicts
        """
        return self.conflicts

    def get_conflicts_count(self) -> int:
        """
        Get number of conflicts

        Returns:
            int: Number of conflicts
        """
        return len(self.conflicts)

    def load_conflicts_from_log(self) -> List[Dict]:
        """
        Load conflicts from log file

        Returns:
            list: List of conflicts from log file
        """
        try:
            with open(CONFLICTS_LOG_FILE, 'r') as f:
                conflicts = [json.loads(line) for line in f]
            logger.info(f"Loaded {len(conflicts)} conflicts from log")
            return conflicts
        except FileNotFoundError:
            logger.info("No conflicts log file found")
            return []
        except Exception as e:
            logger.error(f"Error loading conflicts from log: {e}")
            return []

    def clear_conflicts_log(self):
        """Clear conflicts log file"""
        try:
            with open(CONFLICTS_LOG_FILE, 'w') as f:
                f.write('')
            logger.info("Cleared conflicts log")
        except Exception as e:
            logger.error(f"Error clearing conflicts log: {e}")

    @staticmethod
    def print_conflict_report(conflicts: List[Dict]):
        """
        Print a formatted conflict report

        Args:
            conflicts: List of conflicts
        """
        if not conflicts:
            print("\n✓ No conflicts found")
            return

        print(f"\n⚠️  {len(conflicts)} Conflict(s) Detected\n")
        print("=" * 80)

        for i, conflict in enumerate(conflicts, 1):
            print(f"\nConflict #{i}")
            print(f"  File: {conflict['vault_path']}")
            print(f"  Doc ID: {conflict['doc_id']}")
            print(f"  Detected: {conflict['timestamp']}")
            print(f"  Doc modified: {conflict['doc_modified']}")
            print(f"  Vault modified: {conflict['vault_modified']}")
            print(f"  Last synced: {conflict['last_synced']}")
            print("-" * 80)

        print("\nTo resolve conflicts:")
        print("1. Manually review the conflicting files")
        print("2. Choose which version to keep or merge them manually")
        print("3. The next sync will update based on modification times")
        print("4. Or use --force-doc or --force-vault flags (if implemented)")

    @staticmethod
    def suggest_resolution(conflict: Dict) -> str:
        """
        Suggest a resolution strategy for a conflict

        Args:
            conflict: Conflict information

        Returns:
            str: Suggestion message
        """
        doc_mod = datetime.fromisoformat(conflict['doc_modified'])
        vault_mod = datetime.fromisoformat(conflict['vault_modified'])

        time_diff = abs((doc_mod - vault_mod).total_seconds())

        if time_diff < 60:  # Less than 1 minute apart
            return "⚠️  Modified within 1 minute - likely simultaneous edits. Manual review recommended."
        elif doc_mod > vault_mod:
            return f"📄 Doc is newer (by {time_diff/60:.1f} minutes). Consider keeping doc version."
        else:
            return f"📝 Vault is newer (by {time_diff/60:.1f} minutes). Consider keeping vault version."
