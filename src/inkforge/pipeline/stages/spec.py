"""Stage A — Book Spec (docs/05). Idea → BookSpec; optional enhance pass shown as a field diff.

"Never contradict the author" is enforced by code, not trusted to the model: fields the author set
explicitly are written back over whatever the model produced, on every version.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from inkforge.domain.common import Actor
from inkforge.domain.models import BookSpec, Setting
from inkforge.pipeline.proposals import SpecEnhancement, SpecProposal
from inkforge.pipeline.stage import Problem, Stage, StageContext
from inkforge.prompts.registry import RenderedPrompt
from inkforge.storage.workspace import ArtifactKind

# Author-settable fields (CLI flags / API) → BookSpec attribute
AUTHOR_FIELDS = (
    "genre",
    "audience",
    "tone",
    "pov",
    "tense",
    "target_length",
    "setting_place",
    "setting_time",
    "themes",
)
TARGET_RANGES = {"prose": (1_000, 250_000), "script": (10, 200)}  # words / pages


def spec_from_proposal(
    p: SpecProposal, *, original: str, author: dict[str, Any], target_length: int | None
) -> BookSpec:
    data: dict[str, Any] = p.model_dump(exclude={"setting_place", "setting_time"})
    data["setting"] = Setting(place=p.setting_place, time=p.setting_time)
    data["target_length"] = target_length
    spec = BookSpec(original=original, **data)
    return apply_author_fields(spec, author)


def apply_author_fields(spec: BookSpec, author: dict[str, Any]) -> BookSpec:
    upd: dict[str, Any] = {}
    setting = spec.setting
    for k, v in author.items():
        if v is None:
            continue
        if k == "setting_place":
            setting = setting.model_copy(update={"place": v})
        elif k == "setting_time":
            setting = setting.model_copy(update={"time": v})
        else:
            upd[k] = v
    keys = sorted({*spec.author_fields, *(k for k, v in author.items() if v is not None)})
    return BookSpec.model_validate(
        {**spec.model_dump(by_alias=True), **upd, "setting": setting.model_dump(), "author_fields": keys}
    )


def field_diff(old: BookSpec, new: BookSpec) -> dict[str, tuple[Any, Any]]:
    skip = {"schema_id", "assumptions"}
    a, b = old.model_dump(), new.model_dump()
    return {k: (a[k], b[k]) for k in a if k not in skip and a[k] != b[k]}


class SpecStage(Stage):
    name = "spec"
    profile = "planner"
    gate = "spec"

    def targets(self, ctx: StageContext) -> list[str | None]:
        return [None]

    def _enhancing(self, ctx: StageContext) -> bool:
        return bool(ctx.params.get("enhance")) and ctx.store.try_read("spec") is not None

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if not self._enhancing(ctx) and not (ctx.params.get("idea") or ctx.store.try_read("spec")):
            return [Problem("error", "no idea given (use --idea) and no existing spec")]
        return []

    def _author(self, ctx: StageContext) -> dict[str, Any]:
        given = ctx.params.get("author_fields") or {}
        existing = ctx.store.try_read("spec")
        keep = {k: getattr(existing, k, None) for k in existing.author_fields} if existing else {}
        if existing:
            keep |= {
                k: getattr(existing.setting, k.split("_", 1)[1])
                for k in existing.author_fields
                if k.startswith("setting_")
            }
        return keep | {k: v for k, v in given.items() if k in AUTHOR_FIELDS and v is not None}

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:
        if self._enhancing(ctx):
            spec = ctx.store.read("spec")
            return ctx.prompts.render(
                "spec.enhance",
                spec_json=spec.model_dump_json(indent=2, exclude={"schema_id"}),
                fixed=sorted(self._author(ctx)),
            )
        existing = ctx.store.try_read("spec")
        idea = ctx.params.get("idea") or (existing.original if existing else "")
        return ctx.prompts.render(
            "spec.generate",
            idea=idea.strip(),
            form=ctx.form.label,
            target_length=self._target(ctx),
            unit="pages" if ctx.form.output == "script" else "words",
            fixed=self._author(ctx),
        )

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:
        return SpecEnhancement if self._enhancing(ctx) else SpecProposal

    def _target(self, ctx: StageContext) -> int | None:
        return self._author(ctx).get("target_length") or ctx.store.project.target_length

    def persist(self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str) -> list[str]:
        prov = ctx.provenance(self.name, target, prompt_ref)
        author = self._author(ctx)
        if isinstance(value, SpecEnhancement):
            old = ctx.store.read("spec")
            new = spec_from_proposal(
                value.improved, original=old.original, author=author, target_length=old.target_length
            )
            new = new.model_copy(
                update={
                    "assumptions": [
                        *old.assumptions,
                        *(f"enhance: {c.field} — {c.why}" for c in value.changes),
                    ]
                }
            )
            diff = field_diff(old, new)
            ctx.emit(
                "spec.diff",
                {
                    "changed": {k: {"from": a, "to": b} for k, (a, b) in diff.items()},
                    "critique": value.critique,
                },
            )
            rec = ctx.store.save(
                "spec",
                new,
                actor=Actor.SYSTEM,
                note="enhance",
                extra={**prov, "diff": sorted(diff)},
                job_id=ctx.job.job_id,
            )
        else:
            idea = (ctx.params.get("idea") or ctx.store.read("spec").original).strip()
            spec = spec_from_proposal(value, original=idea, author=author, target_length=self._target(ctx))
            rec = ctx.store.save("spec", spec, actor=Actor.SYSTEM, extra=prov, job_id=ctx.job.job_id)
        return [f"spec@v{rec.version}"]

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        m = ctx.store.meta("spec")
        if m.active and m.get(m.active).extra.get("job_id") == ctx.job.job_id:
            return [f"spec@v{m.active}"]
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        spec = ctx.store.read("spec")
        out = []
        lo, hi = TARGET_RANGES[ctx.form.output]
        if spec.target_length and not lo <= spec.target_length <= hi:
            out.append(
                Problem("warning", f"target length {spec.target_length} is unusual for a {ctx.form.label}")
            )
        if not spec.content_boundaries:
            out.append(Problem("warning", 'no content boundaries; set them or say "none"'))
        return out

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return [("spec", None)]
