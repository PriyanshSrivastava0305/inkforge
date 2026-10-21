"""`inkforge` command-line entry point. Commands are added phase by phase (docs/09-ui-ux.md)."""

from __future__ import annotations

import asyncio
import io
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from inkforge import __version__
from inkforge.bench import run_bench
from inkforge.cli_pipeline import register as register_pipeline
from inkforge.cli_quality import register as register_quality
from inkforge.config import load_app_config, load_model_config
from inkforge.domain.forms import load_forms
from inkforge.models.health import Check, runtime_checks
from inkforge.models.router import ModelRouter
from inkforge.policy.pathguard import PathEscapeError, resolve_inside
from inkforge.prompts.registry import PromptRegistry
from inkforge.prompts.skills import SkillRegistry
from inkforge.storage.atomic import remove_stale_temps
from inkforge.storage.backup import backup_project, restore_project
from inkforge.storage.workspace import ProjectStore, Workspace

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="InkForge — local story/script writing agent."
)

# Windows consoles default to a legacy code page (cp1252) that can't print most of what a writing
# tool shows (curly quotes, em dashes, non-Latin names). Force UTF-8 on our own streams.
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper) and (_stream.encoding or "").lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console()

WorkspaceOpt = typer.Option(
    None, "--workspace", "-w", help="Workspace root (default from config / INKFORGE_WORKSPACE)"
)
ProjectOpt = typer.Option(..., "--project", "-p", help="Project id (slug)")


def _workspace(path: Path | None) -> Workspace:
    return Workspace(load_app_config(workspace=path).workspace_root)


def _open(ws: Path | None, project: str) -> ProjectStore:
    try:
        return _workspace(ws).open(project)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None


_start = register_pipeline(app, console, _open, WorkspaceOpt, ProjectOpt)
register_quality(app, console, _open, WorkspaceOpt, ProjectOpt, _start)


@app.callback()
def main() -> None:
    """InkForge CLI."""


@app.command()
def version() -> None:
    """Print the InkForge version."""
    console.print(f"inkforge {__version__}")


@app.command()
def profiles(profile_set: str | None = typer.Option(None, "--profile-set", help="e.g. 'fast'")) -> None:
    """Show the resolved model profiles."""
    cfg = load_model_config(profile_set)
    table = Table(title=f"Model profiles ({profile_set or 'default'}) @ {cfg.runtime.base_url}")
    for col in ("profile", "model", "num_ctx", "temperature", "enabled"):
        table.add_column(col)
    for name, p in cfg.profiles.items():
        table.add_row(name, p.model, str(p.num_ctx), str(p.temperature), str(p.enabled))
    console.print(table)


