"""Preview export as the last stage of `run --mode full` (D23: an autopilot run ends at a watermarked
draft preview; a final export always needs explicit approval and `inkforge export`). No model call."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from inkforge.exporters.markdown import export_markdown
from inkforge.pipeline.stage import Problem, Stage, StageContext, StepResult
from inkforge.prompts.registry import RenderedPrompt
from inkforge.storage.workspace import ArtifactKind


class PreviewStage(Stage):
    name = "preview"
    profile = "prose"

    def targets(self, ctx: StageContext) -> list[str | None]:
        return [None]

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:  # pragma: no cover
        raise NotImplementedError

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:  # pragma: no cover
        raise NotImplementedError

    def persist(
        self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str
    ) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    async def run(self, ctx: StageContext, target: str | None) -> StepResult:
        res = export_markdown(ctx.store, mode="preview")
        rel = res.path.relative_to(ctx.store.dir).as_posix()
        ctx.emit("export.preview", {"path": rel, "words": res.words, "warnings": len(res.report.warnings)})
        return StepResult([f"export:{rel}"], [Problem("warning", w) for w in res.report.warnings])

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return []
