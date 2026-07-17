"""docspan CLI — push, pull, auth, status, conflicts."""

from __future__ import annotations

import logging
import os
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

import typer
from rich.console import Console
from rich.table import Table

from docspan.backends import BACKENDS
from docspan.config import (
    MarkgateConfig,
    load_central_config,
    load_config,
    resolve_active_project,
)
from docspan.core import (
    MappingState,
    SyncState,
    get_base_content,
    get_state_dir,
    get_state_path,
    orchestrate_pull,
    orchestrate_push,
    record_state,
)
from docspan.core.paths import BASE_STORE_DIR, ORIG_SUFFIX, STATE_FILENAME

app = typer.Typer(
    name="docspan",
    help="Push and pull markdown to Google Docs and Confluence.",
    add_completion=False,
    rich_markup_mode="rich",
)
auth_app = typer.Typer(help="Manage authentication for backends.")
conflicts_app = typer.Typer(help="Manage merge conflicts.")
config_app = typer.Typer(help="Manage the central docspan config (project registry).")
app.add_typer(auth_app, name="auth")
app.add_typer(conflicts_app, name="conflicts")
app.add_typer(config_app, name="config")

console = Console()
err_console = Console(stderr=True, style="bold red")


# ─────────────────────────────────────────────────────────────────────────────
# Backend factory
# ─────────────────────────────────────────────────────────────────────────────

def _can_prompt() -> bool:
    """True only when we can safely prompt the user (real TTY, not CI)."""
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.getenv("CI")


def _get_backend(backend_name: str, config: MarkgateConfig, config_path: Optional[str] = None):
    cls = BACKENDS.get(backend_name)
    if not cls:
        err_console.print(
            f"Unknown backend '{backend_name}'. Available: {list(BACKENDS.keys())}"
        )
        raise typer.Exit(1)
    backend = cls.from_config(config)
    try:
        backend.validate_config()
    except ValueError as exc:
        # Auto-prompt the user to set up credentials inline (interactive only).
        if _can_prompt() and typer.confirm(f"{exc}\n\nRun setup now?", default=True):
            backend.auth_setup(config_path=config_path)
            backend = cls.from_config(load_config(config_path))
            try:
                backend.validate_config()
            except ValueError as exc2:
                err_console.print(f"Still not configured: {exc2}")
                raise typer.Exit(1)
        else:
            err_console.print(f"Configuration error: {exc}")
            raise typer.Exit(1)
    return backend


def _load_state(state_path: str) -> SyncState:
    try:
        return SyncState.load(state_path)
    except FileNotFoundError:
        return SyncState()


def _resolve(config_path: Optional[str], prefix: Optional[str]):
    """Resolve the active markgate config + storage prefix from flags / central config.

    Returns (config, markgate_path, prefix). Storage helpers take (markgate_path, prefix):
    a prefix routes state under XDG; no prefix keeps legacy beside-the-file storage.
    """
    markgate_path, resolved_prefix = resolve_active_project(prefix=prefix, config_path=config_path)
    config = load_config(markgate_path)
    return config, markgate_path, resolved_prefix


