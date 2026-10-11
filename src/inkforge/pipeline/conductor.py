"""Conductor: runs stages as a resumable job (docs/03 § Data flow, docs/05).

- A job is a list of stages; each stage expands into steps (one per target). The job file
  `runs/<job>/job.json` is rewritten after every step: that is the checkpoint. Killing the process at
  any moment loses at most the in-flight model call.
- Gates: after a stage finishes, if its gate is on, the job stops in `awaiting_approval` (unless
  `--yes`, which approves *planning* gates only and is audited as such — D23). Codex/canon candidates
  are never accepted by --yes.
- Failures leave the last valid artifact active: nothing is persisted until output validated.
- Transient runtime errors (timeouts, load failures, runtime down) get one retry per step.
- One conductor per project at a time (`.run.lock`); a `cancel` file in the run dir stops the job.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from inkforge.config import load_app_config
from inkforge.domain.common import utcnow
from inkforge.domain.ids import job_id as new_job_id
from inkforge.domain.models import Job, JobStatus, JobStep, StepStatus
from inkforge.models.router import GenerationCancelled, ModelRouter, StructuredOutputError
from inkforge.models.types import CancelToken, GenerationTimeout, ModelLoadError, RuntimeUnavailable
from inkforge.pipeline.stage import (
    Problem,
    Stage,
    StageContext,
    StageError,
    approve_artifacts,
    gate_satisfied,
)
from inkforge.pipeline.stages.cards import CardsStage
from inkforge.pipeline.stages.chapters import ChaptersStage
from inkforge.pipeline.stages.design import DesignStage
from inkforge.pipeline.stages.draft import DraftStage
from inkforge.pipeline.stages.export import PreviewStage
from inkforge.pipeline.stages.spec import SpecStage
from inkforge.policy.lock import ProjectLock
from inkforge.prompts.registry import PromptError, PromptRegistry
from inkforge.prompts.skills import SkillRegistry
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ProjectStore, repair
from inkforge.system import keep_awake, power_status

STAGES: dict[str, Stage] = {
    s.name: s
    for s in (SpecStage(), DesignStage(), ChaptersStage(), CardsStage(), DraftStage(), PreviewStage())
}
PLAN_ORDER = ["spec", "design", "chapters", "cards"]
# full ends at a watermarked preview: a final export always needs explicit approval (D23)
MODES = {"plan": PLAN_ORDER, "draft": ["drafts"], "full": [*PLAN_ORDER, "drafts", "preview"]}
PLANNING_GATES = {"spec", "design", "outline", "scene_cards"}  # --yes may answer these (D23)
TRANSIENT = (GenerationTimeout, ModelLoadError, RuntimeUnavailable)
EventSink = Callable[[str, dict[str, Any]], None]


class JobBlocked(RuntimeError):
    pass


def stages_for(mode: str | None = None, *, stage: str | None = None, until: str | None = None) -> list[str]:
    if stage:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; have {sorted(STAGES)}")
        return [stage]
    order = MODES[mode or "plan"]
    if until:
        if until not in order:
            raise ValueError(f"--until must be one of {order}")
        order = order[: order.index(until) + 1]
    return list(order)


class Conductor:
    def __init__(
        self,
        store: ProjectStore,
        router: ModelRouter,
        *,
        prompts: PromptRegistry | None = None,
        skills: SkillRegistry | None = None,
        gates: dict[str, bool] | None = None,
        on_event: EventSink | None = None,
    ):
        self.store, self.router = store, router
        self.prompts = prompts or PromptRegistry(project_dir=store.dir / "prompts")
        self.skills = skills or SkillRegistry(project_dir=store.dir / "skills")
        defaults = load_app_config().approval_gates
        self.gates = {**defaults, **store.project.settings.get("approval_gates", {}), **(gates or {})}
        self.on_event = on_event

    # ------------------------------------------------------------ jobs
    def create_job(self, stages: list[str], params: dict[str, Any] | None = None) -> Job:
        job = Job(
            job_id=new_job_id(),
            project_id=self.store.id,
            stage=",".join(stages),
            params={"stages": stages, **(params or {})},
            events_path=None,
        )
        job.events_path = f"runs/{job.job_id}/events.jsonl"
        self.store.save_job(job)
        self._event(job, "job.created", {"stages": stages})
        return job

    def load_job(self, job_id: str) -> Job:
        return Job.model_validate_json(self.store.path(f"runs/{job_id}/job.json").read_text(encoding="utf-8"))

    def gate_on(self, gate: str | None) -> bool:
        return bool(gate) and bool(self.gates.get(gate, False))

    def _ctx(self, job: Job, cancel: CancelToken | None) -> StageContext:
        return StageContext(
            self.store,
            self.router,
            self.prompts,
            self.skills,
            job,
            dict(job.params),
            cancel,
            emit=lambda kind, data: self._event(job, kind, data),
        )

    def _event(self, job: Job, kind: str, data: dict[str, Any]) -> None:
        rec = {"ts": utcnow().isoformat(), "type": kind, **data}
        path = self.store.path(f"runs/{job.job_id}/events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if self.on_event:
            self.on_event(kind, rec)

    def _checkpoint(self, job: Job) -> None:
        self.store.save_job(job)

    def request_cancel(self, job_id: str) -> None:
        atomic_write_text(self.store.path(f"runs/{job_id}/cancel"), "cancel requested\n")

    # ------------------------------------------------------------ running
    async def run(self, job: Job, *, cancel: CancelToken | None = None) -> Job:
        with ProjectLock(self.store.dir, timeout=1.0, name=".run.lock"):
            fixed = repair(self.store)  # leftovers from a crash: temp files, orphan versions
            if any(fixed.values()):
                self._event(job, "project.repaired", fixed)
            cancel = cancel or CancelToken()
            power = power_status()
            if power.on_battery:
                self._event(
                    job,
                    "system.on_battery",
                    {
                        "percent": power.battery_percent,
                        "message": "on battery: Windows may hibernate during a long run; plug in",
                    },
                )
            watcher = asyncio.ensure_future(self._watch_cancel_file(job, cancel))
            try:
                with keep_awake():  # no idle sleep mid-job (lid close / battery hibernate can still happen)
                    return await self._run(job, cancel)
            finally:
                watcher.cancel()

    async def _watch_cancel_file(self, job: Job, cancel: CancelToken) -> None:
        flag = self.store.path(f"runs/{job.job_id}/cancel")
        while not cancel.cancelled:
            if flag.exists():
                cancel.cancel()
                return
            await asyncio.sleep(0.5)

    async def _run(self, job: Job, cancel: CancelToken) -> Job:
        if job.status in (JobStatus.SUCCEEDED, JobStatus.CANCELLED):
            return job
        stages: list[str] = job.params["stages"]
        yes = bool(job.params.get("yes"))
        job.status, job.awaiting, job.error_summary = JobStatus.RUNNING, None, None
        job.started_at = job.started_at or utcnow()
        self._checkpoint(job)
        ctx = self._ctx(job, cancel)
        for i, name in enumerate(stages):
            stage = STAGES[name]
            # prerequisites: upstream gates must be passed (standalone runs too)
            for g in stage.requires_gates:
                if self.gate_on(g):
                    owner = next(st for st in STAGES.values() if st.gate == g)
                    arts = owner.gate_artifacts(ctx)
                    if not gate_satisfied(self.store, arts):
                        if yes and g in PLANNING_GATES:
                            done = approve_artifacts(self.store, arts, via="--yes")
                            self._event(job, "gate.auto_approved", {"gate": g, "approved": done})
                        else:
                            return self._block(job, g, f"{g} is not approved — run `inkforge approve {g}`")
            try:
                targets = stage.targets(ctx)
            except FileNotFoundError as e:
                return self._fail(job, f"{name}: missing input ({e})")
            self._event(job, "stage.start", {"stage": name, "targets": targets})
            for target in targets:
                step = self._step(job, name, target)
                if step.status == StepStatus.DONE:
                    continue
                if (adopted := stage.adopt(ctx, target)) is not None:
                    self._done(job, step, adopted, adopted=True)
                    continue
                for attempt in (1, 2):
                    step.attempts += 1
                    self._event(job, "step.start", {"step": step.key, "attempt": attempt})
                    t0 = time.monotonic()
                    try:
                        result = await stage.run(ctx, target)
                    except GenerationCancelled:
                        return self._cancelled(job, step)
                    except TRANSIENT as e:
                        if cancel.cancelled:
                            return self._cancelled(job, step)
                        if attempt == 2:
                            return self._fail(job, f"{step.key}: {type(e).__name__}: {e}", step)
                        self._event(job, "step.retry", {"step": step.key, "error": str(e)})
                        await asyncio.sleep(2)
                        continue
                    except (StructuredOutputError, StageError, PromptError) as e:
                        return self._fail(job, f"{step.key}: {e}", step)
                    except Exception as e:  # never leave a job stuck in "running"
                        self._fail(job, f"{step.key}: unexpected {type(e).__name__}: {e}", step)
                        raise
                    for p in result.problems:  # e.g. advisory checks the model didn't fix (D32)
                        self._event(
                            job,
                            "problem",
                            {"stage": name, "level": p.level, "message": p.message, "target": p.target},
                        )
                    self._done(
                        job,
                        step,
                        result.output_refs,
                        seconds=time.monotonic() - t0,
                        model_attempts=result.attempts,
                    )
                    break
                if cancel.cancelled:
                    return self._cancelled(job, None)
            problems: list[Problem] = stage.finalize(ctx)
            for p in problems:
                self._event(
                    job,
                    "problem",
                    {"stage": name, "level": p.level, "message": p.message, "target": p.target},
                )
            self._event(job, "stage.done", {"stage": name, "problems": len(problems)})
            more_to_do = i < len(stages) - 1
            if self.gate_on(stage.gate) and not gate_satisfied(self.store, stage.gate_artifacts(ctx)):
                if yes and stage.gate in PLANNING_GATES:
                    done = approve_artifacts(self.store, stage.gate_artifacts(ctx), via="--yes")
                    self._event(job, "gate.auto_approved", {"gate": stage.gate, "approved": done})
                elif more_to_do:
                    return self._block(
                        job, stage.gate, f"review and approve: `inkforge approve {stage.gate}`, then resume"
                    )
                else:
                    self._event(job, "gate.pending", {"gate": stage.gate})
        job.status, job.finished_at = JobStatus.SUCCEEDED, utcnow()
        self._checkpoint(job)
        self._event(job, "job.succeeded", {})
        return job

    async def resume(self, job_id: str, *, cancel: CancelToken | None = None) -> Job:
        job = self.load_job(job_id)
        flag = self.store.path(f"runs/{job_id}/cancel")
        flag.unlink(missing_ok=True)
        if job.status == JobStatus.CANCELLED:
            job.status = JobStatus.QUEUED  # explicit resume of a cancelled job restarts from its checkpoint
        self._event(job, "job.resume", {"from_status": str(job.status)})
        return await self.run(job, cancel=cancel)

    def latest_job(self) -> Job | None:
        jobs = sorted(self.store.jobs(), key=lambda j: j.job_id)
        return jobs[-1] if jobs else None

    # ------------------------------------------------------------ step bookkeeping
    def _step(self, job: Job, stage: str, target: str | None) -> JobStep:
        for s in job.steps:
            if s.stage == stage and s.target == target:
                return s
        s = JobStep(stage=stage, target=target)
        job.steps.append(s)
        return s

    def _done(self, job: Job, step: JobStep, refs: list[str], **detail: Any) -> None:
        step.status, step.output_refs, step.error, step.finished_at = StepStatus.DONE, refs, None, utcnow()
        job.output_refs = [*job.output_refs, *refs]
        self._checkpoint(job)
        self._event(job, "step.done", {"step": step.key, "refs": refs, **detail})

    def _fail(self, job: Job, msg: str, step: JobStep | None = None) -> Job:
        if step:
            step.status, step.error = StepStatus.FAILED, msg
        job.status, job.error_summary, job.finished_at = JobStatus.FAILED, msg, utcnow()
        self._checkpoint(job)
        self._event(job, "job.failed", {"error": msg})
        return job

    def _block(self, job: Job, gate: str | None, msg: str) -> Job:
        job.status, job.awaiting = JobStatus.AWAITING_APPROVAL, gate
        self._checkpoint(job)
        self._event(job, "job.awaiting_approval", {"gate": gate, "message": msg})
        return job

    def _cancelled(self, job: Job, step: JobStep | None) -> Job:
        job.status, job.finished_at = JobStatus.CANCELLED, utcnow()
        self._checkpoint(job)
        self._event(job, "job.cancelled", {"step": step.key if step else None})
        return job
