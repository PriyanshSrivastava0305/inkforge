"""Planning-pipeline CLI (Phase 3): spec / design / outline / cards / run / jobs / resume / cancel /
approve / status / edit / codex. Every generating command runs as a resumable job (docs/09)."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from inkforge.domain.common import Actor
from inkforge.domain.models import CodexStatus, Job, JobStatus
from inkforge.models.router import ModelRouter
from inkforge.pipeline.conductor import STAGES, Conductor, stages_for
from inkforge.pipeline.stage import approve_artifacts
from inkforge.storage.workspace import ArtifactKind, ProjectStore

GATE_ALIASES = {"cards": "scene_cards", "chapters": "outline"}
EDITABLE: dict[str, bool] = {
    "spec": False,
    "design": False,
    "outline": False,
    "timeline": False,
    "chapter": True,
    "card": True,
}
QUIET_EVENTS = {"step.start"}


def register(app: typer.Typer, console: Console, open_store, workspace_opt, project_opt) -> None:
    profile_opt = typer.Option(None, "--profile-set", help="'fast' = llama3.2:1b for quick plumbing runs")
    yes_opt = typer.Option(
        False, "--yes", help="Approve planning gates (spec/design/outline/cards) automatically; audited"
    )

    def on_event(kind: str, rec: dict[str, Any]) -> None:
        if kind in QUIET_EVENTS:
            return
        body = {k: v for k, v in rec.items() if k not in ("ts", "type")}
        style = {
            "problem": "yellow",
            "job.failed": "red",
            "job.awaiting_approval": "cyan",
            "job.succeeded": "green",
        }.get(kind, "dim")
        if kind == "step.done":
            console.print(
                f"[green]✓[/green] {body['step']}  {', '.join(body.get('refs', [])[:4])}"
                + (f"  ({body['seconds']:.0f}s)" if body.get("seconds") else "")
                + ("  (adopted)" if body.get("adopted") else "")
            )
        elif kind == "problem":
            console.print(f"[{style}]! {body['stage']}: {escape(body['message'])}[/{style}]")
        elif kind == "spec.diff":
            for f, ch in body["changed"].items():
                console.print(f"[cyan]~ {f}[/cyan]: {ch['from']!r}\n    → {ch['to']!r}")
        else:
            console.print(
                f"[{style}]{kind}[/{style}] {escape(json.dumps(body, ensure_ascii=False, default=str)[:200])}"
            )

    def execute(
        store: ProjectStore, job: Job | None, profile_set: str | None, *, resume_id: str | None = None
    ) -> Job:
        async def go() -> Job:
            router = ModelRouter.from_config(profile_set)
            conductor = Conductor(store, router, on_event=on_event)
            try:
                if resume_id:
                    return await conductor.resume(resume_id)
                assert job is not None
                return await conductor.run(job)
            finally:
                await router.aclose()

        try:
            done = asyncio.run(go())
        except KeyboardInterrupt:
            jid = resume_id or (job.job_id if job else None)
            if jid:
                j = Job.model_validate_json(store.path(f"runs/{jid}/job.json").read_text(encoding="utf-8"))
                j.status = JobStatus.CANCELLED
                store.save_job(j)
            console.print(f"[yellow]Interrupted. Resume with: inkforge resume -p {store.id} {jid}[/yellow]")
            raise typer.Exit(130) from None
        summary = f"job {done.job_id}: [bold]{done.status}[/bold]"
        if done.status == JobStatus.AWAITING_APPROVAL:
            summary += f" — approve with `inkforge approve -p {store.id} {done.awaiting}`, then `inkforge resume -p {store.id}`"
        if done.error_summary:
            summary += f"\n[red]{done.error_summary}[/red]"
        console.print(summary)
        if done.status == JobStatus.FAILED:
            raise typer.Exit(1)
        return done

    def start(
        project: str,
        workspace: Path | None,
        stages: list[str],
        params: dict[str, Any],
        profile_set: str | None,
    ) -> None:
        store = open_store(workspace, project)
        conductor = Conductor(store, ModelRouter.from_config(profile_set))
        job = conductor.create_job(stages, {**params, "profile_set": profile_set})
        console.print(f"job {job.job_id}: {' → '.join(stages)}")
        execute(store, job, profile_set)

    # ------------------------------------------------------------ stage commands
    @app.command()
    def spec(
        project: str = project_opt,
        idea: str | None = typer.Option(None, "--idea", help="The idea, one line to a page (or @file.txt)"),
        enhance: bool = typer.Option(False, "--enhance", help="Critique and improve the existing spec"),
        genre: str | None = typer.Option(None),
        pov: str | None = typer.Option(None),
        tense: str | None = typer.Option(None),
        tone: str | None = typer.Option(None),
        audience: str | None = typer.Option(None),
        target: int | None = typer.Option(
            None, "--target", help="Target length (words, or pages for scripts)"
        ),
        place: str | None = typer.Option(None),
        time: str | None = typer.Option(None),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage A — Book Spec from your idea. Fields you pass are fixed: the model can't override them."""
        if idea and idea.startswith("@"):
            idea = Path(idea[1:]).read_text(encoding="utf-8")
        fixed = {
            "genre": genre,
            "pov": pov,
            "tense": tense,
            "tone": tone,
            "audience": audience,
            "target_length": target,
            "setting_place": place,
            "setting_time": time,
        }
        start(
            project,
            workspace,
            ["spec"],
            {"idea": idea, "enhance": enhance, "author_fields": fixed},
            profile_set,
        )

    @app.command()
    def design(
        project: str = project_opt,
        structure: str | None = typer.Option(None, help="three-act | save-the-cat | heros-journey | none"),
        acts: int | None = typer.Option(None, help="Number of acts (default 3)"),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage B — Story design: dramatic question, acts, cast (Codex candidates), plot promises."""
        start(project, workspace, ["design"], {"structure": structure, "acts": acts}, profile_set)

    @app.command()
    def outline(
        project: str = project_opt,
        act: str | None = typer.Option(None, help="Only (re)plan this act, e.g. act2"),
        chapters: int | None = typer.Option(None, help="Total chapters (default from target length)"),
        enhance: bool = typer.Option(False, "--enhance", help="Add a whole-outline enhance pass"),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage C — Chapter plans, one act at a time."""
        start(
            project,
            workspace,
            ["chapters"],
            {"act": act, "chapters": chapters, "outline_enhance": enhance},
            profile_set,
        )

    @app.command()
    def cards(
        project: str = project_opt,
        chapter: str | None = typer.Option(None, help="Only this chapter, e.g. ch03"),
        scenes: int | None = typer.Option(None, "--scenes", help="Scenes per chapter (default from length)"),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Stage D — Scene cards per chapter, placed on the story timeline."""
        start(project, workspace, ["cards"], {"chapter": chapter, "scenes_per_chapter": scenes}, profile_set)

    @app.command()
    def run(
        project: str = project_opt,
        mode: str = typer.Option("plan", help="plan (draft/polish arrive in later phases)"),
        until: str | None = typer.Option(None, help="Stop after this stage: spec|design|chapters|cards"),
        idea: str | None = typer.Option(None, "--idea"),
        structure: str | None = typer.Option(None),
        chapters: int | None = typer.Option(None),
        scenes: int | None = typer.Option(None, "--scenes"),
        yes: bool = yes_opt,
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Run a whole mode as one resumable job, pausing at approval gates unless --yes."""
        if idea and idea.startswith("@"):
            idea = Path(idea[1:]).read_text(encoding="utf-8")
        params = {
            "idea": idea,
            "structure": structure,
            "chapters": chapters,
            "scenes_per_chapter": scenes,
            "yes": yes,
        }
        start(project, workspace, stages_for(mode, until=until), params, profile_set)

    # ------------------------------------------------------------ jobs
    @app.command()
    def jobs(project: str = project_opt, workspace: Path | None = workspace_opt) -> None:
        """List jobs with status and progress."""
        store = open_store(workspace, project)
        table = Table("job", "stages", "status", "steps done", "note")
        for j in sorted(store.jobs(), key=lambda j: j.job_id):
            done = sum(1 for s in j.steps if s.status == "done")
            note = j.error_summary or (f"awaiting {j.awaiting}" if j.awaiting else "")
            table.add_row(j.job_id, j.stage, str(j.status), f"{done}/{len(j.steps)}", (note or "")[:60])
        console.print(table)

    @app.command()
    def resume(
        project: str = project_opt,
        job_id: str | None = typer.Argument(None, help="Default: latest job"),
        profile_set: str | None = profile_opt,
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Resume a job from its last checkpoint (after a crash, cancel or approval)."""
        store = open_store(workspace, project)
        jid = job_id or (sorted(store.jobs(), key=lambda j: j.job_id) or [None])[-1]
        if jid is None:
            console.print("[red]no jobs[/red]")
            raise typer.Exit(1)
        jid = jid if isinstance(jid, str) else jid.job_id
        prev = Job.model_validate_json(store.path(f"runs/{jid}/job.json").read_text(encoding="utf-8"))
        execute(store, None, profile_set or prev.params.get("profile_set"), resume_id=jid)

    @app.command()
    def cancel(
        project: str = project_opt, job_id: str = typer.Argument(...), workspace: Path | None = workspace_opt
    ) -> None:
        """Ask a running job (in another terminal) to stop after its current model call."""
        store = open_store(workspace, project)
        Conductor(store, ModelRouter.from_config()).request_cancel(job_id)
        console.print(f"cancel requested for {job_id}")

    # ------------------------------------------------------------ approvals & status
    @app.command()
    def approve(
        project: str = project_opt,
        gate: str = typer.Argument(..., help="spec | design | outline | cards"),
        chapter: str | None = typer.Option(None, help="cards: approve only this chapter's cards"),
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Approve the active version of everything behind a planning gate (scene approval comes in Phase 4)."""
        store = open_store(workspace, project)
        gate = GATE_ALIASES.get(gate, gate)
        stage = next((s for s in STAGES.values() if s.gate == gate), None)
        if stage is None:
            console.print(f"[red]unknown gate {gate!r}[/red]")
            raise typer.Exit(1)
        from inkforge.pipeline.stage import StageContext

        ctx = StageContext(store, None, None, None, None)  # type: ignore[arg-type]
        arts = stage.gate_artifacts(ctx)
        if chapter:
            arts = [(k, t) for k, t in arts if t and t.startswith(f"{chapter}-")]
        if not arts:
            console.print(f"[yellow]nothing to approve for {gate}[/yellow]")
            raise typer.Exit(1)
        stale = [f"{k}:{t}" if t else k for k, t in arts if store.staleness(k, t) == "stale"]
        if stale:
            console.print(f"[yellow]warning: approving stale artifacts: {', '.join(stale)}[/yellow]")
        done = approve_artifacts(store, cast(list[tuple[ArtifactKind, str | None]], arts), via=None)
        console.print(f"approved {len(done)}: {', '.join(done) or '(already approved)'}")

    @app.command()
    def status(project: str = project_opt, workspace: Path | None = workspace_opt) -> None:
        """Every planning artifact: active/approved version and staleness."""
        store = open_store(workspace, project)
        table = Table("artifact", "active", "approved", "freshness")
        rows: list[tuple[Any, str | None]] = [
            ("spec", None),
            ("design", None),
            ("outline", None),
            ("timeline", None),
        ]
        rows += [("chapter", c) for c in store.chapter_ids()] + [("card", s) for s in store.scenes_in_order()]
        for kind, target in rows:
            m = store.meta(kind, target)
            if m.active is None:
                continue
            fresh = str(store.staleness(kind, target))
            color = {"fresh": "green", "review": "yellow", "stale": "red"}[fresh]
            table.add_row(
                f"{kind}:{target}" if target else kind,
                f"v{m.active}",
                f"v{m.approved}" if m.approved else "—",
                f"[{color}]{fresh}[/{color}]",
            )
        console.print(table)
        cands = [e for e in store.list_codex() if e.codex_status == CodexStatus.CANDIDATE]
        if cands:
            console.print(
                f"[yellow]{len(cands)} Codex candidates awaiting review (inkforge codex -p {store.id})[/yellow]"
            )

    @app.command()
    def edit(
        project: str = project_opt,
        kind: str = typer.Argument(..., help="spec | design | outline | timeline | chapter <id> | card <id>"),
        target: str | None = typer.Argument(None),
        workspace: Path | None = workspace_opt,
    ) -> None:
        """Edit an artifact in $EDITOR; saves a new user version and shows what became stale."""
        if kind not in EDITABLE or EDITABLE[kind] != (target is not None):
            console.print(
                "[red]usage: edit spec|design|outline|timeline  or  edit chapter <id> | card <id>[/red]"
            )
            raise typer.Exit(2)
        store = open_store(workspace, project)
        akind: ArtifactKind = kind  # type: ignore[assignment]
        before_states = _states(store)
        current = store.read(akind, target)
        text = current.to_json()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(text)
            tmp = Path(f.name)
        try:
            editor = (
                os.environ.get("VISUAL")
                or os.environ.get("EDITOR")
                or ("notepad" if sys.platform == "win32" else "vi")
            )
            subprocess.run([*editor_command(editor), str(tmp)], check=True)
            new_text = tmp.read_text(encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)
        if new_text.strip() == text.strip():
            console.print("no changes")
            return
        try:
            new = type(current).model_validate_json(new_text)
        except ValidationError as e:
            console.print(f"[red]not saved — invalid {kind}:[/red]\n{e}")
            raise typer.Exit(1) from None
        rec = store.save(akind, new, target=target, actor=Actor.USER, note="manual edit")
        console.print(f"saved {kind}{':' + target if target else ''} v{rec.version} (by you)")
        for ref, (old, now) in _diff_states(before_states, _states(store)).items():
            console.print(f"  {ref}: {old} → [yellow]{now}[/yellow]")

    # ------------------------------------------------------------ codex review
    @app.command()
    def codex(
        project: str = project_opt,
        accept: list[str] = typer.Option(
            [], "--accept", help="Accept candidate id(s); 'all' for every candidate"
        ),
        reject: list[str] = typer.Option([], "--reject", help="Reject candidate id(s)"),
        workspace: Path | None = workspace_opt,
    ) -> None:
        """List Codex entries; accept or reject candidates proposed by the pipeline (never automatic)."""
        store = open_store(workspace, project)
        entries = {e.id: e for e in store.list_codex()}
        if "all" in accept:
            accept = [i for i, e in entries.items() if e.codex_status == CodexStatus.CANDIDATE]
        for ids, status in ((accept, CodexStatus.ACCEPTED), (reject, CodexStatus.REJECTED)):
            for i in ids:
                if i not in entries:
                    console.print(f"[red]unknown id {i}[/red]")
                    raise typer.Exit(1)
                store.save_codex(entries[i].model_copy(update={"codex_status": status}), actor=Actor.USER)
                console.print(f"{status}: {i}")
        table = Table("id", "name", "status", "role / description")
        for e in store.list_codex():
            extra = getattr(e, "role", None) or e.description or ""
            color = {"candidate": "yellow", "accepted": "green", "rejected": "red"}[str(e.codex_status)]
            table.add_row(e.id, e.name, f"[{color}]{e.codex_status}[/{color}]", str(extra)[:50])
        console.print(table)


def editor_command(editor: str) -> list[str]:
    """Split $EDITOR. On Windows shlex keeps quotes in non-POSIX mode, which breaks a quoted path
    such as "C:/Program Files/.../code.exe" --wait; strip them per token."""
    if sys.platform != "win32":
        return shlex.split(editor)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in shlex.split(editor, posix=False)]


def _states(store: ProjectStore) -> dict[str, str]:
    out = {}
    rows: list[tuple[Any, str | None]] = [("design", None), ("outline", None)]
    rows += [("chapter", c) for c in store.chapter_ids()] + [("card", s) for s in store.scene_ids()]
    rows += [("draft", s) for s in store.scene_ids() if store.meta("draft", s).active]
    for kind, target in rows:
        if store.meta(kind, target).active:
            out[f"{kind}:{target}" if target else kind] = str(store.staleness(kind, target))
    return out


def _diff_states(before: dict[str, str], after: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {k: (before.get(k, "-"), v) for k, v in after.items() if before.get(k) != v}
