"""Stage F, continuity half: scene text → verified observations → rule hits → (adjudication) → findings.

A finding from a rule always cites the words in this scene that triggered it and, where the conflicting
canon fact came from prose, the words that established it. Conflicts the rules can't settle by string
comparison (free-text values such as "sea-coloured" vs "blue") go to the model adjudicator: "no" drops
the hit, "unclear" makes it a hypothesis, no answer leaves the rule's finding as it is.

`sync_findings` is shared by every detector family: re-checking a scene keeps findings the author
dismissed, marked intentional or accepted as they are, and marks open ones that no longer occur fixed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from inkforge.domain.models import Evidence, Fact, Finding
from inkforge.models.router import ModelRouter
from inkforge.models.types import CancelToken
from inkforge.prompts.registry import PromptRegistry
from inkforge.quality.critics import FREE_TEXT, Critics
from inkforge.quality.extract import Extraction, FactExtractor
from inkforge.quality.rules import RuleHit, World, check_scene
from inkforge.storage.workspace import ProjectStore

CATEGORY = {
    "single_valued": "continuity",
    "temporal": "continuity",
    "object_state": "continuity",
    "presence": "continuity",
    "knowledge": "knowledge",
    "age_date": "timeline",
    "travel": "timeline",
    "promise": "promise",
}
PREFIX = "rule:canon_"
DECIDED = ("dismissed", "intentional", "accepted")


@dataclass
class SceneCheck:
    scene_id: str
    extraction: Extraction
    hits: list[RuleHit] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # open after this check
    fixed: list[str] = field(default_factory=list)  # finding ids that no longer occur
    adjudicated: list[tuple[str, str]] = field(default_factory=list)  # (claim, verdict)


def to_finding(hit: RuleHit, extraction: Extraction, canon: dict[str, Fact]) -> Finding | None:
    """A rule hit as a Finding. Returns None if the hit has no locatable words (never shown, docs/07)."""
    o = hit.observation
    if o is None or hit.scene_id is None or o.quote not in extraction.spans:
        return None
    evidence = [
        Evidence(
            scene_id=hit.scene_id, version=extraction.version, span=extraction.spans[o.quote], quote=o.quote
        )
    ]
    other = canon.get(hit.conflicting_fact_id or "")
    if other is not None and other.source_ref is not None and other.quote:
        ref = other.source_ref
        evidence.append(
            Evidence(scene_id=ref.scene_id, version=ref.version, span=ref.char_span, quote=other.quote)
        )
    affected = [o.subject_id, *([o.value] if o.value.startswith(("char-", "loc-", "obj-")) else [])]
    return Finding(
        id="find-0000",  # minted when stored
        scene_id=hit.scene_id,
        category=CATEGORY[hit.rule],  # type: ignore[arg-type]
        severity=hit.severity,
        claim=hit.claim,
        evidence=evidence,
        conflicting_fact_id=hit.conflicting_fact_id,
        affected_entities=affected,
        detector=f"{PREFIX}{hit.rule}",
        confidence=0.5 if hit.is_hypothesis else 0.9,
        is_hypothesis=hit.is_hypothesis,
    )


def owned_by(*prefixes: str) -> Callable[[Finding], bool]:
    return lambda f: f.detector.startswith(prefixes)


def sync_findings(
    store: ProjectStore,
    scene_id: str,
    version: int,
    found: list[Finding],
    owns: Callable[[Finding], bool] | None = None,
) -> tuple[list[Finding], list[str]]:
    """Store one detector family's findings for a scene against what's on file. Returns (open now, fixed ids)."""
    owns = owns or owned_by(PREFIX)
    existing = [f for f in store.findings.all() if f.scene_id == scene_id and owns(f)]
    by_key = {(f.detector, f.claim): f for f in existing}
    open_now: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for f in found:
        key = (f.detector, f.claim)
        if key in seen:
            continue
        seen.add(key)
        old = by_key.get(key)
        if old is not None and old.status in DECIDED:
            continue  # the author already decided about this one
        f = f.model_copy(update={"id": old.id if old else store.mint("finding")})
        store.upsert("findings", f)
        open_now.append(f)
    fixed = []
    for old in existing:
        if old.status == "open" and (old.detector, old.claim) not in seen:
            note = f"no longer detected in v{version}"
            store.upsert("findings", old.model_copy(update={"status": "fixed", "resolution_note": note}))
            fixed.append(old.id)
    return open_now, fixed


class ContinuityChecker:
    def __init__(
        self,
        store: ProjectStore,
        router: ModelRouter,
        prompts: PromptRegistry | None = None,
        *,
        adjudicate: bool = True,
    ):
        self.store = store
        self.extractor = FactExtractor(store, router, prompts)
        self.critics = Critics(store, router, prompts) if adjudicate else None

    async def hits(
        self, scene_id: str, *, text: str | None = None, cancel: CancelToken | None = None
    ) -> tuple[Extraction, list[RuleHit], list[tuple[str, str]]]:
        """Extraction and adjudicated rule hits for the scene's text, or for a candidate `text` (dry run)."""
        extraction = await self.extractor.extract(scene_id, cancel=cancel, text=text)
        if extraction.error:
            return extraction, [], []
        world = World(self.store)
        hits = check_scene(world, scene_id, extraction.observations)
        canon = {f.id: f for f in world.canon}
        kept: list[RuleHit] = []
        verdicts: list[tuple[str, str]] = []
        for h in hits:
            o, other = h.observation, canon.get(h.conflicting_fact_id or "")
            if (
                self.critics
                and h.rule == "single_valued"
                and o
                and o.predicate in FREE_TEXT
                and not h.is_hypothesis
            ):
                there = (
                    other.quote
                    if other and other.quote
                    else f"{o.predicate.replace('_', ' ')}: {other.value if other else '?'}"
                )
                verdict = await self.critics.adjudicate(h.claim, o.quote, there, cancel=cancel)
                if verdict is not None:
                    verdicts.append((h.claim, verdict.conflict))
                    if verdict.conflict == "no":
                        continue
                    if verdict.conflict == "unclear":
                        h = RuleHit(h.rule, h.scene_id, h.claim, "low", o, h.conflicting_fact_id, True)
            kept.append(h)
        return extraction, kept, verdicts

    async def check(self, scene_id: str, *, cancel: CancelToken | None = None) -> SceneCheck:
        extraction, hits, verdicts = await self.hits(scene_id, cancel=cancel)
        result = SceneCheck(scene_id, extraction, hits, adjudicated=verdicts)
        if extraction.error:
            return result  # nothing was read from the scene: leave earlier findings as they are
        canon = {f.id: f for f in self.store.facts.all()}
        found = [f for h in hits if (f := to_finding(h, extraction, canon)) is not None]
        result.findings, result.fixed = sync_findings(self.store, scene_id, extraction.version, found)
        return result
