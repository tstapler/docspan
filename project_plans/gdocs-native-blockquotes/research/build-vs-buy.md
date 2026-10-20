# Build vs. Buy: gdocs-native-blockquotes

## 1. Existing OSS library — markdown → Google Docs API converter

Searched PyPI/GitHub for "markdown to google docs api", "google docs api markdown converter python".

- **[MarkGDoc](https://github.com/awesomeadi00/MarkGDoc)** ([PyPI: `markgdoc`](https://pypi.org/project/markgdoc/), v1.1.1) — the only close match. A Python package that turns markdown syntax into Google Docs API `batchUpdate` requests (paragraphs, headings, lists, hyperlinks, text styling). It is a **one-way generator**, not a sync tool: no pull/HTML-export parsing, no diffing against a live document, and search did not surface a dedicated blockquote-to-`indentStart`/`borderLeft` function — its own docs enumerate paragraph/list/hyperlink helpers but not one for blockquotes. Maturity signals (contributor count, last commit date, license) were not resolvable from search results and would need a direct repo visit to confirm; on its face it looks like a small single-maintainer utility, not something with the round-trip guarantees docspan needs.
- Two Google Workspace **add-ons** (Markdown to Docs™, GdocifyMd) do markdown→Docs conversion including blockquotes, but they run inside Apps Script as a Doc add-on UI, not as a Python library callable from a CLI — wrong integration surface entirely.
- Nothing found solves "diff an edited markdown file against a previously-pushed Doc and emit minimal `batchUpdate` requests while preserving comment anchors" — that half of the problem (which is the hard half here) has no library candidates at all.

**Conclusion**: no viable library. MarkGDoc is the nearest thing and it only overlaps with the generation side (turning a markdown node into a paragraph-style request), which is the easy ~10% of this feature. It would need to be gutted and only its request-shape ideas borrowed, not adopted as a dependency.

## 2. SaaS/managed API

Not applicable — the Google Docs API itself *is* the external service docspan integrates with; there's no separate managed offering for "markdown-to-Docs-styling-as-a-service" to buy instead. Google Docs has no native blockquote paragraph style (confirmed in requirements.md and by the API's own `ParagraphBorder`/`indentStart` fields, which is why this feature exists at all) — so there's no vendor capability to swap in for the missing feature; it must be synthesized from primitives regardless of who writes the code.

## 3. LLM-generated implementation vs. battle-tested library — diffing pattern

The repo already leans on a well-known tested library for the core alignment problem: `docs_request_builder.py` uses `difflib.SequenceMatcher` (stdlib, battle-tested) to diff two node sequences by opcode (`equal`/`replace`/`insert`/`delete`), confirmed at [`docs_request_builder.py:113`](../../../src/docspan/backends/google_docs/docs_request_builder.py#L113). What's bespoke on top is `_node_key`/`_content_key` — two hashable-tuple projection functions that decide *what counts as "the same node"* for docspan's specific semantics (e.g., deliberately excluding an image's `src` because Drive re-upload URIs churn without content changing; including `render_prefix` so a code-rendered line never aligns with prose sharing its text). This is domain policy, not a generic data-structure problem — no off-the-shelf library encodes "which Google Docs paragraph-identity fields should participate in SequenceMatcher's alignment vs. only in restyle classification." The extensive docstrings at [`docs_request_builder.py:225-353`](../../../src/docspan/backends/google_docs/docs_request_builder.py#L225-L353) show this has already been tuned through real regressions (issue #54, issue #68).

**Conclusion**: keep using `difflib.SequenceMatcher` as the underlying algorithm (already done — don't reinvent alignment), and extend `_node_key`/`_content_key` by hand for `is_blockquote`/`quote_depth`, following the exact precedent the requirements already specify (participate in `_node_key`, excluded from `_content_key`, mirroring `render_prefix`/image `src`). No generic diff library would shorten this work; the value is in the key functions, which are inherently project-specific.

## 4. Fork or adapt — comparable markdown↔Google-Docs sync tools

Searched GitHub for "markdown to google docs", "google docs to markdown", "gdoc-down"-style tools:

- **[evbacher/gd2md-html](https://github.com/evbacher/gd2md-html)** ("Docs to Markdown" Workspace add-on) — actively maintained, handles Google Doc → Markdown/HTML including blockquotes. Google Apps Script (JS), not Python, and pull-only (no push/diff). Its blockquote detection is presumably the same `indentStart`+border heuristic docspan needs, but it's a one-directional read path with no comment-preservation or diffing concerns — the source logic wasn't inspectable via search (would need a repo clone to read `gd2md-html.js` directly), so applicability is "conceptual precedent only," not code to adapt.
- **[Mr0grog/google-docs-to-markdown](https://github.com/Mr0grog/google-docs-to-markdown)** — JS webapp using Remark/Rehype (unified ecosystem), pull-only, browser-oriented. Same limitation: wrong language, wrong direction, no diff/push story.
- **[AnandChowdhary/docs-markdown](https://github.com/AnandChowdhary/docs-markdown)** — Docs API response → Markdown, JS, pull-only.
- No project found that does bidirectional push+pull with a structural diff engine and comment preservation — docspan's actual hard problem appears to be a fairly unusual combination with no comparable open-source prior art to fork from. Every candidate found is either a one-shot generator (MarkGDoc) or a one-directional Docs→Markdown reader (gd2md-html, google-docs-to-markdown, docs-markdown), never both, and none are Python.

**Conclusion**: nothing to fork. At most, gd2md-html's public wiki/demo pages confirm that `indentStart`+left-border is the standard community heuristic for blockquote detection on the Docs side (independent validation of the approach in requirements.md), which is useful corroboration but not reusable code.

## Overall recommendation

**Build.** No existing library, add-on, or comparable OSS project covers docspan's actual requirement — bidirectional push/pull with structural diffing that preserves comment anchors across restyle-vs-rewrite decisions. The closest library (MarkGDoc) only overlaps with the trivial one-way "emit a paragraph-style request" half of the problem. The diffing core is already correctly built on `difflib.SequenceMatcher`; the only new work is domain-specific key-function extension (`_node_key`/`_content_key`), which is not something any general-purpose diff library could absorb. Use the community indentStart/borderLeft heuristic (independently corroborated by gd2md-html's approach) as the detection marker, as already planned in requirements.md.
