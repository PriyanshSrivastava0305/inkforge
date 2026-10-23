"""Stage C — Chapter plans, generated one act at a time (docs/05).

Small prompts per act keep an 8B model on task. Chapter IDs are minted by code and reused when an act
is regenerated, so references elsewhere stay valid (D7). An optional whole-outline enhance pass sees
only titles + purposes. Outline-wide checks run after the last act and become author warnings.
"""

from __future__ import annotations

import difflib
import itertools
import re
from typing import Any

from pydantic import BaseModel

from inkforge.domain import ids
from inkforge.domain.codex import active_characters
from inkforge.domain.models import ChapterPlan, Outline, OutlineAct, OutlineChapter, PromiseStatus
from inkforge.pipeline.blocks import cast_block, design_block, promises_block, spec_block
from inkforge.pipeline.proposals import act_plan_model, outline_enhance_model
from inkforge.pipeline.stage import Problem, Stage, StageContext
from inkforge.prompts.registry import RenderedPrompt
from inkforge.storage.workspace import ArtifactKind

WORDS_PER_CHAPTER = {"prose": 3000}
ENHANCE = "enhance"


_LABEL = re.compile(r"^\s*(?:chapter|act|part|section)\s+[\w-]+\s*[:.\-–—]\s*", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Drop "Chapter 3: " / "Act 2 — " prefixes models add; numbering comes from outline order."""
    cleaned = _LABEL.sub("", title).strip(" :–—-")
    return cleaned or title.strip()


def total_chapters(ctx: StageContext) -> int:
    explicit = ctx.params.get("chapters") or ctx.store.project.settings.get("chapters")
    if explicit:
        return int(explicit)
    target = ctx.store.read("spec").target_length or ctx.store.project.target_length or 30000
    return max(3, round(target / WORDS_PER_CHAPTER.get(ctx.form.output, 3000)))


def split_over_acts(total: int, n_acts: int) -> list[int]:
    """Largest-remainder split; three acts get 1:2:1 weight (docs/05 three-act proportions)."""
    weights = [1, 2, 1] if n_acts == 3 else [1] * n_acts
    raw = [total * w / sum(weights) for w in weights]
    counts = [int(r) for r in raw]
    for i in sorted(range(n_acts), key=lambda i: raw[i] - counts[i], reverse=True)[: total - sum(counts)]:
        counts[i] += 1
    return [max(1, c) for c in counts]


class ChaptersStage(Stage):
    name = "chapters"
    profile = "planner"
    gate = "outline"
    requires_gates = ("design",)

    def _acts(self, ctx: StageContext) -> list[str]:
        return [a.id for a in ctx.store.read("design").acts]

    def targets(self, ctx: StageContext) -> list[str | None]:
        only = ctx.params.get("act")
        acts: list[str | None] = [a for a in self._acts(ctx) if not only or a == only]
        return [*acts, ENHANCE] if ctx.params.get("outline_enhance") else acts

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if not ctx.store.try_read("design"):
            return [Problem("error", "no story design yet — run the design stage")]
        if target not in (ENHANCE, *self._acts(ctx)):
            return [Problem("error", f"unknown act {target!r}", target)]
        return []

    def _counts(self, ctx: StageContext) -> dict[str, int]:
        acts = self._acts(ctx)
        return dict(zip(acts, split_over_acts(total_chapters(ctx), len(acts)), strict=True))

    def _words_per_chapter(self, ctx: StageContext) -> int:
        target = ctx.store.read("spec").target_length or ctx.store.project.target_length or 30000
        return max(500, round(target / total_chapters(ctx)))

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:
        s = ctx.store
        if target == ENHANCE:
            outline = s.read("outline")
            items = [
                (c.id, c.title, s.read("chapter", c.id).purpose)
                for c in sorted(outline.chapters, key=lambda c: c.order)
            ]
            return ctx.prompts.render(
                "outline.enhance", spec_block=spec_block(s.read("spec")), chapters=items
            )
        assert target is not None
        design = s.read("design")
        act = next(a for a in design.acts if a.id == target)
        earlier = []
        outline = s.try_read("outline")
        acts = self._acts(ctx)
        if outline:
            for c in sorted(outline.chapters, key=lambda c: c.order):
                if c.act_id in acts[: acts.index(target)]:
                    plan = s.try_read("chapter", c.id)
                    if plan:
                        earlier.append(f"{c.title}: {plan.purpose} → {plan.outcome}")
        return ctx.prompts.render(
            "outline.act",
            spec_block=spec_block(s.read("spec")),
            design_block=design_block(design),
            cast_block=cast_block(s),
            promises_block=promises_block(s.get_promises()),
            act=act,
            act_no=acts.index(target) + 1,
            n_acts=len(acts),
            n=self._counts(ctx)[target],
            earlier=earlier,
            words=self._words_per_chapter(ctx),
            unit=ctx.form.unit,
        )

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:
        if target == ENHANCE:
            return outline_enhance_model(len(ctx.store.read("outline").chapters))
        assert target is not None
        chars = [c.id for c in active_characters(ctx.store)]
        proms = [p.id for p in ctx.store.get_promises().promises if p.status == PromiseStatus.OPEN]
        return act_plan_model(self._counts(ctx)[target], chars, proms)

    def advise(self, ctx: StageContext, target: str | None, value: Any) -> list[str]:
        if target == ENHANCE:
            return []
        purposes = [c.purpose.strip().lower() for c in value.chapters]
        dupes = {p for p in purposes if purposes.count(p) > 1}
        return [f"chapters: two chapters share the purpose {p!r}; give each its own function" for p in dupes]

    def persist(self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str) -> list[str]:
        s, prov = ctx.store, ctx.provenance(self.name, target, prompt_ref)
        if target == ENHANCE:
            return self._persist_enhance(ctx, value, prov)
        acts = self._acts(ctx)
        outline = s.try_read("outline") or Outline()
        by_act: dict[str, list[OutlineChapter]] = {a: [] for a in acts}
        for c in sorted(outline.chapters, key=lambda c: c.order):
            by_act.setdefault(c.act_id or acts[0], []).append(c)
        existing_ids = [c.id for c in outline.chapters] + s.chapter_ids()
        reuse = [c.id for c in by_act.get(target, [])]  # type: ignore[arg-type]
        new_entries = []
        for i, ch in enumerate(value.chapters):
            if i < len(reuse):
                cid = reuse[i]
            else:
                cid = ids.chapter_id(existing_ids)
                existing_ids.append(cid)
            new_entries.append(OutlineChapter(id=cid, order=0, act_id=target, title=clean_title(ch.title)))
        by_act[target] = new_entries  # type: ignore[index]
        ordered = [c for a in acts for c in by_act.get(a, [])]
        new_outline = Outline(
            acts=[OutlineAct(id=a, order=i) for i, a in enumerate(acts)],
            chapters=[c.model_copy(update={"order": i}) for i, c in enumerate(ordered)],
        )
        # outline first: chapter plans hash their outline entry as an input
        rec = s.save("outline", new_outline, extra=prov, job_id=ctx.job.job_id)
        refs = [f"outline@v{rec.version}"]
        words = self._words_per_chapter(ctx)
        for entry, ch in zip(new_entries, value.chapters, strict=True):
            plan = ChapterPlan(
                id=entry.id,
                act_id=target,
                title=clean_title(ch.title),
                purpose=ch.purpose,
                conflict=ch.conflict,
                turning_point=ch.turning_point,
                outcome=ch.outcome,
                causal_link_prev=ch.causal_link_prev,
                causal_link_next=ch.causal_link_next,
                characters=list(dict.fromkeys(ch.characters)),
                promises_touched=list(dict.fromkeys(ch.promises_touched)),
                pacing_note=ch.pacing_note,
                target_length=words,
            )
            r = s.save("chapter", plan, extra=prov, job_id=ctx.job.job_id)
            refs.append(f"chapter:{entry.id}@v{r.version}")
        return refs

    def _persist_enhance(self, ctx: StageContext, value: Any, prov: dict[str, Any]) -> list[str]:
        s = ctx.store
        outline = s.read("outline")
        ordered = sorted(outline.chapters, key=lambda c: c.order)
        refs = []
        retitled = {
            c.id: clean_title(e.title)
            for c, e in zip(ordered, value.chapters, strict=True)
            if e.title != c.title
        }
        if retitled:
            new = outline.model_copy(
                update={
                    "chapters": [
                        c.model_copy(update={"title": retitled.get(c.id, c.title)}) for c in outline.chapters
                    ]
                }
            )
            refs.append(
                f"outline@v{s.save('outline', new, extra=prov, note='enhance', job_id=ctx.job.job_id).version}"
            )
        for c, e in zip(ordered, value.chapters, strict=True):
            plan = s.read("chapter", c.id)
            if e.purpose != plan.purpose or e.title != plan.title:
                r = s.save(
                    "chapter",
                    plan.model_copy(update={"purpose": e.purpose, "title": e.title}),
                    extra=prov,
                    note="enhance",
                    job_id=ctx.job.job_id,
                )
                refs.append(f"chapter:{c.id}@v{r.version}")
        ctx.emit("outline.enhance", {"critique": value.critique, "changed": refs})
        return refs

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        step = ctx.step_key(self.name, target)
        if target == ENHANCE:
            return None  # cheap to redo and idempotent in effect
        outline = ctx.store.try_read("outline")
        if not outline:
            return None
        mine = [c.id for c in outline.chapters if c.act_id == target]
        if not mine:
            return None
        for cid in mine:
            m = ctx.store.meta("chapter", cid)
            extra = m.get(m.active).extra if m.active else {}
            if extra.get("job_id") != ctx.job.job_id or extra.get("step") != step:
                return None
        return [f"chapter:{c}" for c in mine]

    def finalize(self, ctx: StageContext) -> list[Problem]:
        out = outline_checks(ctx)
        asked = ctx.params.get("chapters") or ctx.store.project.settings.get("chapters")
        outline = ctx.store.try_read("outline")
        if asked and outline and len(outline.chapters) != int(asked):
            out.append(Problem("warning", f"asked for {asked} chapter(s) but every act needs at least one: "
                               f"planned {len(outline.chapters)}"))  # fmt: skip
        return out

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        outline = ctx.store.try_read("outline")
        if not outline:
            return []
        return [("outline", None), *(("chapter", c.id) for c in outline.chapters)]


def outline_checks(ctx: StageContext) -> list[Problem]:
    """Deterministic outline checks (docs/05 Stage C). Warnings: the author decides."""
    s = ctx.store
    outline, design = s.read("outline"), s.read("design")
    plans = {c.id: s.read("chapter", c.id) for c in outline.chapters}
    out: list[Problem] = []
    total = s.read("spec").target_length or 0
    n = len(plans)
    if total and n and not 1_000 <= total / n <= 8_000:
        out.append(Problem("warning", f"{n} chapters for {total} words is {total // n} words/chapter"))
    ordered = sorted(outline.chapters, key=lambda c: c.order)
    for a, b in itertools.pairwise(ordered):
        r = difflib.SequenceMatcher(None, plans[a.id].purpose.lower(), plans[b.id].purpose.lower()).ratio()
        if r >= 0.9:
            out.append(Problem("warning", f"{a.id} and {b.id} have near-identical purposes ({r:.0%})"))
    touched = {p for plan in plans.values() for p in plan.promises_touched}
    for p in s.get_promises().promises:
        if p.status == PromiseStatus.OPEN and p.importance != "low" and p.id not in touched:
            out.append(Problem("warning", f"promise {p.id} ({p.description}) is never touched by a chapter"))
    main = [c for c in active_characters(s) if c.role in ("protagonist", "antagonist")]
    for act in design.acts:
        in_act = {ch for c in ordered if c.act_id == act.id for ch in plans[c.id].characters}
        for c in main:
            if c.id not in in_act:
                out.append(Problem("warning", f"{c.name} ({c.role}) doesn't appear in {act.id}"))
    for c in ordered:
        p = plans[c.id]
        if not (p.causal_link_prev and p.causal_link_next):
            out.append(Problem("warning", f"{c.id} is missing a causal link"))
    return out
