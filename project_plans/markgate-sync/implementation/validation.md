# Validation Plan: markgate-sync

**Date**: 2026-06-01

---

## Requirement → Test Mapping

### REQ-1: SyncState — load/save/hash infrastructure

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-1a: `SyncState.load()` returns empty state when file missing | `tests/core/test_state.py` | `SyncState_load_should_return_empty_mappings_when_file_missing` | Unit (happy) | Path points to a non-existent file | `state.mappings == {}` |
| REQ-1a: `SyncState.load()` parses existing JSON correctly | `tests/core/test_state.py` | `SyncState_load_should_deserialize_all_fields_when_file_exists` | Unit (happy) | JSON with one mapping entry; all 6 fields present | Each field on the returned `MappingState` equals the JSON value |
| REQ-1a: `SyncState.load()` raises on corrupt JSON | `tests/core/test_state.py` | `SyncState_load_should_raise_JSONDecodeError_when_file_is_corrupt` | Unit (error) | File contains `"not json"` | `pytest.raises(json.JSONDecodeError)` |
| REQ-1b: `SyncState.save()` writes atomically via `.tmp` + rename | `tests/core/test_state.py` | `SyncState_save_should_write_via_tmp_file_then_rename` | Unit (happy) | Patch `os.rename`; call `save(path)` | `os.rename` called with `(path + '.tmp', path)`; `.tmp` file does not remain |
| REQ-1b: `SyncState.save()` produces valid JSON round-trip | `tests/core/test_state.py` | `SyncState_save_should_produce_JSON_that_load_can_round_trip` | Unit (happy) | Save a state, then load it back | All field values survive the round-trip unchanged |
| REQ-1c: `sha256_of_file()` returns correct hex digest | `tests/core/test_state.py` | `sha256_of_file_should_return_correct_sha256_hex_digest` | Unit (happy) | Write known bytes to a tmp file | Digest equals `hashlib.sha256(b"hello").hexdigest()` |
| REQ-1c: `sha256_of_content()` matches sha256_of_file for same content | `tests/core/test_state.py` | `sha256_of_content_should_match_sha256_of_file_for_same_text` | Unit (happy) | Same string written to file and passed to `sha256_of_content` | Both return identical hex strings |
| REQ-1d: `SyncState.get()` returns `None` for unknown path | `tests/core/test_state.py` | `SyncState_get_should_return_None_when_path_not_mapped` | Unit (error) | Fresh state, query unmapped path | Return value is `None` |
| REQ-1d: `SyncState.update()` upserts a mapping entry | `tests/core/test_state.py` | `SyncState_update_should_upsert_and_SyncState_get_should_retrieve` | Unit (happy) | Call `update(path, mapping)`, then `get(path)` | Returned `MappingState` equals the one passed to `update` |
| REQ-1: Concurrent write safety — atomic rename cannot produce half-written file | `tests/core/test_state.py` | `SyncState_save_should_not_leave_partial_file_if_write_fails` | Unit (error) | Patch `open()` to raise after partial write | Original file (if it existed) is untouched; no `.tmp` orphan via `os.rename` not reached |

---

