# Architecture Research — docspan v0.1.0 Release

## Project Structure

```
markgate/                          # git repo root
├── pyproject.toml                 # build config, deps, scripts
├── README.md                      # (to be created)
├── CHANGELOG.md                   # (to be created)
├── CONTRIBUTING.md                # (to be created)
├── mkdocs.yml                     # (to be created)
├── docs/                          # existing: design notes
│   └── google-docs-push.md        # internal design doc (keep as-is)
├── src/
│   └── docspan/                   # main package
│       ├── __init__.py            # __version__ = "0.1.0"
│       ├── __main__.py            # minimal (1 line)
│       ├── config.py              # MarkgateConfig, Mapping, BackendsConfig
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py            # typer app: push, pull, status, auth, conflicts
│       ├── backends/
│       │   ├── __init__.py        # BACKENDS registry dict
│       │   ├── base.py            # Backend ABC, PushResult, PullResult, RemoteDoc
│       │   ├── confluence/
│       │   │   ├── __init__.py
│       │   │   ├── backend.py     # ConfluenceBackend
│       │   │   ├── client.py
│       │   │   ├── adf/           # Atlassian Document Format conversion
│       │   │   │   ├── __init__.py
│       │   │   │   ├── comparator.py
│       │   │   │   ├── converter.py
│       │   │   │   ├── converters.py
│       │   │   │   ├── interfaces.py
│       │   │   │   ├── nodes.py
│       │   │   │   ├── parser.py
│       │   │   │   ├── validators.py
│       │   │   │   └── visitors.py
│       │   │   ├── config/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── loader.py
│       │   │   │   ├── models.py
│       │   │   │   └── validation.py
│       │   │   ├── markdown/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── ast.py
│       │   │   │   ├── inline_parser.py
│       │   │   │   ├── parser.py
│       │   │   │   └── extensions/
│       │   │   │       ├── __init__.py
│       │   │   │       ├── frontmatter.py
│       │   │   │       ├── mermaid.py
│       │   │   │       └── wikilinks.py
│       │   │   ├── models/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── markdown_file.py
│       │   │   │   ├── page.py
│       │   │   │   ├── path_utils.py
│       │   │   │   ├── results.py
│       │   │   │   └── sync_status.py
│       │   │   └── services/
│       │   │       └── confluence/
│       │   │           ├── __init__.py
│       │   │           ├── attachment_client.py
│       │   │           ├── base_client.py
│       │   │           ├── client.py
│       │   │           ├── comment_client.py
│       │   │           ├── crawler.py
│       │   │           ├── label_client.py
│       │   │           ├── page_client.py
│       │   │           ├── space_client.py
│       │   │           └── url_parser.py
│       │   └── google_docs/
│       │       ├── __init__.py
│       │       ├── backend.py     # GoogleDocsBackend
│       │       ├── auth.py        # DualAccountAuth, GoogleAuthenticator
│       │       ├── client.py      # GoogleDocsClient
│       │       ├── converter.py   # DocumentConverter (HTML → markdown)
│       │       ├── docs_request_builder.py    # structural diff engine
│       │       ├── docs_structure_parser.py   # Docs JSON → AST
│       │       └── markdown_to_paragraph_parser.py  # Markdown → AST
│       └── core/
│           ├── __init__.py        # re-exports all public symbols
│           ├── merge.py           # MergeResult, three_way_merge
│           ├── orchestrator.py    # orchestrate_push, orchestrate_pull
│           ├── paths.py           # path constants (STATE_FILENAME, etc.)
│           └── state.py           # SyncState, MappingState
└── tests/                         # (not listed in find output but referenced in pyproject.toml)
```

## Backend ABC Interface (src/docspan/backends/base.py)

### Data Types

```python
class SyncDirection(str, Enum):
    PUSH = "push"
    PULL = "pull"
    BOTH = "both"

@dataclass
class RemoteDoc:
    doc_id: str
    title: str
    content_markdown: str
    last_modified: Optional[str] = None
    url: Optional[str] = None

@dataclass
class PushResult:
    status: Literal["ok", "conflict", "error", "skipped"]
    doc_id: str
    message: Optional[str] = None
    url: Optional[str] = None

@dataclass
class PullResult:
    status: Literal["ok", "conflict", "error", "skipped"]
    doc_id: str
    local_path: str
    message: Optional[str] = None
```

### Backend ABC

```python
class Backend(ABC):
    name: str  # class attribute, must be overridden in every concrete subclass

    @abstractmethod
    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult: ...

    @abstractmethod
    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult: ...

    @abstractmethod
    def auth_setup(self) -> None: ...

    @abstractmethod
    def get_remote_version(self, doc_id: str) -> str: ...

    @abstractmethod
    def validate_config(self) -> None: ...
```

**Enforcement**: `__init_subclass__` raises `TypeError` if a concrete subclass doesn't define `name`.

### Backend Registry (src/docspan/backends/__init__.py)

