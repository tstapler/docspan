"""``docspan mcp`` / ``index`` / ``crawl``: query tooling layered over the existing backends."""
from __future__ import annotations

import time
from typing import Callable, Optional

import typer
from rich.console import Console

from docspan.core.xdg import cache_dir_for_prefix, scratch_dir_for_prefix

console = Console()
err_console = Console(stderr=True, style="bold red")

mcp_app = typer.Typer(
    help="Run the MCP server for querying Google Docs (needs the mcp extra).",
    invoke_without_command=True,
)


def _build_service(resolve: Callable, config_path: Optional[str], prefix: Optional[str], with_index: bool = True):
    from docspan.backends.google_docs.backend import GoogleDocsBackend
    from docspan.core import get_state_path
    from docspan.mcp.index import DocspanIndex
    from docspan.mcp.service import DocService

    config, markgate_path, resolved_prefix = resolve(config_path, prefix)
    backend = GoogleDocsBackend.from_config(config)
    cache = cache_dir_for_prefix(resolved_prefix)
    index = DocspanIndex(cache / "fts5.db") if with_index else None
    service = DocService(
        lambda: backend.client, scratch_dir_for_prefix(resolved_prefix), index=index,
        config=config, state_path=get_state_path(markgate_path, resolved_prefix))
    return service, cache


def register(app: typer.Typer, resolve: Callable) -> None:
    """Mount the commands on ``app``; ``resolve`` is main's ``_resolve`` (config + prefix lookup)."""

    @mcp_app.callback()
    def _mcp(
        ctx: typer.Context,
        config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to markgate.yaml"),
        prefix: Optional[str] = typer.Option(None, "--prefix", "-p", help="Central-config project prefix"),
    ) -> None:
        """Start the stdio MCP server (read tools only)."""
        if ctx.invoked_subcommand is not None:
            return
        from docspan.mcp.server import build_server

        service, _ = _build_service(resolve, config_path, prefix)
        build_server(service).run(transport="stdio")

    app.add_typer(mcp_app, name="mcp")

    @app.command("index")
    def index_cmd(
        force: bool = typer.Option(False, "--force", help="Re-index even if the revision is unchanged"),
        delay: float = typer.Option(0.5, "--delay", help="Seconds between API calls"),
        config_path: Optional[str] = typer.Option(None, "--config", "-c"),
        prefix: Optional[str] = typer.Option(None, "--prefix", "-p"),
    ) -> None:
        """Build/refresh the full-text index over every mapped Google Doc (skips unchanged revisions)."""
        service, cache = _build_service(resolve, config_path, prefix)
        ids = service.mapped_google_doc_ids()
        indexed = skipped = 0
        for i, doc_id in enumerate(ids):
            if i:
                time.sleep(delay)
            if service.index_doc(doc_id, force=force):
                indexed += 1
            else:
                skipped += 1
        console.print(f"Indexed {indexed}, skipped {skipped} unchanged → {cache / 'fts5.db'}")

    @app.command("crawl")
    def crawl_cmd(
        url: str = typer.Argument(..., help="Google Doc URL or id to start from"),
        depth: int = typer.Option(2, "--depth", help="Link hops to follow from the start doc"),
        max_docs: int = typer.Option(25, "--max-docs", help="Circuit breaker: stop after this many docs"),
        delay: float = typer.Option(0.5, "--delay", help="Seconds between docs"),
        scope: str = typer.Option("all", "--scope", help="'all' follows every Doc link; 'mapped' only docs in the config"),
        config_path: Optional[str] = typer.Option(None, "--config", "-c"),
        prefix: Optional[str] = typer.Option(None, "--prefix", "-p"),
    ) -> None:
        """Pull a doc and the docs it links to (BFS), indexing each and recording the link graph."""
        from docspan.mcp.crawl import crawl, write_graph
        from docspan.mcp.links import extract_doc_id

        if scope not in ("all", "mapped"):
            err_console.print("--scope must be 'all' or 'mapped'")
            raise typer.Exit(2)
        service, cache = _build_service(resolve, config_path, prefix)
        allowed = None
        if scope == "mapped":
            allowed = set(service.mapped_google_doc_ids())
        start = extract_doc_id(url)
        result = crawl(service, start, depth=depth, max_docs=max_docs, delay=delay, allowed_ids=allowed)
        write_graph(cache / "graph.json", start, result)
        console.print(f"Fetched {len(result.fetched)} doc(s), {len(result.edges)} edge(s) → {cache / 'graph.json'}")
        for doc_id, why in result.failed.items():
            err_console.print(f"failed {doc_id}: {why}")
        if result.truncated:
            console.print(f"[yellow]Stopped at --max-docs {max_docs}; more linked docs were not fetched[/yellow]")