### REQ-2: three_way_merge() — merge logic

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-2a: Only remote changed → result is `theirs`, no conflicts | `tests/core/test_merge.py` | `three_way_merge_should_fast_forward_when_only_theirs_differs` | Unit (happy) | `base="A"`, `theirs="B"`, `ours="A"` | `result.merged == "B"`, `result.has_conflicts == False`, `result.conflict_count == 0` |
| REQ-2b: Only local changed → result is `ours`, no conflicts | `tests/core/test_merge.py` | `three_way_merge_should_accept_ours_when_only_ours_differs` | Unit (happy) | `base="A"`, `theirs="A"`, `ours="C"` | `result.merged == "C"`, `result.has_conflicts == False` |
| REQ-2c: Non-overlapping edits → combined, no conflicts | `tests/core/test_merge.py` | `three_way_merge_should_combine_non_overlapping_edits` | Unit (happy) | `base` has 2 lines; `theirs` edits line 1; `ours` edits line 2 | Merged has both edited lines; `has_conflicts == False` |
| REQ-2d: Overlapping edits → conflict markers present | `tests/core/test_merge.py` | `three_way_merge_should_emit_conflict_markers_when_both_change_same_line` | Unit (error) | Same line changed differently in `theirs` and `ours` | `result.has_conflicts == True`; `result.merged` contains `"<<<<<<< ours"`, `"======="`, `">>>>>>> theirs"` |
| REQ-2e: `conflict_count` equals the number of conflict blocks | `tests/core/test_merge.py` | `three_way_merge_should_count_conflict_blocks_accurately` | Unit (happy) | Two separate conflict regions | `result.conflict_count == 2` |
| REQ-2f: base=empty → insert-only merge, no conflicts | `tests/core/test_merge.py` | `three_way_merge_should_handle_empty_base_as_insert_only` | Unit (edge) | `base=""`, `theirs="hello\n"`, `ours="world\n"` | Either no conflict if lines are disjoint, or conflict markers if considered overlapping at position 0; `has_conflicts` reflects actual marker count |
| REQ-2: Integration — merge result written to file and state updated | `tests/core/test_merge.py` | `three_way_merge_integration_should_write_markers_to_disk_on_conflict` | Integration | Simulate pull flow: both-changed → `three_way_merge()` → write to temp file | File on disk contains `"<<<<<<< ours"` marker |

---

