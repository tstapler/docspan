# Adversarial Review: markgate-sync

**Date**: 2026-06-01
**Verdict**: CONCERNS

> **Post-review patches applied to plan.md** (2026-06-01):
> - Concern 3 (inverted pull order): Task 4.1.1a rewritten with correct pre-pull hash/version detection order
> - Concern 4 (insert_at_index undefined): Task 3.2.2a expanded with explicit write-backwards index strategy and `doc_end_index` parameter
> - Concern 1 (double-fetch): Task 4.1.1a now calls `get_remote_version()` *before* `backend.pull()` and avoids redundant fetch
> - Concern 2 (MarkdownToParagraphParser underspecified): Task 3.3.1b now commits to `mistune>=3.0` with explicit acceptance criteria
> - Concern 5 (CommentClient instantiation): Task 2.2.1a now includes `_ensure_comment_client()` lazy-init pattern and pre-task read instruction
> - Concern 6 (.markgate-base/ directory creation): `_save_base_content()` now includes `os.makedirs(..., exist_ok=True)`
> - Concern 7 (paragraph-granularity limitation): Story 3.3.1 AC now documents the paragraph-level comment survival semantics
> - Minor: Story 2.1.2 now labeled as a Google Docs bug mis-grouped in Phase 2 for convenience

---

## Blockers

*(None — no show-stoppers that would prevent implementation from starting)*

---

## Concerns

- [ ] **Story 4.1.1 / Task 4.1.1a — pull re-fetches content twice, defeating the diff** — The plan says `backend.pull()` is called first to write the local file, and *then* `backend.get_remote_version()` is called. For Google Docs, `get_remote_version()` calls `get_document()`, which is a *second* network round-trip after the pull already fetched the doc. For Confluence, the pull uses the crawler while `get_remote_version()` calls `get_page()` separately. Neither backend `pull()` currently returns the `remote_version` it saw. The plan should either (a) extend `PullResult` to include `remote_version` so it's captured in the same call, or (b) explicitly accept the double-fetch and note it in the task. As written, Task 4.1.1a calls `backend.pull()` *then* `backend.get_remote_version()` but the pull has already consumed the doc — the version returned is guaranteed consistent but wastes a network call and could differ if a remote edit lands between the two calls. Recommendation: add `remote_version: Optional[str]` to `PullResult` and populate it in each backend's `pull()` implementation to make the state snapshot atomic.

- [ ] **Task 3.3.1b — MarkdownToParagraphParser scope is underspecified and risky** — This task is the keystone of the entire push pipeline. The plan says "use `mistune` or a stdlib `re`-based line parser" but doesn't commit to either, leaving the implementer to make a significant design choice mid-task. The `re`-based fallback cannot correctly handle multi-line paragraphs, nested lists, fenced code blocks with blank lines inside them, or setext-style headings (`===`/`---` underline). If the markdown parser produces wrong `DocsParagraphNode` lists, every downstream acceptance criterion in Stories 3.3.1 and 3.3.2 silently produces incorrect Docs output. Recommendation: commit in the plan to `mistune>=3.0` (check if already a transitive dep from the Confluence markdown side), add it to `pyproject.toml` in Task 4.3.1a, and give `MarkdownToParagraphParser` explicit acceptance criteria (not just "parse headings and lists") including blank-line paragraph handling and fenced code blocks.

- [ ] **Task 4.1.1a — conflict detection algorithm runs on post-pull content, not pre-pull** — The plan's pull flow is: (1) call `backend.pull()` which overwrites the local file, (2) compute `local_hash = sha256_of_file(mapping.local)`. This means `local_hash` computed in step 2 is the *remote* content, not the user's pre-pull local content. The correct algorithm per requirements is: read `local_hash` from disk *before* calling `backend.pull()`, compare it against `state.local_hash` to detect local changes, then decide whether to pull. The plan inverts this order. Recommendation: rewrite Task 4.1.1a to explicitly: (a) read and hash the current local file *before* any backend call, (b) call `get_remote_version()` to check if remote changed *before* fetching content, (c) only call `backend.pull()` if warranted (fast-forward or merge cases), (d) write state after resolving.

