"""Stage G — Revise (docs/07 § Stage G), one scene per step.

For each specialist pass in order (continuity → voice → craft → slop), the scene's **accepted** findings
of that pass are grouped into paragraph windows. For each window:

    instruction = revise.span(passage, issues, card, voices, facts[, why the last try failed])
    candidate   = scene with only that window replaced                    (splice)
    reject if   the word diff touches anything outside the window        (span guard)
                the rewrite's shape is wrong (empty, far longer/shorter, new paragraphs)
                a target finding is still detected by the detector that raised it
                a new high-severity finding appears
                the craft rubric drops more than ε below the scene's score
    keep        → new draft version (never approved automatically), findings → fixed, RevisionProposal
    else retry  ≤ max_attempts with the reason; then → "needs author", proposal auto-discarded

Findings that aren't local (length, a missing participant, an unpaid promise) and findings whose
evidence has left the text are marked "needs author" instead of being guessed at. Each kept edit is its
own version with its own diff, so every change is attributable and reversible (`inkforge diff`).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from inkforge.domain.common import Actor
from inkforge.domain.models import Character, Finding, RevisionProposal
from inkforge.memory.effective import effective_text
from inkforge.models.recorder import RunRecorder
from inkforge.models.router import GenerationCancelled
from inkforge.pipeline.stage import Problem, Stage, StageContext, StageError, StepResult
from inkforge.pipeline.text import clean_scene_text, strip_prompt_echo, word_diff
from inkforge.prompts.registry import RenderedPrompt
from inkforge.quality import deterministic, report
from inkforge.quality.check import PREFIX, ContinuityChecker
from inkforge.quality.critics import Critics
from inkforge.quality.revision import (
    NOT_LOCAL,
    PASSES,
    Window,
    clean_passage,
    evidence_in,
    outside,
    pass_of,
    shape_problem,
    splice,
    windows,
)
from inkforge.quality.rules import World
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ArtifactKind, ProjectStore

MAX_ATTEMPTS = 2
EPSILON = 1.0  # rubric total may drop this much (noise) and still count as "not worse"


def chosen_passes(ctx: StageContext) -> list[str]:
    passes = ctx.params.get("passes") or list(PASSES)
    unknown = set(passes) - set(PASSES)
    if unknown:
        raise StageError(f"unknown passes {sorted(unknown)}; have {list(PASSES)}")
    return [p for p in PASSES if p in passes]


def candidates(store: ProjectStore, scene: str, passes: list[str], accept: list[str]) -> list[Finding]:
    """Findings Stage G may work on: accepted ones, plus open ones of the given severities when the
    caller (polish) asked for them to be accepted."""
    out = []
    for f in store.findings.all():
        if f.scene_id != scene or f.is_hypothesis or pass_of(f) not in passes:
            continue
        if (f.status == "accepted" and not (f.resolution_note or "").startswith("needs author")) or (
            f.status == "open" and f.severity in accept
        ):
            out.append(f)
    return out


class ReviseStage(Stage):
    name = "revise"
    profile = "prose"
    gate = None

    def targets(self, ctx: StageContext) -> list[str | None]:
        s = ctx.store
        if sid := ctx.params.get("scene"):
            return [sid]
        scenes = [x for x in s.scenes_in_order() if s.meta("draft", x).active is not None]
        if ch := ctx.params.get("chapter"):
            scenes = [x for x in scenes if x.startswith(f"{ch}-")]
        passes, accept = chosen_passes(ctx), list(ctx.params.get("accept") or [])
        return [x for x in scenes if candidates(s, x, passes, accept)]

    def check_inputs(self, ctx: StageContext, target: str | None) -> list[Problem]:
        if target is None or effective_text(ctx.store, target) is None:
            return [Problem("error", "no draft to revise", target)]
        return []

    def build_prompt(self, ctx: StageContext, target: str | None) -> RenderedPrompt:  # pragma: no cover
        raise NotImplementedError

    def schema(self, ctx: StageContext, target: str | None) -> type[BaseModel]:  # pragma: no cover
        raise NotImplementedError

    def persist(
        self, ctx: StageContext, target: str | None, value: Any, prompt_ref: str
    ) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------ the loop
    async def run(self, ctx: StageContext, target: str | None) -> StepResult:
        problems = [p for p in self.check_inputs(ctx, target) if p.level == "error"]
        if problems or target is None:
            raise StageError("; ".join(str(p) for p in problems) or "no target")
        s = ctx.store
        self._recover(ctx, target)
        passes, accept = chosen_passes(ctx), list(ctx.params.get("accept") or [])
        max_attempts = int(ctx.params.get("max_attempts", MAX_ATTEMPTS))
        use_rubric = "craft" in (ctx.params.get("checks") or ["craft"]) and ctx.params.get("rubric", True)
        refs: list[str] = []
        warnings: list[Problem] = []
        calls = 0
        rec = RunRecorder(ctx.run_dir / ctx.step_key(self.name, target).replace(":", "_"))
        base_score: float | None = None

        for pass_name in passes:
            todo = candidates(s, target, [pass_name], accept)
            if not todo:
                continue
            for f in todo:
                if f.status == "open":  # polish: the author asked for these to be fixed
                    s.upsert("findings", f.model_copy(update={"status": "accepted", "resolution_note": "accepted by polish"}),
                             actor=Actor.SYSTEM)  # fmt: skip
            todo = [s.findings.get(f.id) or f for f in todo]
            for f in [f for f in todo if f.detector in NOT_LOCAL]:
                self._needs_author(s, f, "not a local edit — revise by hand or redraft the scene")
            todo = [f for f in todo if f.detector not in NOT_LOCAL]
            got = effective_text(s, target)
            assert got is not None
            _, text = got
            groups, lost = windows(text, todo)
            for f in lost:
                self._needs_author(s, f, "the quoted words are no longer in the scene")
            ctx.emit("revise.pass", {"scene": target, "pass": pass_name, "windows": len(groups),
                                     "findings": [f.id for f in todo]})  # fmt: skip
            for w in groups:
                if use_rubric and base_score is None:
                    base_score, n = await self._score(ctx, target, text)
                    calls += n
                outcome, n = await self._revise_window(
                    ctx, target, pass_name, w, max_attempts, base_score, rec
                )
                calls += n
                refs += outcome
                got = effective_text(s, target)
                assert got is not None
                _, new_text = got
                if new_text != text:  # windows after this one moved: re-place the rest
                    text = new_text
                    if use_rubric:
                        base_score = None
                    remaining = [
                        s.findings.get(f.id) for g in groups[groups.index(w) + 1 :] for f in g.findings
                    ]
                    regroup, lost = windows(text, [f for f in remaining if f and f.status == "accepted"])
                    for f in lost:
                        self._needs_author(s, f, "the quoted words are no longer in the scene")
                    groups[groups.index(w) + 1 :] = regroup
        open_needing = [f for f in s.findings.all() if f.scene_id == target and (f.resolution_note or "").startswith("needs author")
                        and f.status == "accepted"]  # fmt: skip
        if open_needing:
            warnings.append(Problem("warning", f"{len(open_needing)} finding(s) need the author", target))
        rec.finish("succeeded", output_refs=refs)
        return StepResult(refs, warnings, calls)

    async def _revise_window(
        self,
        ctx: StageContext,
        scene: str,
        pass_name: str,
        w: Window,
        max_attempts: int,
        base_score: float | None,
        rec: RunRecorder,
    ) -> tuple[list[str], int]:
        s = ctx.store
        got = effective_text(s, scene)
        assert got is not None
        eff, base = got
        passage = base[w.start : w.end]
        rev_id = s.mint("revision")
        failure: str | None = None
        calls = 0
        last_candidate = base
        names = [n for e in s.list_codex() for n in {e.name, e.name.split()[0], *e.aliases} if len(n) > 2]
        for attempt in range(1, max_attempts + 1):
            rp = self._prompt(ctx, scene, w, passage, failure)
            rec.prompt(
                rp.messages,
                prompt_ref=rp.ref,
                profile=self.profile,
                model=ctx.router.profile(self.profile).model,
            )
            g = await ctx.router.generate(
                rp.messages, self.profile, max_tokens=int(len(passage.split()) * 2.2) + 120, cancel=ctx.cancel
            )
            calls += 1
            rec.attempt(attempt, g)
            if g.finish_reason == "cancelled":
                raise GenerationCancelled(g)
            raw, _ = strip_prompt_echo(clean_passage(g.text), "\n".join(m.content for m in rp.messages))
            new = clean_scene_text(raw).text.strip()
            new = _keep_edges(passage, new)
            failure = shape_problem(passage, new, names)
            candidate = splice(base, w, new) if failure is None else base
            if failure is None:
                stray = outside(base, candidate, w.start, w.end)
                if stray:  # cannot happen through splice(); kept as the guarantee the exit criterion tests
                    failure = f"the edit changed text outside the passage at {stray[:3]}"
            if failure is None:
                failure, n = await self._judge(ctx, scene, w, base, candidate, base_score)
                calls += n
            last_candidate = candidate
            ctx.emit("revise.attempt", {"scene": scene, "revision": rev_id, "attempt": attempt, "ok": failure is None,
                                        "reason": failure})  # fmt: skip
            if failure is None:
                return self._keep(
                    ctx, scene, pass_name, w, rev_id, eff.version, base, candidate, rp.ref
                ), calls
        # gave up
        self._save_diff(ctx, scene, rev_id, base, last_candidate)
        s.upsert("revisions", RevisionProposal(
            id=rev_id, scene_id=scene, base_version=eff.version, finding_ids=[f.id for f in w.findings],
            instruction=f"{pass_name}: " + "; ".join(f.claim for f in w.findings),
            diff_path=f"runs/{ctx.job.job_id}/revise/{rev_id}.diff.json", decision="auto-discarded",
        ))  # fmt: skip
        for f in w.findings:
            self._needs_author(s, f, f"revision failed after {max_attempts} attempt(s): {failure}")
        return [f"revision:{rev_id}(discarded)"], calls

    async def _judge(
        self, ctx: StageContext, scene: str, w: Window, base: str, candidate: str, base_score: float | None
    ) -> tuple[str | None, int]:
        """None if the candidate is better, else the reason it isn't (fed back to the next attempt)."""
        s = ctx.store
        calls = 0
        got = effective_text(s, scene)
        assert got is not None
        version = got[0].version
        new_end = w.end + (len(candidate) - len(base))
        before = {(f.detector, f.claim) for f in deterministic.check_text(s, scene, version, base)}
        after = deterministic.check_text(s, scene, version, candidate)
        for f in w.findings:
            if f.detector.startswith("rule:") and not f.detector.startswith((PREFIX, "rule:name")):
                if any(a.detector == f.detector and a.evidence[0].span[0] < new_end and a.evidence[0].span[1] > w.start
                       for a in after):  # fmt: skip
                    return f"still detected: {f.claim}", calls
            elif evidence_in(candidate, f) is not None and not f.detector.startswith(PREFIX):
                return f"the passage the issue points at is unchanged: “{w.quotes[0][:80]}”", calls
            elif f.detector.startswith(PREFIX):
                # the words that contradict canon must change; re-extraction alone could just miss them
                obs_value = next((ev.quote for ev in f.evidence if ev.scene_id == scene), "")
                if obs_value and obs_value in candidate:
                    return f"the contradicting words are unchanged: “{obs_value[:80]}”", calls
        fresh_high = [a for a in after if a.severity == "high" and (a.detector, a.claim) not in before]
        if fresh_high:
            return f"the edit introduced a new problem: {fresh_high[0].claim}", calls
        if any(f.detector.startswith(PREFIX) for f in w.findings):
            checker = ContinuityChecker(s, ctx.router, ctx.prompts)
            extraction, hits, verdicts = await checker.hits(scene, text=candidate, cancel=ctx.cancel)
            calls += 1 + len(verdicts)
            if extraction.error:
                return f"could not re-check continuity: {extraction.error}", calls
            claims = {h.claim for h in hits if not h.is_hypothesis}
            for f in w.findings:
                if f.detector.startswith(PREFIX) and f.claim in claims:
                    return f"still contradicts canon: {f.claim}", calls
            open_claims = {
                f.claim for f in s.findings.all() if f.scene_id == scene and f.detector.startswith(PREFIX)
            }
            new = [
                h for h in hits if h.severity == "high" and not h.is_hypothesis and h.claim not in open_claims
            ]
            if new:
                return f"the edit introduced a contradiction: {new[0].claim}", calls
        if base_score is not None:
            score = await Critics(s, ctx.router, ctx.prompts).craft(
                scene, candidate, version, cancel=ctx.cancel
            )
            calls += 1
            if score.error is None and score.total < base_score - EPSILON:
                return f"the scene reads worse (craft {score.total:.0f} vs {base_score:.0f})", calls
        return None, calls

    async def _score(self, ctx: StageContext, scene: str, text: str) -> tuple[float | None, int]:
        r = report.load(ctx.store, scene)
        got = effective_text(ctx.store, scene)
        if r is not None and got is not None and r.sha256 == got[0].sha256 and r.rubric_total is not None:
            return r.rubric_total, 0
        res = await Critics(ctx.store, ctx.router, ctx.prompts).craft(scene, text, got[0].version if got else 1,
                                                                      cancel=ctx.cancel)  # fmt: skip
        return (None if res.error else res.total), 1

    def _prompt(
        self, ctx: StageContext, scene: str, w: Window, passage: str, failure: str | None
    ) -> RenderedPrompt:
        s = ctx.store
        card = s.try_read("card", scene)
        names = [
            c
            for c in s.list_codex()
            if isinstance(c, Character) and (card is None or c.id in card.participants)
        ]
        voices = [f"{c.name}: {c.voice_card.diction}" + (f"; {', '.join(c.voice_card.verbal_tics)}" if c.voice_card.verbal_tics else "")
                  for c in names if c.voice_card.diction]  # fmt: skip
        world = World(s)
        facts = []
        for f in w.findings:
            other = next((x for x in world.canon if x.id == f.conflicting_fact_id), None)
            if other:
                facts.append(
                    f"{world.name(other.subject_id)}: {other.predicate.replace('_', ' ')} = {world.name(str(other.value))}"
                )
        issues = [{"claim": f.claim, "quote": q} for f, q in zip(w.findings, w.quotes, strict=False)]
        return ctx.prompts.render(
            "revise.span", scene_id=scene, card=(card.concrete_event or "") if card else "", voices=voices,
            facts=facts, passage=passage, issues=issues, failure=failure,
        )  # fmt: skip

    def _keep(self, ctx: StageContext, scene: str, pass_name: str, w: Window, rev_id: str, base_version: int,
              base: str, candidate: str, prompt_ref: str) -> list[str]:  # fmt: skip
        s = ctx.store
        meta = s.meta("draft", scene).get(base_version).extra
        extra = {
            **{k: v for k, v in meta.items() if k in ("scene_id", "model_profile", "context_manifest_path")},
            **ctx.provenance(self.name, scene, prompt_ref),
            "revision_id": rev_id,
            "finding_ids": [f.id for f in w.findings],
            "base_version": base_version,
            "word_count": len(candidate.split()),
        }
        vrec = s.save(
            "draft", candidate, target=scene, actor=Actor.SYSTEM, extra=extra, job_id=ctx.job.job_id
        )
        self._save_diff(ctx, scene, rev_id, base, candidate)
        s.upsert("revisions", RevisionProposal(
            id=rev_id, scene_id=scene, base_version=base_version, finding_ids=[f.id for f in w.findings],
            instruction=f"{pass_name}: " + "; ".join(f.claim for f in w.findings),
            diff_path=f"runs/{ctx.job.job_id}/revise/{rev_id}.diff.json", candidate_version=vrec.version,
            decision="accepted",
        ))  # fmt: skip
        for f in w.findings:
            s.upsert("findings", f.model_copy(update={"status": "fixed",
                                                     "resolution_note": f"revised in v{vrec.version} ({rev_id})"}))  # fmt: skip
        return [f"draft:{scene}@v{vrec.version}", f"revision:{rev_id}"]

    def _save_diff(self, ctx: StageContext, scene: str, rev_id: str, base: str, candidate: str) -> None:
        ops = [
            {"op": o.op, "text": o.text}
            for o in word_diff(base, candidate)
            if o.op != "equal" or len(o.text) < 200
        ]
        atomic_write_text(ctx.store.path(f"runs/{ctx.job.job_id}/revise/{rev_id}.diff.json"),
                          json.dumps({"scene": scene, "revision": rev_id, "ops": ops}, ensure_ascii=False, indent=1))  # fmt: skip

    @staticmethod
    def _needs_author(store: ProjectStore, f: Finding, why: str) -> None:
        current = store.findings.get(f.id) or f
        if current.status in ("fixed", "dismissed", "intentional"):
            return
        store.upsert(
            "findings",
            current.model_copy(update={"status": "accepted", "resolution_note": f"needs author: {why}"}),
        )

    def _recover(self, ctx: StageContext, scene: str) -> None:
        """Crash after a kept version was saved but before its findings were marked: finish the bookkeeping."""
        s = ctx.store
        m = s.meta("draft", scene)
        for v in m.versions:
            extra = v.extra
            if extra.get("job_id") != ctx.job.job_id or not extra.get("revision_id"):
                continue
            for fid in extra.get("finding_ids", []):
                f = s.findings.get(fid)
                if f is not None and f.status == "accepted":
                    s.upsert("findings", f.model_copy(update={"status": "fixed",
                                                             "resolution_note": f"revised in v{v.version} ({extra['revision_id']})"}))  # fmt: skip

    def finalize(self, ctx: StageContext) -> list[Problem]:
        kept = [r for r in ctx.store.revisions.all() if r.decision == "accepted"]
        return (
            [
                Problem(
                    "warning",
                    f"{len(kept)} revision(s) kept as new draft versions — review with `inkforge diff`",
                )
            ]
            if kept
            else []
        )

    def gate_artifacts(self, ctx: StageContext) -> list[tuple[ArtifactKind, str | None]]:
        return []


def _keep_edges(old: str, new: str) -> str:
    """Keep the passage's own leading/trailing whitespace so the splice doesn't merge paragraphs."""
    lead = old[: len(old) - len(old.lstrip())]
    trail = old[len(old.rstrip()) :]
    return lead + new.strip() + trail