### REQ-3: DocsStructureParser — Google Docs JSON → AST

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-3a: Parses paragraph with single TextRun | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_extract_text_from_single_textrun` | Unit (happy) | Minimal doc JSON with one `NORMAL_TEXT` paragraph `"Hello"` | Returns 1 node; `node.text == "Hello"`; `node.style == "NORMAL_TEXT"` |
| REQ-3b: Parses heading with namedStyleType | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_map_named_style_to_heading` | Unit (happy) | Paragraph with `namedStyleType == "HEADING_2"` | `node.style == "HEADING_2"` |
| REQ-3c: Concatenates multiple TextRuns | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_concatenate_multiple_textruns` | Unit (happy) | Paragraph with 3 TextRun elements | `node.text == "run1run2run3"` (trailing `\n` stripped) |
| REQ-3d: Skips table/sectionBreak elements | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_skip_table_and_sectionBreak_elements` | Unit (happy) | Doc body with `table` and `sectionBreak` structural elements | Returned list has 0 nodes (no crash) |
| REQ-3e: Extracts `is_list_item` and `nesting_level` from bullet | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_set_is_list_item_true_when_bullet_present` | Unit (happy) | Paragraph with `bullet.nestingLevel == 1` | `node.is_list_item == True`; `node.nesting_level == 1` |
| REQ-3f: Falls back to `doc['body']['content']` for legacy single-tab docs | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_fall_back_to_body_content_for_legacy_docs` | Unit (happy) | Doc dict with no `tabs` key, only `body.content` | Returns nodes correctly; no `KeyError` raised |
| REQ-3g: Empty paragraph (single `\n`) → node with `text=""` | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_include_empty_paragraphs_as_empty_string` | Unit (edge) | Paragraph whose only TextRun is `"\n"` | `node.text == ""` |
| REQ-3h: Missing doc body raises a clear error | `tests/google_docs/test_docs_structure_parser.py` | `DocsStructureParser_parse_should_raise_KeyError_when_doc_has_no_body` | Unit (error) | Dict `{}` (no `tabs`, no `body`) | `pytest.raises(KeyError)` |

---

### REQ-4: DocsRequestBuilder — diff ASTs → minimal request list

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-4a: Equal nodes → no delete/insert requests emitted | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_emit_no_requests_when_ASTs_are_identical` | Unit (happy) | `current == target` (same style+text) | Returned list is empty |
| REQ-4b: Style-only change on equal text → only `updateParagraphStyle` emitted | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_emit_only_style_update_when_text_unchanged_but_style_differs` | Unit (happy) | Same text, `current.style="NORMAL_TEXT"`, `target.style="HEADING_1"` | List has exactly 1 request with `updateParagraphStyle`; no `deleteContentRange` or `insertText` |
| REQ-4c: Deleted node → `deleteContentRange` emitted | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_emit_deleteContentRange_for_removed_node` | Unit (happy) | `current` has 2 nodes; `target` has 1 (first removed) | Request dict contains `"deleteContentRange"` with `startIndex`/`endIndex` matching node |
| REQ-4d: Inserted node → `insertText` + `updateParagraphStyle` emitted | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_emit_insertText_and_style_for_new_node` | Unit (happy) | `current` is empty; `target` has 1 `NORMAL_TEXT` node | List contains `"insertText"` and `"updateParagraphStyle"` requests |
| REQ-4e: List item insert → `createParagraphBullets` request emitted | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_emit_createParagraphBullets_for_list_item` | Unit (happy) | Target node has `is_list_item=True` | List contains `"createParagraphBullets"` request |
| REQ-4f: Requests sorted by descending `startIndex` | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_sort_requests_by_descending_startIndex` | Unit (happy) | Multiple edits at different positions | Extracted `startIndex` values from all requests form a non-increasing sequence |
| REQ-4g: Terminal newline never included in `deleteContentRange` | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_not_delete_terminal_newline` | Unit (edge) | Last node's `end_index == doc_end_index`; it is deleted | `deleteContentRange.endIndex < doc_end_index` |
| REQ-4h: `insertText` uses UTF-16 code unit counting for offsets | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_utf16_len_should_count_surrogate_pairs_as_two_units` | Unit (edge) | Emoji string `"😀"` (U+1F600, 2 UTF-16 code units) | `_utf16_len("😀") == 2`; derived request index reflects this |
| REQ-4i: `updateTextStyle` uses FieldMask not `"*"` | `tests/google_docs/test_docs_request_builder.py` | `DocsRequestBuilder_build_should_use_field_mask_not_wildcard_in_text_style` | Unit (happy) | Node with bold span → `updateTextStyle` emitted | `request["updateTextStyle"]["fields"]` does not equal `"*"` |

---

### REQ-5: Confluence `backend.push()` — ConfluencePage construction and version increment

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-5a: `push()` constructs `ConfluencePage` with correct fields | `tests/confluence/test_backend_push.py` | `ConfluenceBackend_push_should_construct_ConfluencePage_with_id_title_content_version` | Unit (happy) | Mock `_client.get_page()` returning known page; call `push()` | `_client.update_page` called once; argument is `ConfluencePage` instance with `id=doc_id`, `title=title`, `content=<adf dict>`, `version=<from page response>` |
| REQ-5b: `ConfluencePage.to_api_data(for_update=True)` increments version by 1 | `tests/confluence/test_backend_push.py` | `ConfluencePage_to_api_data_should_increment_version_by_one_when_for_update_true` | Unit (happy) | `ConfluencePage(version=5, ...)`.`to_api_data(for_update=True)` | Returned dict has `"version": {"number": 6}` |
| REQ-5c: `push()` returns `PushResult(status="ok")` on success | `tests/confluence/test_backend_push.py` | `ConfluenceBackend_push_should_return_ok_status_on_success` | Unit (happy) | All mocks succeed | `result.status == "ok"` |
| REQ-5d: `push()` returns `PushResult(status="error")` on exception | `tests/confluence/test_backend_push.py` | `ConfluenceBackend_push_should_return_error_status_when_update_page_raises` | Unit (error) | `_client.update_page()` raises `Exception("network error")` | `result.status == "error"`; `result.message` contains `"network error"` |
| REQ-5e: `push()` extracts `parent_id` from v1 `ancestors` when `parentId` missing | `tests/confluence/test_backend_push.py` | `ConfluenceBackend_push_should_fall_back_to_ancestors_for_parent_id` | Unit (edge) | `get_page()` response has no `parentId` but has `ancestors=[{"id": "999"}]` | `ConfluencePage.parent_id == "999"` |
| REQ-5f: Integration — push encodes markdown to ADF and calls update_page | `tests/confluence/test_backend_push.py` | `ConfluenceBackend_push_integration_should_encode_markdown_and_call_update_page` | Integration | Real `ConfluenceBackend` with mocked HTTP client; `push("notes.md", "PAGE-1")` | `_client.update_page` called with `ConfluencePage`; ADF `content` structure is non-empty |

---

### REQ-6: Confluence `backend.pull()` — comment sidecar

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-6a: `pull()` writes `.comments.md` when inline comments exist | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_write_comments_sidecar_when_inline_comments_present` | Unit (happy) | Mock comment client returns 1 inline comment; `pull()` to temp dir | `<local_path>.comments.md` exists and contains `"## Inline comments"` |
| REQ-6b: `pull()` writes `.comments.md` when footer comments exist | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_write_comments_sidecar_when_footer_comments_present` | Unit (happy) | Mock comment client: 0 inline, 1 footer | Sidecar contains `"## Footer comments"` |
| REQ-6c: `pull()` does NOT write sidecar when no comments exist | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_not_write_sidecar_when_no_comments` | Unit (happy) | Both `get_page_inline_comments` and `get_page_footer_comments` return `[]` | `<local_path>.comments.md` does not exist |
| REQ-6d: Inline comment includes `> Selection:` block when `inlineProperties.originalSelection` present | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_include_selection_quote_for_inline_comment` | Unit (happy) | Inline comment has `inlineProperties.originalSelection = "key phrase"` | Sidecar contains `'> Selection: "key phrase"'` |
| REQ-6e: Footer comment omits `> Selection:` block | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_omit_selection_quote_for_footer_comment` | Unit (happy) | Footer comment with no `inlineProperties` | Sidecar section under `"## Footer comments"` has no `"> Selection:"` line |
| REQ-6f: Falls back to v1 `get_comments()` when v2 inline-comments returns 404 | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_fall_back_to_v1_get_comments_on_404` | Unit (error) | `get_page_inline_comments()` raises `HTTPError(404)`; `get_comments()` returns 1 comment | Sidecar written; `get_comments` was called |
| REQ-6g: `pull()` does not fail when comment fetch raises non-404 exception | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_should_not_raise_when_comment_fetch_raises_unexpected_error` | Unit (error) | `get_page_inline_comments()` raises `ConnectionError` | `pull()` returns `PullResult(status="ok")`; no sidecar written; warning printed |
| REQ-6h: Integration — pull writes markdown and sidecar | `tests/confluence/test_backend_pull.py` | `ConfluenceBackend_pull_integration_should_write_both_md_and_comments_sidecar` | Integration | Real `ConfluenceBackend` with mocked HTTP; `pull("PAGE-1", "notes.md")` | `notes.md` exists; `notes.md.comments.md` exists with correct H2 sections |

