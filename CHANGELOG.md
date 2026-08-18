# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0](https://github.com/tstapler/docspan/compare/docspan-v0.5.0...docspan-v0.6.0) (2026-08-18)


### Features

* **google-docs:** add blockquote identity fields to paragraph diff model (Epic 1) ([2b31ddf](https://github.com/tstapler/docspan/commit/2b31ddfcd1d3aa62b170818ca8a5dd95f966df95))
* **google-docs:** cache rendered mermaid PNGs on disk ([#112](https://github.com/tstapler/docspan/issues/112)) ([8c13a03](https://github.com/tstapler/docspan/commit/8c13a039b309f52833294c034f1aebf16d016f74))
* **google-docs:** pull native blockquote styling back to markdown (Epic 3) ([20aabc9](https://github.com/tstapler/docspan/commit/20aabc9cd64fbef48180219bbf60334f853d0693))
* **google-docs:** push blockquotes as native indent/borderLeft styling (Epic 2) ([06881a5](https://github.com/tstapler/docspan/commit/06881a51909cc5bab09d5b61cf58046045262070))
* **google-docs:** warn and add CI signal for legacy blockquote style upgrades (Epic 4) ([984a2e8](https://github.com/tstapler/docspan/commit/984a2e8bc24e48372e321bbf53008079bcc58c5c))


### Bug Fixes

* **cli:** pass full mapping set to cross-doc link resolver on single-file push ([c86dadb](https://github.com/tstapler/docspan/commit/c86dadb532600fd1580e8556a7e0409dac3e44e2))
* **google-docs:** drop unresolvable new images instead of emitting an empty insertInlineImage uri ([#111](https://github.com/tstapler/docspan/issues/111)) ([bc1883d](https://github.com/tstapler/docspan/commit/bc1883dc995b7887f04c08e3d33ab226bf8649cd))
* **google-docs:** report unreadable bookmark/tab links on tab-scoped pull ([#107](https://github.com/tstapler/docspan/issues/107)) ([be048ca](https://github.com/tstapler/docspan/commit/be048ca00d5e4c40e5d9e617666517d87accb6ae))
* **google-docs:** tolerate 8-bit RGB quantization in blockquote border detection ([2898434](https://github.com/tstapler/docspan/commit/2898434b5edc2511e552d71ef01ea7393f22ab50))

## [Unreleased]

* **google-docs:** removed the `docspan lint`/style-guide warning against `>` blockquotes now that push emits native blockquote styling instead of literal `>`-prefixed text; if a rendering edge case still misrenders a quote post-push, spot it via `push --dry-run`'s structural diff or by visually inspecting the pushed Doc, since no automated check remains for it.

## [0.5.0](https://github.com/tstapler/docspan/compare/docspan-v0.4.0...docspan-v0.5.0) (2026-08-14)


### Features

* **google-docs:** sectioned sync for large document mappings ([#106](https://github.com/tstapler/docspan/issues/106)) ([fe12122](https://github.com/tstapler/docspan/commit/fe121229c8b6b957254020fd2c06241f7506aa80))


### Bug Fixes

* **ci:** dispatch PyPI publish from release-please via workflow_dispatch ([e76ffb8](https://github.com/tstapler/docspan/commit/e76ffb8547bfd7d2d2b453a64a5fb3899445fc5f))
* **confluence:** report internal anchors instead of writing a link to nowhere ([#105](https://github.com/tstapler/docspan/issues/105)) ([cd38f24](https://github.com/tstapler/docspan/commit/cd38f2415e37814e071a180182454d761cfcbfa9))
* **google-docs:** reset table cell paragraph style to NORMAL_TEXT on fill ([e6db797](https://github.com/tstapler/docspan/commit/e6db797d51746c738586c3cab171367e23bd5be0))

## [0.4.0](https://github.com/tstapler/docspan/compare/docspan-v0.3.0...docspan-v0.4.0) (2026-08-13)


### Features

* **docspan:** add docspan map command and push auto-create for unmapped files ([#80](https://github.com/tstapler/docspan/issues/80)) ([aa4258b](https://github.com/tstapler/docspan/commit/aa4258b3ff381c3e6afc389ad55c426abb5850ba))
* **google-docs:** add inline image push/pull support ([#101](https://github.com/tstapler/docspan/issues/101)) ([e5256af](https://github.com/tstapler/docspan/commit/e5256afa18076051b891f44511786e4f2750a489))
* **google-docs:** render mermaid diagrams as inline PNGs on push ([9298d1b](https://github.com/tstapler/docspan/commit/9298d1b0c645231056571a2c5afaa296e14d751b))


### Bug Fixes

* **config:** round-trip YAML comments on save_config writes ([780ddcc](https://github.com/tstapler/docspan/commit/780ddcc18b760f184627eb9c9a2b870249c6ed51))
* **confluence:** confirm mermaid render pipeline is a stub, not a rasterizer ([#94](https://github.com/tstapler/docspan/issues/94)) ([fb5ca80](https://github.com/tstapler/docspan/commit/fb5ca8002e91e050447766d7e498ff30eadae9dc))
* **google-docs:** bound difflib's cubic-ish blowup on duplicate-heavy documents ([#84](https://github.com/tstapler/docspan/issues/84)) ([834df71](https://github.com/tstapler/docspan/commit/834df71d2737b09ee68b14dd1f509bc3928249da))
* **google-docs:** distinguish delete-and-reinsert churn from real removal in push preview ([#86](https://github.com/tstapler/docspan/issues/86)) ([052b64d](https://github.com/tstapler/docspan/commit/052b64d591bc88ab6962e5173c2494b2fedab1c3))
* **google-docs:** escape backticks in monospace spans on both pull paths ([#103](https://github.com/tstapler/docspan/issues/103)) ([74d007d](https://github.com/tstapler/docspan/commit/74d007d162d511641650d2afb8b9f8b482c3e48e))
* **google-docs:** land PR [#70](https://github.com/tstapler/docspan/issues/70) restyle repair, verify AC0-8 (issue [#52](https://github.com/tstapler/docspan/issues/52)) ([#99](https://github.com/tstapler/docspan/issues/99)) ([8203c93](https://github.com/tstapler/docspan/commit/8203c93567a3d962bb3bb5754338298260f610aa))
* **google-docs:** order same-anchor insert groups after restyle/delete groups ([#83](https://github.com/tstapler/docspan/issues/83)) ([b8eace0](https://github.com/tstapler/docspan/commit/b8eace0505f7196eeb5bfe659f2d284a4b8c07ad))
* **google-docs:** render multi-paragraph table cells as HTML tables ([#79](https://github.com/tstapler/docspan/issues/79)) ([45e072d](https://github.com/tstapler/docspan/commit/45e072de2ae845443d6b5862a6c70234a55fb501))
* **google-docs:** resolve cross-tab heading anchors on push ([#102](https://github.com/tstapler/docspan/issues/102)) ([c1be541](https://github.com/tstapler/docspan/commit/c1be541d7e8d227369367ac8d99700405ac31195))
* **google-docs:** resolve relative cross-document markdown links to target Google Doc URLs ([#98](https://github.com/tstapler/docspan/issues/98)) ([27e7f15](https://github.com/tstapler/docspan/commit/27e7f1551549d7bff9d64b5e43abfd09f4f19e36))
* **google-docs:** richer at-risk-comment warning, no anchor migration ([#92](https://github.com/tstapler/docspan/issues/92)) ([#95](https://github.com/tstapler/docspan/issues/95)) ([be854db](https://github.com/tstapler/docspan/commit/be854db8cc4dd5a8252685847455e51d444decee))
* **google-docs:** split/preserve fenced code blocks in list items and blockquotes ([#87](https://github.com/tstapler/docspan/issues/87)) ([0a01f9f](https://github.com/tstapler/docspan/commit/0a01f9f6c927489c9234049aed56b4fe8c895b58))
* **google-docs:** stop force-push from corrupting tab-scoped checkbox docs ([#97](https://github.com/tstapler/docspan/issues/97)) ([63ab43b](https://github.com/tstapler/docspan/commit/63ab43bc755dad67ce22d516b6ea4f1eaeba9c3e))
* **google-docs:** stop replace branch from duplicating the doc-end-clamped newline ([#85](https://github.com/tstapler/docspan/issues/85)) ([68f0de9](https://github.com/tstapler/docspan/commit/68f0de985be5b13df0d4e1b3c4b0177a2e0eced8))

## [0.3.0](https://github.com/tstapler/docspan/compare/docspan-v0.2.0...docspan-v0.3.0) (2026-08-11)


### Features

* **gdocs:** keep inline styling inside table cells ([#51](https://github.com/tstapler/docspan/issues/51)) ([880eeb1](https://github.com/tstapler/docspan/commit/880eeb1feb3a3f700a1641f360abf5bc32cab191)), closes [#49](https://github.com/tstapler/docspan/issues/49)
* **google-docs:** add Google Doc tab support (tab_id) ([#14](https://github.com/tstapler/docspan/issues/14)) ([cee1db6](https://github.com/tstapler/docspan/commit/cee1db6dde0c957088159339527a6b8dbef70b47))
* **google-docs:** resolve internal markdown anchors to heading links ([#36](https://github.com/tstapler/docspan/issues/36)) ([5faaa6c](https://github.com/tstapler/docspan/commit/5faaa6c7ded880eba048d20807ff76a4a5f480a8))
* **google-docs:** restyle a paragraph in place instead of retyping it ([edf0b13](https://github.com/tstapler/docspan/commit/edf0b13520aa1a60b1fb02ee51dc1bd4a967c412))


### Bug Fixes

* **cli:** accept --config and --prefix before the subcommand as well as after ([00f6524](https://github.com/tstapler/docspan/commit/00f65241e41c5bb2deec5642eb36e8ea64a58744))
* **gdocs:** stop a repeated line stealing a live heading's identity ([#50](https://github.com/tstapler/docspan/issues/50)) ([2992d39](https://github.com/tstapler/docspan/commit/2992d3974dc9f7dea2547b5b8b3881f7c141f810))
* **google-docs:** align pass 2 by content so trimmed deletes can't mis-style ([ba56e08](https://github.com/tstapler/docspan/commit/ba56e08b7cc482c6bd569e9e5560e1c71a768ebf))
* **google-docs:** append past the last node without merging it into the last paragraph ([1aef861](https://github.com/tstapler/docspan/commit/1aef861981ee95463e7c2c9caa67bf32d90c9d2a))
* **google-docs:** apply inline styling when it is the only change ([c205552](https://github.com/tstapler/docspan/commit/c205552aed5c0f483ba5213bd39f2aff16587efe))
* **google-docs:** clear the inherited bullet on inserted non-list paragraphs ([dda8a50](https://github.com/tstapler/docspan/commit/dda8a505e8f822c077478656158b87cf6d74132b))
* **google-docs:** insert before a Table/ToC/SectionBreak into the body, not into it ([701e2d0](https://github.com/tstapler/docspan/commit/701e2d01433e9efc2c42a159ffb4f92d90972c56))
* **google-docs:** keep the paragraph newline out of the spans ([#35](https://github.com/tstapler/docspan/issues/35)) ([f73272a](https://github.com/tstapler/docspan/commit/f73272a0422e5c525a63bcedb6cb68fdb7ff517b))
* **google-docs:** make push idempotent for documents with fenced code blocks ([#41](https://github.com/tstapler/docspan/issues/41)) ([e98406e](https://github.com/tstapler/docspan/commit/e98406e2858958421c3bdf5e17c94f880056e540))
* **google-docs:** make render_prefix part of node/content identity ([#67](https://github.com/tstapler/docspan/issues/67)) ([6205850](https://github.com/tstapler/docspan/commit/62058503c38372dec7de64e4a8ea1b6603c7c2fc))
* **google-docs:** normalize away the render glyph Docs writes before a native code block ([#48](https://github.com/tstapler/docspan/issues/48)) ([31b4edd](https://github.com/tstapler/docspan/commit/31b4edd1bc944f1b48605d3a1ffe0d2283e161fe))
* **google-docs:** order pass-1 requests by their anchor, not their own index ([58e2b6a](https://github.com/tstapler/docspan/commit/58e2b6af2271dc200c225c39c44d1a567ae5722a))
* **google-docs:** pair Nth live table with Nth target regardless of emptiness ([#77](https://github.com/tstapler/docspan/issues/77)) ([46f96b6](https://github.com/tstapler/docspan/commit/46f96b6982bfade9656c09ea543bcdf9b692e876))
* **google-docs:** prefer a code-rendered node over a duplicate-text prose node ([#75](https://github.com/tstapler/docspan/issues/75)) ([5853566](https://github.com/tstapler/docspan/commit/5853566d4aaa3f57755373bccdaedde42709e9c5))
* **google-docs:** project empty paragraphs out of the diff instead of deleting them ([d8b1b5f](https://github.com/tstapler/docspan/commit/d8b1b5feb56439da34ae7be64c092b80927c30a7))
* **google-docs:** project the re-fetched live doc before pass-2 style alignment ([#69](https://github.com/tstapler/docspan/issues/69)) ([cf36561](https://github.com/tstapler/docspan/commit/cf36561b953e7d2c2da4c11f60c2a74bca3a2259))
* **google-docs:** recover native checkbox checked state on pull via markdown export ([#78](https://github.com/tstapler/docspan/issues/78)) ([c7352f7](https://github.com/tstapler/docspan/commit/c7352f79d38c19c092ade81cf1368dc3c72ad853))
* **google-docs:** render @-mention person chips as name/email text ([#15](https://github.com/tstapler/docspan/issues/15)) ([f5d7427](https://github.com/tstapler/docspan/commit/f5d742758855aeab435c50fba3a1cb51b9a79eef))
* **google-docs:** render fenced code blocks on tab-scoped pull instead of per-line inline code ([#74](https://github.com/tstapler/docspan/issues/74)) ([3bae98a](https://github.com/tstapler/docspan/commit/3bae98ac01007ca33288c6d87b2bf8288291c216))
* **google-docs:** render TITLE/SUBTITLE as headings instead of silently demoting them ([cc6cd0b](https://github.com/tstapler/docspan/commit/cc6cd0b6e8fdb8337cfa871f77c4466445041922))
* **google-docs:** report a dropped over-long span, and share the delete-trim arithmetic ([daa77a6](https://github.com/tstapler/docspan/commit/daa77a6398f9ed967deef1376b39d73e66050aea))
* **google-docs:** stop deleting the newline that anchors a table/ToC/section break ([9eba496](https://github.com/tstapler/docspan/commit/9eba496ee1d4db3fb788e910c359d9487f217baa))
* **google-docs:** stop replace inserts from duplicating a clamp-spared newline ([#76](https://github.com/tstapler/docspan/issues/76)) ([f440f68](https://github.com/tstapler/docspan/commit/f440f68b3fcd9fce0333dfb95b60a40e5638c7e3))

## [0.2.0](https://github.com/tstapler/docspan/compare/docspan-v0.1.0...docspan-v0.2.0) (2026-07-22)


### Features

* Add Railway Volume support for persistent state storage ([3a4b76e](https://github.com/tstapler/docspan/commit/3a4b76eb0cbc14afa02f3aa3de2c4607808fad9f))
* Add retry mechanism and improved error handling for Google Drive API ([e8a7b5f](https://github.com/tstapler/docspan/commit/e8a7b5f177ad2c3b8a356852eceb827326f8ce76))
* Auto-reload Google Sheet mappings on each sync cycle ([a2647c6](https://github.com/tstapler/docspan/commit/a2647c6c1435eb5624a389a4e673a57ead012123))
* **config:** XDG storage paths + central config with project prefixes ([#7](https://github.com/tstapler/docspan/issues/7)) ([0aa9165](https://github.com/tstapler/docspan/commit/0aa9165d24df95386b514d283cf846a2cdc809f7))
* **confluence:** port adf/markdown/services from markdown-confluence ([e9d1a85](https://github.com/tstapler/docspan/commit/e9d1a85a9747ac75a6d95d6351d18483297726a4))
* **google_docs:** checklist round-trip + comment/glyph-risk push gate ([#8](https://github.com/tstapler/docspan/issues/8)) ([bd2a885](https://github.com/tstapler/docspan/commit/bd2a885d6a2e18b758f12fc4a2aaf588c045d059))
* **google_docs:** docspan comments respond — reply/resolve round-trip ([#13](https://github.com/tstapler/docspan/issues/13)) ([7a662ed](https://github.com/tstapler/docspan/commit/7a662edb14b6f0078fcca110023cfc7e54724726))
* **google-docs:** add per-user OAuth auth option ([#4](https://github.com/tstapler/docspan/issues/4)) ([830369c](https://github.com/tstapler/docspan/commit/830369cba1817224ae0d02f0b14b6a84de84a4eb))
* **google-docs:** push markdown tables and inline links/formatting ([#3](https://github.com/tstapler/docspan/issues/3)) ([5b74246](https://github.com/tstapler/docspan/commit/5b74246eb1070355b39c5e84292e59f443457875))
* **google-docs:** read comments into a {file}.comments.md sidecar on pull ([#5](https://github.com/tstapler/docspan/issues/5)) ([aca2264](https://github.com/tstapler/docspan/commit/aca226412ef74c80603678d7ae1defaa25e38954))
* scaffold markgate package from google-docs-obsidian-sync fork ([44dd3c5](https://github.com/tstapler/docspan/commit/44dd3c586a670b4689154b2db9bc6cb8673d9702))
* **sync:** Google Docs structural-diff push, Confluence comments, three-way merge ([9a20e34](https://github.com/tstapler/docspan/commit/9a20e3452f2d92a240128d7c8e2f9c4b63a547f9))


### Bug Fixes

* **ci:** add __future__ annotations for Python 3.9 compat in test ([9ceca65](https://github.com/tstapler/docspan/commit/9ceca65ca5ad2040c1c2ec215fc097b02ba1a0c4))
* **ci:** apply ruff autofix across all src and test files ([bee2784](https://github.com/tstapler/docspan/commit/bee2784b940d872045ce33faf0ce53f65150d80d))
* **ci:** resolve ruff lint failures and enable Actions PR creation ([8727e7b](https://github.com/tstapler/docspan/commit/8727e7bcabf1ddec4b4116b06e605ff24d0eaffe))
* **google-docs:** don't drop blockquote paragraphs on push ([#9](https://github.com/tstapler/docspan/issues/9)) ([e3b2597](https://github.com/tstapler/docspan/commit/e3b259799acfcdaa7f884edb49633f349860a978))
* **google-docs:** fix inline-style paragraph misalignment on push ([#10](https://github.com/tstapler/docspan/issues/10)) ([4f79ef8](https://github.com/tstapler/docspan/commit/4f79ef8b4669eee4d6fe361af5bae68bb4486019))
* **google-docs:** fix mid-document insert off-by-one causing paragraph merges ([#12](https://github.com/tstapler/docspan/issues/12)) ([c74bea2](https://github.com/tstapler/docspan/commit/c74bea2d6464df0c19abce31a39aea0bc18d1e46))
* **google-docs:** restore inline styling and unwrap redirect links on pull ([#11](https://github.com/tstapler/docspan/issues/11)) ([b90466c](https://github.com/tstapler/docspan/commit/b90466cd9765a290b02634bbe9b3869185e308bc))
* Improve nested list indentation in Google Docs to Markdown conversion ([d6a7539](https://github.com/tstapler/docspan/commit/d6a7539d4beade3426e5d0db838f1d9974f7294b))
* Remove CONFIG_YAML dependency, prefer individual env vars ([d5d5d4a](https://github.com/tstapler/docspan/commit/d5d5d4ae25a7c1d7a213071f968eba56a9289da3))
* Resolve service account storage quota error by storing sync state locally ([00e9cb6](https://github.com/tstapler/docspan/commit/00e9cb65033dfb6cca8e0aae2258cde458cfb342))

## [Unreleased]

### Added
- **google-docs:** internal markdown anchors (`[A1](#a1-current-state)`) now resolve to
  Google Docs heading links instead of being written as a `#fragment` URL the Doc cannot
  follow. Slugs follow `github-slugger`, checked against vectors generated from the real
  implementation. `TITLE`/`SUBTITLE` paragraphs count as anchor targets, and both the
  modern `Link.heading` and the legacy `Link.headingId` union members are read, so an
  anchor survives a pull whether or not the fetch used `includeTabsContent`.
- **google-docs:** an anchor that names no heading is written as plain text with no link
  and reported — `docspan push --dry-run` lists it, `docspan push` exits non-zero with a
  warning naming the anchor and the heading anchors that *are* available. It is never
  written as a link a reader can click and land nowhere, and never reported as a clean ✓.
- **google-docs:** both pull paths now emit the heading's slug. A default (no `tab_id`) pull
  goes through Drive's HTML export, which carries the Doc's opaque `#h.abc123` through
  verbatim; it is upgraded to the slug, so the pulled markdown works as markdown.
- **google-docs:** a Markdown `> ...` blockquote now pushes as a native indented,
  left-bordered paragraph (`indentStart`/`borderLeft`) instead of literal `>` text, and
  pulling it back reconstructs the `> ` prefix from that styling, byte-for-byte round trip
  for plain, nested, list-in-quote, and code-fence-in-quote quotes. A Doc still carrying a
  pre-migration literal-`>` blockquote pulls unchanged and is migrated to the native styling
  the next time its file is pushed for any reason — a one-time rewrite that, like any other
  paragraph rewrite, drops comments anchored to it (see the comments-destroyed limitation
  below).

### Changed
- **google-docs:** pass 2 parses and aligns the document once per push instead of three
  times. The discarded work sat inside the window between pass 2's read and its write, where
  a concurrent edit costs a conflict on a document pass 1 has already changed.

### Known limitations
Each of these is tracked as a follow-up rather than half-addressed here.
- An anchor into a heading in a *different tab* of the same document cannot be resolved and
  is reported unresolved. The flat `headingId` member resolves against the tab named in the
  request, so expressing one needs the tabs-aware `Link.heading` member.
- A pull cannot express a `bookmark`/`bookmarkId` link, a link to a tab, or any link inside
  a table cell, so those are dropped from the pulled file without a report.
- Confluence writes an internal anchor as a literal `#fragment` href, which it does not
  resolve. `push` now reports this as a warning naming the anchor(s) instead of shipping it
  silently; the href itself is unchanged, since no live instance was available to establish
  what Confluence actually generates for a heading.
- An anchor that resolves to nothing is written as plain text, so a later pull replaces the
  author's `[text](#anchor)` with `text`. The push reports it; nothing does afterwards.
- Such a push exits non-zero on every run, with no flag to suppress it.
- A heading containing an HTML entity reference (`## Team &amp; process`) or inline HTML
  (`## <code>push()</code> …`) is slugged from the markdown *source* rather than the rendered
  text, so its slug differs from GitHub's. Because duplicate numbering depends on the
  headings before it, that can land an anchor on a neighbouring heading. Pre-existing; a fix
  attempt was reverted on this branch because it needs the slug text and the
  document text separated, which is its own change.

## [0.1.0] - 2026-06-07

### Added
- `docspan push` — push local markdown files to Google Docs or Confluence
- `docspan pull` — pull remote documents into local markdown files with three-way merge
- `docspan status` — show current mapping status in a table
- `docspan auth setup` — interactive authentication setup for `google_docs` and `confluence` backends
- `docspan conflicts list` — list files with unresolved merge conflicts
- `docspan conflicts resolve` — resolve merge conflicts with `remote`, `local`, or `merged` strategy
- Google Docs backend: push and pull via Google Docs API (service account auth)
- Confluence backend: push and pull via Atlassian REST API (API token auth)
- Three-way merge for bidirectional sync conflict detection
- Confluence comment sidecar: pull writes inline and footer comments to `{file}.comments.md`
- `markgate.yaml` config file format with per-mapping direction control (`push`/`pull`/`both`)
- Sync state tracking via `.markgate-state.json` and content-addressed base store in `.markgate-base/`

### Known Limitations
- Google Docs: comments on edited paragraphs are destroyed on push (paragraph-level diff; comments on unchanged paragraphs are preserved)
- Push: no image support — local image files cannot be pushed to Google Docs or Confluence
- Push: no table support — markdown tables are not rendered in Google Docs
- Confluence: requires an Atlassian API token; no OAuth flow
- Confluence: comment sidecar (`{file}.comments.md`) is informational only; comments cannot be pushed back
- Config file is named `markgate.yaml` (not `docspan.yaml`) and state file is `.markgate-state.json` (not `.docspan-state.json`). These will be renamed in v0.2.0.

[Unreleased]: https://github.com/tstapler/docspan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tstapler/docspan/releases/tag/v0.1.0