# ─────────────────────────────────────────────────────────────────────────────
# push command
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def push(
    files: Optional[list[str]] = typer.Argument(
        None, help="Local markdown files to push (default: all mappings)"
    ),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to markgate.yaml"),
    prefix: Optional[str] = typer.Option(None, "--prefix", "-p", help="Central-config project prefix"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
) -> None:
    """Push local markdown to remote docs."""
    config, config_path, prefix = _resolve(config_path, prefix)
    mappings = config.mappings

    if files:
        mappings = [m for m in mappings if m.local in files]
        if not mappings:
            err_console.print(f"No mappings found for: {files}")
            raise typer.Exit(1)

    if not mappings:
        err_console.print("No mappings configured. Add entries to markgate.yaml.")
        raise typer.Exit(1)

    state_path = get_state_path(config_path, prefix)
    state_dir = get_state_dir(config_path, prefix)
    state = _load_state(state_path)

    had_error = False
    for mapping in mappings:
        if mapping.direction == "pull":
            console.print(f"[dim]Skipping {mapping.local} (pull-only)[/dim]")
            continue
        if dry_run:
            console.print(
                f"[yellow]dry-run[/yellow]  {mapping.local} → [{mapping.backend}] {mapping.remote_id}"
            )
            continue

        backend = _get_backend(mapping.backend, config, config_path)
        outcome = orchestrate_push(mapping, backend, state, state_dir, state_path)
        result = outcome.result

        icon = "✓" if result.status in ("ok", "skipped") else "✗"
        style = "green" if result.status in ("ok", "skipped") else "red"
        console.print(f"[{style}]{icon}[/{style}]  {mapping.local} → {result.url or mapping.remote_id}")
        if result.message:
            console.print(f"   [dim]{result.message}[/dim]")
        if result.status == "ok" and not outcome.state_saved:
            console.print("   [yellow]Warning: could not save sync state[/yellow]")
        if result.status == "error":
            had_error = True

    if had_error:
        raise typer.Exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# pull command
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def pull(
    files: Optional[list[str]] = typer.Argument(
        None, help="Local paths to pull into (default: all mappings)"
    ),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
    prefix: Optional[str] = typer.Option(None, "--prefix", "-p", help="Central-config project prefix"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Pull remote docs into local markdown files."""
    config, config_path, prefix = _resolve(config_path, prefix)
    mappings = config.mappings

    if files:
        mappings = [m for m in mappings if m.local in files]

    if not mappings:
        err_console.print("No mappings configured.")
        raise typer.Exit(1)

    state_path = get_state_path(config_path, prefix)
    state_dir = get_state_dir(config_path, prefix)
    state = _load_state(state_path)

    had_error = False
    for mapping in mappings:
        if mapping.direction == "push":
            console.print(f"[dim]Skipping {mapping.local} (push-only)[/dim]")
            continue
        if dry_run:
            console.print(
                f"[yellow]dry-run[/yellow]  [{mapping.backend}] {mapping.remote_id} → {mapping.local}"
            )
            continue

        backend = _get_backend(mapping.backend, config, config_path)
        outcome = orchestrate_pull(mapping, backend, state, state_dir, state_path)

        if outcome.action == "up-to-date":
            console.print(f"[dim]up to date[/dim]  {mapping.local}")
        elif outcome.action == "local-only":
            console.print(
                f"[yellow]warning[/yellow]  {mapping.local} has local changes not yet pushed. "
                "Pull skipped. Push first or use 'docspan conflicts resolve'."
            )
        elif outcome.action == "merged":
            console.print(f"[yellow]merging[/yellow]  {mapping.local}")
            if outcome.has_conflicts:
                console.print(
                    f"   [yellow]Merge conflicts ({outcome.conflict_count}) written to "
                    f"{mapping.local}. Resolve with: docspan conflicts resolve {mapping.local}[/yellow]"
                )
            else:
                console.print("   [green]Merged cleanly.[/green]")
        elif outcome.action == "error":
            had_error = True
            result = outcome.result
            err_console.print(
                f"✗  {mapping.remote_id} → {mapping.local}: "
                f"{result.message if result else 'unknown error'}"
            )
        else:
            # first-sync or fast-forward
            result = outcome.result
            if result:
                icon = "✓" if result.status == "ok" else "✗"
                style = "green" if result.status == "ok" else "red"
                console.print(f"[{style}]{icon}[/{style}]  {mapping.remote_id} → {mapping.local}")
                if result.message:
                    console.print(f"   [dim]{result.message}[/dim]")

    if had_error:
        raise typer.Exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# status command
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def status(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
    prefix: Optional[str] = typer.Option(None, "--prefix", "-p", help="Central-config project prefix"),
) -> None:
    """Show current mapping status."""
    config, config_path, prefix = _resolve(config_path, prefix)

    if not config.mappings:
        console.print("[yellow]No mappings configured.[/yellow] Add entries to markgate.yaml.")
        return

    table = Table(title="docspan mappings")
    table.add_column("Local file", style="cyan")
    table.add_column("Backend", style="magenta")
    table.add_column("Remote ID")
    table.add_column("Direction")

    for m in config.mappings:
        table.add_row(m.local, m.backend, m.remote_id, m.direction)

    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# config subcommand — central project registry (XDG)
# ─────────────────────────────────────────────────────────────────────────────

def _write_central(raw: dict) -> str:
    import yaml as _yaml

    from docspan.core.xdg import central_config_path
    path = central_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))
    return str(path)


def _read_central_raw() -> dict:
    import yaml as _yaml

    from docspan.core.xdg import central_config_path
    path = central_config_path()
    if path.exists():
        return _yaml.safe_load(path.read_text()) or {}
    return {}


@config_app.command("show")
def config_show() -> None:
    """Show the central config (registered projects) and the active resolution."""
    from docspan.core.xdg import central_config_path
    central = load_central_config()
    console.print(f"Central config: {central_config_path()}")
    console.print(f"default_prefix: {central.default_prefix or '(none)'}")
    if not central.projects:
        console.print(
            "No projects registered. Add one:\n"
            "  docspan config add <prefix> <path-to-markgate.yaml>"
        )
        return
    table = Table(title="docspan projects")
    table.add_column("Prefix", style="cyan")
    table.add_column("markgate.yaml")
    for name, entry in central.projects.items():
        table.add_row(name, entry.markgate)
    console.print(table)


@config_app.command("add")
def config_add(
    prefix: str = typer.Argument(..., help="Project prefix (name)"),
    markgate: str = typer.Argument(..., help="Path to that project's markgate.yaml"),
    default: bool = typer.Option(False, "--default", help="Also set as default_prefix"),
) -> None:
    """Register a project (prefix → markgate.yaml) in the central config."""
    raw = _read_central_raw()
    raw.setdefault("projects", {})[prefix] = {"markgate": markgate}
    if default or not raw.get("default_prefix"):
        raw["default_prefix"] = prefix
    path = _write_central(raw)
    console.print(f"✓ Registered '{prefix}' → {markgate} in {path}")


@app.command("migrate-xdg")
def migrate_xdg(
    prefix: str = typer.Option(..., "--prefix", "-p", help="Prefix to migrate this project's storage into"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Move legacy in-repo storage (.markgate-state.json, .markgate-base/) to XDG and register the project."""
    import shutil

    from docspan.core.xdg import state_dir_for_prefix
    legacy_dir = get_state_dir(config_path)  # cwd / dirname(config) — legacy location
    dest = str(state_dir_for_prefix(prefix))
    os.makedirs(dest, exist_ok=True)

    moved = []
    for name in (STATE_FILENAME, BASE_STORE_DIR):
        src = os.path.join(legacy_dir, name)
        if os.path.exists(src):
            target = os.path.join(dest, name)
            if os.path.exists(target):
                err_console.print(f"Refusing to overwrite existing {target}")
                raise typer.Exit(1)
            shutil.move(src, target)
            moved.append(name)

    markgate_abs = os.path.abspath(os.path.expanduser(config_path or "markgate.yaml"))
    raw = _read_central_raw()
    raw.setdefault("projects", {})[prefix] = {"markgate": markgate_abs}
    if not raw.get("default_prefix"):
        raw["default_prefix"] = prefix
    _write_central(raw)

    console.print(f"✓ Moved {moved or '(nothing)'} → {dest}")
    console.print(f"✓ Registered '{prefix}' → {markgate_abs}")


# ─────────────────────────────────────────────────────────────────────────────
# auth subcommand
# ─────────────────────────────────────────────────────────────────────────────

@auth_app.command("setup")
def auth_setup(
    backend: str = typer.Argument(..., help="Backend to authenticate: google_docs | confluence"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
    oauth: bool = typer.Option(False, "--oauth", help="Use per-user OAuth (google_docs)."),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="Path to an OAuth client secret JSON (google_docs, with --oauth)."
    ),
) -> None:
    """Interactive authentication setup for a backend."""
    config = load_config(config_path)
    cls = BACKENDS.get(backend)
    if not cls:
        err_console.print(f"Unknown backend '{backend}'. Available: {list(BACKENDS.keys())}")
        raise typer.Exit(1)

    if oauth or client_secret:
        from docspan.config import GoogleDocsConfig
        gd = config.backends.google_docs or GoogleDocsConfig()
        if client_secret:
            gd.oauth_client_secret_path = client_secret
        if not gd.oauth_client_secret_path:
            err_console.print(
                "--oauth requires --client-secret PATH (or oauth_client_secret_path in markgate.yaml)."
            )
            raise typer.Exit(1)
        config.backends.google_docs = gd

    b = cls.from_config(config)
    b.auth_setup(config_path=config_path)


# ─────────────────────────────────────────────────────────────────────────────
# conflicts subcommand
# ─────────────────────────────────────────────────────────────────────────────

@conflicts_app.command("list")
def conflicts_list(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """List files with unresolved merge conflicts."""
    state_path = get_state_path(config_path)
    state = _load_state(state_path)

    conflicted = []
    for local_path in state.mappings:
        if not os.path.exists(local_path):
            continue
        with open(local_path, encoding="utf-8") as fh:
            content = fh.read()
        count = sum(1 for line in content.splitlines() if line.startswith("<<<<<<< "))
        if count > 0:
            conflicted.append((local_path, count))

    if not conflicted:
        console.print("No unresolved conflicts.")
        return

    table = Table(title="Files with merge conflicts")
    table.add_column("File", style="cyan")
    table.add_column("Conflict blocks", style="red")
    for local_path, count in conflicted:
        table.add_row(local_path, str(count))
    console.print(table)


@conflicts_app.command("resolve")
def conflicts_resolve(
    file: str = typer.Argument(..., help="Local file path to resolve"),
    accept: str = typer.Option(..., "--accept", help="Resolution strategy: remote | local | merged"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Resolve a merge conflict in a tracked file."""
    if accept not in ("remote", "local", "merged"):
        err_console.print("--accept must be one of: remote, local, merged")
        raise typer.Exit(1)

    state_path = get_state_path(config_path)
    state_dir = get_state_dir(config_path)
    state = _load_state(state_path)

    entry = state.get(file)
    if entry is None:
        err_console.print(f"File '{file}' is not tracked in .markgate-state.json")
        raise typer.Exit(1)

    config = load_config(config_path)
    backend = _get_backend(entry.backend, config)

    if accept == "remote":
        _resolve_remote(file, entry, backend, state, state_path, state_dir)
    elif accept == "local":
        _resolve_local(file, entry, state, state_path, state_dir)
    elif accept == "merged":
        _resolve_merged(file, entry, state, state_path, state_dir)


def _resolve_remote(
    file: str,
    entry: MappingState,
    backend,
    state: SyncState,
    state_path: str,
    state_dir: str,
) -> None:
    result = backend.pull(entry.doc_id, file)
    if result.status != "ok":
        err_console.print(f"Could not re-fetch remote: {result.message}")
        raise typer.Exit(1)
    orig_path = file + ORIG_SUFFIX
    if os.path.exists(orig_path):
        os.unlink(orig_path)
    with open(file, encoding="utf-8") as fh:
        new_content = fh.read()
    try:
        remote_version = backend.get_remote_version(entry.doc_id)
    except Exception:
        logger.warning(
            "Could not fetch remote version for %s after resolve; retaining stale version %s",
            entry.doc_id, entry.remote_version, exc_info=True,
        )
        remote_version = entry.remote_version
    record_state(state, state_path, state_dir, file, entry.doc_id, entry.backend, new_content, remote_version)
    console.print(f"[green]Resolved[/green] {file} (accepted remote)")


def _resolve_local(
    file: str,
    entry: MappingState,
    state: SyncState,
    state_path: str,
    state_dir: str,
) -> None:
    orig_path = file + ORIG_SUFFIX
    if os.path.exists(orig_path):
        shutil.copy2(orig_path, file)
        os.unlink(orig_path)
        console.print(f"[green]Restored[/green] {file} from {orig_path}")
    else:
        base_content = get_base_content(state_dir, entry.base_hash)
        if base_content:
            with open(file, "w", encoding="utf-8") as fh:
                fh.write(base_content)
            console.print("[yellow]Warning:[/yellow] .orig not found; restored from base content")
        else:
            err_console.print(f"No .orig file and no base content for '{file}'. Cannot restore.")
            raise typer.Exit(1)
    with open(file, encoding="utf-8") as fh:
        new_content = fh.read()
    record_state(state, state_path, state_dir, file, entry.doc_id, entry.backend, new_content, entry.remote_version)
    console.print(f"[green]Resolved[/green] {file} (accepted local)")


def _resolve_merged(
    file: str,
    entry: MappingState,
    state: SyncState,
    state_path: str,
    state_dir: str,
) -> None:
    if not os.path.exists(file):
        err_console.print(f"File '{file}' does not exist")
        raise typer.Exit(1)
    with open(file, encoding="utf-8") as fh:
        content = fh.read()
    if "<<<<<<< " in content:
        err_console.print(
            f"File '{file}' still contains conflict markers. "
            "Resolve all conflicts before accepting as merged."
        )
        raise typer.Exit(1)
    record_state(state, state_path, state_dir, file, entry.doc_id, entry.backend, content, entry.remote_version)
    console.print(f"[green]Resolved[/green] {file} (accepted merged)")



# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    app()


if __name__ == "__main__":
    main()
