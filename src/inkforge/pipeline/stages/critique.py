"""Stage F — Critique (docs/07), one scene per step.

Per scene, on its effective text (approved, else active — D34):
  deterministic checks → fact extraction + canon rules (+ adjudication) → craft rubric → voice critics
Each family syncs its own findings (re-checks keep the author's decisions and mark vanished findings
fixed), then a QualityReport records the version, the families that ran and the rubric scores.

Model failures never fail the job: the family is recorded as errored, its earlier findings are left as
they were, and the scene is re-checked next time. Nothing here changes text or canon; extracted facts
only reach the review queue.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel

from inkforge.memory.effective import effective_text
from inkforge.pipeline.stage import Problem, Stage, StageContext, StageError, StepResult
from inkforge.prompts.registry import RenderedPrompt
from inkforge.quality import deterministic, report
from inkforge.quality.check import ContinuityChecker, owned_by, sync_findings
from inkforge.quality.critics import Critics
from inkforge.storage.workspace import ArtifactKind

FAMILIES = ("deterministic", "continuity", "craft", "voice")


def families(ctx: StageContext) -> list[str]:
    """Which checks run: all by default; `params["checks"]` or `settings["checks"]` narrows them."""
    chosen = ctx.params.get("checks") or ctx.store.project.settings.get("checks") or list(FAMILIES)
    unknown = set(chosen) - set(FAMILIES)
    if unknown:
        raise StageError(f"unknown checks {sorted(unknown)}; have {list(FAMILIES)}")
    return [f for f in FAMILIES if f in chosen]


class CritiqueStage(Stage):
    name = "critique"
    profile = "critic"
    gate = None

    def targets(self, ctx: StageContext) -> list[str | None]:
        s = ctx.store
        scenes = [x for x in s.scenes_in_order() if s.meta("draft", x).active is not None]
        if sid := ctx.params.get("scene"):
            scenes = [sid]
        elif ch := ctx.params.get("chapter"):
            scenes = [x for x in scenes if x.startswith(f"{ch}-")]
        if ctx.params.get("recheck"):
            return list(scenes)
        fam = families(ctx)
        return [x for x in scenes if not report.is_fresh(s, x, fam)]

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if target is None or effective_text(ctx.store, target) is None:
            return [Problem("error", "no draft to check", target)]
        return []

    # structured hooks unused: this stage orchestrates several calls
    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:  # pragma: no cover
        raise NotImplementedError

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:  # pragma: no cover
        raise NotImplementedError

    def persist(
        self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str
    ) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    async def run(self, ctx: StageContext, target: str | None) -> StepResult:
        problems = [p for p in self.check_inputs(ctx, target) if p.level == "error"]
        if problems or target is None:
            raise StageError("; ".join(str(p) for p in problems) or "no target")
        s = ctx.store
        got = effective_text(s, target)
        assert got is not None
        eff, text = got
        fam = families(ctx)
        rep = report.QualityReport(
            scene_id=target, version=eff.version, sha256=eff.sha256, job_id=ctx.job.job_id
        )
        warnings: list[Problem] = []
        opened: list[str] = []
        calls = 0

        if "deterministic" in fam:
            found = deterministic.check_text(s, target, eff.version, text)
            now, _ = sync_findings(s, target, eff.version, found, owned_by(*deterministic.DETECTORS))
            opened += [f.id for f in now]
            rep.families.append("deterministic")

        if "continuity" in fam:
            adjudicate = bool(ctx.params.get("adjudicate", s.project.settings.get("adjudicate", True)))
            checker = ContinuityChecker(s, ctx.router, ctx.prompts, adjudicate=adjudicate)
            res = await checker.check(target, cancel=ctx.cancel)
            calls += 1 + len(res.adjudicated)
            if res.extraction.error:
                rep.errors.append(f"continuity: {res.extraction.error}")
                warnings.append(Problem("warning", f"fact extraction failed: {res.extraction.error}", target))
            else:
                opened += [f.id for f in res.findings]
                rep.families.append("continuity")
                ctx.emit(
                    "critique.facts",
                    {
                        "scene": target,
                        "kept": len(res.extraction.observations),
                        "discarded": len(res.extraction.discarded),
                        "queued": [f.id for f in res.extraction.candidates],
                        "adjudicated": res.adjudicated,
                    },
                )

        critics = Critics(s, ctx.router, ctx.prompts)
        if "craft" in fam:
            craft = await critics.craft(target, text, eff.version, cancel=ctx.cancel)
            calls += 1
            if craft.error:
                rep.errors.append(f"craft: {craft.error}")
                warnings.append(Problem("warning", f"craft rubric failed: {craft.error}", target))
            else:
                now, _ = sync_findings(s, target, eff.version, craft.findings, owned_by("critic:craft_"))
                opened += [f.id for f in now]
                rep.scores = craft.scores
                rep.families.append("craft")
                if craft.dropped:
                    ctx.emit(
                        "critique.dropped", {"scene": target, "critic": "craft", "reasons": craft.dropped}
                    )

        if "voice" in fam:
            voiced = await critics.voice(target, text, eff.version, cancel=ctx.cancel)
            now, _ = sync_findings(s, target, eff.version, voiced, owned_by("critic:voice"))
            opened += [f.id for f in now]
            rep.families.append("voice")

        open_now = [f for f in s.findings.all() if f.scene_id == target and f.status == "open"]
        rep.open_findings = dict(Counter(f.severity for f in open_now))
        report.save(s, rep)
        ctx.emit("critique.scene", {"scene": target, "version": eff.version, "open": rep.open_findings,
                                    "scores": rep.scores, "errors": rep.errors})  # fmt: skip
        return StepResult(
            [f"report:{target}@v{eff.version}", *(f"finding:{f}" for f in opened)], warnings, calls
        )

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        """A report this job already wrote for the scene's current text (crash before the checkpoint)."""
        if target is None:
            return None
        r = report.load(ctx.store, target)
        if r is not None and r.job_id == ctx.job.job_id and report.is_fresh(ctx.store, target, families(ctx)):
            return [f"report:{target}@v{r.version}"]
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        open_ = [f for f in ctx.store.findings.all() if f.status == "open" and not f.is_hypothesis]
        by = Counter(f.severity for f in open_)
        if not open_:
            return []
        summary = ", ".join(f"{by[k]} {k}" for k in ("blocker", "high", "med", "low") if by[k])
        return [Problem("warning", f"open findings: {summary} — review with `inkforge findings`")]

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return []
