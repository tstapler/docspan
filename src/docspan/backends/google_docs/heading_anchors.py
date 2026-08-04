"""Internal markdown anchors (`[A1](#a1-current-state)`) as Google Docs heading links.

A Google Doc has no URL fragment, so a link whose href is `#a1-current-state`
is stored verbatim and is a **dead link** for every reader of the Doc. The Docs
API's `Link` is a union of six members — `url`, `tabId`, `bookmark`, `heading`,
and the legacy `bookmarkId`/`headingId` — and every heading paragraph already
carries a `headingId` in its `paragraphStyle`, so the target exists; the anchor
just has to be resolved to it. Which of `heading` / `headingId` a *read* returns
depends on `includeTabsContent`, not on the document; see
`DocsStructureParser._parse_link`.

Both directions go through this module for the document body, so they cannot
drift there. They do **not** cover table cells: `_parse_table` flattens a cell
to a plain string, so a heading inside a cell is not an anchor target and a
heading link inside a cell is dropped on read — pre-existing for `url` links
too, and out of scope here, but the invariant above is about paragraphs only.

* write — markdown `#target` -> ``{"headingId": ...}`` (`link_payload`)
* read  — either the flat `headingId` or the tabs-aware `heading: {id, tabId}`
  -> markdown `#slug` (`heading_id_to_slug`). Which one a read returns depends on
  the request flag, so both are accepted.

`#target` is percent-decoded and then matched **exactly** (`resolve_anchor`) —
heading id first, then slug. No Unicode normalization: two attempts at folding NFD
to NFC each produced a silent link to the wrong heading, and folding only ever
helped a hand-written cross-form anchor, since one a pull wrote comes from the same
source as the slug. Matching ids by set membership rather than by an `h.xxxx` shape
guess means the read direction can emit a bare id when a slug is unavailable and
the write direction still resolves it, with no invented escape syntax and no
assumption about how Docs formats an id.

A leading `#` is the only discriminator needed between an anchor and a URL — no
absolute or relative URL begins with one — so `TextSpan.link` keeps carrying a
single string rather than growing a parallel "is this an anchor" field.

Out of scope, and each tracked as its own follow-up rather than half-done here:
bookmarks (both `bookmark` and the legacy `bookmarkId`), links to a whole tab
(`tabId`), an anchor into a *different tab* of the same document (the flat
`headingId` resolves against the tab named in the request, so it cannot express
one — such an anchor is reported unresolved), cross-*document* anchors, and the
Confluence backend, which still writes a literal `#fragment` href.

A link this module cannot express reads back as no link at all, so a **tab-scoped**
pull drops it from the author's file. A default pull is unaffected: it goes through
Drive's HTML export, which carries those hrefs through without consulting this
module. Pre-existing for every member except the two handled here; reporting the
structural-path loss is a follow-up.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

# Characters github-slugger keeps besides the categories below. Space is kept
# here and turned into "-" afterwards, which is what makes the double-hyphen
# case work.
_KEPT = frozenset("-_ ")

# Unicode general categories github-slugger keeps: letters, decimal and letter
# numbers, combining marks, and connector punctuation. It is generated as a
# character class of everything to *remove*, so this is its complement stated as
# categories — see slugify() for what that costs and why it is not the blob.
#
# `Mn`/`Mc`/`Me` are the load-bearing entries. `str.isalnum()` is False for a
# combining mark, so slugging "café" in NFD (an `e` followed by U+0301, which is
# how macOS often hands text over) dropped the accent and produced
# "cafe-measurements" where GitHub produces "café-measurements" — an anchor that
# silently resolves to nothing. `No` is the other direction: "x²" kept the ²
# where GitHub drops it.
_KEPT_CATEGORIES = frozenset(
    {"Ll", "Lu", "Lt", "Lm", "Lo", "Nd", "Nl", "Mn", "Mc", "Me", "Pc"}
)



def slugify(text: str) -> str:
    """Slug a single heading as GitHub does, ignoring duplicates.

    Mirrors `github-slugger` (the implementation GitHub's markdown pipeline
    uses, https://github.com/Flet/github-slugger): lowercase, drop punctuation
    and symbols, then replace each remaining space with a hyphen.

    Two details that a naive `re.sub(r"\\s+", "-", ...)` gets wrong, and that a
    cross-reference silently fails to match on:

    * Spaces are replaced **one at a time**, never as a run. `## A1 — Current`
      loses the em dash and keeps both spaces that surrounded it, so the slug is
      `a1--current` with *two* hyphens.
    * A tab is *dropped*, not turned into a hyphen — only U+0020 is.

    Fidelity, measured rather than asserted:
    `tests/fixtures/github_slugger_vectors.json` holds outputs generated by
    running the real github-slugger, and `tests/test_heading_anchors.py` checks
    this function against every one of them. Two documented divergences, both
    recorded in that fixture:

    * Leading/trailing whitespace. github-slugger alone turns `"  Intro  "`
      into `"--intro--"`; the `.strip()` here yields `"intro"`, which is parity
      with **GitHub end to end**, because a markdown parser trims ATX heading
      text before the slugger ever sees it.
    * Circled letters, plus a residue of other code points across the BMP. This
      keeps whole Unicode *categories* (`_KEPT_CATEGORIES`) where github-slugger
      ships an 8 KB generated character class. **No total is stated on purpose:**
      it moves with both the slugger's version and Python's Unicode tables, and
      three independent measurements of it came back 145, 147 and 148. Quoting
      any one of them would be quoting a coincidence.

      What is stable is the shape, in two directions:
      52 circled letters (U+24B6–U+24E9) that the slugger keeps and this drops,
      and — the larger half — letters and marks from blocks added to Unicode
      after that class was generated, which this keeps and the slugger drops.
      Those are dominated by **Arabic Extended-A/B**, not by the antique Latin
      scripts an earlier version of this comment named.

      Embedding the blob would trade the divergence for a frozen Unicode
      version, so the categories win. A heading that lands in the residue gets a
      *reported* unresolvable anchor, never a silently wrong link.
    """
    lowered = text.strip().lower()
    kept = "".join(
        char
        for char in lowered
        if unicodedata.category(char) in _KEPT_CATEGORIES or char in _KEPT
    )
    return kept.replace(" ", "-")


def slugify_all(texts: Iterable[str]) -> List[str]:
    """Slug headings in document order, applying GitHub's duplicate suffixes.

    The second heading slugging to `intro` becomes `intro-1`, the third
    `intro-2`. The suffix search loops until it finds an unused slug rather
    than trusting the counter, because a document can contain a *literal*
    heading "Intro 1" whose own slug is already `intro-1` — github-slugger
    handles that by skipping to `intro-2`, and a bare counter would hand two
    different headings the same anchor.

    Order matters, so this takes the whole sequence: a slug's suffix is a fact
    about the headings before it, not about the heading itself.
    """
    occurrences: Dict[str, int] = {}
    slugs: List[str] = []
    for text in texts:
        base = slugify(text)
        slug = base
        while slug in occurrences:
            occurrences[base] = occurrences.get(base, 0) + 1
            slug = f"{base}-{occurrences[base]}"
        occurrences[slug] = 0
        slugs.append(slug)
    return slugs


def is_heading_style(style: object) -> bool:
    """Whether a paragraph's namedStyleType is an anchor target.

    `TITLE` and `SUBTITLE` count. Google Docs' own outline treats them as
    document-level headings and `projection.project()` maps them to
    `HEADING_1`/`HEADING_2` because that is what markdown can express — so an
    anchor to a document's title has a real target and a
    `startswith("HEADING_")` test silently refuses it.

    What produces a TITLE paragraph is Google Docs itself, for any document whose
    title was typed into the title bar — markdown `#` parses to `HEADING_1`, so
    the *target* side never carries one. Both directions still reach this branch:
    `pull` maps the id back to a slug, and on push `_align_for_styling` parses the
    document **unprojected**, so `heading_slug_to_id(current)` asks this question
    about document nodes. A partial push of one section against a doc whose title
    is a TITLE resolves `#the-title` through exactly that path.
    """
    return isinstance(style, str) and (
        style.startswith("HEADING_") or style in ("TITLE", "SUBTITLE")
    )


def _heading_texts_and_ids(nodes: Iterable[object]) -> List[Tuple[str, Optional[str]]]:
    """(text, heading_id) for every heading paragraph, in document order.

    Headings with no `headingId` are still returned. They cannot be linked to,
    but they must occupy their slot so the duplicate suffixes of the headings
    after them stay aligned with what GitHub would produce for the same
    markdown.
    """
    out: List[Tuple[str, Optional[str]]] = []
    for node in nodes:
        if not is_heading_style(getattr(node, "style", "")):
            continue
        out.append((getattr(node, "text", "") or "", getattr(node, "heading_id", None)))
    return out


def heading_slug_to_id(nodes: Iterable[object]) -> Dict[str, str]:
    """slug -> headingId for every linkable heading, in document order."""
    pairs = _heading_texts_and_ids(nodes)
    slugs = slugify_all(text for text, _ in pairs)
    return {
        slug: heading_id
        for slug, (_, heading_id) in zip(slugs, pairs)
        if heading_id
    }


def heading_id_to_slug(nodes: Iterable[object]) -> Dict[str, str]:
    """headingId -> slug, the inverse map the read direction needs."""
    return {heading_id: slug for slug, heading_id in heading_slug_to_id(nodes).items()}


def upgrade_heading_id_anchors(markdown: str, id_to_slug: Dict[str, str]) -> str:
    """Rewrite `](#h.abc123)` to `](#the-headings-slug)` in rendered markdown.

    For the **default** pull path, which goes through Drive's HTML export rather
    than DocsStructureParser and therefore never gets the slug upgrade the
    structural path does. Verified live: that export emits the Doc's own opaque
    fragment, so the pulled file holds `[A1](#h.70l3py5ob5tg)` — which docspan
    pushes back correctly, and which no markdown renderer can resolve.

    Deliberately conservative. Only a fragment that is a **known heading id of
    this document** is rewritten:

    * an id the document does not report is left alone rather than guessed at —
      it may be a bookmark, a heading in another tab, or a fragment a human
      typed;
    * a heading with no id contributes nothing to the map, so it cannot capture
      an unrelated fragment;
    * only the link-destination position is touched, so `#h.abc` appearing in
      prose or in a code fence is untouched.

    Not merged into the structural path, which resolves the union member before
    a slug ever exists as text. This one has only rendered markdown to work with.
    """
    if not id_to_slug:
        return markdown

    def replace(match: "re.Match[str]") -> str:
        slug = id_to_slug.get(match.group("target"))
        return match.group(0) if slug is None else f"](#{slug})"

    return _ANCHOR_DESTINATION.sub(replace, markdown)


# The destination half of an inline markdown link whose target is a fragment.
# Anchored on "](#" so it cannot match a bare "#h.abc" in prose, and the target
# excludes ")" and whitespace so it stops at the end of the destination.
_ANCHOR_DESTINATION = re.compile(r"\]\(#(?P<target>[^)\s]+)\)")


def available_anchor_slugs(
    target_nodes: Sequence[object], document_nodes: Sequence[object] = ()
) -> List[str]:
    """Anchors that would actually resolve, for the "did you mean" tail.

    Must be derived from the same sets resolution consults, not from the
    markdown's headings alone. Feeding it `heading_slugs(target_nodes)` produced
    a report that named the very anchor it had just called dead — the markdown
    heading is there, so its slug was listed, while resolution had discarded the
    mapping because the document reported that heading without a `headingId`. It
    also denied any anchors existed for a document-only heading that resolves
    perfectly well.

    A list that disagrees with resolution is worse than no list: it tells the
    author their correct spelling is both wrong and right.

    Two subtleties, both learned by getting them wrong:

    * an earlier version guarded the markdown side with `if heading_id is None or
      heading_id`, which is **always true** for both producers — the markdown
      parser never sets an id, and the document side is already filtered on
      truthiness. It was dead code, so the function still offered every markdown
      heading, which is exactly what it was written to stop.
    * an empty heading slugs to `""`, which would be offered as a bare `#` — not
      an anchor at all (`is_anchor` needs more than the `#`). Filtered out.

    A markdown heading is only offerable when the *push* will make it resolvable,
    which means it must actually be paired to a document heading — and that is
    knowable only in pass 2. So the honest dry-run answer is the markdown's
    headings *minus* those the document already contradicts, and `push()` passes
    `sorted(alignment.slug_to_id)` instead, which is the real map.
    """
    document_slugs = set(heading_slug_to_id(document_nodes))
    document_headings = _heading_texts_and_ids(document_nodes)
    # A slug the document reports for a heading with no id cannot resolve.
    unlinkable = {
        slug
        for slug, (_text, heading_id) in zip(
            slugify_all(text for text, _ in document_headings), document_headings
        )
        if not heading_id
    }
    target_slugs = set(heading_slugs(target_nodes)) - unlinkable
    return sorted(slug for slug in document_slugs | target_slugs if slug)


def heading_slugs(nodes: Iterable[object]) -> List[str]:
    """Every heading's slug in document order, linkable or not.

    Used by unresolved_anchors(), where the markdown-side heading nodes have no
    ids yet and only their slugs matter.
    """
    return slugify_all(text for text, _ in _heading_texts_and_ids(nodes))


# Stands in for the id Docs will assign to a heading this push has not written
# yet. Truthy, and shaped so it could never be mistaken for a real `h.xxxx` id if
# it ever escapes unresolved_anchors().
_UNWRITTEN_HEADING = "\0not-yet-written"


def is_anchor(href: Optional[str]) -> bool:
    """True for an internal anchor. No URL, absolute or relative, starts with `#`.

    A bare "#" is not an anchor — it names no heading and is a link to the top
    of the page in a browser, which has no Docs equivalent.
    """
    if href is None:
        return False
    return href.startswith("#") and len(href) > 1


def anchor_target(href: str) -> str:
    """The part of an anchor after the `#`, as a key comparable with a slug.

    Percent-decoding, which a non-ASCII anchor needs to resolve at all:

    * A CommonMark parser normalizes link destinations by
      percent-encoding them, so `[Café](#café-notes)` reaches this module as
      `#caf%C3%A9-notes` — measured, not assumed::

          >>> mistune.create_markdown(renderer=None)('[Café](#café-notes)')
          ... {'type': 'link', 'attrs': {'url': '#caf%C3%A9-notes'}}

      The slug side is built from the heading's own text and is never encoded,
      so without decoding here the two can never meet and every accented anchor
      silently resolves to nothing. GitHub decodes for the same reason.
    Deliberately *only* the decode. An earlier version also NFC-normalized, so an
    NFD heading would match an NFC href — see `resolve_anchor` for why that was
    removed: it could only help a hand-written cross-form anchor, and it bought
    that by silently arbitrating between headings that look identical on screen.
    """
    return unquote(href[1:])


def resolve_anchor(
    href: str,
    slug_to_id: Dict[str, str],
    known_ids: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Resolve an anchor to a headingId, or None when it names no heading.

    Percent-decoded (a CommonMark parser encodes the destination, so decoding
    restores what the author typed), then matched **exactly** — heading id first,
    then slug. The id branch closes the round trip: a pull that could not name a
    slug emits the bare id and this takes it back, with no invented escape syntax.

    **No Unicode normalization.** Two earlier versions folded to NFC so an NFD
    heading would match an NFC href, and each introduced a silent wrong link:
    first by letting the fold pick a winner between two headings that differ only
    by normal form, then — after that was refused — by letting an exact match
    arbitrate the same collision one layer earlier. The second was the first bug
    wearing a different hat, and the test written for it could not see it.

    Folding is not worth that. It only ever helped a *hand-written* anchor in a
    different normal form from its heading, because an anchor a pull wrote comes
    from the same source as the slug and already matches byte for byte. The cost
    was arbitrating, silently, between headings that look identical on screen.
    Unmatched now means *reported*, with the available-anchors list showing the
    spelling that works — which the author can copy. Loud over silent is the
    principle the rest of this module is built on; this is that principle applied
    to the module itself.
    """
    target = unquote(href[1:])
    ids = known_ids if isinstance(known_ids, (set, frozenset, dict)) else set(known_ids or ())
    if target in ids:
        return target
    return slug_to_id.get(target)


def link_payload(
    href: str,
    slug_to_id: Optional[Dict[str, str]] = None,
    known_ids: Optional[Iterable[str]] = None,
) -> Optional[dict]:
    """The Docs `Link` union member for a markdown href.

    Returns ``{"url": ...}`` for a URL, ``{"headingId": ...}`` for a resolvable
    anchor, and None for an anchor that resolves to nothing — never a `url` link
    holding a `#fragment`, which is the dead link this module exists to stop
    writing. A None return is a caller's cue to write no link at all and to report
    the anchor, never to fall back to a `url`.

    Only the flat `headingId` member is written. It resolves against the tab named
    in the request, so it cannot express a link into a *different* tab of the same
    document; such an anchor is reported unresolved. See the cross-tab follow-up.
    """
    if not is_anchor(href):
        return {"url": href}
    heading_id = resolve_anchor(href, slug_to_id or {}, known_ids)
    return {"headingId": heading_id} if heading_id else None


def anchors_in(nodes: Iterable[object]) -> List[str]:
    """Every distinct internal anchor used by a node list, in first-use order."""
    seen: Dict[str, None] = {}
    for node in nodes:
        for span in getattr(node, "spans", None) or []:
            href = getattr(span, "link", None)
            if isinstance(href, str) and is_anchor(href) and href not in seen:
                seen[href] = None
    return list(seen)


def unresolved_anchors(
    target_nodes: Sequence[object],
    document_nodes: Sequence[object] = (),
) -> List[str]:
    """Anchors in ``target_nodes`` that no heading can satisfy, in use order.

    Checked against the headings of the markdown being pushed *and* of the
    document as it stands, because both can supply a target: a heading this
    push is about to create is only in the markdown, while a `#h.abc123` id
    emitted by an earlier pull is only in the document.

    This is the ``--dry-run`` view, computed before anything is written and
    therefore an approximation. The authoritative answer is
    DocsRequestBuilder.unresolved_anchor_links(), which runs in pass 2 against
    the document as actually written.

    It **under-reports and never over-reports** — the direction that matters,
    since a dry-run that invented a problem would be worse than one that misses
    it. Three causes, all of which count as resolvable here and are caught only
    by pass 2:

    * a heading this push *creates* — its slug is already in the markdown;
    * a heading this push *deletes*, whose id is still in the document;
    * a heading present in both that the document reports with no `headingId`.

    Do not restate this as "only" one cause. Each of the three was measured.

    """
    document_slugs = dict(heading_slug_to_id(document_nodes))
    known_ids = {
        heading_id
        for _text, heading_id in _heading_texts_and_ids(document_nodes)
        if heading_id
    }
    # Resolved through resolve_anchor rather than against a set built here, so the
    # advisory and the write agree on *how* a target is matched. A second
    # set-membership test drifted from it once resolve_anchor changed, and reported
    # an anchor the push then resolved.
    #
    # The markdown's own headings have no ids yet, so they are mapped to a
    # sentinel: only whether the anchor resolves matters here, never to what.
    #
    # Non-empty deliberately: `link_payload` treats a falsy id as "did not
    # resolve", so an empty string would make this map silently produce no link if
    # it ever reached that function.
    resolvable = {slug: _UNWRITTEN_HEADING for slug in heading_slugs(target_nodes)}
    resolvable.update(document_slugs)
    return [
        href
        for href in anchors_in(target_nodes)
        if resolve_anchor(href, resolvable, known_ids) is None
    ]
