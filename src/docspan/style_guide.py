"""Per-backend authoring guidance, shipped inside the installed package.

This is the single source of truth for "how to write markdown that renders
correctly on backend X" — the `docspan style-guide` CLI command prints or
embeds it into a consuming repo (e.g. its CLAUDE.md), so upgrading docspan
and re-running the command picks up new guidance automatically. Anything
added here should also be reflected in docs/backends/<backend>.md's
Limitations section for readers of this repo's own docs.
"""

from __future__ import annotations

GOOGLE_DOCS_STYLE_GUIDE = """\
## Google Docs (docspan) authoring notes

- **One image per line.** `![alt](path)` uploads correctly only when it's alone on \
its own line — an image mixed into a paragraph with running text is left as plain \
text.
- **Mermaid fences render as static PNGs**, not editable diagrams, and don't \
round-trip back into a ` ```mermaid ` fence on pull.
- **Table cells hold a single paragraph.** Don't rely on multiple paragraphs \
inside one markdown table cell.
"""

CONFLUENCE_STYLE_GUIDE = """\
## Confluence (docspan) authoring notes

- Blockquotes (`>`) are supported natively and render as a real Confluence quote \
block.
"""

STYLE_GUIDES: dict[str, str] = {
    "google_docs": GOOGLE_DOCS_STYLE_GUIDE,
    "confluence": CONFLUENCE_STYLE_GUIDE,
}


def render_style_guide(backend: str | None = None) -> str:
    """Return the style-guide markdown for one backend, or all of them.

    Raises KeyError for an unknown backend name (the caller already knows
    the valid set from `STYLE_GUIDES`, so this is a programmer error, not a
    user-facing validation case).
    """
    if backend is None:
        return "\n".join(STYLE_GUIDES[b] for b in STYLE_GUIDES)
    return STYLE_GUIDES[backend]


def _markers(backend: str | None) -> tuple[str, str]:
    tag = backend or "all"
    return (
        f"<!-- docspan:style-guide:{tag}:begin (auto-generated; re-run `docspan style-guide` to refresh) -->",
        f"<!-- docspan:style-guide:{tag}:end -->",
    )


def upsert_managed_block(existing: str, backend: str | None) -> str:
    """Replace docspan's managed block in `existing`, or append a new one.

    The markers make re-running `docspan style-guide --write` idempotent: it
    updates only the block it owns rather than duplicating content on every
    run or clobbering the rest of the file.
    """
    begin, end = _markers(backend)
    block = f"{begin}\n{render_style_guide(backend)}\n{end}\n"

    start_idx = existing.find(begin)
    if start_idx == -1:
        separator = "\n" if existing and not existing.endswith("\n") else ""
        return f"{existing}{separator}\n{block}" if existing else block

    end_idx = existing.find(end, start_idx)
    if end_idx == -1:
        # Begin marker present without a matching end marker means the block
        # was hand-edited into an inconsistent state — append fresh rather
        # than guessing where the corrupted block ends.
        separator = "\n" if not existing.endswith("\n") else ""
        return f"{existing}{separator}\n{block}"

    return existing[:start_idx] + block + existing[end_idx + len(end):]