```python
BACKENDS: dict[str, type[Backend]] = {
    "google_docs": GoogleDocsBackend,
    "confluence": ConfluenceBackend,
}
```

## Core Module Exported Symbols (src/docspan/core/__init__.py)

```python
# State
SyncState, MappingState, sha256_of_file, sha256_of_content

# Merge
MergeResult, three_way_merge

# Orchestrator
PushOutcome, PullOutcome, get_state_path, get_state_dir,
get_base_content, save_base_content, orchestrate_push, orchestrate_pull, record_state

# Paths
STATE_FILENAME, BASE_STORE_DIR, BASE_FILE_SUFFIX, ORIG_SUFFIX, COMMENTS_SUFFIX, GOOGLE_TOKEN_PATH
```

## Project Structure Diagram for CONTRIBUTING.md

```
src/docspan/
├── cli/main.py          ← Typer commands (push, pull, status, auth, conflicts)
├── config.py            ← MarkgateConfig Pydantic model + YAML loader
├── backends/
│   ├── base.py          ← Backend ABC (push, pull, auth_setup, validate_config, get_remote_version)
│   ├── __init__.py      ← BACKENDS registry {"google_docs": ..., "confluence": ...}
│   ├── google_docs/     ← Google Docs backend implementation
│   └── confluence/      ← Confluence backend implementation
└── core/
    ├── state.py         ← SyncState / MappingState (JSON persistence)
    ├── orchestrator.py  ← Push/pull orchestration (conflict detection, three-way merge)
    ├── merge.py         ← Three-way text merge
    └── paths.py         ← Path constants (.markgate-state.json, etc.)
```

## How to Add a New Backend

### Step 1: Create the backend package

```
src/docspan/backends/myplatform/
├── __init__.py
└── backend.py
```

### Step 2: Implement the Backend ABC in backend.py

```python
from docspan.backends.base import Backend, PushResult, PullResult
from docspan.config import MarkgateConfig

class MyPlatformBackend(Backend):
    name = "myplatform"  # must match the key you'll use in markgate.yaml

    def __init__(self, config) -> None:
        self.config = config

    @classmethod
    def from_config(cls, markgate_config: MarkgateConfig) -> "MyPlatformBackend":
        # extract your config section from MarkgateConfig
        return cls(markgate_config.backends.myplatform)

    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        # read local_path, convert to platform format, upload to doc_id
        # return PushResult(status="ok", doc_id=doc_id, url=url)
        # on error: return PushResult(status="error", doc_id=doc_id, message=str(exc))

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        # fetch from platform, convert to markdown, write to local_path
        # return PullResult(status="ok", doc_id=doc_id, local_path=local_path)

    def auth_setup(self) -> None:
        # print setup instructions or run interactive prompts

    def get_remote_version(self, doc_id: str) -> str:
        # return an opaque version token (e.g. revision ID, version number)
        # used to detect whether remote has changed since last sync

    def validate_config(self) -> None:
        # raise ValueError("Missing X. Run: docspan auth setup myplatform") if misconfigured
```

### Step 3: Add config model to config.py

```python
class MyPlatformConfig(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None

class BackendsConfig(BaseModel):
    google_docs: Optional[GoogleDocsConfig] = None
    confluence: Optional[ConfluenceConfig] = None
    myplatform: Optional[MyPlatformConfig] = None  # add this
```

### Step 4: Register in the BACKENDS dict

In `src/docspan/backends/__init__.py`:
```python
from docspan.backends.myplatform.backend import MyPlatformBackend

BACKENDS: dict[str, type[Backend]] = {
    "google_docs": GoogleDocsBackend,
    "confluence": ConfluenceBackend,
    "myplatform": MyPlatformBackend,  # add this
}
```

### Step 5: Test

Create `tests/backends/test_myplatform.py` with unit tests using mocked HTTP.

### Step 6: Document

Add `docs/backends/myplatform.md` with auth setup steps and an example `markgate.yaml` snippet.

---

## Recommended Developer Setup Commands

Based on pyproject.toml tooling:

```bash
# 1. Clone and enter the repo
git clone https://github.com/tstapler/docspan
cd docspan

# 2. Create a virtual environment and install in editable mode with dev deps
uv venv
source .venv/bin/activate  # or .venv/Scripts/activate on Windows
uv pip install -e ".[dev]"

# 3. Verify the CLI works
docspan --help

# 4. Run tests
pytest

# 5. Run tests with coverage
pytest --cov=docspan --cov-report=term-missing

# 6. Lint
ruff check src/ tests/

# 7. Format (ruff also handles formatting)
ruff format src/ tests/

# 8. Type check
mypy src/

# 9. Build the package
uv build

# 10. Check PyPI README rendering
twine check dist/*
```

### For docs development:
```bash
# Install docs deps
uv pip install -e ".[docs]"

# Live preview
mkdocs serve

# Deploy to GitHub Pages
mkdocs gh-deploy --force
```