- [ ] **Story 3.2.2 / Task 3.2.2a — `insert_at_index` parameter origin not defined** — `_make_insert_requests(nodes, insert_at_index)` takes an `insert_at_index` argument but the plan never explains how the caller computes this value for an `insert` opcode. For a `replace` opcode (delete + insert same position), the insert index is the start of the deleted range. For a pure `insert` opcode, the index must be derived from the current doc's paragraph at that position — but target nodes have `start_index=0` (per Task 3.3.1b). The `build()` method must track running insertion offsets and maintain a mapping from opcode position back to a Docs index. This is the hardest part of the request builder and the plan handwaves it. Recommendation: add a task that explicitly designs the index-tracking strategy — either (a) maintain an `offset_tracker` that accumulates index shifts as requests are built (write-forwards), or (b) use the write-backwards approach where current-doc `start_index`/`end_index` from `equal` opcodes act as anchors for adjacent insert positions.

- [ ] **Task 2.2.1a — `ConfluenceCommentClient` instantiation path not resolved** — The task says "check if `self._client` has a comment client or instantiate `ConfluenceCommentClient`." But `_ensure_client()` only creates a `ConfluenceClient`, and there's no `_comment_client` field or lazy init pattern in the backend. The `ConfluenceCommentClient` constructor signature is unknown from the plan (the task says "read comment_client.py to confirm" — but this is an open question at planning time, not implementation time). If `ConfluenceCommentClient` requires different or additional constructor args than `ConfluenceClient`, this could fail silently. Recommendation: read `comment_client.py` during planning (before writing the plan) and specify the exact instantiation in the task — e.g., `self._comment_client = ConfluenceCommentClient(self._client.config)` or similar.

- [ ] **Epic 4.1 — `.markgate-base/` directory creation is not assigned to any task** — Multiple tasks reference writing to `.markgate-base/<sha256>.base` files, but no task includes `os.makedirs('.markgate-base/', exist_ok=True)`. This will raise `FileNotFoundError` on the first push/pull of a new project. Recommendation: add directory creation to `_save_base_content()` helper in Task 4.1.1a.

- [ ] **Acceptance criterion: "A comment on unchanged text survives a push" — not testable without integration test** — The plan has no task for integration/smoke tests. The structural diff approach is the mechanism, but the paragraph-granularity guarantee (Story 3.2.1 AC: "diff uses text only") means any inline style change within a paragraph (e.g., making a word bold) triggers a full paragraph replace and orphans comments on that paragraph. This is a behavioral gap between the requirements ("comment on unchanged text survives") and the implementation (paragraph-level granularity, not span-level). The plan should explicitly document this limitation: comments survive only if the *entire paragraph* is unchanged, not just the surrounding text. Recommendation: add a note to Story 3.3.1 AC clarifying paragraph-granularity semantics.

---

## Minors

- Task 1.1.3a inline note "Add `merge3` to `pyproject.toml` dependencies (in Task 4.3.1a — do this first if merge.py is created before pyproject update)" is confusing — the sequencing summary in the same plan already lists Task 4.3.1a as a Phase 1 blocker. These should be consistent.

- Task 2.1.2a is categorized under Epic 2.1 "Fix broken Confluence push" but fixes a *Google Docs pull* bug (`export_as_html` reference). This is a mis-categorization that may cause it to be missed in a Confluence-only sprint.

- Story 2.1.2 acceptance criterion says "calls `self._client.get_doc_content(doc_id)`" but notes the correct name needs to be confirmed from `client.py`. If the correct name is something else entirely, the AC is wrong before implementation starts.

- The dependency diagram shows Task 4.3.1a at the end (Phase 4) but the sequencing summary correctly lists it as a Phase 1 blocker. The diagram is misleading.

- `_get_state_path(config)` in Task 4.1.1a uses `os.path.dirname(config_path or 'markgate.yaml')` — if `config_path` is `None`, `dirname('markgate.yaml')` returns `''` (empty string), making the state path `.markgate-state.json` in the current working directory, which may not be the project root. This should use `os.getcwd()` as the fallback, or read the config path from the loaded config object.

- The plan counts "12 Epics" but only enumerates 4 phases × 3 epics = 12, while some phases have exactly 3. This is correct but the phase 1 single epic (1.1) with 4 stories is unusual — the plan might read more cleanly if the 4 Foundation stories were 4 epics (1.1 State, 1.2 Merge, 1.3 Base Interface, 1.4 Cleanup).

- Task 3.1.2a adds `drive.readonly` scope alongside `documents` scope. The plan should verify whether this duplicates the existing Drive API scope already used by `get_doc_content()`. If the existing auth already requests Drive access, adding it again is harmless but creates confusion in the scope list.

- No task adds `ConfluenceCommentClient` import to `confluence/backend.py`'s `_ensure_client` or class-level setup — the comment client will need to be initialized with the same `ConfluenceConfig` object used by `ConfluenceClient`.
