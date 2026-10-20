# Architecture Research: Sync State Management, Three-Way Merge Design, and Backend Versioning

## Sync State Management with JSON State Files

### State File Design

The `.markgate-state.json` file should live at the project root (alongside the config file). The schema from requirements.md is well-suited; key implementation notes:

**Hashing**: Use `hashlib.sha256(content.encode('utf-8')).hexdigest()` for both `base_hash` and `local_hash`. Hash the raw UTF-8 file bytes, not normalized content, to avoid false "changed" signals from encoding differences.

**Atomic writes**: Write state via a temp file + `os.rename()` to avoid corruption on crash:
```python
import tempfile, os, json
def save(path: str, state: dict) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.rename(tmp, path)
```

**Locking**: For single-process CLI use, a simple `.lock` file via `fcntl.flock` is sufficient. A full advisory lock is overkill for this tool.

### SyncState Dataclass

```python
@dataclass
class MappingState:
    doc_id: str
    backend: str                  # 'google_docs' | 'confluence'
    last_synced_at: str           # ISO-8601
    base_hash: str                # sha256 of content at last sync
    remote_version: str           # revisionId or str(version_number)
    local_hash: str               # sha256 of local file at last sync

@dataclass
class SyncState:
    mappings: dict[str, MappingState]  # keyed by local_path (absolute or project-relative)

    @classmethod
    def load(cls, path: str) -> 'SyncState': ...
    def save(self, path: str) -> None: ...
    def get(self, local_path: str) -> MappingState | None: ...
    def update(self, local_path: str, mapping: MappingState) -> None: ...
```

### Conflict Detection Algorithm

```
on pull(local_path):
  state = SyncState.load('.markgate-state.json')
  entry = state.get(local_path)

  if entry is None:
    # First sync — fast-forward pull, create entry
    ...

  remote_content = backend.pull(doc_id)
  remote_version = backend.get_remote_version(doc_id)
  local_content = read(local_path)

  remote_changed = (remote_version != entry.remote_version)
  local_changed  = (sha256(local_content) != entry.local_hash)

  if not remote_changed and not local_changed:
    return  # nothing to do

  if remote_changed and not local_changed:
    write(local_path, remote_content)     # fast-forward
    update_state(...)

  elif local_changed and not remote_changed:
    warn("Local changes not pushed; skipping pull. Use --force-remote to overwrite.")

  else:  # both changed → three-way merge
    base_content = entry.base_hash  # NOTE: store base content, not just hash
    result = three_way_merge(base_content, remote_content, local_content)
    write(local_path, result.merged)
    if result.has_conflicts:
      warn("Merge conflict in {local_path}. Edit file and run `markgate conflicts resolve`.")
    update_state(...)
```

**Important**: The state must store the actual base content (or a path to it) so three-way merge is possible, not just a hash. Options:
- Store base content inline as a compressed base64 field (simple but bloats state file for large docs).
- Store a sidecar `.markgate-base/<local_path>.base` file referenced by hash (cleaner for large files).
- Recommended: use a `.markgate-base/` directory with files named by `{sha256}.base`.

## Three-Way Merge Algorithm for Line-Based Text

### Unified Diff vs Content-Addressable

**Standard unified diff** (`difflib.unified_diff`): Produces human-readable patch format but is not directly usable for programmatic merge. It shows context and hunks but doesn't yield the merge result.

**`merge3` line-based three-way merge** (recommended): Operates on sequences of lines. Works identically to `git merge` internals — finds common chunks between BASE→OURS and BASE→THEIRS and merges them, emitting conflict markers for overlapping changes.

**Content-addressable approach**: Not practical for text files. Works well for binary/JSON trees (e.g., Automerge CRDT), but for markdown line text, line-based merge3 is the right tool.

### MergeResult Type

```python
@dataclass
class MergeResult:
    merged: str          # merged content (may contain conflict markers)
    has_conflicts: bool
    conflict_count: int  # number of conflict blocks
```

### Implementation in core/merge.py

```python
from merge3 import Merge3

def three_way_merge(base: str, theirs: str, ours: str) -> MergeResult:
    base_lines   = base.splitlines(keepends=True)
    theirs_lines = theirs.splitlines(keepends=True)
    ours_lines   = ours.splitlines(keepends=True)

    m3 = Merge3(base_lines, ours_lines, theirs_lines)
    merged_lines = list(m3.merge_lines(
        name_a='ours',
        name_b='theirs',
        start_marker='<<<<<<< ours',
        mid_marker='=======',
        end_marker='>>>>>>> theirs',
    ))
    merged = ''.join(merged_lines)
    conflicts = sum(1 for line in merged_lines if line.startswith('<<<<<<<'))
    return MergeResult(merged=merged, has_conflicts=conflicts > 0, conflict_count=conflicts)
```

## Backend.get_remote_version() Abstract Method Design

### Asymmetry Between Backends

Google Docs uses `revisionId` (an opaque string, not monotonic) from `documents.get`. Confluence uses `version.number` (a monotonically increasing integer) from the page metadata response.

Both can be stored as `str` in the state file for uniformity.

### Abstract Interface

```python
# in src/markgate/backends/base.py
from abc import ABC, abstractmethod

class Backend(ABC):
    @abstractmethod
    def push(self, doc_id: str, content: str) -> None: ...

    @abstractmethod
    def pull(self, doc_id: str) -> str: ...

    @abstractmethod
    def get_remote_version(self, doc_id: str) -> str:
        """
        Return an opaque version token for the current remote document.
        Google Docs: returns doc['revisionId'] from documents.get response.
        Confluence: returns str(page['version']['number']) from pages.get response.
        The token is compared with == to detect remote changes between syncs.
        """
```

### Google Docs Implementation

```python
def get_remote_version(self, doc_id: str) -> str:
    doc = self._client.docs_service.documents().get(documentId=doc_id).execute()
    return doc['revisionId']
```

The `revisionId` changes on every edit. It is returned by both `documents.get` and in the `batchUpdate` response (`writeControl.requiredRevisionId` / `replies[].writeRevisionId`). **Do not use Drive's `modifiedTime`** — it has 1-second precision and is unreliable for rapid edits.

### Confluence Implementation

```python
def get_remote_version(self, doc_id: str) -> str:
    page = self._client.get_page(doc_id)  # existing method
    return str(page['version']['number'])
```

### Version Tracking at Push Time

After a successful push, record the new `revisionId` returned in the batchUpdate response (field `replies[-1].writeRevisionId` or re-fetch via `documents.get`). For Confluence, re-fetch the page version after `update_page`.

## State File Location and Discovery

The state file should be located by walking up from the local file's directory until `.markgate.yaml` or `.markgate-state.json` is found — matching the pattern used by git for `.git/`. A simpler alternative: always use the directory containing the config file (`~/.markgate.yaml` or `./markgate.yaml`), which is already the working directory convention in the existing codebase.
