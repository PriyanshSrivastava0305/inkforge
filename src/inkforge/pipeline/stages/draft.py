"""Stage E — Draft scenes (docs/05), one scene per step.

Prose, not JSON: this stage overrides `run`. Per scene:
  context builder (budgeted, manifest) → generate → while cut off or mid-sentence: continue with an
  anchor and strip the overlap → strip meta lines → deterministic checks → save a new draft version.

Checks never block the draft (approval happens at export); they become events, and name typos become
evidence-linked Findings (the Phase 4 continuity rule).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from inkforge.context.builder import ContextTooSmall, DraftContextBuilder, card_block
from inkforge.domain.codex import active_characters
from inkforge.domain.common import Actor, Staleness
from inkforge.domain.models import Character, Evidence, Finding, SceneVersionMeta
from inkforge.memory.digests import Digester
from inkforge.models.recorder import RunRecorder
from inkforge.models.router import GenerationCancelled
from inkforge.pipeline.stage import Problem, Stage, StageContext, StageError, StepResult
from inkforge.pipeline.text import (
    clean_scene_text,
    ends_mid_sentence,
    join_without_overlap,
    meta_leaks,
    near_copies,
    strip_prompt_echo,
    word_count,
)
from inkforge.prompts.registry import RenderedPrompt
from inkforge.quality.names import missing_participants, name_inconsistencies, unknown_names
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ArtifactKind

MAX_CONTINUATIONS = 3
LENGTH_TOLERANCE = 0.35  # ±35% of the card's target before warning
HARD_CAP = 1.6  # stop continuing beyond 160% of target
EXTEND_BELOW = 0.6  # a scene that ends under 60% of target gets one extension
ANCHOR_CHARS = 1300  # ≈300 tokens of prose


def memory_on(ctx: StageContext) -> bool:
    """Long-book memory is on unless switched off per run (`--no-memory`) or per project."""
    v = ctx.params.get("memory")
    return bool(ctx.store.project.settings.get("memory", True) if v is None else v)


class DraftStage(Stage):
    name = "drafts"
    profile = "prose"
    gate = None  # drafts are approved at export (docs/05 § Stage H), never by --yes
    requires_gates = ("scene_cards",)

    def targets(self, ctx: StageContext) -> list[str | None]:
        s = ctx.store
        if sid := ctx.params.get("scene"):
            return [sid]
        scenes = s.scenes_in_order()
        if ch := ctx.params.get("chapter"):
            scenes = [x for x in scenes if x.startswith(f"{ch}-")]
        if ctx.params.get("redraft"):
            return list(scenes)
        # default: scenes without a draft, or whose draft no longer matches its card/voices
        return [
            x
            for x in scenes
            if s.meta("draft", x).active is None or s.staleness("draft", x) == Staleness.STALE
        ]

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if target is None or ctx.store.try_read("card", target) is None:
            return [Problem("error", "no scene card", target)]
        return []

    # Stage's structured hooks are unused: drafting is prose.
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
        s, router = ctx.store, ctx.router
        step_dir = ctx.run_dir / ctx.step_key(self.name, target).replace(":", "_")
        builder = DraftContextBuilder(s, router, ctx.prompts, profile=self.profile)
        try:
            built = builder.build(target, job_id=ctx.job.job_id, memory=memory_on(ctx))
        except ContextTooSmall as e:
            raise StageError(str(e)) from e
        card = s.read("card", target)
        target_words = card.target_length or 1500
        rec = RunRecorder(step_dir)
        rec.prompt(
            built.prompt.messages,
            prompt_ref=built.prompt.ref,
            profile=self.profile,
            model=router.profile(self.profile).model,
        )
        manifest_path = step_dir / "manifest.json"
        atomic_write_text(manifest_path, built.manifest.to_json())

        names = {c.id: c.name for c in active_characters(s)}
        loc = s.get_codex(card.location_id) if card.location_id else None
        cb = card_block(card, names, loc if loc is not None and loc.id.startswith("loc-") else None)  # type: ignore[arg-type]
        messages, text, calls, finish = built.prompt.messages, "", 0, "stop"
        extended = False
        echoed: list[str] = []
        prompt_text = "\n".join(m.content for m in built.prompt.messages)
        try:
            for i in range(1 + MAX_CONTINUATIONS):
                g = await router.generate(
                    messages, self.profile, max_tokens=built.max_tokens, cancel=ctx.cancel
                )
                calls += 1
                rec.attempt(i + 1, g)
                if g.finish_reason == "cancelled":
                    raise GenerationCancelled(g)
                piece, echoed_now = strip_prompt_echo(g.text, prompt_text)
                echoed += echoed_now
                text = piece if i == 0 else join_without_overlap(text, piece)
                finish = g.finish_reason
                cut_off = g.finish_reason in ("length", "repetition") or ends_mid_sentence(text)
                # The 8B ends long scenes early (635-865 words for a 1,200 target, 2026-10-12): extend once.
                too_short = not cut_off and not extended and word_count(text) < target_words * EXTEND_BELOW
                if too_short:
                    extended = True
                elif not cut_off or word_count(text) >= target_words * HARD_CAP:
                    break
                cont = ctx.prompts.render(
                    "draft.continue",
                    2,
                    card_block=cb,
                    anchor=text[-ANCHOR_CHARS:],
                    mid_sentence=ends_mid_sentence(text),
                    remaining_words=max(80, target_words - word_count(text)),
                    exit_condition=card.exit_condition or "the scene ends",
                )
                messages = cont.messages
        except BaseException as e:
            rec.finish("failed", error=f"{type(e).__name__}: {e}")
            raise
        cleaned = clean_scene_text(text)
        body = cleaned.text
        if not body.strip():
            rec.finish("failed", error="empty draft after cleanup")
            raise StageError(f"{target}: the model returned no usable prose")

        # ---- deterministic checks
        checks: list[dict[str, Any]] = []
        words = word_count(body)
        if abs(words - target_words) > target_words * LENGTH_TOLERANCE:
            checks.append({"check": "length", "message": f"{words} words vs target {target_words}"})
        spec = s.read("spec")
        # a first-person narrator is rarely named in their own scene
        skip = {card.pov_character_id} if spec.pov == "first" else set()
        present = [c for c in active_characters(s) if c.id in card.participants and c.id not in skip]
        for c in missing_participants(body, present):
            checks.append(
                {"check": "participants", "message": f"{c.name} is in the card but never named in the scene"}
            )
        if cleaned.removed:
            checks.append(
                {
                    "check": "meta_stripped",
                    "message": f"removed {len(cleaned.removed)} meta line(s)",
                    "lines": [ln for _, ln in cleaned.removed],
                }
            )
        for pos, snippet in meta_leaks(body):
            checks.append({"check": "meta_leak", "message": f"model/meta text at {pos}: {snippet!r}"})
        new_names = unknown_names(body, s.list_codex())
        if new_names:
            checks.append(
                {
                    "check": "unknown_names",
                    "message": "possible new characters/places: " + ", ".join(new_names),
                }
            )
        spec = s.read("spec")
        if spec.pov == "first" and " I " not in f" {body} ":
            checks.append({"check": "pov", "message": "spec is first person but the scene never uses 'I'"})
        if echoed:
            n = sum(len(x.split()) for x in echoed)
            checks.append(
                {
                    "check": "prompt_echo",
                    "message": f"removed {len(echoed)} line(s) / {n} words copied from the prompt",
                }
            )
        shown = [
            built.sections[k].text
            for k in ("recall", "recap")
            if k in built.sections and built.sections[k].text
        ]
        for opening, sim in near_copies(body, shown):
            checks.append(
                {
                    "check": "near_copy",
                    "message": f"paragraph closely repeats earlier text ({sim:.0%}): {opening!r}",
                }
            )
        if extended:
            checks.append(
                {"check": "extended", "message": "first pass ended under 60% of target; extended once"}
            )
        if finish in ("length", "repetition") or ends_mid_sentence(body):
            checks.append(
                {"check": "truncated", "message": "scene still ends mid-sentence after continuations"}
            )

        meta = SceneVersionMeta(
            scene_id=target,
            version=1,
            word_count=words,
            model_profile=self.profile,
            model_name=router.profile(self.profile).model,
            prompt_ref=built.prompt.ref,
            context_manifest_path=manifest_path.relative_to(s.dir).as_posix(),
            job_id=ctx.job.job_id,
        )
        extra = {
            **ctx.provenance(self.name, target, built.prompt.ref),
            "calls": calls,
            "checks": checks,
            "context_tokens": built.manifest.total_tokens,
            "unknown_names": new_names,
        }
        vrec = s.save(
            "draft",
            body,
            target=target,
            actor=Actor.SYSTEM,
            extra={**meta.model_dump(mode="json"), **extra},
            job_id=ctx.job.job_id,
        )
        findings = self._name_findings(ctx, target, vrec.version, body)
        digests: list[str] = []
        if memory_on(ctx):  # DIGEST tier: this scene now, its chapter/act once complete (D34)
            use_model = bool(ctx.params.get("digest_model", s.project.settings.get("digest_model", True)))
            built_digests = await Digester(s, router, ctx.prompts).after_scene(
                target, use_model=use_model, cancel=ctx.cancel
            )
            digests = [f"digest:{d.kind}:{d.target}({d.method})" for d in built_digests]
        rec.finish(
            "succeeded", output_refs=[f"draft:{target}@v{vrec.version}"], checks=checks, findings=findings
        )
        for c in checks:
            ctx.emit("draft.check", {"scene": target, **c})
        return StepResult(
            [f"draft:{target}@v{vrec.version}", *(f"finding:{f}" for f in findings), *digests], [], calls
        )

    def _name_findings(self, ctx: StageContext, scene: str, version: int, text: str) -> list[str]:
        """Name/alias typos → Findings with verbatim evidence. Replaces this rule's open findings for the scene."""
        s = ctx.store
        for f in s.findings.all():
            if f.scene_id == scene and f.detector == "rule:name_consistency" and f.status == "open":
                s.findings.remove(f.id)
        ids = []
        entries = [e for e in s.list_codex() if isinstance(e, Character) or e.id.startswith("loc-")]
        for hit in name_inconsistencies(text, entries):
            fid = s.mint("finding")
            s.upsert(
                "findings",
                Finding(
                    id=fid,
                    scene_id=scene,
                    category="continuity",
                    severity="med",
                    claim=f"“{hit.found}” looks like a misspelling of “{hit.expected}”",
                    evidence=[
                        Evidence(
                            scene_id=scene,
                            version=version,
                            span=hit.span,
                            quote=text[hit.span[0] : hit.span[1]],
                        )
                    ],
                    affected_entities=[hit.entity_id],
                    detector="rule:name_consistency",
                    confidence=0.7,
                ),
            )
            ids.append(fid)
        return ids

    def adopt(self, ctx: StageContext, target: str | None) -> list[str] | None:
        if target is None:
            return None
        m = ctx.store.meta("draft", target)
        if m.active and m.get(m.active).extra.get("job_id") == ctx.job.job_id:
            return [f"draft:{target}@v{m.active}"]
        return None

    def finalize(self, ctx: StageContext) -> list[Problem]:
        s = ctx.store
        out = []
        undrafted = [x for x in s.scenes_in_order() if s.meta("draft", x).active is None]
        if undrafted:
            out.append(Problem("warning", f"{len(undrafted)} scenes have no draft yet"))
        open_names = [
            f for f in s.findings.all() if f.detector == "rule:name_consistency" and f.status == "open"
        ]
        if open_names:
            out.append(
                Problem("warning", f"{len(open_names)} possible name misspellings (inkforge findings)")
            )
        return out

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return []


def manifest_summary(path_text: str) -> str:
    m = json.loads(path_text)
    parts = [
        f"{s['name']}:{s['tokens']}{'' if s['disposition'] == 'included' else '(' + s['disposition'] + ')'}"
        for s in m["sections"]
    ]
    return f"{m['total_tokens']}/{m['budget_input']} tokens — " + ", ".join(parts)