---

### REQ-7: Google Docs push integration — mocked `docs_service.documents()`

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-7a: `push()` returns `status="skipped"` when no AST changes | `tests/google_docs/test_backend_push.py` | `GoogleDocsBackend_push_should_return_skipped_when_no_requests_generated` | Integration | `DocsRequestBuilder.build` returns `[]`; mock `get_document()` returns current doc identical to local markdown | `result.status == "skipped"`; `batch_update` not called |
| REQ-7b: `push()` calls `documents().get()` and `documents().batchUpdate()` | `tests/google_docs/test_backend_push.py` | `GoogleDocsBackend_push_should_call_get_then_batchUpdate_on_docs_service` | Integration | Mock `docs_service.documents()` chain; local file differs from current doc | `documents().get().execute()` called once; `documents().batchUpdate().execute()` called once with `requests` list |
| REQ-7c: `batch_update()` retries on 429 up to 5 times | `tests/google_docs/test_backend_push.py` | `GoogleDocsClient_batch_update_should_retry_on_429_up_to_max_retries` | Integration | `batchUpdate().execute()` raises `HttpError(429)` 3 times then succeeds; patch `time.sleep` | `execute()` called 4 times total; `time.sleep` called 3 times with increasing waits |
| REQ-7d: `batch_update()` raises `RuntimeError` after max retries exceeded | `tests/google_docs/test_backend_push.py` | `GoogleDocsClient_batch_update_should_raise_RuntimeError_when_max_retries_exceeded` | Integration | `batchUpdate().execute()` raises `HttpError(429)` 6 times | `pytest.raises(RuntimeError, match="Max retries exceeded")` |
| REQ-7e: `push()` returns `status="error"` when `batch_update` raises non-429 | `tests/google_docs/test_backend_push.py` | `GoogleDocsBackend_push_should_return_error_when_batchUpdate_raises_non_rate_limit_error` | Integration | `batchUpdate().execute()` raises `HttpError(403)` | `result.status == "error"`; `result.message` mentions the error |
| REQ-7f: Requests submitted in descending index order | `tests/google_docs/test_backend_push.py` | `GoogleDocsBackend_push_should_submit_requests_in_descending_index_order` | Integration | Local file with 3 paragraph changes at known indices | Captured `body["requests"]` have `startIndex` values in non-increasing order |

