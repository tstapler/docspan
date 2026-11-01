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
- The same limit applies to an explicit `--title`, so both cases are validated (criterion 2).

## Acceptance Criteria

1. When the derived tab title (H1 or basename) exceeds 50 characters and no `--title` is given,
   the command exits with code 1 and prints a message naming the 50-character limit and
   suggesting `--title`.
2. When `--title` is given explicitly and exceeds 50 characters, the command exits with code 1
   and prints a message naming the limit and suggesting the value be shortened.
3. The limit is a named constant, `_TAB_TITLE_MAX_LEN`.
4. Validation fires before any Google Docs API call (no `HttpError` for this case).
5. A tab title of exactly 50 characters is accepted (exit 0).
6. Existing `--new-tab-in` tests continue to pass.
7. Three new unit tests: derived 51 chars (exit 1, message has '50' and '--title'); derived
   exactly 50 chars (exit 0); explicit `--title` 51 chars (exit 1, message has '50' and 'Shorten').

## Out of Scope

- Truncation mode (the item text notes it as an option but does not require it).
- Title-length enforcement for regular `map` (no `--new-tab-in`) — the Google Docs create API
  has no documented hard title limit (tested titles up to 500+ chars without a 400).
- Confluence backend title limits.
