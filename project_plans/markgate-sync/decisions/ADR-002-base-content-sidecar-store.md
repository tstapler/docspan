# ADR-002: Store base content in .markgate-base/ directory

**Date**: 2026-06-01
**Status**: Accepted

## Context

Three-way merge requires the actual base content (the document content at the time of the last sync), not just a hash. The `base_hash` in `.markgate-state.json` identifies the base version but cannot reconstruct it. The base content must be retrievable to pass to `three_way_merge(base, theirs, ours)`.

Options considered:

1. **Inline base64 in state JSON** — Compress and base64-encode the base content and store it in `.markgate-state.json` alongside the hash. Simple, single-file approach.
2. **`.markgate-base/<sha256>.base` sidecar files** — Store base content as plain text files in a content-addressed directory, named by their sha256 hash. The state JSON references the hash; the actual content lives on disk.
3. **Re-fetch from remote on three-way merge** — Skip storing base; when a three-way conflict is detected, use the remote content as both "base" and "theirs." This degrades to a two-way merge and loses the change attribution.

## Decision

Use `.markgate-base/<sha256>.base` content-addressed sidecar files.

## Rationale

- **State JSON stays readable**: Large documents (10k+ characters) would make the state file unwieldy if base64-encoded inline.
- **Content-addressed = write-once**: Files named by sha256 are immutable. No stale cleanup needed; if two local paths happen to sync the same content, they share a single base file. Overwrites are safe (same content).
- **Atomic-safe**: Each base file is written fully before being referenced. The state JSON is updated atomically via `os.rename()` after the base file exists.
- **No network round-trip**: Unlike option 3, the base content is available locally for offline conflict inspection.

## Consequences

- `.markgate-base/` must be listed in `.gitignore` (added in Task 4.3.1a)
- The directory is created lazily by `_save_base_content()` using `os.makedirs(..., exist_ok=True)`
- If `.markgate-base/` is deleted, the next pull that would have used three-way merge falls back to a fast-forward pull (remote wins) with a user warning. No crash.
- Base files accumulate over time (one per unique synced content version). A future `markgate gc` command could prune unreferenced base files, but this is out of scope for the current implementation.
