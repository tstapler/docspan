"""markgate CLI — push, pull, auth, status."""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from markgate.config import load_config
from markgate.backends import BACKENDS

app = typer.Typer(
    name="markgate",
    help="Push and pull markdown to Google Docs and Confluence.",
    add_completion=False,
    rich_markup_mode="rich",
)
auth_app = typer.Typer(help="Manage authentication for backends.")
app.add_typer(auth_app, name="auth")

console = Console()
err_console = Console(stderr=True, style="bold red")


def _get_backend(backend_name: str, config):
    cls = BACKENDS.get(backend_name)
    if not cls:
        err_console.print(f"Unknown backend '{backend_name}'. Available: {list(BACKENDS.keys())}")
        raise typer.Exit(1)
    return cls(config.model_dump())


@app.command()
def push(
    files: Optional[list[str]] = typer.Argument(None, help="Local markdown files to push (default: all mappings)"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to markgate.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
):
    """Push local markdown to remote docs."""
    config = load_config(config_path)
    mappings = config.mappings

    if files:
        mappings = [m for m in mappings if m.local in files]
        if not mappings:
            err_console.print(f"No mappings found for: {files}")
            raise typer.Exit(1)

    if not mappings:
        err_console.print("No mappings configured. Add entries to markgate.yaml.")
        raise typer.Exit(1)

    results = []
    for mapping in mappings:
        if mapping.direction == "pull":
            console.print(f"[dim]Skipping {mapping.local} (pull-only)[/dim]")
            continue
        if dry_run:
            console.print(f"[yellow]dry-run[/yellow]  {mapping.local} → [{mapping.backend}] {mapping.remote_id}")
            continue
        backend = _get_backend(mapping.backend, config)
        result = backend.push(mapping.local, mapping.remote_id)
        results.append((mapping.local, result))
        icon = "✓" if result.status == "ok" else "✗"
        style = "green" if result.status == "ok" else "red"
        console.print(f"[{style}]{icon}[/{style}]  {mapping.local} → {result.url or mapping.remote_id}")
        if result.message:
            console.print(f"   [dim]{result.message}[/dim]")

    errors = [r for _, r in results if r.status == "error"]
    if errors:
        raise typer.Exit(1)


@app.command()
def pull(
    files: Optional[list[str]] = typer.Argument(None, help="Local paths to pull into (default: all mappings)"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Pull remote docs into local markdown files."""
    config = load_config(config_path)
    mappings = config.mappings

    if files:
        mappings = [m for m in mappings if m.local in files]

    if not mappings:
        err_console.print("No mappings configured.")
        raise typer.Exit(1)

    for mapping in mappings:
        if mapping.direction == "push":
            console.print(f"[dim]Skipping {mapping.local} (push-only)[/dim]")
            continue
        if dry_run:
            console.print(f"[yellow]dry-run[/yellow]  [{mapping.backend}] {mapping.remote_id} → {mapping.local}")
            continue
        backend = _get_backend(mapping.backend, config)
        result = backend.pull(mapping.remote_id, mapping.local)
        icon = "✓" if result.status == "ok" else "✗"
        style = "green" if result.status == "ok" else "red"
        console.print(f"[{style}]{icon}[/{style}]  {mapping.remote_id} → {mapping.local}")
        if result.message:
            console.print(f"   [dim]{result.message}[/dim]")


@app.command()
def status(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
):
    """Show current mapping status."""
    config = load_config(config_path)

    if not config.mappings:
        console.print("[yellow]No mappings configured.[/yellow] Add entries to markgate.yaml.")
        return

    table = Table(title="markgate mappings")
    table.add_column("Local file", style="cyan")
    table.add_column("Backend", style="magenta")
    table.add_column("Remote ID")
    table.add_column("Direction")

    for m in config.mappings:
        table.add_row(m.local, m.backend, m.remote_id, m.direction)

    console.print(table)


@auth_app.command("setup")
def auth_setup(
    backend: str = typer.Argument(..., help="Backend to authenticate: google_docs | confluence"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
):
    """Interactive authentication setup for a backend."""
    config = load_config(config_path)
    b = _get_backend(backend, config)
    b.auth_setup()


def main():
    app()


if __name__ == "__main__":
    main()
