"""Canon store operations (docs/07 § 2): the only code that turns a candidate fact into canon.

Extraction fills the review queue; nothing becomes canon until the author accepts it here. Accepting a
fact that contradicts canon is refused unless forced, and the conflicts are returned so the author can
see what they'd be overriding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inkforge.domain.common import Actor
from inkforge.domain.models import CanonStatus, Fact
from inkforge.quality.rules import Observation, RuleHit, World, check_scene
from inkforge.storage.workspace import ProjectStore

# single-valued predicates whose value changes over the story: a new value ends the previous one
TIME_VARYING = {"held_by", "located_at", "location_at", "condition", "status", "age"}
LOW_RISK = {"location_at"}  # safe to bulk-accept: where someone stood in a scene


class CanonError(ValueError):
    pass


@dataclass
class Accepted:
    fact: Fact | None = None
    conflicts: list[RuleHit] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)  # earlier facts that got a valid_to

    @property
    def ok(self) -> bool:
        return self.fact is not None


def pending(store: ProjectStore) -> list[Fact]:
    return [f for f in store.review_queue.all() if f.canon_status == CanonStatus.CANDIDATE]


def conflicts(store: ProjectStore, fact: Fact) -> list[RuleHit]:
    """What the rules say about this candidate if it were asserted in its scene."""
    if fact.valid_from is None:
        return []
    obs = Observation(fact.valid_from, fact.subject_id, fact.predicate, str(fact.value), fact.quote or "")
    return [h for h in check_scene(World(store), fact.valid_from, [obs]) if not h.is_hypothesis]


def accept(store: ProjectStore, fact_id: str, *, force: bool = False, actor: Actor = Actor.USER) -> Accepted:
    """Candidate → canon. The author's decision; never called by extraction."""
    fact = store.review_queue.get(fact_id)
    if fact is None or fact.canon_status != CanonStatus.CANDIDATE:
        raise CanonError(f"{fact_id} is not waiting for review")
    found = conflicts(store, fact)
    if found and not force:
        return Accepted(conflicts=found)
    world = World(store)
    closed: list[str] = []
    update: dict[str, object] = {"canon_status": CanonStatus.CANON}
    if fact.predicate in TIME_VARYING and fact.valid_from is not None:
        here = world.at(fact.valid_from)
        same = [f for f in world.any_time(fact.subject_id, fact.predicate) if f.valid_from is not None]
        earlier = [f for f in same if world.at(f.valid_from or "") < here and f.valid_to is None]
        later = sorted(
            (f for f in same if world.at(f.valid_from or "") > here),
            key=lambda f: world.at(f.valid_from or ""),
        )
        for f in earlier:
            store.upsert("facts", f.model_copy(update={"valid_to": fact.valid_from}), actor=actor)
            closed.append(f.id)
        if later:
            update["valid_to"] = later[0].valid_from
    canon = fact.model_copy(update=update)
    store.upsert("facts", canon, actor=actor)
    store.remove("review_queue", fact_id, actor=actor)
    return Accepted(fact=canon, conflicts=found, closed=closed)


def reject(store: ProjectStore, fact_id: str, *, actor: Actor = Actor.USER) -> Fact:
    """Kept in the queue as rejected, so the same fact isn't proposed again for that scene."""
    fact = store.review_queue.get(fact_id)
    if fact is None or fact.canon_status != CanonStatus.CANDIDATE:
        raise CanonError(f"{fact_id} is not waiting for review")
    rejected = fact.model_copy(update={"canon_status": CanonStatus.REJECTED})
    store.upsert("review_queue", rejected, actor=actor)
    return rejected


def accept_low_risk(store: ProjectStore, *, actor: Actor = Actor.USER) -> list[Fact]:
    """Bulk-accept candidates that can't contradict anything lasting and have no conflict."""
    out = []
    for f in pending(store):
        if f.predicate in LOW_RISK and f.confidence > 0.5 and not conflicts(store, f):
            got = accept(store, f.id, actor=actor)
            if got.fact:
                out.append(got.fact)
    return out


def retcon(store: ProjectStore, fact_id: str, *, actor: Actor = Actor.USER) -> Fact:
    """Withdraw a canon fact (the author changed their mind). It stays on file, marked retconned."""
    fact = store.facts.get(fact_id)
    if fact is None or fact.canon_status != CanonStatus.CANON:
        raise CanonError(f"{fact_id} is not canon")
    gone = fact.model_copy(update={"canon_status": CanonStatus.RETCONNED})
    store.upsert("facts", gone, actor=actor)
    return gone
