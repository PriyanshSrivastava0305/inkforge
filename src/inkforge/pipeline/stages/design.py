"""Stage B — Story Design (docs/05): dramatic question, acts, arcs, plot promises, cast candidates.

Promises go to the single registry (D22); characters go to the Codex as *candidates* — usable by
later stages, but only accepted by the author (never by --yes, D23).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from inkforge.domain.codex import active_characters, find_by_name
from inkforge.domain.common import Actor
from inkforge.domain.models import (
    Act,
    Arc,
    Character,
    CodexStatus,
    PlotPromise,
    PromiseHistory,
    PromiseRegistry,
    PromiseStatus,
    StoryDesign,
    VoiceCard,
)
from inkforge.pipeline.blocks import spec_block
from inkforge.pipeline.proposals import design_model
from inkforge.pipeline.stage import Problem, Stage, StageContext
from inkforge.prompts.registry import RenderedPrompt
from inkforge.storage.workspace import ArtifactKind

DEFAULT_STRUCTURE = {"prose": "three-act", "script": "save-the-cat"}


def structure_name(ctx: StageContext) -> str | None:
    s = (
        ctx.params.get("structure")
        or ctx.store.project.settings.get("structure")
        or DEFAULT_STRUCTURE[ctx.form.output]
    )
    return None if s == "none" else s


def n_acts(ctx: StageContext) -> int:
    return int(ctx.params.get("acts") or ctx.store.project.settings.get("acts") or 3)


class DesignStage(Stage):
    name = "design"
    profile = "planner"
    gate = "design"
    requires_gates = ("spec",)

    def targets(self, ctx: StageContext) -> list[str | None]:
        return [None]

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        return (
            [] if ctx.store.try_read("spec") else [Problem("error", "no book spec yet — run the spec stage")]
        )

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:
        name = structure_name(ctx)
        skill = ctx.skills.get(name) if name else None
        known = [f"{c.name} ({c.role})" for c in active_characters(ctx.store)]
        return ctx.prompts.render(
            "design.generate",
            spec_block=spec_block(ctx.store.read("spec")),
            structure=skill,
            acts=n_acts(ctx),
            known_cast=known,
        )

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:
        return design_model(n_acts(ctx))

    def advise(self, ctx: StageContext, target: str | None, value: Any) -> list[str]:
        errs = []
        if not any(c.role == "protagonist" for c in value.characters):
            errs.append("characters: exactly one character must have role 'protagonist'")
        for c in value.characters:
            if c.arc_start.strip().lower() == c.arc_end.strip().lower():
                errs.append(
                    f"characters[{c.name}]: arc_start and arc_end must differ — the arc must change them"
                )
        names = [c.name.strip().lower() for c in value.characters]
        if len(names) != len(set(names)):
            errs.append("characters: names must be unique")
        tps = [a.turning_point.strip().lower() for a in value.acts]
        if len(tps) != len(set(tps)):
            errs.append("acts: each act needs its own distinct turning point")
        return errs

    def persist(self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str) -> list[str]:
        s, job = ctx.store, ctx.job.job_id
        refs: list[str] = []
        # cast → Codex candidates (existing entries with the same name are reused, never overwritten)
        char_ids: dict[str, str] = {}
        for c in value.characters:
            existing = find_by_name(s.list_codex("character"), c.name)
            if existing is not None:
                char_ids[c.name] = existing.id
                continue
            cid = s.new_codex_id("character", c.name)
            s.save_codex(
                Character(
                    id=cid,
                    name=c.name.strip(),
                    role=c.role,
                    goals=[c.goal],
                    fears=[c.fear],
                    secrets=[c.secret],
                    voice_card=VoiceCard(diction=c.voice),
                    codex_status=CodexStatus.CANDIDATE,
                    source_refs=[f"design:{job}"],
                ),
                actor=Actor.SYSTEM,
                job_id=job,
            )
            char_ids[c.name] = cid
            refs.append(f"codex:{cid}")
        # promises → registry; replace this stage's earlier open proposals that nothing references yet
        reg = s.get_promises()
        referenced = _referenced_promises(ctx)
        kept = [
            p
            for p in reg.promises
            if not (
                p.created_by_stage == "design" and p.status == PromiseStatus.OPEN and p.id not in referenced
            )
        ]
        new_ids = []
        for p in value.promises:
            pid = s.mint("promise")
            kept.append(
                PlotPromise(
                    id=pid,
                    description=p.description,
                    setup_ref=p.setup_hint,
                    payoff_ref=None,
                    importance=p.importance,
                    created_by_stage="design",
                    history=[
                        PromiseHistory(actor=Actor.SYSTEM, from_status=None, to_status=PromiseStatus.OPEN)
                    ],
                )
            )
            new_ids.append(pid)
        s.save_promises(PromiseRegistry(promises=kept), actor=Actor.SYSTEM)
        skill = structure_name(ctx)
        design = StoryDesign(
            dramatic_question=value.dramatic_question,
            theme_statement=value.theme_statement,
            reader_promise=value.reader_promise,
            structure_template=skill,
            acts=[
                Act(id=f"act{i}", purpose=a.purpose, turning_point=a.turning_point)
                for i, a in enumerate(value.acts, 1)
            ],
            arcs=[
                Arc(character_id=char_ids[c.name], start_state=c.arc_start, end_state=c.arc_end)
                for c in value.characters
            ],
            promise_ids=[
                p.id
                for p in kept
                if p.created_by_stage == "design" and (p.id in new_ids or p.id in referenced)
            ],
            ending_constraints=value.ending_constraints,
        )
        rec = s.save("design", design, extra=ctx.provenance(self.name, target, prompt_ref), job_id=job)
        return [f"design@v{rec.version}", *refs, *(f"promise:{p}" for p in new_ids)]

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        m = ctx.store.meta("design")
        if m.active and m.get(m.active).extra.get("job_id") == ctx.job.job_id:
            return [f"design@v{m.active}"]
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        out = []
        for a in ctx.store.read("design").acts:
            if not a.turning_point:
                out.append(Problem("warning", f"{a.id} has no turning point"))
        cands = [c for c in ctx.store.list_codex("character") if c.codex_status == CodexStatus.CANDIDATE]
        if cands:
            out.append(
                Problem(
                    "warning",
                    f"{len(cands)} character candidates await review: "
                    + ", ".join(c.id for c in cands)
                    + " (inkforge codex review)",
                )
            )
        return out

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return [("design", None)]


def _referenced_promises(ctx: StageContext) -> set[str]:
    s = ctx.store
    refs: set[str] = set()
    for cid in s.chapter_ids():
        plan = s.try_read("chapter", cid)
        if plan:
            refs |= set(plan.promises_touched)
    for sid in s.scene_ids():
        card = s.try_read("card", sid)
        if card:
            refs |= set(card.promises_touched)
    return refs
