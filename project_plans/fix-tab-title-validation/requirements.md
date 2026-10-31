# Requirements: fix-tab-title-validation

## Problem Statement

`docspan map --new-tab-in EXISTING.md NEW.md` crashes with an unhandled `HttpError 400`
traceback when the tab title derived from `NEW.md`'s first H1 exceeds 50 characters (Google
Docs API hard limit on `addDocumentTab` tab titles). The user sees a raw Python traceback
rather than a clear actionable error message. The `--title` flag is a working workaround, but
users only discover it from the docs, not from the error.

## Scope

- Command: `docspan map --new-tab-in`
- Failure mode: H1 heading in the new file is > 50 characters → API returns `HttpError 400`
- Same risk exists with `--title` passed explicitly by the user, but is considered out of scope
  for the derived-from-H1 case (when the user passes `--title`, they can see the length).

## Acceptance Criteria

1. When the derived tab title (from H1 or basename) would exceed 50 characters and no `--title`
   is given, the command exits with code 1 and prints a one-line message that:
   - Names the 50-character limit
   - Suggests the `--title` flag as a fix
2. When `--title` is given explicitly and its value exceeds 50 characters, the command exits
   with code 1 and prints the same kind of actionable message (naming limit + `--title` already
   in use — suggest shortening).
3. The limit (50) is defined as a named constant, not a magic number.
4. The validation happens before any network call to the Google Docs API.
5. Unit tests cover: derived title too long (exit 1, message mentions limit and `--title`);
   derived title exactly 50 chars (exit 0); derived title 51 chars (exit 1).
6. Existing tests for `--new-tab-in` continue to pass.

## Out of Scope

- Truncation mode (the item text notes it as an option but does not require it).
- Title-length enforcement for regular `map` (no `--new-tab-in`) — the Google Docs create API
  has no documented hard title limit (tested titles up to 500+ chars without a 400).
- Confluence backend title limits.
