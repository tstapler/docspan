"""
Models for tracking file sync status.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

from docspan.backends.confluence.models.path_utils import safe_relative_path

logger = logging.getLogger(__name__)


@dataclass
class FileSyncRecord:
    """
    Represents sync status for a single file.

    Attributes:
        relative_path: Path relative to the root directory
        page_id: Confluence page ID
        title: Page title in Confluence
        last_synced: Timestamp of last successful sync
        last_modified: Last modification time of the file when synced
        status: Current sync status (synced, modified, deleted, etc.)
        history: List of previous paths if the file was renamed
    """
    relative_path: str
    page_id: str
    title: str
    last_synced: str
    last_modified: float
    status: str = "synced"  # synced, modified, renamed, deleted
    history: List[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, file_path: Path, root_dir: Path, page_id: str, title: str) -> "FileSyncRecord":
        """
        Create a sync record from a file.

        Args:
            file_path: Path to the file
            root_dir: Root directory for calculating relative path
            page_id: Confluence page ID
            title: Page title

        Returns:
            New FileSyncRecord instance
        """
        now = datetime.now().isoformat()
        last_modified = file_path.stat().st_mtime
        relative_path = safe_relative_path(file_path, root_dir)

        return cls(
            relative_path=relative_path,
            page_id=page_id,
            title=title,
            last_synced=now,
            last_modified=last_modified,
        )
    
    def mark_as_renamed(self, new_path: str) -> None:
        """
        Mark the file as renamed and update its path.

        Args:
            new_path: New relative path
        """
        # Add current path to history
        if self.relative_path not in self.history:
            self.history.append(self.relative_path)
        
        # Update path and status
        self.relative_path = new_path
        self.status = "renamed"
        
    def mark_as_deleted(self) -> None:
        """
        Mark the file as deleted.
        """
        self.status = "deleted"
        self.last_synced = datetime.now().isoformat()
        
    def mark_as_synced(self, file_path: Path, title: Optional[str] = None) -> None:
        """
        Update the record after successful sync.
        
        Args:
            file_path: Path to the file
            title: Updated title (if changed)
        """
        self.status = "synced"
        self.last_synced = datetime.now().isoformat()
        self.last_modified = file_path.stat().st_mtime
        
        if title:
            self.title = title


@dataclass
class SyncStatusTracker:
    """
    Tracks sync status for all files in a project.

    Attributes:
        status_file: Path to the status file
        files: Dictionary mapping page IDs to sync records
        by_path: Dictionary mapping relative paths to sync records
        root_dir: Root directory for calculating relative paths
    """
    status_file: Path
    root_dir: Path
    files: Dict[str, FileSyncRecord] = field(default_factory=dict)
    by_path: Dict[str, FileSyncRecord] = field(default_factory=dict)
    
    @classmethod
    def load_or_create(cls, status_file_path: Path, root_dir: Path) -> "SyncStatusTracker":
        """
        Load the status tracker from a file or create a new one.

        Args:
            status_file_path: Path to the status file
            root_dir: Root directory for calculating relative paths

        Returns:
            SyncStatusTracker instance
        """
        tracker = cls(status_file=status_file_path, root_dir=root_dir)
        
        if status_file_path.exists():
            tracker.load()
        
        return tracker
    
    def load(self) -> None:
        """
        Load sync status from the status file.
        """
        try:
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Reset existing data
            self.files = {}
            self.by_path = {}
            
            # Load records
            for page_id, record_data in data.get("files", {}).items():
                record = FileSyncRecord(
                    relative_path=record_data["relative_path"],
                    page_id=page_id,
                    title=record_data["title"],
                    last_synced=record_data["last_synced"],
                    last_modified=record_data["last_modified"],
                    status=record_data.get("status", "synced"),
                    history=record_data.get("history", []),
                )
                
                self.files[page_id] = record
                self.by_path[record.relative_path] = record
                
            logger.debug(f"Loaded sync status for {len(self.files)} files")
            
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Failed to load sync status file: {e}")
    
    def save(self) -> None:
        """
        Save sync status to the status file.
        """
        # Create data structure for serialization
        data = {
            "files": {record.page_id: asdict(record) for record in self.files.values()},
            "last_updated": datetime.now().isoformat(),
        }
        
        try:
            # Ensure directory exists
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to file
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                
            logger.debug(f"Saved sync status for {len(self.files)} files")
            
        except Exception as e:
            logger.error(f"Failed to save sync status file: {e}")
            raise
    
    def add_or_update_file(self, file_path: Path, page_id: str, title: str) -> FileSyncRecord:
        """
        Add or update a file in the tracker.

        Args:
            file_path: Path to the file
            page_id: Confluence page ID
            title: Page title

        Returns:
            The sync record
        """
        relative_path = safe_relative_path(file_path, self.root_dir)
        
        # Check if this path already exists with a different page ID
        if relative_path in self.by_path and self.by_path[relative_path].page_id != page_id:
            # This is a new file at a path that was previously used by a different file
            # Remove the old path mapping
            old_record = self.by_path[relative_path]
            del self.by_path[relative_path]
            
            if old_record.page_id in self.files:
                logger.warning(f"Path {relative_path} was previously used by page {old_record.page_id}, "
                              f"now used by {page_id}")
                
                # Mark the old record as deleted if it's still in files
                old_record.mark_as_deleted()
        
        # Check if the page ID already exists but with a different path
        if page_id in self.files:
            record = self.files[page_id]
            old_path = record.relative_path
            
            # If path changed, this is a rename
            if old_path != relative_path:
                # Update path mappings
                if old_path in self.by_path:
                    del self.by_path[old_path]
                
                # Mark as renamed
                record.mark_as_renamed(relative_path)
                logger.info(f"Detected rename: {old_path} -> {relative_path}")
            
            # Update other fields
            record.title = title
            record.last_synced = datetime.now().isoformat()
            record.last_modified = file_path.stat().st_mtime
            
            # Update the path mapping
            self.by_path[relative_path] = record
            
            return record
        else:
            # New file
            record = FileSyncRecord.from_file(file_path, self.root_dir, page_id, title)
            
            # Add to mappings
            self.files[page_id] = record
            self.by_path[relative_path] = record
            
            return record
    
    def detect_changes(self, current_files: Set[Path]) -> Dict[str, List[FileSyncRecord]]:
        """
        Detect renamed and deleted files.
        
        Args:
            current_files: Set of current file paths
            
        Returns:
            Dictionary with "renamed" and "deleted" lists of records
        """
        changes = {
            "renamed": [],
            "deleted": [],
        }
        
        current_relative_paths = {safe_relative_path(f, self.root_dir) for f in current_files}
        
        # Check for missing files
        for path, record in list(self.by_path.items()):
            if path not in current_relative_paths:
                if record.status != "deleted":
                    logger.info(f"File missing: {path} (page ID: {record.page_id})")
                    
                    # Check if the file might have been renamed
                    renamed = False
                    for current_path in current_relative_paths:
                        if (current_path not in self.by_path and 
                            Path(current_path).name == Path(path).name):
                            # Potential rename - same filename but different directory
                            record.mark_as_renamed(current_path)
                            self.by_path[current_path] = record
                            del self.by_path[path]
                            changes["renamed"].append(record)
                            renamed = True
                            logger.info(f"Detected potential rename: {path} -> {current_path}")
                            break
                    
                    if not renamed:
                        # Mark as deleted
                        record.mark_as_deleted()
                        changes["deleted"].append(record)
        
        return changes
    
    def get_record_by_path(self, file_path: Union[str, Path]) -> Optional[FileSyncRecord]:
        """
        Get sync record by path.
        
        Args:
            file_path: File path (absolute or relative)
            
        Returns:
            FileSyncRecord or None if not found
        """
        # Convert to relative path if needed
        if isinstance(file_path, Path):
            if file_path.is_absolute():
                rel_path = safe_relative_path(file_path, self.root_dir)
            else:
                rel_path = str(file_path)
        else:
            rel_path = file_path
        
        return self.by_path.get(rel_path)
    
    def get_record_by_id(self, page_id: str) -> Optional[FileSyncRecord]:
        """
        Get sync record by Confluence page ID.
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            FileSyncRecord or None if not found
        """
        return self.files.get(page_id)
    
    def remove_file(self, file_path: Union[str, Path]) -> Optional[FileSyncRecord]:
        """
        Remove a file from tracking.
        
        Args:
            file_path: File path
            
        Returns:
            Removed record or None if not found
        """
        record = self.get_record_by_path(file_path)
        
        if record:
            # Remove from mappings
            if record.relative_path in self.by_path:
                del self.by_path[record.relative_path]
            
            if record.page_id in self.files:
                del self.files[record.page_id]
            
            return record
        
        return None
    
    def get_all_synced_files(self) -> List[FileSyncRecord]:
        """
        Get all successfully synced files.
        
        Returns:
            List of sync records for synced files
        """
        return [r for r in self.files.values() if r.status == "synced"]
    
    def get_all_deleted_files(self) -> List[FileSyncRecord]:
        """
        Get all deleted files.
        
        Returns:
            List of sync records for deleted files
        """
        return [r for r in self.files.values() if r.status == "deleted"]
        
    def get_all_tracked_files(self) -> Dict[str, FileSyncRecord]:
        """
        Get a dictionary of all tracked files by relative path.
        
        Returns:
            Dictionary mapping relative paths to sync records
        """
        return self.by_path