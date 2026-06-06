"""Sync state — tracks last-synced hashes and remote versions per local file."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class MappingState:
    doc_id: str
    backend: str
    last_synced_at: str
    base_hash: str
    remote_version: str
    local_hash: str


@dataclass
class SyncState:
    mappings: dict[str, MappingState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "SyncState":
        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            data = json.load(f)
        mappings = {k: MappingState(**v) for k, v in data.get("mappings", {}).items()}
        return cls(mappings=mappings)

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"mappings": {k: asdict(v) for k, v in self.mappings.items()}}, f, indent=2)
        os.rename(tmp, path)

    def get(self, local_path: str) -> Optional[MappingState]:
        return self.mappings.get(local_path)

    def update(self, local_path: str, mapping: MappingState) -> None:
        self.mappings[local_path] = mapping


def sha256_of_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def sha256_of_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
