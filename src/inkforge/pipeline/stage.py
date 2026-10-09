"""Stage contract (docs/05): every stage is deterministic except its model call.

    check_inputs → build_prompt → generate_structured (router: validate, repair, backoff) → persist

A stage runs for one *target* at a time (None, an act id, a chapter id…). The conductor checkpoints
after every target. `adopt()` lets a resumed job recognise work that was persisted just before a
crash (the version carries this job's id and step key), so it isn't generated twice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from inkforge.domain.common import Actor
from inkforge.domain.forms import FormProfile, get_form
from inkforge.domain.models import Job
from inkforge.models.recorder import RunRecorder
from inkforge.models.router import ModelRouter
from inkforge.models.types import CancelToken
from inkforge.prompts.registry import PromptRegistry, RenderedPrompt
from inkforge.prompts.skills import SkillRegistry
from inkforge.storage.workspace import ArtifactKind, ProjectStore


@dataclass
class Problem:
    level: Literal["error", "warning"]
    message: str
    target: str | None = None

    def __str__(self) -> str:
        return f"[{self.level}] {self.target + ': ' if self.target else ''}{self.message}"


@dataclass
class StepResult:
    output_refs: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    attempts: int = 0


@dataclass
class StageContext:
    store: ProjectStore
    router: ModelRouter
    prompts: PromptRegistry
    skills: SkillRegistry
    job: Job
    params: dict[str, Any] = field(default_factory=dict)
    cancel: CancelToken | None = None
    emit: Callable[[str, dict[str, Any]], None] = lambda kind, data: None

    @property
    def form(self) -> FormProfile:
        return get_form(self.store.project.form)

    @property
    def run_dir(self) -> Path:
        return self.store.path(f"runs/{self.job.job_id}")

    def step_key(self, stage: str, target: str | None) -> str:
        return f"{stage}:{target}" if target else stage

    def provenance(self, stage: str, target: str | None, prompt_ref: str) -> dict[str, Any]:
        """Stored in the version's `extra` — used by adopt() on resume and for traceability."""
        return {"job_id": self.job.job_id, "step": self.step_key(stage, target), "prompt_ref": prompt_ref}


class StageError(RuntimeError):
    """Inputs missing or not approved: the job fails without calling the model."""


class Stage(ABC):
    name: ClassVar[str]
    profile: ClassVar[str] = "planner"
    gate: ClassVar[str | None] = None  # approval gate this stage's output sits behind (defaults.toml keys)
    requires_gates: ClassVar[tuple[str, ...]] = ()

    # ---- planning
    @abstractmethod
    def targets(self, ctx: StageContext) -> list[str | None]: ...

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        return []

    # ---- one target
    @abstractmethod
    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt: ...

    @abstractmethod
    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]: ...

    def allowed_ids(self, ctx: StageContext, target: str | None) -> Mapping[str, Collection[str]] | None:
        return None

    def validate(self, ctx: StageContext, target: str | None, value: Any) -> list[str]:
        """Rules whose failure should go back to the model as a repair request."""
        return []

    @abstractmethod
    def persist(self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str) -> list[str]: ...

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        """Refs of output this job already persisted for this target (crash after write, before checkpoint)."""
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        """Whole-stage deterministic checks after all targets (warnings for the author)."""
        return []

    async def run(self, ctx: StageContext, target: str | None) -> StepResult:
        problems = [p for p in self.check_inputs(ctx, target) if p.level == "error"]
        if problems:
            raise StageError("; ".join(str(p) for p in problems))
        rp = self.build_prompt(ctx, target)
        rec = RunRecorder(ctx.run_dir / ctx.step_key(self.name, target).replace(":", "_"))
        rec.prompt(
            rp.messages, prompt_ref=rp.ref, profile=rp.profile, model=ctx.router.profile(rp.profile).model
        )
        try:
            res = await ctx.router.generate_structured(
                rp.messages,
                self.schema(ctx, target),
                rp.profile,
                allowed_ids=self.allowed_ids(ctx, target),
                validate=lambda v: self.validate(ctx, target, v),
                cancel=ctx.cancel,
                recorder=rec,
            )
        except BaseException as e:
            rec.finish("failed", error=f"{type(e).__name__}: {e}")
            raise
        refs = self.persist(ctx, target, res.value, rp.ref)
        rec.finish("succeeded", output_refs=refs)
        return StepResult(refs, [], len(res.attempts))

    # ---- gates
    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        """Artifacts that must be approved for this stage's gate to count as passed."""
        return []


def gate_satisfied(store: ProjectStore, artifacts: list[tuple[ArtifactKind, str | None]]) -> bool:
    if not artifacts:
        return False
    for kind, target in artifacts:
        m = store.meta(kind, target)
        if m.active is None or m.approved != m.active:
            return False
    return True


def approve_artifacts(
    store: ProjectStore, artifacts: list[tuple[ArtifactKind, str | None]], *, via: str | None
) -> list[str]:
    done = []
    for kind, target in artifacts:
        m = store.meta(kind, target)
        if m.active is not None and m.approved != m.active:
            store.approve(kind, target, via=via)
            done.append(f"{kind}:{target}" if target else kind)
    return done


ACTOR_SYSTEM = Actor.SYSTEM
