"""Cross-document markdown links (`[link](../other-doc/README.md#heading)`) as
Google Docs URLs.

Companion to `heading_anchors.py`, which resolves same-document `#fragment`
anchors. That module cannot help with a *relative path* href — `is_anchor`
is False for it, so `link_payload` falls through to `{"url": href}` and a
Google Doc (or a browser) renders the literal, scheme-less path, auto-prefixed
with `http://`. This module gives the pass-2 request builder a second chance
at exactly that fallback: before writing a bare `url` link, ask whether the
href names a file in `markgate.yaml`'s mapping table, and if so, resolve it to
that target's Google Doc instead.

Three pieces:

* `parse_cross_doc_href` — is this href even shaped like a relative link to
  another markdown file, and if so, what path (resolved against the pushing
  file's directory) and optional fragment does it name?
* `resolve_local_mapping` — does that path match exactly one entry in the
  mapping table?
* `CrossDocLinkResolver` — stateful, constructed once per CLI invocation
  (across every mapping in one `push`/`push --all` run) so that N links to the
  same target document cost exactly one fetch, not N.

Out of scope for v1, each because the mapping table cannot yet express it:
a cross-doc link whose target is a *different* backend (e.g. source
`google_docs`, target `confluence`) is reported unresolved rather than
attempted; a fragment is matched against the target's headings with the same
percent-decode-then-exact-match rule as `heading_anchors.resolve_anchor` (no
Unicode normalization — see that module for why).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from docspan.backends.google_docs.heading_anchors import is_anchor, resolve_anchor

# A markdown href is "shaped like" an absolute/external link, not a relative
# path to another file, when it has a URI scheme (`http:`, `https:`,
# `mailto:`, ...) or starts with `//` (protocol-relative). RFC 3986's scheme
# grammar is `ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )` followed by ":".
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def parse_cross_doc_href(href: Optional[str], source_path: str) -> Optional[Tuple[str, Optional[str]]]:
    """Split `href` into (resolved_path, fragment) if it is a relative link to
    another markdown file, else None.

    None (not two, "not a cross-doc link") is returned for:
    * a bare same-document anchor (`#fragment` — `heading_anchors` already
      handles this);
    * an absolute or scheme-qualified URL (`https://...`, `mailto:...`) or a
      protocol-relative one (`//...`);
    * a target that is not a `.md` file — an image or other asset can never be
      a cross-doc link even if its literal path happens to collide with a
      mapping's `local` entry.

    `resolved_path` is `os.path.normpath`'d against `source_path`'s directory,
    so `../other-doc/README.md` from `docs/a/b.md` resolves to
    `docs/other-doc/README.md`. Case is preserved — matching is case-sensitive,
    independent of the host filesystem (see `resolve_local_mapping`).
    """
    if not href or is_anchor(href) or href == "#":
        return None
    if _SCHEME_RE.match(href) or href.startswith("//"):
        return None

    if "#" in href:
        path_part, fragment = href.split("#", 1)
    else:
        path_part, fragment = href, None

    if not path_part or not path_part.lower().endswith(".md"):
        return None

    source_dir = os.path.dirname(source_path)
    resolved = os.path.normpath(os.path.join(source_dir, path_part))
    return resolved, (fragment or None)


class AmbiguousMappingError(Exception):
    """Two or more mapping entries normalize to the same local path.

    Raised loudly rather than silently picking one — mirrors the philosophy
    `heading_anchors.py` documents for anchor resolution. This is a push-time
    check against the mappings a link actually resolves to, not a startup-time
    validation of the whole config: `config.load_config()` does not dedup
    mappings, and most configs never hit this because their `local` paths are
    already distinct strings.
    """

    def __init__(self, normalized_path: str, mappings: List[object]) -> None:
        remote_ids = ", ".join(f"{m.local!r} -> {m.remote_id!r}" for m in mappings)
        super().__init__(
            f"Ambiguous cross-doc link target: {len(mappings)} mappings normalize to "
            f"'{normalized_path}': {remote_ids}"
        )
        self.normalized_path = normalized_path
        self.mappings = mappings


def resolve_local_mapping(resolved_path: str, mappings: List[object]) -> Optional[object]:
    """The single `Mapping` whose `local` normalizes to `resolved_path`, or None.

    Comparison is `os.path.normpath` only — deliberately not
    `os.path.samefile`/case-folding, so this behaves identically regardless of
    the host filesystem's case sensitivity and does not require the files to
    exist on disk.

    Zero matches is the common, unremarkable case (a link to a file this
    project does not sync) and returns None, not an error. Two or more
    *different* mapping entries normalizing to the same path raises
    `AmbiguousMappingError` instead of silently choosing the first.
    """
    normalized_target = os.path.normpath(resolved_path)
    matches = [m for m in mappings if os.path.normpath(m.local) == normalized_target]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    raise AmbiguousMappingError(normalized_target, matches)


@dataclass(frozen=True)
class CrossDocResolution:
    """What `CrossDocLinkResolver.resolve()` decided about one href.

    Exactly one of the following describes the result:

    * `untouched` — not a cross-doc link (didn't parse as one) or names no
      mapped file. The caller must leave `href` exactly as today:
      `{"url": href}`, no report entry.
    * `same_doc_fragment` is not None — the href is a *self-reference with a
      fragment*: it resolves to the document currently being pushed, and
      names a heading in it. The caller should resolve `same_doc_fragment`
      against its own document's `slug_to_id`/`known_ids` (via
      `heading_anchors.resolve_anchor`) exactly as it would a `#fragment`
      anchor, rather than fetching anything. A self-reference with *no*
      fragment needs no such routing — it resolves to the doc's own edit URL
      via `payload` below, which costs no fetch either way.
    * `payload` is not None — resolved: this dict is the Docs `Link` payload
      to write (`{"url": "https://docs.google.com/document/d/<id>/edit"}`,
      optionally with a `#heading=<id>` fragment).
    * `unresolved` is True — mapped, but the link cannot be satisfied (no
      fragment match in the target's live headings, the target fetch failed,
      or the target uses an unsupported backend). `reason` is a human-readable
      explanation. The caller must write no link (mirroring a dead same-doc
      anchor) and report it via the same unresolved-anchor list.
    """

    untouched: bool = False
    same_doc_fragment: Optional[str] = None
    payload: Optional[dict] = None
    unresolved: bool = False
    reason: Optional[str] = None


# What `fetch_headings` returns for one target document: slug -> headingId
# (heading_anchors.heading_slug_to_id's shape) and the set of every headingId
# the document reports, whether or not it has a slug — resolve_anchor needs
# both.
FetchedHeadings = Tuple[Dict[str, str], Set[str]]


class CrossDocLinkResolver:
    """Resolves relative markdown hrefs to other mapped documents.

    Constructed once per CLI invocation (`push`/`push --all`) and shared
    across every mapping pushed in that run — its `_cache` is what makes N
    links to the same target document cost one fetch rather than N, and what
    makes a whole `push --all` run's fetch count bound by the number of
    *distinct* target documents referenced, not by the number of
    mapping/link pairs.

    `fetch_headings` is injected rather than baked in so this module stays
    network-free and unit-testable: the real callback (built in
    `backend.py`, where a live `GoogleDocsClient` exists) fetches the target
    document and returns its live headings — deliberately the *document's*
    current headings, never the target mapping's own local markdown, since a
    fragment must resolve against what the target push actually wrote, which
    can differ from what is in that file right now.

    `fetch_headings` may be supplied after construction via
    `bind_fetch_headings`, since the CLI layer (backend-agnostic, one
    resolver per invocation) does not itself hold a `GoogleDocsClient` — only
    a `GoogleDocsBackend` does, and a fresh backend instance is constructed
    per mapping iteration by `cli/main.py`'s `_get_backend`. The first
    `google_docs` mapping in a run binds the callback; later bindings within
    the same run are no-ops, since every `google_docs` mapping in one
    `markgate.yaml` shares the same credentials.
    """

    def __init__(
        self,
        mappings: List[object],
        fetch_headings: Optional[Callable[[object], FetchedHeadings]] = None,
    ) -> None:
        self.mappings = mappings
        self._fetch_headings = fetch_headings
        # (remote_id, tab_id) -> ("ok", FetchedHeadings) | ("error", str).
        # Caching the *failure* too, not just successes, so a target document
        # that 403s costs one failed fetch per push, not one per link.
        self._cache: Dict[Tuple[str, Optional[str]], Tuple[str, object]] = {}

    def bind_fetch_headings(self, fetch_headings: Callable[[object], FetchedHeadings]) -> None:
        """Supply the network callback, if none was given at construction.

        A no-op once bound — see the class docstring for why binding more
        than once is expected, not an error.
        """
        if self._fetch_headings is None:
            self._fetch_headings = fetch_headings

    def resolve(
        self,
        href: Optional[str],
        source_path: str,
        current_doc_id: str,
        current_tab_id: Optional[str] = None,
    ) -> CrossDocResolution:
        """Resolve `href` (from a markdown file at `source_path`) against the
        mapping table, in the context of the document currently being pushed
        (`current_doc_id`/`current_tab_id`, for self-reference detection).
        """
        parsed = parse_cross_doc_href(href, source_path)
        if parsed is None:
            return CrossDocResolution(untouched=True)
        resolved_path, fragment = parsed

        match = resolve_local_mapping(resolved_path, self.mappings)
        if match is None:
            return CrossDocResolution(untouched=True)

        if match.backend != "google_docs":
            return CrossDocResolution(
                unresolved=True,
                reason=(
                    f"cross-doc link target '{match.local}' uses backend "
                    f"'{match.backend}', which is not supported yet"
                ),
            )

        base_url = f"https://docs.google.com/document/d/{match.remote_id}/edit"
        # A `tab_id`-scoped target must link into that tab specifically, not
        # the document's default tab — otherwise the link (and any heading
        # fragment on it, since a headingId resolves against the tab named in
        # the request per `heading_anchors.py`) lands in the wrong place.
        if match.tab_id:
            base_url += f"?tab={match.tab_id}"

        if not fragment:
            # Resolves to the doc's own edit URL whether or not this is a
            # self-reference — no fetch needed either way.
            return CrossDocResolution(payload={"url": base_url})

        is_self_reference = match.remote_id == current_doc_id and (
            (match.tab_id or None) == (current_tab_id or None)
        )
        if is_self_reference:
            return CrossDocResolution(same_doc_fragment=fragment)

        slug_to_id, known_ids = self._fetch(match)
        if slug_to_id is None:
            return CrossDocResolution(unresolved=True, reason=str(known_ids))

        heading_id = resolve_anchor(f"#{fragment}", slug_to_id, known_ids)
        if heading_id is None:
            return CrossDocResolution(
                unresolved=True,
                reason=f"heading '{fragment}' not found in '{match.local}'",
            )
        # ASSUMPTION, not verified against a live Google Docs UI (not
        # reachable from this sandbox): `#heading=<headingId>` on a
        # `/document/d/<id>/edit[?tab=<tabId>]` URL navigates to that heading
        # when opened, the same way Docs' own "Copy link to this heading"
        # feature does for a heading in the *current* document/tab.
        # Documented here as the one place this shape is constructed, so a
        # future correction has a single call site to fix.
        url = f"{base_url}#heading={heading_id}"
        return CrossDocResolution(payload={"url": url})

    def _fetch(self, mapping: object) -> Tuple[Optional[Dict[str, str]], object]:
        """Fetch (through the cache) `mapping`'s live headings.

        Returns `(slug_to_id, known_ids)` on success, or `(None, reason_str)`
        on failure — never raises, since a target document that fails to
        fetch must not abort the push of the *source* document (`backend.py`'s
        outer exception handling is for the source push; this call must not
        let that machinery catch a target-fetch failure instead).
        """
        key = (mapping.remote_id, mapping.tab_id or None)
        if key not in self._cache:
            if self._fetch_headings is None:
                self._cache[key] = ("error", "no fetch_headings callback configured")
            else:
                try:
                    self._cache[key] = ("ok", self._fetch_headings(mapping))
                except Exception as exc:  # noqa: BLE001 - see docstring
                    self._cache[key] = (
                        "error",
                        f"failed to fetch target document '{mapping.remote_id}': {exc}",
                    )
        status, payload = self._cache[key]
        if status == "error":
            return None, payload
        return payload  # type: ignore[return-value]
