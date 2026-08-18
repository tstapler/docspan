"""Parse a Google Docs JSON document into a list of DocsParagraphNode objects."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Union

from docspan.backends.google_docs.heading_anchors import (
    anchor_target,
    heading_id_to_slug,
    is_anchor,
)

# Structural elements whose leading newline the Docs API refuses to delete on
# its own: "Deleting the newline character before a Table, TableOfContents or
# SectionBreak without deleting the element" is an invalid deleteContentRange
# (documents.request reference, DeleteContentRangeRequest). A paragraph's
# trailing newline IS that newline when one of these follows it, so a delete
# covering the paragraph's full [start_index, end_index) is rejected with
# "Invalid deletion range. Cannot delete the requested range." — see
# DocsRequestBuilder._make_delete_requests.
UNDELETABLE_BOUNDARY_KEYS = ("table", "tableOfContents", "sectionBreak")

# Google Docs renders some blocks itself — a native code block, for one — and marks
# each paragraph of such a block by writing a Private-Use-Area glyph in front of it
# (U+E907 for a code block). It arrives as its **own leading textRun**, which is what
# makes it identifiable; see `_render_prefix_of`.
#
# U+E000-U+F8FF is the BMP Private Use Area by definition, so a `unicodedata.category`
# filter over it returned every codepoint — the filter was a no-op. Matching the range
# directly says the same thing without building a 6400-character class on every parse.
#
# Only the BMP range, so a supplementary-plane Private Use glyph (planes 15 and 16) is
# not recognised. No observed document uses one; if one appears the result is the old
# loud failure, not corruption.
_PRIVATE_USE = range(0xE000, 0xF900)

# `docs_structure_parser` is the sole owner of both blockquote-marker constants
# below — any other module (e.g. `docs_request_builder._blockquote_paragraph_style_fields`)
# imports them by name rather than redefining or copying their values, so there is
# exactly one place a future format change is made.
#
# Values are an engineering decision documented in
# project_plans/gdocs-native-blockquotes/implementation/epic-0-spike-findings.md,
# reasoned from the public Docs API v1 schema and WCAG contrast math — NOT yet
# confirmed against a live `documents.get` echo. See that file's "Explicitly left
# unverified" section before treating these as final.
BLOCKQUOTE_BORDER_MARKER: dict = {
    "color": {"color": {"rgbColor": {"red": 0.494, "green": 0.549, "blue": 0.612}}},
    "width": {"magnitude": 1, "unit": "PT"},
    "dashStyle": "SOLID",
    "padding": {"magnitude": 1, "unit": "PT"},
}
BLOCKQUOTE_INDENT_PT_PER_LEVEL: float = 18.0

# Fonts Google Docs' own code-block picker offers, beyond "Courier"/"mono" — the
# "Courier"/"mono" check this extends. Not exhaustive — an arbitrary custom
# monospace font will still miss — but "Courier"/"mono" alone missed every
# other font the picker offers, so a real code block set in one of these
# tripped `ambiguous_code_prefix` on every single push.
_MONOSPACE_FONT_MARKERS = (
    "courier",
    "mono",
    "consolas",
    "menlo",
    "monaco",
    "fira code",
    "inconsolata",
    "source code pro",
    "cascadia code",
    "roboto mono",
    "jetbrains mono",
    "ibm plex mono",
    "space mono",
    "pt mono",
    "andale mono",
)


# Sub-fields of BLOCKQUOTE_BORDER_MARKER docspan actually writes on push
# (`docs_request_builder._blockquote_paragraph_style_fields`). Detection below
# compares only these, not the whole `borderLeft` dict, against a live Doc's
# echo — Docs is free to round-trip additional normalized defaults (e.g. a
# `padding` Docs fills in itself) that docspan never specified, and a
# whole-dict `==` would then read every real match as a non-match. See
# Story 3.1's Given-When-Then and Unresolved Question 2.
_BLOCKQUOTE_BORDER_SUBFIELDS = ("color", "width", "dashStyle")


def _detect_blockquote_depth(paragraph_style: dict) -> int:
    """0 if `paragraph_style` carries no docspan-written blockquote border,
    else the quote depth implied by `indentStart`.

    Matches iff every sub-field in `_BLOCKQUOTE_BORDER_SUBFIELDS` is present
    on `borderLeft` and equals the corresponding sub-field of
    `BLOCKQUOTE_BORDER_MARKER` — sub-field-by-sub-field, not `borderLeft ==
    BLOCKQUOTE_BORDER_MARKER` wholesale (see module comment above).
    """
    border_left = paragraph_style.get("borderLeft")
    if not isinstance(border_left, dict):
        return 0
    for key in _BLOCKQUOTE_BORDER_SUBFIELDS:
        if border_left.get(key) != BLOCKQUOTE_BORDER_MARKER.get(key):
            return 0
    indent_start = paragraph_style.get("indentStart")
    magnitude = indent_start.get("magnitude") if isinstance(indent_start, dict) else None
    if not isinstance(magnitude, (int, float)) or magnitude <= 0:
        return 0
    depth = round(magnitude / BLOCKQUOTE_INDENT_PT_PER_LEVEL)
    return depth if depth > 0 else 0


def _is_all_private_use(text: str) -> bool:
    """True when `text` is non-empty and holds nothing but PUA, ignoring surrounding whitespace."""
    stripped = text.strip()
    return bool(stripped) and all(ord(ch) in _PRIVATE_USE for ch in stripped)


def _utf16_len(text: str) -> int:
    """UTF-16 code units in `text` — the unit Docs indices are measured in."""
    return len(text.encode("utf-16-le")) // 2


def _trim_spans_to_cell_text(
    spans: List["TextSpan"], joined: str, text: str
) -> List["TextSpan"]:
    """Trim `spans` at both ends so they concatenate to exactly `text`.

    `text` is `joined.strip()`, so the amount to remove is the leading and
    trailing whitespace width. Marks on what survives are preserved; a span left
    holding nothing is dropped, because a bold span with no text renders as
    `****`.
    """
    lead = len(joined) - len(joined.lstrip())
    tail = len(joined) - len(joined.rstrip())
    out: List["TextSpan"] = []
    for span in spans:
        out.append(span)
    # Front.
    while lead > 0 and out:
        head = out[0]
        if len(head.text) <= lead:
            lead -= len(head.text)
            out.pop(0)
        else:
            out[0] = replace(head, text=head.text[lead:])
            lead = 0
    # Back.
    while tail > 0 and out:
        last = out[-1]
        if len(last.text) <= tail:
            tail -= len(last.text)
            out.pop()
        else:
            out[-1] = replace(last, text=last.text[: len(last.text) - tail])
            tail = 0
    return [span for span in out if span.text]


def _person_display_text(person: dict) -> str:
    """Return display text for a Docs API `person` structural element.

    An @-mention "smart chip" (Insert > Smart chip > Person) is represented
    in the Docs API v1 JSON model as a `person` structural element — a
    sibling of `textRun` inside `paragraph.elements`, never a textRun
    itself. Its `personProperties` dict carries `name` and/or `email`;
    prefer the name, falling back to email when name is absent (e.g. the
    person hasn't shared a display name with the doc's viewer/API caller).
    Returns "" if neither is present so callers can skip emitting a span.
    """
    if not isinstance(person, dict):
        return ""
    person_properties = person.get("personProperties")
    if not isinstance(person_properties, dict):
        return ""
    name = person_properties.get("name")
    if isinstance(name, str) and name:
        return name
    email = person_properties.get("email")
    if isinstance(email, str) and email:
        return email
    return ""


@dataclass
class TextSpan:
    text: str
    bold: bool = False
    italic: bool = False
    # Either a URL or an internal anchor (`#slug`). A leading "#" is the whole
    # discriminator — no URL, absolute or relative, starts with one — so a
    # Docs `Link` carrying a `headingId` reads back into this same field as
    # "#" + the heading's slug rather than needing a parallel field. See
    # heading_anchors.
    link: Optional[str] = None
    monospace: bool = False


@dataclass
class DocsParagraphNode:
    """Represents a single paragraph in a Google Docs document."""
    style: str  # e.g. "NORMAL_TEXT", "HEADING_1", "HEADING_2", ...
    text: str   # Concatenated plain text (trailing \n stripped)
    is_list_item: bool = False
    nesting_level: int = 0
    start_index: int = 0
    end_index: int = 0
    spans: List[TextSpan] = field(default_factory=list)
    # The Private-Use glyph Docs writes in front of a paragraph it renders itself
    # (a native code block writes U+E907). Non-empty means the paragraph belongs to
    # a Docs-rendered block: `.text` still contains it, `projection.project()` drops
    # it so the diff never sees it, and DocsRequestBuilder deletes only the
    # paragraph's *text*, never the paragraph: the API refuses a range covering the
    # glyph, and a range that skips it silently orphans the glyph onto the next
    # paragraph. Both verified against the live API.
    render_prefix: str = ""
    # True when this paragraph's bullet resolves to a native BULLET_CHECKBOX
    # glyph (glyphType == GLYPH_TYPE_UNSPECIFIED), resolved live by
    # DocsStructureParser from the document's `lists` map. NOT part of the
    # diff key (style, bullet, nesting, text) — feeds GlyphShapeCheck only
    # (via DiffEntry.current_is_native_checkbox), never
    # DocsRequestBuilder.build()'s equality/opcode logic. See ADR-001,
    # plan.md Task 1.2.2d.
    is_native_checkbox: bool = False
    # True when the very next structural element in the document body is a
    # Table, TableOfContents or SectionBreak (UNDELETABLE_BOUNDARY_KEYS). This
    # paragraph's trailing newline is then the newline that anchors that
    # element, and the Docs API rejects any deleteContentRange covering it
    # unless the element itself is deleted in the same range. Resolved live by
    # DocsStructureParser from the raw body content (sectionBreak and
    # tableOfContents elements are never turned into nodes, so this flag is the
    # only trace of them). NOT part of the diff key — consumed solely by
    # DocsRequestBuilder._make_delete_requests.
    precedes_structural_element: bool = False
    # paragraphStyle.headingId — Docs' own id for a heading, and the only thing
    # a `headingId` link can point at. Present on headings, None elsewhere.
    # NOT part of the diff key: it is assigned by Docs, so treating it as
    # identity would make every freshly written heading look like a different
    # paragraph from the one the markdown describes.
    heading_id: Optional[str] = None
    # Part of the diff key (`_node_key`), NOT `_content_key`: a blockquote
    # restyle-in-place should still fold to `equal` via `_repair`, but a
    # blockquote paragraph and a plain paragraph sharing identical text are
    # not the same live paragraph to align against. True iff quote_depth > 0
    # — see __post_init__.
    is_blockquote: bool = False
    # Nesting depth of a markdown blockquote ("> " = 1, "> > " = 2, ...).
    # Part of the diff key alongside is_blockquote, same rationale. 0 iff
    # is_blockquote is False — see __post_init__.
    quote_depth: int = 0

    def __post_init__(self) -> None:
        # is_blockquote/quote_depth are an intentionally-paired invariant, not
        # two independent fields: quote_depth only means anything when
        # is_blockquote is True, and there is no such thing as a depth-0
        # blockquote. Enforcing this at construction time closes the illegal
        # states (False, 2) and (True, 0) without a wider field-shape change.
        if self.is_blockquote != (self.quote_depth > 0):
            raise ValueError(
                "DocsParagraphNode: is_blockquote and quote_depth must agree "
                f"(is_blockquote={self.is_blockquote!r}, quote_depth={self.quote_depth!r})"
            )


@dataclass
class TableCell:
    """One table cell: its text, and the inline styling on that text.

    Cells were plain `str`, so every mark inside one was dropped on both sides —
    bold, monospace, and **links, including internal `#anchor` cross-references**.
    A reference written inside a table cell rendered as dead text in the Doc while
    the identical reference in a paragraph resolved, with nothing reported.

    `text` and `spans` are co-located rather than kept in parallel structures so they
    are easy to hold in agreement: pass 2 walks span widths against `text` to place
    index ranges, so a character in one and not the other shifts every range after
    it. Same intended invariant as `DocsParagraphNode` — `spans` is either empty
    (unstyled) or concatenates to exactly `text`.

    Nothing *enforces* it. `TableCell(text="a", spans=[TextSpan(text="bbbb")])` is
    constructible, and `__post_init__` normalises a `str` cell without checking.
    What actually protects the document is the `limit` in
    `DocsRequestBuilder._span_requests_in`, which bounds a disagreement to inside the
    cell instead of spilling a range into whatever follows it.
    """
    text: str = ""
    spans: List[TextSpan] = field(default_factory=list)

    @property
    def styled(self) -> bool:
        return bool(self.spans)


@dataclass
class DocsTableNode:
    """Represents a table in a Google Docs document."""
    rows: List[List[TableCell]] = field(default_factory=list)
    start_index: int = 0
    end_index: int = 0

    def __post_init__(self) -> None:
        """Accept plain strings for cells and normalise them to `TableCell`.

        Cells were `str` before they had to carry inline styling. Callers that pass
        strings are normalised here rather than left to fail later on
        `cell.text` — an `AttributeError` several frames deep inside request
        building, which says nothing about the actual mistake. After this, `rows`
        is uniformly `List[List[TableCell]]` everywhere downstream.
        """
        self.rows = [
            [TableCell(text=cell) if isinstance(cell, str) else cell for cell in row]
            for row in self.rows
        ]

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class DocsImageNode:
    """Represents a standalone (block-level) inline image in a Google Doc.

    v1 scope only covers an image that is the sole content of its own
    paragraph — not one interleaved with running text — mirroring
    DocsTableNode's own paragraph-shaped footprint. `src` holds a URI: on the
    push side, a resolved image URL (http(s):// or a temp Drive share link,
    see image_source.py); on the pull side, the Docs API's `contentUri` for
    the embeddedObject. `object_id` is the Docs `inlineObjectId`, present only
    when parsed from a live document (pull) — never set on a node built from
    markdown (push), since Docs assigns it on insert.

    `mermaid_source` carries the raw diagram text of a ```mermaid fenced code
    block through to resolve time (see image_source.py's MermaidSource); it
    is never set on a node parsed from a live document (pull has no notion of
    "this image came from a mermaid fence").
    """
    src: str = ""
    alt: str = ""
    start_index: int = 0
    end_index: int = 0
    object_id: Optional[str] = None
    width_pt: Optional[float] = None
    height_pt: Optional[float] = None
    mermaid_source: Optional[str] = None


class DocsStructureParser:
    """Parse a Google Docs document dict into a list of DocsParagraphNode."""

    def __init__(self) -> None:
        # Per-instance, not a class attribute: a shared mutable default would
        # accumulate across every parse in the process and put one document's
        # warning on another's pull.
        self._unreadable_links: List[str] = []

    @property
    def unreadable_links(self) -> List[str]:
        """Link kinds the last `parse()` could not express in markdown.

        A `Link` union member this parser does not handle — a bookmark, or a
        link to a whole tab — comes back as no link at all, so `pull` writes
        the text without it and the author's file loses the reference
        *silently*. Table cells route through the same `_parse_link`, so a
        bookmark/tab link inside a cell is covered too.

        One entry per kind, in first-seen order. Not raised and not a
        failure: the document is fine, and this is what markdown can
        express — it exists so the loss is reported, not just made.
        """
        return list(self._unreadable_links)

    def parse(self, doc: dict) -> List[Union[DocsParagraphNode, DocsTableNode, DocsImageNode]]:
        """
        Parse a Google Docs document dict.

        Handles both tabs-based format (doc['tabs'][0]['documentTab']['body']['content'])
        and legacy single-tab format (doc['body']['content']).

        Args:
            doc: Full Google Docs document resource dict (from documents.get())

        Returns:
            List of DocsParagraphNode in document order.

        Raises:
            KeyError: If the document has neither 'tabs' nor 'body' key.
        """
        # Determine body content — handle tabs-based and legacy structure
        if "tabs" in doc and doc["tabs"]:
            tab_doc = doc["tabs"][0].get("documentTab", doc)
            body = tab_doc.get("body", {})
            lists = tab_doc.get("lists", {})
            inline_objects = tab_doc.get("inlineObjects", {})
        elif "body" in doc:
            body = doc["body"]
            lists = doc.get("lists", {})
            inline_objects = doc.get("inlineObjects", {})
        else:
            raise KeyError("Document has neither 'tabs' nor 'body' key")

        # Reset per parse, so re-parsing the same instance does not accumulate.
        self._unreadable_links = []

        content = body.get("content", [])
        nodes: List[Union[DocsParagraphNode, DocsTableNode, DocsImageNode]] = []
        for position, element in enumerate(content):
            if "paragraph" in element:
                image_node = self._parse_image_paragraph(element, inline_objects)
                if image_node is not None:
                    nodes.append(image_node)
                    continue
                node = self._parse_paragraph(element, lists)
                if node is None:
                    continue
                node.precedes_structural_element = self._precedes_structural_element(
                    content, position
                )
                nodes.append(node)
            elif "table" in element:
                nodes.append(self._parse_table(element))
            # sectionBreak, tableOfContents are silently skipped as nodes — a
            # preceding paragraph still records them via
            # precedes_structural_element so the delete path can see them.

        self._resolve_heading_links(nodes)
        return nodes

    @staticmethod
    def _parse_image_paragraph(
        element: dict, inline_objects: dict
    ) -> Optional[DocsImageNode]:
        """Detect a paragraph whose only content is a single inline image.

        Docs represents an inline image as an `inlineObjectElement` inside
        `paragraph.elements`, never as its own top-level structural element
        (unlike a table). v1 only recognizes an image that is the paragraph's
        sole content — any other textRun with non-whitespace content means
        this is an image interleaved with real text, out of v1 scope, and the
        paragraph falls through to `_parse_paragraph` (where the image's
        1-code-unit footprint is currently dropped from `.text`, a known,
        pre-existing gap — see the off-by-one comment on `_cell_placement` in
        docs_request_builder.py).
        """
        elements = element.get("paragraph", {}).get("elements", [])
        image_element: Optional[dict] = None
        for pe in elements:
            inline_object_element = pe.get("inlineObjectElement")
            if inline_object_element is not None:
                if image_element is not None:
                    return None  # more than one image in the paragraph: out of v1 scope
                image_element = inline_object_element
                continue
            text_run = pe.get("textRun")
            if text_run is not None:
                if text_run.get("content", "").strip("\n"):
                    return None  # real text alongside the image: out of v1 scope
                continue
            return None  # person chip, richLink, etc. alongside the image
        if image_element is None:
            return None

        object_id = image_element.get("inlineObjectId")
        embedded = (
            inline_objects.get(object_id, {})
            .get("inlineObjectProperties", {})
            .get("embeddedObject", {})
        )
        src = embedded.get("contentUri") or embedded.get("imageProperties", {}).get(
            "contentUri", ""
        )
        size = embedded.get("size", {})
        width_pt = size.get("width", {}).get("magnitude")
        height_pt = size.get("height", {}).get("magnitude")
        return DocsImageNode(
            src=src or "",
            alt=embedded.get("description", "") or embedded.get("title", ""),
            start_index=element.get("startIndex", 0),
            end_index=element.get("endIndex", 0),
            object_id=object_id,
            width_pt=width_pt,
            height_pt=height_pt,
        )

    @staticmethod
    def _resolve_heading_links(
        nodes: List[Union[DocsParagraphNode, DocsTableNode, DocsImageNode]],
    ) -> None:
        """Rewrite `headingId` links into markdown anchors, in place.

        Runs after the whole body is parsed because a heading link can point
        backwards or forwards and the slug of any heading depends on every
        heading before it (duplicate suffixes).

        Without this the read direction loses the link outright — a `headingId`
        link carries no `url`, so _parse_paragraph sees no link at all and
        `pull` renders plain text. The anchor then disappears from the markdown
        file while the Doc keeps it, and nothing reports the divergence.

        _parse_paragraph has already written "#" + headingId into span.link, so
        this only upgrades an id to the friendlier slug. An id with no heading
        of its own in this body (a link into a deleted heading, or into another
        tab) keeps the bare id rather than being dropped:
        heading_anchors.resolve_anchor tries ids before slugs, so the write
        direction takes it straight back.
        """
        id_to_slug = heading_id_to_slug(nodes)
        if not id_to_slug:
            return
        for node in nodes:
            if not isinstance(node, DocsParagraphNode):
                continue
            for span in node.spans:
                if span.link is None or not is_anchor(span.link):
                    continue
                slug = id_to_slug.get(anchor_target(span.link))
                if slug is not None:
                    span.link = "#" + slug

    @staticmethod
    def _precedes_structural_element(content: List[dict], position: int) -> bool:
        """Whether content[position] is directly followed by an undeletable boundary."""
        following = content[position + 1] if position + 1 < len(content) else None
        if not isinstance(following, dict):
            return False
        return any(key in following for key in UNDELETABLE_BOUNDARY_KEYS)

    def _parse_table(self, element: dict) -> DocsTableNode:
        """Parse a structural element that contains a table into a DocsTableNode."""
        table = element["table"]
        rows: List[List[TableCell]] = []
        for table_row in table.get("tableRows", []):
            cells: List[TableCell] = []
            for cell in table_row.get("tableCells", []):
                cells.append(self._parse_cell(cell))
            rows.append(cells)
        return DocsTableNode(
            rows=rows,
            start_index=element.get("startIndex", 0),
            end_index=element.get("endIndex", 0),
        )

    def _parse_cell(self, cell: dict) -> TableCell:
        """Collect a cell's runs into text plus matching spans.

        The joined text is `strip()`ped — a Docs cell paragraph ends in "\n" and
        cells are conventionally compared without surrounding whitespace — so the
        spans are trimmed by the same amount at each end. Letting them keep the
        whitespace would break the spans-concatenate-to-text invariant, and pass 2
        would then place every range in the cell off by the width of the trim.
        """
        spans: List[TextSpan] = []
        for cell_element in cell.get("content", []):
            paragraph = cell_element.get("paragraph")
            if paragraph is None:
                continue
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if text_run is not None:
                    content = text_run.get("content", "")
                    if not content:
                        continue
                    text_style = text_run.get("textStyle", {})
                    font = text_style.get("weightedFontFamily", {}).get("fontFamily", "")
                    spans.append(TextSpan(
                        text=content,
                        bold=bool(text_style.get("bold", False)),
                        italic=bool(text_style.get("italic", False)),
                        link=self._parse_link(text_style.get("link")),
                        monospace="Courier" in font or "mono" in font.lower(),
                    ))
                    continue
                person = pe.get("person")
                if person is not None:
                    name = _person_display_text(person)
                    if name:
                        spans.append(TextSpan(text=name))

        joined = "".join(span.text for span in spans)
        text = joined.strip()
        spans = _trim_spans_to_cell_text(spans, joined, text)
        # An unstyled cell carries no spans, matching DocsParagraphNode: it keeps
        # the diff key and the renderer on the plain-text path.
        if not any(s.bold or s.italic or s.link or s.monospace for s in spans):
            spans = []
        return TableCell(text=text, spans=spans)

    def _parse_paragraph(
        self, element: dict, lists: Optional[dict] = None
    ) -> Optional[DocsParagraphNode]:
        """Parse a structural element that contains a paragraph."""
        paragraph = element["paragraph"]
        paragraph_style = paragraph.get("paragraphStyle", {})
        style = paragraph_style.get("namedStyleType", "NORMAL_TEXT")

        start_index = element.get("startIndex", 0)
        end_index = element.get("endIndex", 0)

        # Extract text from all TextRuns, collecting spans
        spans: List[TextSpan] = []
        text_parts: List[str] = []

        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run is None:
                person = pe.get("person")
                if person is not None:
                    name = _person_display_text(person)
                    if name:
                        text_parts.append(name)
                        spans.append(TextSpan(text=name))
                continue
            run_content = text_run.get("content", "")
            text_style = text_run.get("textStyle", {})
            bold = text_style.get("bold", False)
            italic = text_style.get("italic", False)
            link = self._parse_link(text_style.get("link"))
            # Monospace: check weightedFontFamily.fontFamily against known monospace fonts
            font_family = text_style.get("weightedFontFamily", {}).get("fontFamily", "")
            font_family_lower = font_family.lower()
            monospace = any(marker in font_family_lower for marker in _MONOSPACE_FONT_MARKERS)

            text_parts.append(run_content)
            spans.append(TextSpan(
                text=run_content,
                bold=bool(bold),
                italic=bool(italic),
                link=link,
                monospace=monospace,
            ))

        raw_text = "".join(text_parts)
        # Strip trailing newline (each paragraph ends with \n in the Docs model)
        text = raw_text.rstrip("\n")

        # A glyph Docs writes to mark a paragraph it renders itself is a *rendering
        # artifact*, not content — markdown has no syntax for one, so the diff read it
        # as a difference the author had asked for and rewrote the block on every push.
        #
        # It is recorded rather than removed. `text` stays faithful to the document,
        # because two different consumers read it: the index arithmetic (delete bounds,
        # span ranges) needs what the document actually contains, and the diff and
        # renderer need what the markdown should say. Stripping it here made the parser
        # lie to the first group — a delete then either covered the glyph, which the
        # API refuses outright (#47), or skipped it, which the API accepts and which
        # orphans the glyph onto the following paragraph. Verified against the live API:
        # `[34052,34069)` → "Invalid deletion range"; `[34053,34069)` → accepted, and
        # the next paragraph came back reading "\ue907mappings:".
        #
        # `projection.project()` is the layer that drops it for the diff, and
        # `render_prefix` is how `DocsRequestBuilder` knows the paragraph belongs to a
        # block it must not take apart.
        render_prefix = self._render_prefix_of(paragraph.get("elements", []))

        spans = self._trim_spans_to_text(spans, len(text))

        # Check for bullet / list item
        bullet = paragraph.get("bullet")
        is_list_item = bullet is not None
        nesting_level = bullet.get("nestingLevel", 0) if bullet else 0
        is_native_checkbox = self._resolve_is_native_checkbox(bullet, lists or {})

        quote_depth = _detect_blockquote_depth(paragraph_style)

        return DocsParagraphNode(
            style=style,
            text=text,
            is_list_item=is_list_item,
            nesting_level=nesting_level,
            start_index=start_index,
            end_index=end_index,
            spans=spans,
            render_prefix=render_prefix,
            is_native_checkbox=is_native_checkbox,
            heading_id=paragraph_style.get("headingId"),
            is_blockquote=quote_depth > 0,
            quote_depth=quote_depth,
        )

    @staticmethod
    def _render_prefix_of(elements: List[dict]) -> str:
        """The leading run(s) Docs writes to mark a paragraph it renders itself.

        Matched **per run**, not against the concatenated text, and that is the whole
        point of the signature:

        * An empty `textRun` — which the API does send — cannot hide the glyph behind
          it. Testing `lstrip` on the joined text and walking spans to match stopped at
          the empty run, read it as "no glyph here", and then reconciled the
          length mismatch by trimming from the *end*, destroying a real character.
        * An author's own Private-Use character is left alone. Someone typing an Apple
          logo or a Nerd Font glyph types it inside a run with their text, so the run
          is not *entirely* PUA and this returns "". Treating any leading PUA as an
          artifact silently altered legitimate content.

        Both shapes are what the live document shows: `run0 = "\ue907"` with
        `textStyle {}`, content following in its own runs. `textStyle` is deliberately
        not part of the test — Docs is free to put a `fontSize` on it.
        """
        prefix: List[str] = []
        for element in elements:
            text_run = element.get("textRun")
            if text_run is None:
                break
            content = text_run.get("content", "")
            if not content:
                continue  # an empty run neither is nor conceals a prefix
            if not _is_all_private_use(content):
                break
            prefix.append(content.strip("\n"))
        return "".join(prefix)

    @staticmethod
    def _trim_spans_to_text(spans: List[TextSpan], keep: int) -> List[TextSpan]:
        """Drop the paragraph-terminating newline from the spans, as .text already does.

        Every Docs paragraph ends with "\\n", and it arrives inside the *last
        textRun's* content — so it lands in that run's span while `.text`
        rstrips it. Two things break on the resulting disagreement, both when
        the run carrying the newline is also the run carrying a mark:

        * `pull` renders the newline *inside* the markdown link, emitting
          ``[it\\n](https://…)`` — which re-parses as a literal
          ``](https://…)`` line rather than a link.
        * The spans then total one code unit more than the paragraph can hold,
          so DocsRequestBuilder._spans_overflow reports the paragraph and pass 2
          drops its styling.

        Trimming to `.text`'s length restores the invariant the rest of the
        pipeline already assumes: the spans concatenate to exactly `.text`.

        Empty spans are dropped on the way out. That is a separate concern from
        the newline — a run whose whole content *is* the newline is removed by
        the trim itself — and covers an empty textRun, which the API can send
        and which the loop above would leave in place: a span with no text
        styles nothing and renders as stray marks (``****``).
        """
        trimmed = list(spans)
        total = sum(len(span.text) for span in trimmed)
        while trimmed and total > keep:
            excess = total - keep
            last = trimmed[-1]
            if len(last.text) <= excess:
                trimmed.pop()
                total -= len(last.text)
            else:
                trimmed[-1] = replace(last, text=last.text[: len(last.text) - excess])
                total = keep
        return [span for span in trimmed if span.text]

    def _parse_link(self, link: Optional[dict]) -> Optional[str]:
        """Flatten a Docs `Link` union into the markdown href it round-trips as.

        Reading only `url` makes a heading link indistinguishable from no link at
        all, so `pull` silently drops the cross-reference.

        `Link` has **six** members, and which one a heading link arrives as
        depends on a request flag rather than on the document: with
        `includeTabsContent=true` it is `heading: {id, tabId}`, and only without
        it is the flat `headingId` returned. Google documents the flat forms as
        legacy. `client.get_document` defaults the flag to True, so the object
        form is the one this parser actually sees — handling only `headingId`
        parses a live tab-scoped document to zero links:

            $ # same doc, one flag apart
            $ ...includeTabsContent=false -> 5 linked spans
            $ ...includeTabsContent=true  -> 0 linked spans

        Both are accepted here so the parser does not depend on how it was
        fetched. Either becomes "#" + the id; _resolve_heading_links then
        upgrades it to the heading's slug once the whole body is known.

        `bookmark`/`bookmarkId` and `tabId` remain unexpressible in markdown and
        return None — expressing a bookmark in markdown is a separate feature,
        out of scope here. They are named so the next reader knows the union is
        closed, and they are now *recorded* on the way out rather than dropped
        in silence: see `unreadable_links`. A default pull does not go through
        this parser for its content, so it is unaffected.
        """
        if not isinstance(link, dict):
            return None
        url = link.get("url")
        if isinstance(url, str) and url:
            return url
        heading_id = link.get("headingId")
        if isinstance(heading_id, str) and heading_id:
            return "#" + heading_id
        heading = link.get("heading")
        if isinstance(heading, dict):
            heading_id = heading.get("id")
            if isinstance(heading_id, str) and heading_id:
                return "#" + heading_id
        if link:
            self._record_unreadable_link(link)
        return None

    def _record_unreadable_link(self, link: dict) -> None:
        """Note a `Link` this parser cannot express, for `pull()` to report."""
        for member, described in (
            ("bookmarkId", "bookmark link"),
            ("bookmark", "bookmark link"),
            ("tabId", "link to a tab"),
        ):
            if link.get(member):
                break
        else:
            described = f"unrecognised link ({', '.join(sorted(link))})"
        self._note_unreadable(described)

    def _note_unreadable(self, described: str) -> None:
        """Record one kind of unreadable link, once, in first-seen order."""
        if described not in self._unreadable_links:
            self._unreadable_links.append(described)

    def _resolve_is_native_checkbox(self, bullet: Optional[dict], lists: dict) -> bool:
        """Resolve whether a bullet paragraph is a native BULLET_CHECKBOX glyph.

        Looks up bullet.listId -> lists[listId].listProperties.nestingLevels[n]
        .glyphType and returns True only when it equals GLYPH_TYPE_UNSPECIFIED
        (the confirmed, if counter-intuitive, signature Google Docs uses for a
        checkbox bullet — see ADR-001's Verification Evidence). Defensively
        returns False (never raises) on any missing/malformed piece — e.g. a
        bullet paragraph with no listId, or a lists map that doesn't contain
        the referenced list.
        """
        if not bullet:
            return False
        list_id = bullet.get("listId")
        if not list_id:
            return False
        nesting_level = bullet.get("nestingLevel", 0)

        list_entry = lists.get(list_id)
        if not isinstance(list_entry, dict):
            return False
        list_properties = list_entry.get("listProperties")
        if not isinstance(list_properties, dict):
            return False
        nesting_levels = list_properties.get("nestingLevels")
        if not isinstance(nesting_levels, list):
            return False
        if not isinstance(nesting_level, int) or nesting_level < 0 or nesting_level >= len(nesting_levels):
            return False
        level_props = nesting_levels[nesting_level]
        if not isinstance(level_props, dict):
            return False

        return level_props.get("glyphType") == "GLYPH_TYPE_UNSPECIFIED"
