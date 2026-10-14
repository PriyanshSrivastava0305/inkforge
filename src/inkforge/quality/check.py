"""Stage F, continuity half: scene text → verified observations → rule hits → evidence-linked findings.

A finding from a rule always cites the words in this scene that triggered it and, where the conflicting
canon fact came from prose, the words that established it. Re-checking a scene keeps findings the
author already dismissed or marked intentional closed, and marks findings that no longer occur as fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inkforge.domain.models import Evidence, Fact, Finding
from inkforge.models.router import ModelRouter
from inkforge.models.types import CancelToken
from inkforge.prompts.registry import PromptRegistry
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


@dataclass
class SceneCheck:
    scene_id: str
    extraction: Extraction
    hits: list[RuleHit] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # open after this check
    fixed: list[str] = field(default_factory=list)  # finding ids that no longer occur


def to_finding(
    store: ProjectStore, hit: RuleHit, extraction: Extraction, canon: dict[str, Fact]
) -> Finding | None:
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


def sync_findings(
    store: ProjectStore, scene_id: str, version: int, found: list[Finding]
) -> tuple[list[Finding], list[str]]:
    """Store this check's findings against what's on file for the scene. Returns (open now, fixed ids)."""
    existing = [f for f in store.findings.all() if f.scene_id == scene_id and f.detector.startswith(PREFIX)]
    by_key = {(f.detector, f.claim): f for f in existing}
    open_now: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for f in found:
        key = (f.detector, f.claim)
        if key in seen:
            continue
        seen.add(key)
        old = by_key.get(key)
        if old is not None and old.status in ("dismissed", "intentional", "accepted"):
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
    def __init__(self, store: ProjectStore, router: ModelRouter, prompts: PromptRegistry | None = None):
        self.store = store
        self.extractor = FactExtractor(store, router, prompts)

    async def check(self, scene_id: str, *, cancel: CancelToken | None = None) -> SceneCheck:
        extraction = await self.extractor.extract(scene_id, cancel=cancel)
        result = SceneCheck(scene_id, extraction)
        if extraction.error:
            return result  # nothing was read from the scene: leave earlier findings as they are
        world = World(self.store)
        result.hits = check_scene(world, scene_id, extraction.observations)
        canon = {f.id: f for f in world.canon}
        found = [f for h in result.hits if (f := to_finding(self.store, h, extraction, canon)) is not None]
        result.findings, result.fixed = sync_findings(self.store, scene_id, extraction.version, found)
        return result
