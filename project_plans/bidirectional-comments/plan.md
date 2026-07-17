# SDD Plan: Bidirectional Google Docs Comments

**Status:** planning only (no code) · **Backend:** `google_docs` · **Related:** read-only comments reader (PR #5)

> Storage locations below (base snapshot, state) are shown at their current repo-relative paths. If the
> XDG-paths + central-config refactor lands first, these move under the XDG data/state root — the design
> is unchanged, only the root differs.

---

## 1. Requirements

### Functional
- **FR1 — Pull others' comments** (exists in PR #5, being refactored): fetch all threads (open + resolved) with replies, quoted selection, author, timestamps; materialize locally.
- **FR2 — Reply to existing threads:** user writes a reply into a local thread file; `push` creates it via `replies.create` on the parent comment.
- **FR3 — Add new comments:** `push` creates via `comments.create`. On Google Docs these land **unanchored** (ADR-004).
- **FR4 — Resolve / reopen:** `push` applies via `replies.create` with `action=resolve|reopen`.
- **FR5 — Round-trip idempotency:** re-`push` with no local edits = **zero** remote writes; re-`pull` with unchanged remote = **zero** local rewrites.
- **FR6 — Migration:** existing single `{file}.comments.md` sidecars convert to the `comments/` layout losslessly.

### Non-functional invariants ("bidirectional, no clobber")
- **NC1 — Append-only to remote:** docspan only ever *creates* comments/replies and *resolves/reopens*. It never updates or deletes remote comments/replies (anyone's, including the user's). Kills the whole "sync rewrote/lost a comment" class.
- **NC2 — Un-pushed local additions are sacred:** `pull` never discards/overwrites a locally-authored, not-yet-pushed reply/comment.
- **NC3 — Remote additions never clobber local:** `pull` merges by stable identity, never wholesale file regeneration.
- **NC4 — Stable thread/reply identity:** every thread/reply addressable by Drive id; not-yet-pushed items carry an explicit "no id yet" marker.
- **NC5 — Offline-testable:** all request-building/parsing/reconciliation unit-testable with fixtures, no network.
- **NC6 — Crash-safety:** atomic writes (temp-then-rename, as `SyncState.save` does); a mid-push crash must not silently double-post (best-effort — R1).

### Non-goals (v1)
- Anchored/text-range placement of new comments (Drive ignores `anchor` on Workspace editor files — §2).
- Editing/deleting anyone's existing remote comment/reply.
- Rich formatting (comment `content` is plain text on write).
- Confluence inline-comment write path (separate later effort).

---

## 2. Research findings — Drive comments/replies API

Comments live on **Drive API v3**, not Docs v1. `GoogleDocsClient` already holds `drive_service` — no new wiring.

| Capability | Method | Notes |
|---|---|---|
| List threads + replies | `comments.list(fileId, fields=…, pageToken)` | Paginated; must request nested `replies` + output-only fields |
| Get one thread | `comments.get(fileId, commentId, fields=…)` | Cheap resolved/modifiedTime check |
| Create top-level comment | `comments.create(fileId, body={content, quotedFileContent?, anchor?})` | `content` = plain text (write); `htmlContent` output-only |
| Create reply | `replies.create(fileId, commentId, body={content})` | Same content semantics |
| Resolve / reopen | `replies.create(fileId, commentId, body={action:"resolve"|"reopen", content?})` | Resolving = a reply with an action |
| List replies | `replies.list(fileId, commentId)` | Usually unneeded — `comments.list` embeds replies |

**Design-shaping limits:**
- **`anchor` is ignored on Google Docs** — a new anchored comment renders as "Original content deleted" / no highlight. → new comments must be document-level/unanchored (ADR-004). *(High confidence — documented + reproduced.)*
- **`content` plain text on write; `htmlContent` read-only** — no rich round-trip.
- **Output-only fields need explicit `fields=`** (`id`, `createdTime`, `htmlContent`, `resolved`, nested `replies`).
- **Editing/deleting others' items** → 403; avoided entirely per NC1.
- **Scopes:** writing needs full `drive` scope. `PUSH_SCOPES` already has it → existing push tokens suffice; `drive.readonly` tokens can't write and must re-consent.
- **Quoted selection is read-only context** — surfaced, never used for positioning writes.

Stable identity = Drive comment/reply `id` (stable server strings). A not-yet-pushed local item has no server id → explicit empty-id + `pushed=false` marker (NC4), filled after `create`.

Sources: [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments) · [REST: comments](https://developers.google.com/workspace/drive/api/reference/rest/v3/comments) · [anchor "Original content deleted" issue](https://github.com/googleworkspace/cli/issues/169)

---

## 3. Design

### 3.1 The `comments/` directory layout

Replace the single regenerated `{local}.comments.md` blob with a directory beside the local file:

```
notes/spec.md
notes/spec.comments/            ← COMMENTS_DIR_SUFFIX = ".comments"
  t-8f3a2c.md                   ← one file per thread; filename = stable LOCAL id
  local-4c9d.md                 ← a brand-new thread not yet pushed (no server id)
```

**Naming (ADR-002): filename = stable local id; server id in front matter.** Filename never changes across syncs (avoids git churn + rename races). New local thread = `local-<short-uuid>.md`; after push it keeps the filename but gains `remote_id`. A `remote_id → filename` index lives in state for O(1) reconcile.

**Per-thread file — YAML front matter + id-addressed marker blocks:**

```markdown
---
thread: t-8f3a2c            # stable local id (== filename stem), never changes
remote_id: "AAAABBBBcomment"  # Drive comment id; null until pushed
resolved: false
author: "Alice <alice@ex.com>"
created: 2026-07-16T10:00:00Z
quoted: "the p50 latency figure"   # read-only context
anchored: false             # informational; Docs ignores anchor
---

<!-- comment id=AAAABBBBcomment author="Alice" created=... pushed=true -->
Where does the p50 number come from?

<!-- reply id=CCCCreply author="Bob" created=... pushed=true -->
From the June dashboard.

<!-- reply id= author=me created=... pushed=false -->
I'll switch this to p99 and cite the source.
```

- **`pushed=false` + empty `id=`** is the sole "net-new local, push me" signal (NC4).
- Block body = text between one marker and the next. Adding a reply = appending a `pushed=false` block (`docspan comments reply <thread>` scaffolds it).
- After `create`, push rewrites **only that block's marker** (fills id, `pushed=true`) + the file's `remote_id`/`resolved` — nothing else touched.

### 3.2 Identity & sync-state

Comments are structured, id-bearing records → reconciled **by identity**, not the line-based `three_way_merge` used for doc bodies (ADR-003). Keep the three-way *philosophy* (base/ours/theirs) at thread+reply granularity.

Extend the existing state store:
- `comments` section in `MappingState` (or sibling keyed by local path) in `.markgate-state.json`:
  ```jsonc
  "comments": {
    "index": { "AAAABBBBcomment": "t-8f3a2c" },   // remote_id → filename stem
    "base_snapshot_hash": "<sha256>"               // pointer into base store
  }
  ```
- Comments base snapshot in the content-addressed base store (reuse `save_base_content`/`get_base_content`), under `.markgate-base/comments/`:
  ```jsonc
  { "threads": { "AAAABBBBcomment": { "resolved": false, "replies": ["CCCCreply"], "content_hash": "…" } } }
  ```
  This snapshot is the **base**: `base` = last-synced remote, `theirs` = current `comments.list`, `ours` = local files.

### 3.3 Push semantics

`orchestrate_push` gains a comments pass (guarded by `comments_mode != off`; push-direction only):
1. Parse every thread file.
2. Collect actionable items (stable order — comments before replies, oldest first):
   - `pushed=false` block on a thread with null `remote_id` → `comments.create` (unanchored); store id, update index, mark pushed.
   - `pushed=false` reply under a thread with `remote_id` → `replies.create`; fill id, pushed.
   - Front-matter `resolved` flipped vs base + user asked → `replies.create(action=resolve|reopen)`.
3. **NC1:** only create + resolve/reopen calls. No update/delete.
4. **Persist after each create** → idempotent re-runs (FR5), bounded crash window (R1).

### 3.4 Pull semantics (anti-clobber core)

`orchestrate_pull` gains a reconcile pass:
1. `comments.list` (paginated, nested replies/resolved/quoted) → **theirs**.
2. Load **base** snapshot + local files (**ours**).
3. Reconcile per thread by `remote_id`, then per reply by reply id:

| Case (by id) | Action | Invariant |
|---|---|---|
| Thread in theirs, not base/local | Create local thread file | — |
| Reply id in theirs, not local | **Append** reply block (`pushed=true`) | NC3 |
| Local block `pushed=false` | **Preserve verbatim** | NC2 |
| `resolved` changed in theirs | Update front-matter | — |
| Remote body edited (id in base+theirs, hash differs) | Replace **only that block's** body | NC1/NC3 |
| Thread in base, absent from theirs (deleted remotely) | Mark `deleted: true` (never `rm`) | NC2 |

4. Order remote by `createdTime`; local `pushed=false` blocks sort last.
5. Write new base snapshot = current remote; update index.

A **structured merge keyed by id** — the anti-clobber replacement for the regenerated blob.

### 3.5 Migration from `{file}.comments.md`
- New config `comments_mode: dir | sidecar | off` (replaces boolean `pull_comments`; `true` → `sidecar` w/ deprecation note).
- First `dir`-mode run: if the old sidecar exists, regenerate the `comments/` dir from remote (authoritative; the old blob was read-only so nothing lost), rename old → `.bak`.
- `docspan comments migrate [path]` (dry-runnable). Add `COMMENTS_DIR_SUFFIX = ".comments"` in `core/paths.py`.

---

## 4. ADRs

- **ADR-001 — Directory-per-thread over single regenerated sidecar.** The single blob regenerates wholesale on every pull → can't safely hold user edits. Per-thread files give stable handles, surgical appends, localized conflicts.
- **ADR-002 — Filename = stable local id; server id in front matter.** Rejected filename=server-id (rename on first push → git churn, identity break, half-push races).
- **ADR-003 — Structured id-keyed reconciliation, not line-based merge.** `merge3` reused only for a rare edited body within one block. Line merge corrupts identity-bearing records / interleaves replies.
- **ADR-004 — v1 = replies + resolve + unanchored new comments; no anchored creation.** Drive ignores `anchor` on Docs; anchored create renders "Original content deleted." Ship replies + resolve first; new comments (Phase 3) unanchored, flagged, documented.

---

## 5. Phased implementation plan

- **Phase 0 — Directory refactor (read-only).** `comments/` layout, marker-block serializer/parser (round-trip tested), `comments_mode` + migration, `COMMENTS_DIR_SUFFIX`. Pull still regenerates from remote (safe). Smallest useful step.
- **Phase 1 — Bidirectional replies + reconcile-aware pull (SHIP TOGETHER).** `client.create_reply`/resolve, push comments-pass, base snapshot, id-keyed reconcile pull (§3.4), `docspan comments reply`. *Push and reconcile-pull must ship in one release* — shipping push while pull still regenerates would clobber un-pushed local replies (violates NC2).
- **Phase 2 — New unanchored top-level comments.** `client.create_comment` + push, behind `comments_new: true`, limitation surfaced. `docspan comments new`.
- **Phase 3 — Robustness & polish.** Remote-deletion UX, resolve edge cases, crash-window dedupe (R1), `docspan comments status`. Later: Confluence parity (separate plan).

---

## 6. Test strategy (offline-first)

Mirror `tests/test_orchestrator.py` (in-memory fake, `tmp_path`, no network):
- Fixtures: captured `comments.list` JSON (open+resolved, nested replies, quoted, multi-page). Assert `fields` mask requests nested replies + output-only fields.
- Serializer/parser round-trip byte-stable; empty-id `pushed=false` markers survive; hand-appended reply parses.
- Reconcile as a **pure table-driven function** `(base, remote, local) → (files, action_plan)` covering every §3.4 row — especially **local-unpushed-preserved (NC2)**.
- Push planner → ordered create/reply/resolve calls against a `FakeDriveComments` double; payload shapes (plain-text content; resolve `action`); **second run = zero calls (FR5)**.
- Migration: old sidecar → dir generated, `.bak` created, no loss.
- Scope guard: read-only token → clear "re-auth for write", not raw 403.

---

## 7. Risks & open questions

- **R1 — Crash-window double-post.** No idempotency key on Drive comments; a create that succeeds then crashes before local persist re-posts next run. *Mitigation:* persist after each create; Phase 3 dedupe on next pull. **Open:** acceptable for v1?
- **R2 — `me` attribution** for un-pushed blocks; confirm no display-name reconcile needed (cosmetic).
- **R3 — Resolve permissions** may 403 for non-authors depending on sharing; best-effort, per-thread report, never fail whole push.
- **R4 — Unanchored comments UX** (Phase 2): document-level only. **Operator decision:** acceptable, or omit new-comment creation from v1?
- **R5 — Large threads / pagination cost.** Reuse `_with_backoff`; consider a `modifiedTime`-gated skip. **Open:** cheap "any comments changed?" probe? (none first-class).
- **R6 — Ordering churn** when local blocks gain ids; ensure minimal diff.

---

## 8. Adversarial self-review (fixes folded in)

- **Clobber window between push and pull** → Phase 1 ships push + reconcile-pull together; Phase 0 stays read-only.
- **Filename-as-server-id churn/races** → ADR-002 stable local id.
- **Line-merge on comments corrupts/interleaves** → ADR-003 id-keyed reconcile.
- **Silent loss on remote deletion** → mark `deleted: true`, never `rm`.
- **Idempotency false-confidence** → persist after each create; residual window = R1.
- **Scope trap** (readonly token silently fails writes) → explicit scope check + re-auth message.
- **Broken anchored feature** → ADR-004 unanchored-only, documented.
