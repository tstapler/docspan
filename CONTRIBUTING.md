# Contributing to docspan

## Prerequisites

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- git

## Dev Setup

```bash
git clone https://github.com/tstapler/docspan
cd docspan
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
docspan --help   # verify the CLI works
```

## Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=docspan --cov-report=term-missing

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy src/
```

## Project Structure

```
src/docspan/
├── cli/main.py          <- Typer commands (push, pull, status, auth, conflicts)
├── config.py            <- MarkgateConfig Pydantic model + YAML loader
├── backends/
│   ├── base.py          <- Backend ABC (push, pull, auth_setup, validate_config, get_remote_version)
│   ├── __init__.py      <- BACKENDS registry {"google_docs": ..., "confluence": ...}
│   ├── google_docs/     <- Google Docs backend implementation
│   └── confluence/      <- Confluence backend implementation
└── core/
    ├── state.py         <- SyncState / MappingState (JSON persistence)
    ├── orchestrator.py  <- Push/pull orchestration (conflict detection, three-way merge)
    ├── merge.py         <- Three-way text merge
    └── paths.py         <- Path constants (.markgate-state.json, etc.)
```

## How to Add a New Backend

### Step 1: Create the backend package

```
src/docspan/backends/myplatform/
├── __init__.py
└── backend.py
```

### Step 2: Implement the Backend ABC in `backend.py`

```python
from docspan.backends.base import Backend, PushResult, PullResult
from docspan.config import MarkgateConfig

class MyPlatformBackend(Backend):
    name = "myplatform"  # must match the key used in markgate.yaml

    def __init__(self, config) -> None:
        self.config = config

    @classmethod
    def from_config(cls, markgate_config: MarkgateConfig) -> "MyPlatformBackend":
        return cls(markgate_config.backends.myplatform)

    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        # read local_path, convert to platform format, upload to doc_id
        # return PushResult(status="ok", doc_id=doc_id, url=url)
        # on error: return PushResult(status="error", doc_id=doc_id, message=str(exc))
        ...

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        # fetch from platform, convert to markdown, write to local_path
        # return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        ...

    def auth_setup(self) -> None:
        # print setup instructions or run interactive prompts
        ...

    def get_remote_version(self, doc_id: str) -> str:
        # return an opaque version token (e.g. revision ID, version number string)
        # used to detect whether the remote has changed since last sync
        ...

    def validate_config(self) -> None:
        # raise ValueError("Missing X. Run: docspan auth setup myplatform") if misconfigured
        ...
```

### Step 3: Add a config model to `config.py`

```python
class MyPlatformConfig(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None

class BackendsConfig(BaseModel):
    google_docs: Optional[GoogleDocsConfig] = None
    confluence: Optional[ConfluenceConfig] = None
    myplatform: Optional[MyPlatformConfig] = None  # add this
```

### Step 4: Register in the `BACKENDS` dict

In `src/docspan/backends/__init__.py`:

```python
from docspan.backends.myplatform.backend import MyPlatformBackend

BACKENDS: dict[str, type[Backend]] = {
    "google_docs": GoogleDocsBackend,
    "confluence": ConfluenceBackend,
    "myplatform": MyPlatformBackend,  # add this
}
```

Then write tests in `tests/backends/test_myplatform.py` and a docs page at `docs/backends/myplatform.md`.

## Note about `markgate.yaml`

The config file is gitignored by default because it contains API tokens. Add your own `markgate.yaml` locally based on the `markgate.yaml.example` template — it will not be committed.

## PR Process

1. Fork the repository and create a feature branch from `main`
2. Ensure all tests pass: `pytest`
3. Ensure lint passes: `ruff check src/ tests/`
4. Add tests for any new functionality
5. Open a PR against `main` — CI runs `pytest`, `ruff`, and `mypy` automatically
6. At least one review approval is required before merge; keep each PR to one logical change