@app.command()
def init(
    title: str = typer.Argument(..., help="Working title"),
    form: str = typer.Option(
        "novel", "--form", "-f", help="novel | short-story (script forms arrive in Phase 9)"
    ),
    slug: str | None = typer.Option(None, "--id", help="Override the project id"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Create a new project in the workspace."""
    try:
        store = _workspace(workspace).create_project(title, form, slug=slug)
    except (ValueError, KeyError, FileExistsError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    console.print(f"Created [bold]{store.id}[/bold] ({form}) at {store.dir}")


@app.command("projects")
def list_projects(workspace: Path | None = WorkspaceOpt) -> None:
    """List projects in the workspace."""
    ws = _workspace(workspace)
    ids = ws.project_ids()
    if not ids:
        console.print(f"No projects in {ws.projects_dir}")
        return
    table = Table("id", "title", "form", "updated")
    for pid in ids:
        p = ws.open(pid).project
        table.add_row(p.id, p.title, p.form, f"{p.updated_at:%Y-%m-%d %H:%M}")
    console.print(table)


@app.command()
def reindex(project: str = ProjectOpt, workspace: Path | None = WorkspaceOpt) -> None:
    """Drop and rebuild the project's SQLite index from its files."""
    store = _open(workspace, project)
    with store.lock:
        store.index.rebuild()
    counts = {t: len(rows) for t, rows in store.index.dump().items()}
    console.print(f"Rebuilt index for {project}: {counts}")


@app.command()
def backup(
    project: str = ProjectOpt,
    out: Path | None = typer.Option(None, "--out", help="Zip path (default: <project>/backups/)"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Zip the project (without the rebuildable index) with a hash manifest."""
    dest = backup_project(_open(workspace, project), out)
    console.print(f"Backup written: {dest}")


@app.command()
def restore(
    zip_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    as_id: str | None = typer.Option(None, "--as", help="Restore under a different project id"),
    workspace: Path | None = WorkspaceOpt,
) -> None:
    """Restore a project from a backup zip (never overwrites an existing project)."""
    try:
        store = restore_project(_workspace(workspace), zip_path, project_id=as_id)
    except (FileExistsError, ValueError, PathEscapeError) as e:
        console.print(f"[red]Restore refused: {e}[/red]")
        raise typer.Exit(1) from None
    console.print(f"Restored [bold]{store.id}[/bold] at {store.dir}")


@app.command()
def doctor(
    workspace: Path | None = WorkspaceOpt,
    profile_set: str | None = typer.Option(None, "--profile-set", help="Check a profile set, e.g. 'fast'"),
    runtime: bool = typer.Option(True, "--runtime/--no-runtime", help="Also check the model runtime"),
) -> None:
    """Check storage and model-runtime health."""
    ok = True

    def check(name: str, passed: bool, detail: str = "", warn_only: bool = False) -> None:
        nonlocal ok
        ok &= passed or warn_only
        label = (
            "[green]OK  [/green]" if passed else ("[yellow]WARN[/yellow]" if warn_only else "[red]FAIL[/red]")
        )
        console.print(f"{label} {name}{f' — {detail}' if detail else ''}")

    cfg = load_app_config(workspace=workspace)
    ws = Workspace(cfg.workspace_root)
    try:
        ws.ensure()
        probe = ws.root / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("workspace writable", True, str(ws.root))
    except OSError as e:
        check("workspace writable", False, str(e))
    free_gb = shutil.disk_usage(ws.root if ws.root.exists() else Path.home()).free / 2**30
    check("free disk ≥ 2 GB", free_gb >= 2, f"{free_gb:.1f} GB")
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        check("SQLite FTS5 available", True, sqlite3.sqlite_version)
    except sqlite3.OperationalError as e:
        check("SQLite FTS5 available", False, str(e))
    with tempfile.TemporaryDirectory() as d:
        escapes = ["../x", "..\\x", "C:\\Windows", "/etc/passwd", "a/../../b", "con", "x:stream"]
        blocked = 0
        for e in escapes:
            try:
                resolve_inside(Path(d), e)
            except PathEscapeError:
                blocked += 1
        check("path guard blocks escapes", blocked == len(escapes), f"{blocked}/{len(escapes)}")
    check("api bound to loopback", True, f"{cfg.api_host}:{cfg.api_port}")
    check("form profiles load", bool(load_forms()), ", ".join(sorted(load_forms())))
    for pid in ws.project_ids():
        store = ws.open(pid)
        n = remove_stale_temps(store.dir)
        orphans = [
            str(p.relative_to(store.dir))
            for sid in store.scene_ids()
            for k in ("card", "draft")
            for p in store.slot(k, sid).orphans()
        ]  # type: ignore[arg-type]
        check(
            f"project {pid}",
            not orphans,
            (f"orphan versions: {orphans}" if orphans else "consistent")
            + (f"; removed {n} temp files" if n else ""),
        )
    if runtime:
        for c in asyncio.run(_runtime_checks(profile_set, cfg.cache_root)):
            check(c.name, c.ok, c.detail, c.warn_only)
    raise typer.Exit(0 if ok else 1)


async def _runtime_checks(profile_set: str | None, cache_root: Path) -> list[Check]:
    router = ModelRouter.from_config(profile_set, cache_root=cache_root)
    try:
        return await runtime_checks(router, cache_root)
    finally:
        await router.aclose()


@app.command()
def prompts(prompt_id: str | None = typer.Argument(None, help="Show one prompt template")) -> None:
    """List prompt templates and skills, or show one template."""
    reg = PromptRegistry()
    if prompt_id:
        t = reg.get(prompt_id)
        console.print(f"[bold]{t.ref}[/bold] ({t.source}, profile {t.profile}) — {t.path}")
        console.print(t.body, markup=False, highlight=False)
        return
    table = Table("prompt", "versions", "profile", "description")
    for pid in reg.ids():
        t = reg.get(pid)
        table.add_row(pid, ",".join(map(str, reg.versions(pid))), t.profile, t.description)
    console.print(table)
    skills = Table("skill", "kind", "description")
    for sk in SkillRegistry().list():
        skills.add_row(sk.name, sk.kind, sk.description)
    console.print(skills)


@app.command()
def bench(
    profile_set: str | None = typer.Option(
        None, "--profile-set", help="'fast' = llama3.2:1b (≈5 min); default 8B ≈25 min"
    ),
    structure: str = typer.Option("three-act", "--structure", help="Structure skill for the outline"),
) -> None:
    """Run the mini-book benchmark through the router and prompt registry."""

    async def go() -> Path:
        router = ModelRouter.from_config(profile_set)
        try:
            return await run_bench(router, profile_set=profile_set, structure=structure)
        finally:
            await router.aclose()

    console.print(f"Report: {asyncio.run(go())}")


if __name__ == "__main__":
    app()
