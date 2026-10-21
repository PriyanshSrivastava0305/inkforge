"""CLI for Phase 6: check (Stage F), revise / polish (Stage G), the fact review queue, lessons.

Every model-driven command runs as a Conductor job (resumable, cancellable, checkpointed per scene).
Canon changes only through `facts accept` / `facts retcon`; lessons only through `lessons add/remove`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from inkforge.domain.common import Actor
from inkforge.quality import canon, lessons
from inkforge.quality.revision import PASSES
from inkforge.storage.workspace import ProjectStore

Start = Callable[[str, Path | None, list[str], dict[str, Any], str | None], None]


def register(
    app: typer.Typer,
    console: Console,
    open_store: Callable[[Path | None, str], ProjectStore],
    workspace_opt: Any,
    project_opt: Any,
    start: Start,
) -> None:
    profile_opt = typer.Option(None, "--profile-set", help="'fast' = llama3.2:1b for quick plumbing runs")
    checks_opt = typer.Option(
        [], "--only", help="Run only these checks: deterministic, continuity, craft, voice (repeatable)"
    )

    @app.command()
    def check(
        project: str = project_opt,
        scene: str | None = typer.Option(None, help="Check this scene"),
        chapter: str | None = typer.Option(None, help="Only this chapter"),
        recheck: bool = typer.Option(
            False, "--recheck", help="Check scenes even if unchanged since last time"
        ),
        only: list[str] = checks_opt,
        adjudicate: bool = typer.Option(
            True, "--adjudicate/--no-adjudicate", help="Ask the model about free-text conflicts"
        ),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage F — critique drafted scenes: rules, canon conflicts, craft rubric, voice. Never edits text."""
        params = {"scene": scene, "chapter": chapter, "recheck": recheck, "checks": only or None,
                  "adjudicate": adjudicate}  # fmt: skip
        start(project, workspace, ["critique"], params, profile_set)

    @app.command()
    def revise(
        project: str = project_opt,
        scene: str | None = typer.Option(None, help="Revise this scene"),
        chapter: str | None = typer.Option(None, help="Only this chapter"),
        passes: list[str] = typer.Option([], "--pass", help=f"Only these passes: {', '.join(PASSES)}"),
        attempts: int = typer.Option(2, min=1, max=5, help="Tries per passage before leaving it to you"),
        rubric: bool = typer.Option(
            True, "--rubric/--no-rubric", help="Keep an edit only if the craft score holds"
        ),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage G — fix *accepted* findings with scoped edits; each kept edit is a new draft version."""
        params = {"scene": scene, "chapter": chapter, "passes": passes or None, "max_attempts": attempts,
                  "rubric": rubric}  # fmt: skip
        start(project, workspace, ["revise"], params, profile_set)

    @app.command()
    def polish(
        project: str = project_opt,
        chapter: str | None = typer.Option(None, help="Polish this chapter"),
        scene: str | None = typer.Option(None, help="Polish this scene"),
        severity: list[str] = typer.Option(
            ["high", "med"], "--severity", help="Fix open findings of these severities (repeatable)"
        ),
        passes: list[str] = typer.Option([], "--pass", help=f"Only these passes: {', '.join(PASSES)}"),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Check, then fix open findings pass by pass: continuity → voice → craft → slop."""
        if not (chapter or scene):
            console.print("[red]polish needs --chapter or --scene[/red]")
            raise typer.Exit(2)
        params = {"scene": scene, "chapter": chapter, "accept": severity, "passes": passes or None}
        start(project, workspace, ["critique", "revise"], params, profile_set)

    # ------------------------------------------------------------ facts review queue
    facts_app = typer.Typer(help="Review facts extracted from the manuscript; only you make them canon.")
    app.add_typer(facts_app, name="facts")

    @facts_app.command("list")
    def facts_list(
        project: str = project_opt,
        scene: str | None = typer.Option(None),
        canon_only: bool = typer.Option(False, "--canon", help="Show canon instead of the review queue"),
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Pending candidates (default) or canon facts, with the words that state them."""
        store = open_store(workspace, project)
        rows = store.facts.all() if canon_only else canon.pending(store)
        table = Table(
            "id", "scene", "subject", "predicate", "value", "quote", "conf" if not canon_only else "status"
        )
        for f in rows:
            if scene and f.valid_from != scene:
                continue
            last = f"{f.confidence:.1f}" if not canon_only else str(f.canon_status)
            table.add_row(f.id, f.valid_from or "", f.subject_id, f.predicate, escape(str(f.value)),
                          escape(f"“{f.quote}”" if f.quote else ""), last)  # fmt: skip
        console.print(table)

    @facts_app.command("accept")
    def facts_accept(
        ids: list[str] = typer.Argument(None, help="Candidate fact ids"),
        project: str = project_opt,
        force: bool = typer.Option(False, "--force", help="Accept even if it contradicts canon"),
        low_risk: bool = typer.Option(
            False, "--low-risk", help="Accept every low-risk candidate with no conflict"
        ),
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Make candidates canon. A candidate that contradicts canon is refused unless --force."""
        store = open_store(workspace, project)
        if low_risk:
            done = canon.accept_low_risk(store, actor=Actor.USER)
            console.print(f"accepted {len(done)} low-risk fact(s): {', '.join(f.id for f in done) or '—'}")
        failed = False
        for fid in ids or []:
            try:
                got = canon.accept(store, fid, force=force, actor=Actor.USER)
            except canon.CanonError as e:
                console.print(f"[red]{e}[/red]")
                failed = True
                continue
            if not got.ok:
                failed = True
                console.print(f"[yellow]{fid} not accepted — it contradicts canon:[/yellow]")
                for h in got.conflicts:
                    console.print(f"  • {escape(h.claim)}")
                console.print("  (fix the scene, reject the fact, or accept with --force)")
                continue
            note = f" (ended {', '.join(got.closed)})" if got.closed else ""
            console.print(f"[green]{fid} is canon[/green]{note}")
        if failed:
            raise typer.Exit(1)

    @facts_app.command("reject")
    def facts_reject(
        ids: list[str] = typer.Argument(..., help="Candidate fact ids"),
        project: str = project_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Reject candidates; they won't be proposed again for that scene."""
        store = open_store(workspace, project)
        for fid in ids:
            try:
                canon.reject(store, fid, actor=Actor.USER)
                console.print(f"{fid}: rejected")
            except canon.CanonError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1) from None

    @facts_app.command("retcon")
    def facts_retcon(
        ids: list[str] = typer.Argument(..., help="Canon fact ids"),
        project: str = project_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Withdraw canon facts (kept on file, marked retconned)."""
        store = open_store(workspace, project)
        for fid in ids:
            try:
                canon.retcon(store, fid, actor=Actor.USER)
                console.print(f"{fid}: retconned")
            except canon.CanonError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1) from None

    # ------------------------------------------------------------ lessons
    lessons_app = typer.Typer(help="Style rules for drafting, promoted from recurring findings.")
    app.add_typer(lessons_app, name="lessons")

    @lessons_app.command("list")
    def lessons_list(project: str = project_opt, workspace: Path | None = workspace_opt) -> None:
        """List lessons and suggestions."""
        store = open_store(workspace, project)
        book = lessons.load(store)
        for x in book.lessons:
            mark = "[green]●[/green]" if x.active else "[dim]○[/dim]"
            console.print(
                f"{mark} {x.id}  {escape(x.text)}" + (f"  [dim]({x.source})[/dim]" if x.source else "")
            )
        sugg = lessons.suggest(store)
        if sugg:
            console.print("\n[cyan]Suggested (recurring in recent scenes):[/cyan]")
            for text, source, n in sugg:
                console.print(f"  + {escape(text)}  [dim]{source}, {n} scenes[/dim]")
            console.print('  add one with: inkforge lessons add "<rule>"')
        elif not book.lessons:
            console.print("no lessons yet")

    @lessons_app.command("add")
    def lessons_add(
        text: str = typer.Argument(..., help="The rule, e.g. 'Avoid the phrase “suddenly”.'"),
        project: str = project_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        store = open_store(workspace, project)
        source = next((src for t, src, _ in lessons.suggest(store) if t == text), None)
        lesson = lessons.add(store, text, source=source, actor=Actor.USER)
        console.print(f"{lesson.id}: {escape(lesson.text)} — shown to the model on future drafts")

    @lessons_app.command("remove")
    def lessons_remove(
        lesson_id: str = typer.Argument(...),
        project: str = project_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        store = open_store(workspace, project)
        if not lessons.remove(store, lesson_id, actor=Actor.USER):
            console.print(f"[red]no active lesson {lesson_id}[/red]")
            raise typer.Exit(1)
        console.print(f"{lesson_id}: removed")
