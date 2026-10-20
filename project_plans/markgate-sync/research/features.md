# Features Research: Google Docs Structure, Confluence v2 Comments, and ConfluencePage Model

## Google Docs documents.get Response Structure

### Top-Level Schema

```json
{
  "documentId": "string",
  "title": "string",
  "revisionId": "string",
  "suggestionsViewMode": "enum",
  "tabs": [ Tab ]
}
```

`revisionId` is the key field for version tracking (see architecture.md). The document body lives inside `tabs[0].documentTab.body`.

### Body → StructuralElement hierarchy

```
document.tabs[N].documentTab.body.content: [ StructuralElement ]
  StructuralElement:
    startIndex: int   (UTF-16 code unit offset from segment start)
    endIndex: int
    paragraph: Paragraph   -- OR --
    table: Table           -- OR --
    tableOfContents: ...   -- OR --
    sectionBreak: ...

  Paragraph:
    elements: [ ParagraphElement ]
    paragraphStyle: ParagraphStyle   (namedStyleType, alignment, indent, etc.)
    bullet: Bullet?   (present if paragraph is in a list)

  ParagraphElement:
    startIndex: int
    endIndex: int
    textRun: TextRun     -- OR --
    inlineObjectElement: InlineObjectElement  -- OR --
    columnBreak: ...     -- OR --
    footnoteReference: ...

  TextRun:
    content: string      (the actual text, including trailing \n for paragraph end)
    textStyle: TextStyle (bold, italic, underline, link, weightedFontFamily, etc.)
```

### Parsing Strategy for DocsStructureParser

Walk `body.content` array. For each `StructuralElement`:
- If `paragraph` present: extract `namedStyleType` from `paragraphStyle` and collect `TextRun` content strings to build paragraph text. Check `bullet` field for list membership and `nestingLevel`.
- If `table` present: skip (deferred per requirements).
- Concatenate `textRun.content` values within a paragraph to get full paragraph text.
- Strip the trailing `\n` (paragraph terminator) before storing.

**Key edge case**: A paragraph can have multiple `TextRun` elements with different `TextStyle` (e.g., "Hello **world** foo" = three runs). The parser must merge these runs per paragraph node, storing inline style spans separately.

### Indexes

Indexes are zero-based UTF-16 code units. The body's first element typically has `startIndex=1` (index 0 is reserved for the body start sentinel). The document always ends with a terminal newline at the last `endIndex`. **Never delete the last newline** — the API returns 400.

## Parsing Docs JSON into Comparable AST

The recommended AST node types that map 1:1 to markdown constructs:

```python
@dataclass
class DocsParagraphNode:
    style: str          # 'HEADING_1'..'HEADING_6', 'NORMAL_TEXT'
    text: str           # normalized, stripped of trailing \n
    spans: list[TextSpan]  # for inline style preservation
    is_list_item: bool
    nesting_level: int
    list_id: str | None
    start_index: int    # from original Docs JSON (for comment-anchor tracking)
    end_index: int
```

When diffing, compare nodes by `(style, text)` tuple. The `start_index`/`end_index` from the current doc are used to identify which ranges have unchanged content (comment anchors remain valid on those ranges).

## Confluence v2 API Comment Endpoints

### Inline Comments

The `ConfluenceCommentClient` already implements all relevant endpoints under `/wiki/api/v2/`. The key methods for the pull sidecar feature:

**`get_page_inline_comments(page_id)`** → `GET /wiki/api/v2/pages/{id}/inline-comments`

Response per comment (relevant fields):
```json
{
  "id": "string",
  "status": "current",
  "title": "string",
  "pageId": "string",
  "version": {
    "number": 1,
    "createdAt": "ISO-8601",
    "authorId": "string"
  },
  "body": {
    "storage": { "value": "...", "representation": "storage" }
  },
  "inlineProperties": {
    "originalSelection": "the highlighted text"
  }
}
```

**Known issue**: The v2 inline-comments endpoint has been reported to return 404 in some edge cases where v1 works. The existing `comment_client.py` retains the v1 endpoint `get_comments()` for rendered-HTML fallback — use that as fallback if v2 returns 404.

**`get_page_footer_comments(page_id)`** → `GET /wiki/api/v2/pages/{id}/footer-comments`

Response is similar structure without `inlineProperties`.

### Comment Anchor Preservation on Push

When `update_page` replaces the entire ADF body, **all inline comment anchors are broken** because the anchor is tied to character positions in the stored content. This is a known Atlassian limitation (confirmed in Atlassian MCP Server issue #54). The requirements specify comment pull only (sidecar .comments.md) — not round-trip push of comments — so this is acceptable for the current scope.

## ConfluencePage Model

Located at `src/markgate/backends/confluence/models/page.py`. Key fields:

```python
@dataclass
class ConfluencePage:
    title: str
    content: Union[str, Dict[str, Any]]  # Must be ADF dict
    parent_id: str
    space_key: Optional[str] = None
    id: Optional[str] = None             # Required for updates
    version: Optional[int] = None        # Required for updates; to_api_data() increments by 1
    labels: List[str] = field(default_factory=list)
    force_update: bool = False
    restrictions: Optional[List[Dict[str, Any]]] = None
```

`to_api_data(for_update=True)` produces the dict for the API call. It automatically increments `version + 1`. The `content` field **must** be an ADF dict (not a string); passing a non-ADF string raises `ADFConversionError`.

### Fixing the update_page call in confluence/backend.py

The current broken call passes kwargs. The fix is:

```python
page = ConfluencePage(
    id=page_id,
    title=title,
    content=adf_content,    # already an ADF dict from markdown→ADF conversion
    parent_id=parent_id,
    version=current_version,  # fetched from get_page() response
)
self._client.update_page(page)
```

The `ConfluenceClient.update_page()` signature expects a single `ConfluencePage` object and calls `page.to_api_data(for_update=True)` internally.

## Comment Sidecar Generation

When pulling a Confluence page, call both comment methods and generate `.comments.md` only if at least one comment exists:

```python
inline = self._comment_client.get_page_inline_comments(page_id)
footer = self._comment_client.get_page_footer_comments(page_id)
if inline or footer:
    write_sidecar(local_path, page_title, inline, footer)
```

The `inlineProperties.originalSelection` field provides the anchored text excerpt to render as `> Selection: "..."` in the sidecar. Footer comments have no anchor text.
