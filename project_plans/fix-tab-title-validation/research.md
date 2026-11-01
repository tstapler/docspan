# Research: fix-tab-title-validation

## Code Paths Traced

### Title derivation (`src/docspan/cli/main.py`)

- `_default_title(file)` (line 915–926): reads the file's first H1 after stripping
  YAML frontmatter and code fences; falls back to basename. No length cap.
- `doc_title = title or _default_title(file)` (line 1012): result goes directly
  into `create_tab()` or `create()`.

### Tab creation (`src/docspan/backends/google_docs/backend.py`)

- `create_tab(doc_id, title)` (line 1918): calls `_client.add_document_tab(doc_id, title)`.
  No length validation.

### API call (`src/docspan/backends/google_docs/client.py`)

- `add_document_tab(doc_id, title)` (line 161–176): sends
  `{"addDocumentTab": {"tabProperties": {"title": title}}}` directly to the Docs API.
  Returns the tab properties dict. No validation or error-specific handling.

### Error propagation

The `try/except ValueError` block in `main.py` (lines 1019–1026) catches only `ValueError`
from `backend_instance.create_tab()`. An `HttpError 400` from the Google Docs API propagates
unhandled, producing a raw traceback.

### Existing `--new-tab-in` guard checks

Lines 985–1010 validate: backend is google_docs, `--tab-id` not present, `--space` not
present, parent is mapped, parent backend is google_docs, parent has a remote_id. Title
length is not checked here.

## Google Docs API Limit

The `addDocumentTab` API returns `HttpError 400` with message
`"addDocumentTab: The tab title cannot be longer than 50 characters."` when the title
exceeds 50 characters. This is a hard server-side constraint with no override.

The `documents.create` API (used for normal `map` without `--new-tab-in`) does **not** have
a documented 50-char limit, so the validation should be scoped to the tab creation path only.

## Existing Test Infrastructure

- `tests/test_cli.py` class `TestMapCommand`: seven tests for `--new-tab-in`, using a
  `FakeBackend` that implements `create_tab()`. The `_new_tab_fixtures()` helper writes
  `"# Discussion\n"` (9 chars — well under 50).
- `FakeBackend.create_tab()` (line 69 in test_cli.py) returns a fixed `CreateResult`
  without any length validation, so a new test can either test the CLI-level guard before
  the backend is called, or add a length check in `FakeBackend` too.

## Decision: validate in CLI before network call

Validating in `main.py` before calling `backend_instance.create_tab()` is the cleanest
approach: it keeps the backend class free of Google-specific API constraints, produces
the user-facing error in the same place as all other user-facing pre-flight errors for
this command, and avoids a network round-trip.

A named constant `_TAB_TITLE_MAX_LEN = 50` belongs in `main.py` alongside the guard block.
