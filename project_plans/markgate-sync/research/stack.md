# Stack Research: Google Docs batchUpdate, Structural Diff, and Three-Way Merge

## Google Docs batchUpdate Request Types

The `documents.batchUpdate` endpoint accepts a list of `Request` objects, each containing exactly one of the following (union field `request`). All requests in a batch are applied **atomically** — if any fails, none are applied.

**Core request types for markdown-to-Docs push:**

| Request Type | Purpose | Key Fields |
|---|---|---|
| `insertText` | Insert text at index or end-of-segment | `text`, `location.index`, `location.segmentId` |
| `deleteContentRange` | Delete a range of content | `range.startIndex`, `range.endIndex` |
| `updateTextStyle` | Apply bold, italic, link, font | `textStyle`, `fields` (FieldMask), `range` |
| `updateParagraphStyle` | Set heading level, alignment | `paragraphStyle`, `fields`, `range` |
| `createParagraphBullets` | Apply bullet/numbered list preset | `range`, `bulletPreset` (enum) |
| `deleteParagraphBullets` | Remove bullet formatting | `range` |
| `insertInlineImage` | Insert image from public URI | `uri`, `objectSize`, `location` |
| `createNamedRange` | Tag a range for later reference | `name`, `range` |
| `replaceNamedRangeContent` | Replace named-range content atomically | `namedRangeId`, `text` or `insertText` |

**Heading style values** for `updateParagraphStyle.paragraphStyle.namedStyleType`: `HEADING_1` through `HEADING_6`, `NORMAL_TEXT`, `TITLE`, `SUBTITLE`.

**Bullet presets**: `BULLET_DISC_CIRCLE_SQUARE` (unordered), `NUMBERED_DECIMAL_ALPHA_ROMAN` (ordered).

**Field masks**: `updateTextStyle` and `updateParagraphStyle` require a `fields` FieldMask string specifying which properties to set. Use `"bold"`, `"italic"`, `"link"`, `"weightedFontFamily"`, or `"*"` for all. This prevents unintentionally clearing other styles.

**InsertText behavior**: Inserting a `\n` implicitly creates a new `Paragraph`, inheriting the style of the paragraph at the insertion point. Text style for inserted text matches the text immediately before the insertion index. Control characters `U+0000-U+0008` and `U+E000-U+F8FF` are stripped.

**UpdateTextStyle for links**: Set `textStyle.link.url` with fields mask `"link"`.

**updateTextStyle for monospace (code)**: Set `textStyle.weightedFontFamily.fontFamily = "Courier New"` with fields `"weightedFontFamily"`.

## Structural Diff Approach for Documents

### AST Diffing Strategy

The recommended approach for comment-preserving push is a **paragraph-level diff**, not a character-level one:

1. Parse both the current Docs JSON and the target markdown into a list of `ParagraphNode` objects (each node contains: type, text content, style attributes).
2. Use `difflib.SequenceMatcher` on the list of paragraphs (comparing by normalized text content hash) to produce opcodes: `equal`, `replace`, `insert`, `delete`.
3. For `equal` paragraphs, only apply style updates if needed (no text changes = comment anchors on that range survive).
4. For `replace`/`insert`/`delete`, build the minimal `deleteContentRange` + `insertText` + `updateParagraphStyle` requests.

### Named Range Strategy (Alternative)

An alternative that provides stronger comment preservation: before the push, create a `namedRange` around each paragraph the diff shows as unchanged. After batchUpdate, those named ranges confirm the unchanged paragraphs kept their indexes. Use `replaceNamedRangeContent` to update only changed paragraphs by name rather than by absolute index.

### Implementation order for requests in a batch

Always order by **descending index** (write backwards). Inserting text at index N shifts all indices above N by the insertion length. Processing from end-to-start means prior operations don't invalidate later indices. The official guide explicitly recommends this: "do the insertion at the highest-numbered index first."

## Three-Way Merge: merge3 vs difflib

### merge3 Package (recommended)

`merge3` (PyPI: `merge3`, GitHub: `breezy-team/merge3`, latest 0.0.16, Oct 2025) is a direct Python port of Bazaar's merge3 algorithm. Usage:

```python
import merge3
m3 = merge3.Merge3(base_lines, this_lines, other_lines)
# Returns iterator of chunks: 'unchanged', 'a', 'b', or 'conflict'
result = list(m3.merge_lines(
    name_a='ours', name_b='theirs',
    start_marker='<<<<<<< ours',
    mid_marker='=======',
    end_marker='>>>>>>> theirs'
))
```

Conflict markers default to `<<<<`, `----`, `>>>>` (annotated form) but `merge_lines()` produces standard Git-style `<<<<<<<`/`=======`/`>>>>>>>` when names are provided. Input is **sequences of lines** (strings ending in `\n`).

### difflib-based fallback

`difflib.SequenceMatcher` provides two-way diff as opcodes. A three-way merge can be built by:
1. Diff BASE→OURS and BASE→THEIRS independently via `SequenceMatcher`.
2. Walk both opcode lists simultaneously; where both changed the same line range, emit a conflict block.

This is more code to write correctly. `merge3` is preferred as a dependency unless zero-dependency is a constraint.

### `three-merge` package

`three-merge` (PyPI) is a simpler alternative. Its conflict markers use `<<<<<<< ++ [content] ======= ++ [content] >>>>>>>` format — non-standard vs Git. Avoid this unless the exact marker format doesn't matter.

### Recommendation

Use `merge3` directly. Split file content into lines with `content.splitlines(keepends=True)`. Pass `base_lines`, `this_lines`, `other_lines`. Call `merge_lines()` with explicit marker strings to produce Git-compatible `<<<<<<<`/`=======`/`>>>>>>>` output. Detect conflicts by checking if any output line starts with `<<<<<<<`.
