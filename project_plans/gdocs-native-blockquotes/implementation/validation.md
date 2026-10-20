# Validation Plan: gdocs-native-blockquotes

**Date**: 2026-08-17

## Happy Path Scenario
Given the Baseline in requirements.md (a pushed `> note` line renders as literal `"> note"` text with no visual distinction), when the author pushes markdown containing `> This is a callout.` to a Google Doc and then pulls that same Doc back, then the Doc shows an indented, left-bordered paragraph with no literal `>` character, and the pulled markdown reconstructs `"> This is a callout."` byte-for-byte.

## Requirement → Test Mapping

All tests live in `tests/test_google_docs_backend.py` (this repo's single test file for the `google_docs` backend), following its existing test-function-per-scenario convention. `DocsParagraphNode`, `is_blockquote`, `quote_depth`, `BLOCKQUOTE_BORDER_MARKER`, `BLOCKQUOTE_INDENT_PT_PER_LEVEL` are the Domain Glossary terms from plan.md used below.

| Requirement | Test File | Test Name | Type | Scenario |
|-------------|-----------|-----------|------|----------|
| REQ (Epic 1/S1.1): `is_blockquote`/`quote_depth` fields + invariant | test_google_docs_backend.py | `DocsParagraphNode_should_AcceptBlockquoteFields_When_ConstructedWithValidPair` | Unit | Happy path — `is_blockquote=True, quote_depth=2` readable back |
| REQ (Epic 1/S1.1): illegal-state invariant | test_google_docs_backend.py | `DocsParagraphNode_should_RaiseValueError_When_ConstructedWithIllegalBlockquotePair` | Unit | Error path — both `(False, 2)` and `(True, 0)` raise `ValueError` |
| REQ (Epic 1/S1.1): marker constant single ownership | test_google_docs_backend.py | `DocsRequestBuilder_should_ImportSameMarkerObject_When_ComparedByIdentity` | Integration | `docs_request_builder.BLOCKQUOTE_BORDER_MARKER is docs_structure_parser.BLOCKQUOTE_BORDER_MARKER` |
| REQ (Epic 1/S1.2): `_node_key` includes blockquote identity | test_google_docs_backend.py | `_node_key_should_DifferByBlockquoteFields_When_TextIsIdentical` | Unit | Happy path — two same-text nodes, one blockquote, produce differing keys |
| REQ (Epic 1/S1.2): `_content_key` stays text-only | test_google_docs_backend.py | `_content_key_should_IgnoreBlockquoteFields_When_TextIsIdentical` | Unit | Error path (negative assertion) — keys equal despite differing blockquote fields, so pure restyle still folds to `equal` |
| REQ (Epic 1/S1.3): `_repair` cross-doc pooling doesn't cross-pair blockquote/non-blockquote | test_google_docs_backend.py | `_repair_should_NotCrossPairBlockquoteAndPlainParagraph_When_TextIsIdentical` | Integration | Two same-text paragraphs (one blockquote, one not) in one document; `_repair` must not misclassify either as a restyle target of the other |
| REQ (Epic 2/S2.1): `_walk_block_quote` drops text prefix, sets fields | test_google_docs_backend.py | `_walk_block_quote_should_SetBlockquoteFieldsWithoutPrefix_When_ParsingPlainQuote` | Unit | Happy path — `"> hello\n"` → `text=="hello"`, `is_blockquote=True`, `quote_depth=1` |
| REQ (Epic 2/S2.1): nested depth | test_google_docs_backend.py | `_walk_block_quote_should_SetDepthTwo_When_ParsingNestedQuote` | Unit | Happy path variant — `"> > nested\n"` → `quote_depth==2` |
| REQ (Epic 2/S2.1): empty quote line not dropped later | test_google_docs_backend.py | `_walk_block_quote_should_ProduceEmptyTextBlockquoteNode_When_ParsingBlankQuoteLine` | Unit | Edge/error path — `"> "` → `text==""`, `is_blockquote=True` (paired with REQ S2.5 projection test below) |
| REQ (Epic 2/S2.1): code-in-quote language marker threading | test_google_docs_backend.py | `_walk_block_quote_should_EmitLanguageMarker_When_QuoteContainsFencedCodeBlock` | Integration | Push→pull round trip — fenced ` ```python ` inside a quote keeps `lang=="python"` (regression for the `emit_language_marker=True` fix) |
| REQ (Epic 2/S2.1): pre-existing `- > note` bug not silently changed | test_google_docs_backend.py | `_walk_list_items_should_StillMisrenderBlockquoteChild_When_ListContainsQuote` | Unit | Error path (documents known-broken, non-regressed behavior) |
| REQ (Epic 2/S2.2): `_blockquote_paragraph_style_fields` happy path | test_google_docs_backend.py | `_blockquote_paragraph_style_fields_should_ReturnIndentAndBorder_When_NodeIsBlockquote` | Unit | Happy path — depth 2 → `indentStart == 2 * BLOCKQUOTE_INDENT_PT_PER_LEVEL`, `borderLeft == BLOCKQUOTE_BORDER_MARKER`, fields `["indentStart","borderLeft"]` |
| REQ (Epic 2/S2.2): non-blockquote no-op | test_google_docs_backend.py | `_blockquote_paragraph_style_fields_should_ReturnEmpty_When_NodeIsNotBlockquote` | Unit | Error/negative path |
| REQ (Epic 2/S2.3): insert path emits blockquote fields | test_google_docs_backend.py | `_build_insert_requests_should_IncludeBlockquoteFields_When_InsertingNewBlockquoteNode` | Unit | Happy path |
| REQ (Epic 2/S2.3): restyle path fires on pure style change | test_google_docs_backend.py | `_restyles_should_ReturnTrue_When_OnlyBlockquoteFieldsDiffer` | Unit | Happy path — text unchanged, blockquote flag flips |
| REQ (Epic 2/S2.3): restyle emits merged request | test_google_docs_backend.py | `_make_style_update_requests_should_MergeBlockquoteFields_When_RestylingToBlockquote` | Integration | Mocked `updateParagraphStyle` request assertion |
| REQ (Epic 2/S2.4): table-cell fill clears blockquote fields (contingent) | test_google_docs_backend.py | `_cell_fill_request_should_ClearBlockquoteFields_When_CellAdjacentToBlockquoteInheritsStyle` | Integration | Only if Epic 0 spike confirms inheritance; otherwise this test is a skip-tagged placeholder citing the spike fixture as evidence of "not applicable" |
| REQ (Epic 2/S2.5): blank blockquote line not dropped by projection | test_google_docs_backend.py | `projection_should_KeepEmptyBlockquoteParagraph_When_TextIsBlank` | Unit | Happy path |
| REQ (Epic 2/S2.5): ordinary blank paragraph still dropped | test_google_docs_backend.py | `projection_should_DropEmptyParagraph_When_TextIsBlankAndNotBlockquote` | Unit | Error/negative path — regression guard on existing behavior |
| REQ (Epic 2/S2.6): list-in-quote indent composition doesn't clobber | test_google_docs_backend.py | `_make_style_update_requests_should_ComposeListAndBlockquoteIndent_When_NodeIsBothListItemAndBlockquote` | Integration | Both Bullets-preset request and `indentStart`/`borderLeft` present, neither field overwritten |
| REQ (Epic 3/S3.1): marker detection happy path | test_google_docs_backend.py | `_parse_paragraph_should_SetBlockquoteFields_When_BorderMatchesMarker` | Unit | Happy path |
| REQ (Epic 3/S3.1): non-matching border is not a false positive | test_google_docs_backend.py | `_parse_paragraph_should_LeaveBlockquoteFalse_When_BorderDoesNotMatchMarker` | Unit | Error path |
| REQ (Epic 3/S3.1): sub-field-only comparison tolerates echoed defaults | test_google_docs_backend.py | `_detect_blockquote_depth_should_MatchOnSubfields_When_DocsEchoesExtraPaddingDefault` | Integration | Docs API JSON fixture with an extra `padding` sub-field docspan didn't write |
| REQ (Epic 3/S3.1b): legacy literal-text pull, no forced migration | test_google_docs_backend.py | `render_nodes_to_markdown_should_PreserveLiteralPrefix_When_ParagraphIsLegacyUnmigratedQuote` | Integration | Plain `paragraphStyle`, text `"> legacy note"` → unchanged output, `is_blockquote=False` |
| REQ (Epic 3/S3.1b): legacy nested quote passthrough | test_google_docs_backend.py | `render_nodes_to_markdown_should_PreserveLiteralPrefix_When_ParagraphIsLegacyNestedQuote` | Integration | `"> > legacy nested"`, still plain style |
| REQ (Epic 3/S3.2): `_group_blockquote_runs` groups contiguous quote nodes | test_google_docs_backend.py | `_group_blockquote_runs_should_GroupContiguousQuoteNodes_When_SequenceHasMixedNodes` | Unit | Happy path |
| REQ (Epic 3/S3.2): composes with `_group_code_runs` and preserves language | test_google_docs_backend.py | `_group_blockquote_runs_should_PreserveCodeLanguage_When_QuoteContainsFencedCodeBlock` | Integration | Requires Epic 2/S2.1's `emit_language_marker=True` fix; asserts `lang=="python"` not `None` |
| REQ (Epic 3/S3.3): `BlockquoteNodeRenderer` plain rendering | test_google_docs_backend.py | `BlockquoteNodeRenderer_should_PrefixEachLine_When_RenderingPlainQuoteRun` | Unit | Happy path — `"> first line\n> second line\n"` |
| REQ (Epic 3/S3.3): nested rendering depth 2 | test_google_docs_backend.py | `BlockquoteNodeRenderer_should_DoublePrefixLines_When_RenderingDepthTwoRun` | Unit | Happy path variant |
| REQ (Epic 3/S3.3): registry wiring / outer-stage call order | test_google_docs_backend.py | `render_nodes_to_markdown_should_CallGroupBlockquoteRunsAsOuterStage_When_SequenceHasMixedNodes` | Unit | Error path guard — asserts `_group_code_runs` is not called directly on raw sequence |
| REQ (Epic 3/S3.3): full round trip, all four cases | test_google_docs_backend.py | `push_pull_roundtrip_should_ReproduceByteIdenticalMarkdown_When_QuoteIsPlainNestedListOrCode` | Integration | Parametrized over plain/nested/list-in-quote/code-in-quote; code-in-quote asserts fence language tag survives |
| REQ (Epic 4/S4.1): `style_upgrade` literal added | test_google_docs_backend.py | `HighRiskParagraph_reasons_should_IncludeStyleUpgrade_When_LegacyBlockquoteRewrittenWithSameText` | Unit | Happy path |
| REQ (Epic 4/S4.1): no false positive on unrelated churn | test_google_docs_backend.py | `find_high_risk_paragraphs_should_NotTagStyleUpgrade_When_TextActuallyChanged` | Unit | Error path |
| REQ (Epic 4/S4.1): render wording distinguishes style upgrade | test_google_docs_backend.py | `render_high_risk_should_EmitDistinctWording_When_OnlyReasonIsStyleUpgrade` | Unit | Happy path |
| REQ (Epic 4/S4.1): summarization at scale | test_google_docs_backend.py | `render_high_risk_should_SummarizeCount_When_FiveOrMoreStyleUpgradeParagraphsPresent` | Integration | 5+ paragraphs collapse to one count line, not N blocks |
| REQ (Epic 4/S4.2): churn note uses style-upgrade wording | test_google_docs_backend.py | `render_churn_note_should_UseStyleUpgradeWording_When_PairReasonIsStyleUpgrade` | Unit | Happy path |
| REQ (Epic 4/S4.2): ordinary churn wording unchanged | test_google_docs_backend.py | `render_churn_note_should_UseGenericWording_When_PairHasNoStyleUpgradeReason` | Unit | Error path (regression guard) |
| REQ (Epic 5/S5.1): lint rule deleted | test_google_docs_backend.py (or tests/test_lint.py if separate) | `find_blockquote_issues_should_NotExist_When_ModuleImported` | Unit | Happy path — `hasattr` false / import fails as expected; existing blockquote-lint test cases removed |
| REQ (Epic 5/S5.2): style guide bullet removed | tests/test_style_guide.py (or equivalent) | `GOOGLE_DOCS_STYLE_GUIDE_should_NotMentionBlockquotes_When_Rendered` | Unit | Happy path — snapshot/golden-file updated, old bullet absent |
| REQ (Epic 0/S0.1): live-Doc spike is re-runnable, not just a one-time note | tests/test_google_docs_backend.py (skip-tagged) | `live_doc_spike_should_ReproduceRecordedBorderBehavior_When_RerunAgainstFixture` | Integration (manual/skip-tagged) | Replays committed request/response JSON fixture from the spike; marked `@pytest.mark.skip("requires live Docs API credentials")` for CI, runnable manually |

## UX Acceptance Tests
(design/ux.md defines two surfaces: the rendered Doc itself, and the `--dry-run` terminal warning. Neither is a browser UI — both are manual/CLI-output checks, per the task instructions' note that `ui-playwright` doesn't apply to this CLI tool.)

| UX Criterion | Test File | Test Name | Tool | Steps |
|---|---|---|---|---|
| 1. Reader identifies callout via indent alone, no color needed | manual | `manual_indent_visible_in_grayscale` | Manual | Push a plain-depth-1 quote; view the Doc in print-preview/grayscale; confirm the indent alone reads as "set apart" without relying on border color |
| 2. Left border contrast ≥3:1 against white | manual | `manual_border_contrast_check` | Manual | Sample `BLOCKQUOTE_BORDER_MARKER`'s color hex; run it through a contrast checker against `#FFFFFF`; confirm ≥3:1 |
| 3. Depth-2 quote visually distinguishable from depth-1 by indent | manual | `manual_nested_depth_indent_increase` | Manual | Push a `> outer` then `> > inner`; open the Doc; confirm inner line is visibly further indented than outer, independent of text content |
| 4. List inside quote: bullets visible, not clipped/overlapped | manual | `manual_list_in_quote_bullets_legible` | Manual | Push `> - item1\n> - item2`; open Doc; confirm bullet markers are visible and indented past the quote's own indent |
| 5. Code block inside quote keeps monospace styling, still inside border | manual | `manual_code_in_quote_visual_treatment` | Manual | Push a quote containing a fenced code block; open Doc; confirm monospace font unchanged and the quote's indent/border still wraps the code |
| 6. No dead end — doc explains the styling to a confused reader | manual | `manual_docs_limitations_section_explains_rendering` | Manual | Open `docs/backends/google-docs.md`'s Limitations section; confirm it describes native indent+border rendering and the legacy-migration caveat |
| 7. Legacy quote unchanged until re-pushed | manual | `manual_legacy_quote_unchanged_across_unrelated_push` | Manual | Push an unrelated edit to a Doc containing a legacy (literal `>`) quote; before/after screenshot comparison shows no change to the legacy quote |
| 8. Round-trip fidelity human-verifiable across all 4 cases | manual | `manual_pull_reproduces_byte_identical_source` | Manual | Push a file containing plain/nested/list-in-quote/code-in-quote quotes; pull it back; diff against the original source file, confirm zero diff |
| `--dry-run` names `style_upgrade` distinctly from generic churn | CLI output | `cli_dry_run_style_upgrade_wording_distinct` | Manual (`docspan push --dry-run`) | Run against a Doc with one legacy quote and one unrelated churned paragraph; confirm the two warnings read differently and the style-upgrade line states "comment on it is lost" in the same line |
| `--dry-run` unrelated churn wording unchanged | CLI output | `cli_dry_run_generic_churn_wording_unchanged` | Manual (`docspan push --dry-run`) | Confirm non-migration churn still shows pre-existing generic wording |
| No new flag/exit-code change | CLI output | `cli_dry_run_exit_code_unchanged` | Manual | Run `--dry-run` before/after this feature ships on an unrelated doc; confirm exit code and summary-count format unchanged |

## Test Stack
- **Unit**: pytest (existing repo convention — `tests/test_google_docs_backend.py`), plain `assert` statements, no separate assertion library.
- **Integration**: pytest with mocked Google Docs API responses (existing repo pattern — Docs API JSON fixtures passed to `_parse_paragraph`/request-builder functions; no live network calls in CI).
- **E2E / UX**: Manual checklist against a throwaway live Google Doc (per Epic 0's spike and design/ux.md's manual-verification criteria) — no browser automation applies since this is a CLI tool producing documents, not a web UI docspan itself renders.

## Coverage Targets and How to Measure

| Stack | Coverage command | Target |
|---|---|---|
| Python | `pytest --cov=src/docspan/backends/google_docs --cov=src/docspan/cli/lint.py --cov=src/docspan/style_guide.py tests/` | ≥80% line, 100% on new `_blockquote_*`/`_group_blockquote_runs`/`BlockquoteNodeRenderer`/`_detect_blockquote_depth` functions |

- All public service methods touched (`_walk_block_quote`, `_blockquote_paragraph_style_fields`, `_parse_paragraph`, `_group_blockquote_runs`, `BlockquoteNodeRenderer`, `find_high_risk_paragraphs`, `render_high_risk`, `render_churn_note`): happy path + error paths covered.
- All external integrations (Google Docs API request/response shapes): unit-tested with mocked JSON fixtures + the Epic 0 skip-tagged re-runnable integration test against a live Doc.
- UX acceptance criteria: all 8 criteria in design/ux.md §1.4 plus the 4 criteria in §Surface 2 each have a corresponding manual test above.

## Migration Plan Test
**N/A.** This project has no literal schema up/down migration — plan.md's Step 4 ("Migration Plan") describes a lazy, in-place re-push migration path (a legacy blockquote is only rewritten when its paragraph is next edited and pushed; no bulk/eager migration tool is in scope). The closest analogue is `push_pull_roundtrip_should_ReproduceByteIdenticalMarkdown_When_QuoteIsPlainNestedListOrCode` (new-scheme round trip) plus `render_nodes_to_markdown_should_PreserveLiteralPrefix_When_ParagraphIsLegacyUnmigratedQuote` (old-scheme passthrough, i.e. the "down"/pre-migration state stays valid) — both already listed above; no separate `migration_should_be_reversible` test is designed per Step 5's instruction.
