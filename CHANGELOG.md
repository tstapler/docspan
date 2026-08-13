# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  resolve.
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