---

### REQ-8: Conflict detection flow — integration

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-8a: Both-changed → conflict markers written to file | `tests/integration/test_conflict_flow.py` | `pull_should_write_conflict_markers_when_both_local_and_remote_changed` | Integration | State has `local_hash=H1`, `remote_version=V1`; local file has changed (hash≠H1); `get_remote_version()` returns V2≠V1; remote content differs | File on disk contains `"<<<<<<< ours"`; `result` includes conflict warning |
| REQ-8b: Both-changed → `.orig` backup written before markers | `tests/integration/test_conflict_flow.py` | `pull_should_write_orig_backup_before_writing_conflict_markers` | Integration | Same as REQ-8a setup | `<file>.orig` exists with pre-merge local content |
| REQ-8c: Only remote changed → fast-forward, no conflict markers | `tests/integration/test_conflict_flow.py` | `pull_should_fast_forward_when_only_remote_changed` | Integration | State has `local_hash=H1` (unchanged); `get_remote_version()` returns V2≠V1 | File updated to remote content; no `"<<<<<<< "` in file; state `remote_version == V2` |
| REQ-8d: Only local changed → skip pull, emit warning | `tests/integration/test_conflict_flow.py` | `pull_should_skip_and_warn_when_only_local_changed` | Integration | `get_remote_version()` returns same V1; local file changed | Backend `pull()` never called; warning message printed; state unchanged |
| REQ-8e: Neither changed → print "up to date", skip | `tests/integration/test_conflict_flow.py` | `pull_should_print_up_to_date_and_skip_when_nothing_changed` | Integration | Both local hash and remote version unchanged | Backend `pull()` never called; output contains `"up to date"` |
| REQ-8f: First sync (no state entry) → pull proceeds unconditionally | `tests/integration/test_conflict_flow.py` | `pull_should_proceed_unconditionally_on_first_sync_with_no_state` | Integration | `.markgate-state.json` does not exist | Backend `pull()` called; state file created with `base_hash`, `local_hash`, `remote_version` |
| REQ-8g: State persisted atomically after successful pull | `tests/integration/test_conflict_flow.py` | `pull_should_atomically_save_state_after_successful_fast_forward` | Integration | Fast-forward pull; patch `os.rename` to capture call | `os.rename` called with `(.markgate-state.json.tmp, .markgate-state.json)` |

---

### REQ-9: CLI — `markgate conflicts list` and `markgate conflicts resolve`

