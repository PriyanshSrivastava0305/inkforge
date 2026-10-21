"""Model critics (docs/07 § 4–5): adjudication of ambiguous conflicts, the craft rubric, voice critics.

All of them are advisory and all of them are checked by code:
- every quote a critic returns must be found in the scene (`locate_quote`), or the finding is dropped;
- a "problem" that merely repeats its quote is dropped (seen in Phase 0: claim == quote);
- a failed or unparseable model call yields no findings — never a guess.
The critics never change text or canon; Stage G acts only on findings the author (or polish) accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from inkforge.domain.models import Character, Evidence, Finding
from inkforge.models.router import GenerationCancelled, ModelRouter
from inkforge.models.types import CancelToken, ModelError
from inkforge.prompts.registry import PromptRegistry
from inkforge.quality.extract import locate_quote
from inkforge.storage.workspace import ProjectStore

DIMENSIONS = ("goal", "tension", "clarity", "pacing", "sensory", "dialogue", "show_tell", "ending")
DIMENSION_CATEGORY = {
    "goal": "motivation",
    "tension": "pacing",
    "clarity": "clarity",
    "pacing": "pacing",
    "sensory": "prose",
    "dialogue": "prose",
    "show_tell": "prose",
    "ending": "pacing",
}
FREE_TEXT = {
    "eye_color",
    "hair",
    "height",
}  # values a string comparison can't settle ("sea-coloured" vs blue)
MIN_PROBLEM = 10
SPEECH = r"\b(?:said|says|asked|asks|replied|answered|whispered|muttered|called|shouted|told)\b"


# ------------------------------------------------------------------ schemas
class Adjudication(BaseModel):
    conflict: Literal["yes", "no", "unclear"]
    explanation: str = Field(min_length=10, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)


class CraftNote(BaseModel):
    # lenient on purpose: one weak note must not cost the whole rubric; weak notes are dropped in code
    quote: str = Field(max_length=400, description="8–30 words copied exactly from the scene")
    dimension: Literal["goal", "tension", "clarity", "pacing", "sensory", "dialogue", "show_tell", "ending"]
    problem: str = Field(max_length=400)
    suggestion: str = Field(default="", max_length=400)


class CraftScores(BaseModel):
    goal: int = Field(ge=1, le=5)
    tension: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    pacing: int = Field(ge=1, le=5)
    sensory: int = Field(ge=1, le=5)
    dialogue: int = Field(ge=1, le=5)
    show_tell: int = Field(ge=1, le=5)
    ending: int = Field(ge=1, le=5)


class CraftReview(BaseModel):
    scores: CraftScores
    notes: list[CraftNote] = Field(default_factory=list, max_length=5)


class VoiceNote(BaseModel):
    quote: str = Field(max_length=400, description="one of the character's lines, copied exactly")
    problem: str = Field(max_length=400)
    rewrite: str = Field(default="", max_length=300)


class VoiceReview(BaseModel):
    notes: list[VoiceNote] = Field(default_factory=list, max_length=4)


# ------------------------------------------------------------------ helpers
@dataclass
class CraftResult:
    scores: dict[str, float] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> float:
        return sum(self.scores.values())


def restates(problem: str, quote: str) -> bool:
    """Does the 'problem' just repeat the quoted words instead of naming what's wrong?"""
    a, b = re.sub(r"\W+", " ", problem.lower()).strip(), re.sub(r"\W+", " ", quote.lower()).strip()
    return a in b or SequenceMatcher(None, a, b).ratio() > 0.8


def dialogue_lines(
    text: str, character: Character, cast: list[Character]
) -> list[tuple[tuple[int, int], str]]:
    """Quoted speech attributed to `character`: in a paragraph with a speech verb that names them and
    no other cast member. Conservative on purpose — a misattributed line makes a wrong voice finding."""
    names = {n.lower() for n in [character.name, character.name.split()[0], *character.aliases]}
    others = {
        n.lower() for c in cast if c.id != character.id for n in [c.name, c.name.split()[0], *c.aliases]
    }
    out = []
    pos = 0
    for para in text.split("\n\n"):
        start = text.find(para, pos)
        pos = start + len(para)
        low = para.lower()
        outside = re.sub(r"[“\"][^”\"]*[”\"]", " ", low)  # tags live outside the quotes
        if not re.search(SPEECH, outside):
            continue
        if not any(re.search(rf"\b{re.escape(n)}\b", outside) for n in names):
            continue
        if any(re.search(rf"\b{re.escape(n)}\b", outside) for n in others):
            continue
        for m in re.finditer(r"[“\"]([^”\"]{2,})[”\"]", para):
            out.append(((start + m.start(1), start + m.end(1)), m.group(1)))
    return out


