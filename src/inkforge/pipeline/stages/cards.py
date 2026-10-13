"""Stage D — Scene cards, one chapter at a time (docs/05), plus Timeline placement (D20).

- POV/participants/promises are enums of known IDs; locations are names, resolved to the Codex by
  name/alias or added as *candidate* locations.
- Scene IDs are minted by code and reused when a chapter's cards are regenerated.
- Linear scenes go on the timeline after the story-order predecessor; flashbacks/flashforwards must
  name the earlier scene they follow (`happens_after`), validated against the allowed list.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from inkforge.domain import ids
from inkforge.domain.codex import active_characters, active_locations, find_by_name
from inkforge.domain.common import Actor
from inkforge.domain.models import (
    CodexStatus,
    Location,
    PromiseStatus,
    SceneCard,
    SceneTime,
    TemporalMode,
    Timeline,
    TimelineEntry,
)
from inkforge.pipeline.blocks import cast_block, promises_block, spec_block
from inkforge.pipeline.proposals import cards_model
from inkforge.pipeline.stage import Problem, Stage, StageContext
from inkforge.prompts.registry import RenderedPrompt
from inkforge.storage.workspace import ArtifactKind

NON_LINEAR = {"flashback", "flashforward"}


def scenes_for(ctx: StageContext, plan_target: int | None) -> int:
    explicit = ctx.params.get("scenes_per_chapter") or ctx.store.project.settings.get("scenes_per_chapter")
    if explicit:
        return int(explicit)
    # plan scenes at the size the prose model actually writes (profile `scene_words`), not a fixed 1,500
    natural = ctx.router.profile("prose").scene_words
    return min(8, max(1, round((plan_target or 3000) / natural)))


_CHARGE = re.compile(r"^\s*([+-])\s*(?:->|→)\s*([+-])\s*$")


def normalize_charge(charge: str) -> str:
    """ "+   ->-" → "+ -> -" (small models vary the spacing)."""
    m = _CHARGE.match(charge)
    return f"{m.group(1)} -> {m.group(2)}" if m else charge


def charge_changes(charge: str) -> bool:
    """ "+ -> -" changes; "+ -> +" doesn't. (Don't count the "-" inside "->" as a sign.)"""
    m = _CHARGE.match(charge)
    return bool(m) and m.group(1) != m.group(2)


class CardsStage(Stage):
    name = "cards"
    profile = "planner"
    gate = "scene_cards"
    requires_gates = ("outline",)

    def targets(self, ctx: StageContext) -> list[str | None]:
        outline = ctx.store.read("outline")
        order = outline.ordered_chapter_ids()
        only = ctx.params.get("chapter")
        return [c for c in order if not only or c == only]

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if target is None or ctx.store.try_read("chapter", target) is None:
            return [Problem("error", "no chapter plan", target)]
        if not active_characters(ctx.store):
            return [Problem("error", "no characters in the Codex — run the design stage")]
        return []

    def _earlier_scenes(self, ctx: StageContext, chapter: str) -> list[str]:
        """Scenes before this chapter in narrative order (candidates for `happens_after`)."""
        s = ctx.store
        order = s.read("outline").ordered_chapter_ids()
        before = set(order[: order.index(chapter)])
        return [sid for sid in s.scenes_in_order() if sid.split("-sc")[0] in before]

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:
        s = ctx.store
        assert target is not None
        plan = s.read("chapter", target)
        order = s.read("outline").ordered_chapter_ids()
        i = order.index(target)
        neighbour: dict[str, str | None] = {"previous": None, "next": None}
        for label, j in (("previous", i - 1), ("next", i + 1)):
            if 0 <= j < len(order) and (p := s.try_read("chapter", order[j])):
                neighbour[label] = f"{p.title}: {p.purpose} → {p.outcome}"
        earlier = []
        for sid in self._earlier_scenes(ctx, target)[-12:]:
            card = s.try_read("card", sid)
            if card:
                earlier.append(f"{sid}: {card.concrete_event}")
        n = scenes_for(ctx, plan.target_length)
        return ctx.prompts.render(
            "cards.chapter",
            spec_block=spec_block(s.read("spec")),
            cast_block=cast_block(s),
            promises_block=promises_block(s.get_promises()),
            chapter=plan,
            chapter_no=i + 1,
            neighbours=neighbour,
            locations=sorted(loc.name for loc in active_locations(s)),
            earlier=earlier,
            n=n,
            words=round((plan.target_length or 3000) / n),
        )

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:
        assert target is not None
        plan = ctx.store.read("chapter", target)
        chars = [c.id for c in active_characters(ctx.store)]
        proms = [p.id for p in ctx.store.get_promises().promises if p.status == PromiseStatus.OPEN]
        return cards_model(
            scenes_for(ctx, plan.target_length), chars, proms, self._earlier_scenes(ctx, target)
        )

    def advise(self, ctx: StageContext, target: str | None, value: Any) -> list[str]:
        errs = []
        for i, c in enumerate(value.cards):
            if not charge_changes(c.value_charge):
                errs.append(
                    f"cards[{i}].value_charge {c.value_charge!r}: the value must change (e.g. '+ -> -')"
                )
            if c.temporal_mode in NON_LINEAR and c.happens_after == "none":
                errs.append(
                    f"cards[{i}]: a {c.temporal_mode} must name the earlier scene it follows in happens_after"
                )
        return errs

    def persist(self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str) -> list[str]:
        s, prov, job = ctx.store, ctx.provenance(self.name, target, prompt_ref), ctx.job.job_id
        assert target is not None
        plan = s.read("chapter", target)
        refs: list[str] = []
        existing = [sid for sid in s.scene_ids() if sid.startswith(f"{target}-")]
        all_ids = list(s.scene_ids())
        per_scene = round((plan.target_length or 3000) / len(value.cards))
        new_ids = []
        for i, c in enumerate(value.cards):
            sid = existing[i] if i < len(existing) else ids.scene_id(target, all_ids)
            all_ids.append(sid)
            loc_id = self._location(ctx, c.location, refs)
            participants = list(dict.fromkeys([c.pov_character_id, *c.participants]))
            card = SceneCard(
                id=sid,
                chapter_id=target,
                order=i,
                pov_character_id=c.pov_character_id,
                location_id=loc_id,
                time=SceneTime(label=c.time_label),
                temporal_mode=TemporalMode(c.temporal_mode),
                participants=participants,
                want=c.want,
                obstacle=c.obstacle,
                change=c.change,
                story_value=c.story_value,
                value_charge=normalize_charge(c.value_charge),
                mood=c.mood,
                beats=c.beats,
                concrete_event=c.concrete_event,
                turn=c.turn,
                exit_condition=c.exit_condition,
                promises_touched=list(dict.fromkeys(c.promises_touched)),
                target_length=per_scene,
            )
            r = s.save("card", card, extra=prov, job_id=job)
            refs.append(f"card:{sid}@v{r.version}")
            new_ids.append((sid, c.temporal_mode, c.happens_after))
        for sid in existing[len(value.cards) :]:  # chapter now has fewer scenes: archive the extras
            s.delete_scene(sid, actor=Actor.SYSTEM)
            refs.append(f"archived:{sid}")
        refs.append(self._place_on_timeline(ctx, target, new_ids, [x for x in existing[len(value.cards) :]]))
        return refs

    def _location(self, ctx: StageContext, name: str, refs: list[str]) -> str:
        s = ctx.store
        found = find_by_name(active_locations(s), name)
        if found is not None:
            return found.id
        lid = s.new_codex_id("location", name)
        s.save_codex(
            Location(
                id=lid,
                name=name.strip(),
                codex_status=CodexStatus.CANDIDATE,
                source_refs=[f"cards:{ctx.job.job_id}"],
            ),
            actor=Actor.SYSTEM,
            job_id=ctx.job.job_id,
        )
        refs.append(f"codex:{lid}")
        return lid

    def _place_on_timeline(
        self, ctx: StageContext, chapter: str, scenes: list[tuple[str, str, str]], dropped: list[str]
    ) -> str:
        """Re-place this chapter's scenes; other chapters' entries keep their positions.

        - flashback/flashforward with `happens_after`: directly after that scene
        - linear: directly after the nearest earlier *linear* scene in narrative order (a flashback
          must not become the anchor for what follows it); at the start if there is none
        """
        s = ctx.store
        tl = s.try_read("timeline") or Timeline()
        mine = {sid for sid, _, _ in scenes} | set(dropped)
        entries = [e for e in tl.entries if e.scene_id not in mine and e.concurrent_with not in mine]
        narrative = s.scenes_in_order()

        def is_linear(sid: str) -> bool:
            card = s.try_read("card", sid)
            return card is None or card.temporal_mode not in NON_LINEAR

        for sid, mode, after in scenes:
            if mode in NON_LINEAR and after != "none":
                anchor: str | None = after
            else:
                placed = {e.scene_id for e in entries}
                earlier = [x for x in narrative[: narrative.index(sid)] if x in placed and is_linear(x)]
                anchor = earlier[-1] if earlier else None
            idx = next((i + 1 for i, e in enumerate(entries) if e.scene_id == anchor), 0)
            entries.insert(idx, TimelineEntry(scene_id=sid, label=s.read("card", sid).time.label))
        r = s.save(
            "timeline",
            Timeline(entries=entries),
            extra=ctx.provenance(self.name, chapter, "timeline"),
            job_id=ctx.job.job_id,
        )
        return f"timeline@v{r.version}"

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        step = ctx.step_key(self.name, target)
        m = ctx.store.meta("timeline")
        if (
            m.active
            and m.get(m.active).extra.get("step") == step
            and m.get(m.active).extra.get("job_id") == ctx.job.job_id
        ):
            return [f"timeline@v{m.active}"]  # the timeline is written last, so the whole step completed
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        out = []
        cands = [c for c in ctx.store.list_codex("location") if c.codex_status == CodexStatus.CANDIDATE]
        if cands:
            out.append(
                Problem(
                    "warning", f"{len(cands)} new locations await review: {', '.join(c.name for c in cands)}"
                )
            )
        tl = ctx.store.try_read("timeline")
        on_tl = {e.scene_id for e in tl.entries} if tl else set()
        missing = [sid for sid in ctx.store.scene_ids() if sid not in on_tl]
        if missing:
            out.append(Problem("warning", f"scenes not on the timeline: {missing}"))
        return out

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return [("card", sid) for sid in ctx.store.scene_ids()]