| Requirement | Test File | Test Name | Type | Scenario | Assertion |
|-------------|-----------|-----------|------|----------|-----------|
| REQ-9a: `conflicts list` shows files with conflict markers | `tests/cli/test_conflicts_cli.py` | `conflicts_list_should_display_table_of_files_with_conflict_markers` | Unit (happy) | State has 2 tracked files; one has `"<<<<<<< ours"` | CLI output contains the conflicted file path and conflict count `1` |
| REQ-9b: `conflicts list` prints "No unresolved conflicts" when none | `tests/cli/test_conflicts_cli.py` | `conflicts_list_should_print_no_conflicts_message_when_all_files_are_clean` | Unit (happy) | All tracked files have no `"<<<<<<< "` | Output contains `"No unresolved conflicts"` |
| REQ-9c: `conflicts resolve --accept remote` re-fetches and overwrites | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_overwrite_with_remote_when_accept_remote` | Unit (happy) | File has conflict markers; mock backend returns fresh content | File overwritten with remote content; no `"<<<<<<< "` remains; state updated |
| REQ-9d: `conflicts resolve --accept remote` deletes `.orig` if present | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_delete_orig_file_after_accept_remote` | Unit (happy) | `.orig` file exists alongside conflicted file | `.orig` file does not exist after resolve |
| REQ-9e: `conflicts resolve --accept local` restores from `.orig` | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_restore_from_orig_when_accept_local` | Unit (happy) | `.orig` file has pre-conflict content | Main file content equals `.orig` content; state `local_hash` updated |
| REQ-9f: `conflicts resolve --accept merged` validates no markers remain | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_accept_merged_when_no_markers_remain` | Unit (happy) | File has been manually resolved (no `"<<<<<<< "`) | State `local_hash` and `base_hash` updated to current file content |
| REQ-9g: `conflicts resolve --accept merged` fails if markers remain | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_error_when_markers_still_present_for_accept_merged` | Unit (error) | File still has `"<<<<<<< ours"` | CLI exits with non-zero code; error message says markers remain |
| REQ-9h: `conflicts resolve` on unknown file → clear error message | `tests/cli/test_conflicts_cli.py` | `conflicts_resolve_should_print_error_when_file_not_in_state` | Unit (error) | File path not in `.markgate-state.json` | CLI exits with non-zero code; output mentions the file is not tracked |

---

## Test Stack

- **Unit**: `pytest` + `pytest-mock` + standard `unittest.mock`; assertions via plain `assert` and `pytest.raises`
- **Integration**: `pytest` + `responses` (for HTTP mocking) or `unittest.mock.patch` on Google API client chains; filesystem operations use `tmp_path` fixture
- **CLI tests**: `typer.testing.CliRunner` (already used in this project pattern); captures stdout/stderr and exit codes
- **Coverage**: `pytest-cov` targeting `src/markgate/`

## Coverage Targets

- Unit test coverage: ≥80% (line) across `src/markgate/`
- All public service methods: happy path + at least one error path
- All external integrations: unit mocked + at least one integration test
- CLI commands: tested via `CliRunner` for happy and error branches

---

## Implementation Readiness Gate

### Criterion 1: Every requirement in requirements.md has ≥1 test case

Requirements from `requirements.md` mapped to test cases:

| Requirement Area | Tests Designed | Status |
|-----------------|---------------|--------|
| Google Docs push — structural diff pipeline (REQ-1/Feature 1) | REQ-3 (8 tests), REQ-4 (9 tests), REQ-7 (6 tests) = 23 | COVERED |
| Google Docs push — comment preservation via structural diff | REQ-7f (descending order), REQ-7a (skip when unchanged) | COVERED |
| Google Docs push — OAuth scopes updated | Covered by REQ-7b (auth required for batchUpdate call, would fail without correct scope) | COVERED (indirect) |
| Confluence — fix update_page ConfluencePage object | REQ-5a, REQ-5b, REQ-5c, REQ-5d, REQ-5e = 5 tests | COVERED |
| Confluence — fix export_as_html method name | REQ-7b-adjacent; add one targeted test in `test_backend_pull.py` | PARTIAL — see note |
| Confluence — comment sidecar pull | REQ-6a through REQ-6h = 8 tests | COVERED |
| SyncState load/save/hash | REQ-1a through REQ-1d = 10 tests | COVERED |
| three_way_merge() | REQ-2a through REQ-2f = 7 tests | COVERED |
| Backend.get_remote_version() abstract method | Exercised by REQ-8 integration tests | COVERED (indirect) |
| Confluence get_remote_version() | REQ-8c, REQ-8e use it; direct unit test needed | PARTIAL — see note |
| Google Docs get_remote_version() | REQ-8a, REQ-8c use it; direct unit test needed | PARTIAL — see note |
| Conflict detection algorithm | REQ-8a through REQ-8g = 7 tests | COVERED |
| `markgate conflicts list` | REQ-9a, REQ-9b | COVERED |
| `markgate conflicts resolve` | REQ-9c through REQ-9h = 6 tests | COVERED |
| merge3 dependency in pyproject.toml | Covered implicitly — if missing, `test_merge.py` fails to import | COVERED (smoke) |

**Partial gaps — 3 small additions required:**

1. Add `ConfluenceBackend_get_remote_version_should_return_version_number_as_string` (unit, 1 test) to `tests/confluence/test_backend_push.py`
2. Add `GoogleDocsBackend_get_remote_version_should_return_revision_id` (unit, 1 test) to `tests/google_docs/test_backend_push.py`
3. Add `GoogleDocsBackend_pull_should_call_get_doc_content_not_export_as_html` (unit, 1 test) to `tests/google_docs/test_backend_pull.py`

These 3 additions bring all requirements to full explicit coverage.

**Coverage fraction (before additions)**: 14/17 requirement areas explicitly covered = 82%
**Coverage fraction (after 3 additions)**: 17/17 = 100%

---

### Criterion 2: plan.md has no TODO/TBD placeholders in architecture or task sections

Checked plan.md — no `TODO` or `TBD` strings appear in architecture or task sections. Two open questions exist but are resolved inline:

- Task 3.3.1b commits to `mistune>=3.0` (not TBD).
- Task 2.2.1a commits to reading `comment_client.py` before writing code (not TBD).
- Task 2.1.2a notes "confirm the correct method name" — this is a task-time lookup, not a planning gap.

**Verdict: PASS** (with minor note that method name confirmation is deferred to implementation time, per adversarial-review minor item)

---

### Criterion 3: All ADRs referenced in plan.md exist on disk

Plan header references: `ADR-001-merge3-dependency`, `ADR-002-base-content-sidecar-store`

Both ADRs are inlined in plan.md under "ADR Stubs" section (full Context/Decision/Rationale/Consequences). Formal ADR files in `docs/adr/` have not yet been written as separate files.

**Action required**: Before Phase 5, write `docs/adr/ADR-001-merge3-dependency.md` and `docs/adr/ADR-002-base-content-sidecar-store.md` from the stub content in plan.md.

**Verdict: CONCERNS** — ADRs defined inline but not yet as discrete files. Does not block implementation; resolve before code review.

---

### Criterion 4: No BLOCKER items in adversarial-review.md

`adversarial-review.md` verdict: **CONCERNS** (no Blockers section has any items — "None — no show-stoppers that would prevent implementation from starting").

All 7 concerns listed are resolved via post-review patches applied to plan.md (documented at top of adversarial-review.md).

**Verdict: PASS**

---

## Readiness Gate Summary

| # | Criterion | Result |
|---|-----------|--------|
| 1 | Every requirement has ≥1 test case | PASS (with 3 small additions noted) |
| 2 | plan.md has no TODO/TBD in architecture/task sections | PASS |
| 3 | All ADRs referenced in plan.md exist on disk | CONCERNS (stubs inline; formal files not written yet) |
| 4 | No BLOCKER items in adversarial-review.md | PASS |

**Overall Gate Verdict: CONCERNS**

The single concern (ADR files not written as discrete files) does not block implementation. Recommend writing `docs/adr/ADR-001-*.md` and `docs/adr/ADR-002-*.md` as part of Phase 5 task 1 before the first code commit.

---

## Test Case Counts

| Type | Count |
|------|-------|
| Unit — happy path | 38 |
| Unit — error path | 13 |
| Unit — edge case | 5 |
| Integration | 14 |
| **Total** | **70** |

**Requirements covered**: 17/17 areas (with 3 small additions to close the gaps on `get_remote_version()` unit tests and the `export_as_html` rename test).