# ------------------------------------------------------------------ critics
class Critics:
    def __init__(self, store: ProjectStore, router: ModelRouter, prompts: PromptRegistry | None = None):
        self.store, self.router = store, router
        self.prompts = prompts or PromptRegistry(project_dir=store.dir / "prompts")

    async def adjudicate(
        self, claim: str, here: str, there: str, *, cancel: CancelToken | None = None
    ) -> Adjudication | None:
        """Is this really a contradiction? None if the model couldn't answer (the rule's finding stands)."""
        rp = self.prompts.render("critique.adjudicate", claim=claim, here=here, there=there)
        try:
            res = await self.router.generate_structured(
                rp.messages, Adjudication, rp.profile, max_tokens=220, temperature=0.0, cancel=cancel
            )
        except GenerationCancelled:
            raise
        except ModelError:
            return None
        return res.value

    async def craft(
        self, scene_id: str, text: str, version: int, *, cancel: CancelToken | None = None
    ) -> CraftResult:
        card = self.store.try_read("card", scene_id)
        rp = self.prompts.render(
            "critique.rubric",
            text=text,
            goal=(card.change or card.concrete_event or "") if card else "",
            exit_condition=(card.exit_condition or "") if card else "",
        )
        out = CraftResult()
        try:
            res = await self.router.generate_structured(
                rp.messages, CraftReview, rp.profile, max_tokens=900, temperature=0.2, cancel=cancel
            )
        except GenerationCancelled:
            raise
        except ModelError as e:
            out.error = str(e)
            return out
        out.scores = {k: float(v) for k, v in res.value.scores.model_dump().items()}
        for n in res.value.notes:
            found = locate_quote(text, n.quote)
            if found is None:
                out.dropped.append(f"quote not in scene: {n.quote[:60]!r}")
                continue
            if len(n.problem.strip()) < MIN_PROBLEM:
                out.dropped.append(f"no real problem stated: {n.quote[:60]!r}")
                continue
            if restates(n.problem, n.quote):
                out.dropped.append(f"problem only repeats the quote: {n.quote[:60]!r}")
                continue
            span = (found[0], found[1])
            out.findings.append(
                Finding(
                    id="find-0000",
                    scene_id=scene_id,
                    category=DIMENSION_CATEGORY[n.dimension],  # type: ignore[arg-type]
                    severity="low",
                    claim=f"{n.dimension.replace('_', ' ')}: {n.problem.strip()}"
                    + (f" Suggestion: {n.suggestion.strip()}" if n.suggestion.strip() else ""),
                    evidence=[
                        Evidence(scene_id=scene_id, version=version, span=span, quote=text[span[0] : span[1]])
                    ],
                    detector=f"critic:craft_{n.dimension}",
                    confidence=0.5,
                )
            )
        return out

    async def voice(
        self, scene_id: str, text: str, version: int, *, cancel: CancelToken | None = None
    ) -> list[Finding]:
        """One call per character who speaks in the scene and has a voice card."""
        card = self.store.try_read("card", scene_id)
        cast = [c for c in self.store.list_codex() if isinstance(c, Character)]
        present = [c for c in cast if card is None or c.id in card.participants]
        found: list[Finding] = []
        for c in present:
            vc = c.voice_card
            if not (vc.diction or vc.verbal_tics or vc.sample_lines or vc.taboo_words):
                continue
            lines = dialogue_lines(text, c, cast)
            if not lines:
                continue
            rp = self.prompts.render(
                "critique.voice", name=c.name, voice=vc, lines=[line for _, line in lines][:12]
            )
            try:
                res = await self.router.generate_structured(
                    rp.messages, VoiceReview, rp.profile, max_tokens=600, temperature=0.2, cancel=cancel
                )
            except GenerationCancelled:
                raise
            except ModelError:
                continue
            for n in res.value.notes:
                # the quote must be (part of) one of *this character's* lines, not just anywhere
                hit = next(
                    (
                        (s, e)
                        for (s, e), line in lines
                        if (loc := locate_quote(line, n.quote)) is not None
                        for s, e in [(s + loc[0], s + loc[1])]
                    ),
                    None,
                )
                if hit is None or len(n.problem.strip()) < MIN_PROBLEM or restates(n.problem, n.quote):
                    continue
                claim = f"{c.name} doesn't sound like themselves: {n.problem.strip()}"
                if n.rewrite.strip():
                    claim += f" In voice: “{n.rewrite.strip()}”"
                found.append(
                    Finding(
                        id="find-0000",
                        scene_id=scene_id,
                        category="voice",
                        severity="med",
                        claim=claim,
                        evidence=[
                            Evidence(
                                scene_id=scene_id, version=version, span=hit, quote=text[hit[0] : hit[1]]
                            )
                        ],
                        affected_entities=[c.id],
                        detector="critic:voice",
                        confidence=0.5,
                    )
                )
        return found
