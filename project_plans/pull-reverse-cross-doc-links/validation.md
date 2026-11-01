# Validation: pull-reverse-cross-doc-links

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Tab-matching overly broad (matches wrong mapping) | Low | High | Match both `doc_id` AND `tab_id` exactly; separate test cases for tab/no-tab disambiguation |
| `posixpath.relpath` wrong for absolute paths | Low | High | Existing fixtures use absolute `tmp_path`; add explicit test |
| Drive-export path ordering conflict with `upgrade_heading_id_anchors` | Low | Low | Patterns are disjoint; document ordering in code comment |
| `pull_sectioned` uses wrong source path for relpath | Medium | High | Use section file path, not directory; explicit test covering multi-section pull |
| Backward compat broken when `mappings` absent | Low | High | `if mappings:` guard; dedicated regression test |
| Heading fetch fails silently (target doc deleted/moved) | Medium | Medium | Degrade to path-only (AC-13); log WARNING; test with mock raising exception |

## Acceptance Criteria Validation Matrix

| AC | Verified by | Test location |
|----|------------|--------------|
| AC-1: URL → path | `TestReverseDocLinkResolver.test_url_to_path_no_fragment` | `test_cross_doc_links.py` |
| AC-2: URL + tab → path | `test_url_with_tab_resolves_correctly` | `test_cross_doc_links.py` |
| AC-3: URL + heading → path#slug | `test_url_with_heading_resolves_to_slug` | `test_cross_doc_links.py` |
| AC-4: Unmapped Doc URL verbatim | `test_unmapped_doc_id_left_verbatim` | `test_cross_doc_links.py` |
| AC-5: Non-Doc URL verbatim | `test_non_doc_url_left_verbatim` | `test_cross_doc_links.py` |
| AC-6: Correct relative path | `test_relative_path_computed_correctly` | `test_cross_doc_links.py` |
| AC-7: Ambiguous mapping warning | `test_ambiguous_mapping_warns` | `test_cross_doc_links.py` |
| AC-8: Structural pull rewrites | `test_pull_rewrites_cross_doc_url_to_relative_path` | `test_google_docs_backend.py` |
| AC-9: Drive-export pull rewrites | `test_pull_default_path_rewrites_cross_doc_url` | `test_google_docs_backend.py` |
| AC-10: pull_sectioned rewrites | (future: test_gdocs_sectioned backend tests) | `test_google_docs_backend.py` |
| AC-11: mappings kwarg accepted | signature test + `test_pull_without_mappings_leaves_verbatim` | `test_google_docs_backend.py` |
| AC-12: heading fetch cached | `test_heading_fetch_called_once_per_target` | `test_cross_doc_links.py` |
| AC-13: Unknown heading id → path only | `test_unknown_heading_id_falls_back_to_path` | `test_cross_doc_links.py` |
| AC-14: Test coverage exists | All tests above | Both test files |

## Open Questions

1. **Orchestrator wiring**: Does `orchestrate_pull()` in `src/docspan/core/` (or wherever
   orchestration lives) need to be updated to pass `mappings` to `pull()`? The plan assumes
   it does — check the call site and add it there, otherwise the kwarg is accepted but never
   used in normal CLI flows.

2. **`pull_sectioned` per-section or shared resolver?** Plan says build resolver once and share
   across sections. Confirm that `source_local_path` for `posixpath.relpath` can be a per-section
   value while the heading cache is shared (it can — the resolver stores `source_local_path` and
   uses it only in `reverse()`, while the heading cache is keyed on `(doc_id, tab_id)`).

3. **Confluence backend**: Out of scope per requirements, but the Confluence pull path may have
   the same issue if it ever rewrites links to GDocs targets. Noted for follow-up.

## Definition of Done

- [ ] `parse_doc_url()` parses all four canonical URL forms correctly
- [ ] `ReverseDocLinkResolver.reverse()` satisfies AC-1 through AC-7 and AC-13
- [ ] `reverse_cross_doc_links()` regex matches `](URL)` patterns correctly and leaves all other
      markdown untouched
- [ ] `pull()` structural path applies reversal when `mappings` kwarg present
- [ ] `pull()` Drive-export path applies reversal when `mappings` kwarg present
- [ ] `pull_sectioned()` applies reversal per section
- [ ] Orchestrator call sites updated to pass `mappings`
- [ ] All tests pass (`uv run pytest tests/test_cross_doc_links.py tests/test_google_docs_backend.py`)
- [ ] No regression in existing cross-doc push tests
