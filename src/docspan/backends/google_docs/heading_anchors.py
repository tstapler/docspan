"""Internal markdown anchors (`[A1](#a1-current-state)`) as Google Docs heading links.

A Google Doc has no URL fragment, so a link whose href is `#a1-current-state`
is stored verbatim and is a **dead link** for every reader of the Doc. The Docs
API's `Link` is a union — `url` | `bookmarkId` | `headingId` — and every heading
paragraph already carries a `headingId` in its `paragraphStyle`, so the target
exists; the anchor just has to be resolved to it.

Both directions go through this module so they cannot drift:

* write — markdown `#target` -> ``{"headingId": ...}`` (`link_payload`)
* read  — ``{"headingId": ...}`` -> markdown `#slug` (`heading_id_to_slug`)

`#target` is resolved against the document by **exact heading id first, then
slug** (`resolve_anchor`). Matching ids by set membership rather than by a
`h.xxxx` shape guess means the read direction can emit a bare id when a slug is
unavailable and the write direction will still resolve it, with no invented
escape syntax and no assumption about how Docs formats an id.

A leading `#` is the only discriminator needed between an anchor and a URL — no
absolute or relative URL begins with one — so `TextSpan.link` keeps carrying a
single string rather than growing a parallel "is this an anchor" field.

Out of scope, deliberately: bookmarks (`bookmarkId`), cross-document anchors,
and the Confluence backend.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Characters github-slugger keeps besides alphanumerics. Space is kept here and
# turned into "-" afterwards, which is what makes the double-hyphen case work.
_KEPT = frozenset("-_ ")


class UnresolvedAnchorError(Exception):
    """An internal anchor names no heading in the document or the markdown.

    Raised instead of writing a link with no target: a `#typo` anchor applied as
    a `url` link renders in the Doc as something a reader can click and land
    nowhere, and reporting success over that is the failure this whole path
    exists to remove.
    """

    def __init__(self, anchors: Sequence[str], available: Sequence[str], source: str = "") -> None:
        self.anchors = list(anchors)
        self.available = list(available)
        self.source = source
        super().__init__(self._render())

    def _render(self) -> str:
        where = f" in {self.source}" if self.source else ""
        lines = [
            f"{len(self.anchors)} internal anchor(s){where} name no heading in the document:",
        ]
        lines += [f"    • {anchor}" for anchor in self.anchors]
        if self.available:
            shown = self.available[:10]
            more = len(self.available) - len(shown)
            lines.append("  available heading anchors:")
            lines += [f"    • #{slug}" for slug in shown]
            if more:
                lines.append(f"    • … and {more} more")
        else:
            lines.append("  the document has no headings to anchor to.")
        return "\n".join(lines)


def slugify(text: str) -> str:
    """Slug a single heading exactly as GitHub does, ignoring duplicates.

    Mirrors `github-slugger` (the implementation GitHub's markdown pipeline
    uses, https://github.com/Flet/github-slugger): lowercase, drop punctuation
    and symbols, then replace each remaining space with a hyphen.

    Two details that a naive `re.sub(r"\\s+", "-", ...)` gets wrong, and that a
    cross-reference silently fails to match on:

    * Spaces are replaced **one at a time**, never as a run. `## A1 — Current`
      loses the em dash and keeps both spaces that surrounded it, so the slug is
      `a1--current` with *two* hyphens.
    * Accented letters survive (`é` -> `é`) while dashes, quotes and other
      symbols are dropped, because the rule is "is this alphanumeric" rather
      than "is this ASCII".

    The leading `.strip()` stands in for the markdown parser's own trimming of
    ATX heading text, which happens before GitHub's slugger ever sees it — so
    this is parity with GitHub end to end, not a deviation from the slugger.
    """
    lowered = text.strip().lower()
    kept = "".join(char for char in lowered if char.isalnum() or char in _KEPT)
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


def _heading_texts_and_ids(nodes: Iterable[object]) -> List[Tuple[str, Optional[str]]]:
    """(text, heading_id) for every heading paragraph, in document order.

    Headings with no `headingId` are still returned. They cannot be linked to,
    but they must occupy their slot so the duplicate suffixes of the headings
    after them stay aligned with what GitHub would produce for the same
    markdown.
    """
    out: List[Tuple[str, Optional[str]]] = []
    for node in nodes:
        style = getattr(node, "style", "")
        if not isinstance(style, str) or not style.startswith("HEADING_"):
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


def heading_slugs(nodes: Iterable[object]) -> List[str]:
    """Every heading's slug in document order, linkable or not.

    Used for the "available anchors" half of an UnresolvedAnchorError and for
    pre-write validation, where markdown-side heading nodes have no ids yet.
    """
    return slugify_all(text for text, _ in _heading_texts_and_ids(nodes))


def is_anchor(href: Optional[str]) -> bool:
    """True for an internal anchor. No URL, absolute or relative, starts with `#`.

    A bare "#" is not an anchor — it names no heading and is a link to the top
    of the page in a browser, which has no Docs equivalent.
    """
    if href is None:
        return False
    return href.startswith("#") and len(href) > 1


def anchor_target(href: str) -> str:
    """The part of an anchor after the `#`."""
    return href[1:]


def resolve_anchor(
    href: str,
    slug_to_id: Dict[str, str],
    known_ids: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Resolve an anchor to a headingId, or None when it names no heading.

    Heading id first, then slug. The id branch is what closes the round trip:
    a pull that could not name a slug for some heading emits the bare id, and
    this resolves it back without either side inventing a syntax for "this is
    an id, not a slug".
    """
    target = anchor_target(href)
    if known_ids is not None and target in set(known_ids):
        return target
    return slug_to_id.get(target)


def link_payload(
    href: str,
    slug_to_id: Optional[Dict[str, str]] = None,
    known_ids: Optional[Iterable[str]] = None,
) -> Optional[dict]:
    """The Docs `Link` union member for a markdown href.

    Returns ``{"url": ...}`` for a URL, ``{"headingId": ...}`` for a resolvable
    anchor, and None for an anchor that resolves to nothing — never a `url`
    link holding a `#fragment`, which is the dead link this module exists to
    stop writing. A None return is a caller's cue to fail, not to skip.
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

    Runs before anything is written, so an anchor typo costs a rejected push
    rather than a document with a link a reader can click and land nowhere.
    """
    slug_to_id = dict(heading_slug_to_id(document_nodes))
    resolvable = set(heading_slugs(target_nodes)) | set(slug_to_id)
    known_ids = set(slug_to_id.values())
    return [
        href
        for href in anchors_in(target_nodes)
        if anchor_target(href) not in resolvable and anchor_target(href) not in known_ids
    ]
