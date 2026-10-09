"""`inkforge` command-line entry point. Commands are added phase by phase (docs/09-ui-ux.md)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from inkforge import __version__
from inkforge.config import load_model_config

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="InkForge — local story/script writing agent."
)
console = Console()


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
