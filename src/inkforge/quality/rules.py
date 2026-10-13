"""Canon conflict rules (docs/07 § 3): deterministic checks of what a scene asserts against canon.

Input is a list of **observations** for one scene — `(subject, predicate, value, quote)` in a controlled
vocabulary. Fact extraction (model) produces them from prose; the rules themselves never call a model.

Two kinds of predicate:
  state    eye_color, hair, height, handedness, title_rank, status, location_at, held_by, located_at,
           condition, age, knows_fact, date_of_event, departs_on      (may become canon when accepted)
  event    active (acts/speaks/is met alive), has_item, mentions_fact, pays_off
           (never canon; only checked)

All time comparisons use **story order** from the Timeline (D20): a flashback is judged at its place in
the chronology. `temporal_mode` relaxations: a dream is not checked for state, presence or death; an
unreliable account produces hypotheses at low severity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from inkforge.domain.models import CanonStatus, Character, Fact, Location, PromiseStatus, SceneCard
from inkforge.storage.workspace import ProjectStore

Rule = Literal[
    "single_valued", "temporal", "knowledge", "presence", "object_state", "age_date", "promise", "travel"
]
STATIC = {"eye_color", "hair", "height", "handedness", "title_rank", "date_of_event"}  # never change once set
TERMINAL_CONDITIONS = {"destroyed", "lost"}  # an item can't be intact again after these
ALIVE_EVENTS = {"active", "has_item", "mentions_fact"}


@dataclass(frozen=True)
class Observation:
    scene_id: str
    subject_id: str
    predicate: str
    value: str
    quote: str = ""


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    scene_id: str | None
    claim: str
    severity: Literal["low", "med", "high"] = "high"
    observation: Observation | None = None
    conflicting_fact_id: str | None = None
    is_hypothesis: bool = False


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


class World:
    """Read-only view of canon at a point in story time."""

    def __init__(self, store: ProjectStore):
        self.store = store
        tl = store.try_read("timeline")
        self.index: dict[str, int] = tl.story_index() if tl else {}
        self.entry_date = {e.scene_id: _day(e.story_datetime) for e in (tl.entries if tl else [])}
        self.canon: list[Fact] = [f for f in store.facts.all() if f.canon_status == CanonStatus.CANON]
        self.codex = {e.id: e for e in store.list_codex()}
        self.cards: dict[str, SceneCard] = {}
        for sid in store.scene_ids():
            card = store.try_read("card", sid)
            if card is not None:
                self.cards[sid] = card
        self.travel = [
            (t["from"], t["to"], int(t["min_days"])) for t in store.project.settings.get("travel", [])
        ]

    def name(self, entity_id: str) -> str:
        e = self.codex.get(entity_id)
        return e.name if e else entity_id

    def at(self, scene_id: str) -> int:
        return self.index.get(scene_id, max(self.index.values(), default=-1) + 1)

    def scene_date(self, scene_id: str) -> date | None:
        card = self.cards.get(scene_id)
        return self.entry_date.get(scene_id) or (_day(card.time.story_datetime) if card else None)

    def mode(self, scene_id: str) -> str:
        card = self.cards.get(scene_id)
        return str(card.temporal_mode) if card else "linear"

    def _start(self, f: Fact) -> int:
        return -1 if f.valid_from is None else self.index.get(f.valid_from, 10**9)

    def current(self, subject: str, predicate: str, scene_id: str, *, inclusive: bool = False) -> Fact | None:
        """The canon value in force when `scene_id` begins (or by its end, if inclusive)."""
        here = self.at(scene_id)
        best: tuple[int, Fact] | None = None
        for f in self.canon:
            if f.subject_id != subject or f.predicate != predicate:
                continue
            start = self._start(f)
            if start > here or (start == here and not inclusive):
                continue
            if f.valid_to is not None and self.index.get(f.valid_to, 10**9) < here:
                continue
            if best is None or start >= best[0]:
                best = (start, f)
        return best[1] if best else None

    def any_time(self, subject: str, predicate: str) -> list[Fact]:
        return [f for f in self.canon if f.subject_id == subject and f.predicate == predicate]

    def top_location(self, loc_id: str) -> str:
        seen = set()
        while loc_id not in seen:
            seen.add(loc_id)
            e = self.codex.get(loc_id)
            if not isinstance(e, Location) or not e.parent_location:
                break
            loc_id = e.parent_location
        return loc_id

    def min_days(self, a: str, b: str) -> int | None:
        a, b = self.top_location(a), self.top_location(b)
        for x, y, d in self.travel:
            if {x, y} == {a, b}:
                return d
        return None


def check_scene(world: World, scene_id: str, observations: list[Observation]) -> list[RuleHit]:
    mode = world.mode(scene_id)
    hits: list[RuleHit] = []
    for o in observations:
        if mode == "dream" and o.predicate != "mentions_fact":
            continue  # dreams: no state, presence, death or object checks (docs/07)
        for hit in _check_one(world, scene_id, o):
            if mode == "unreliable":
                hit = RuleHit(
                    hit.rule, hit.scene_id, hit.claim, "low", hit.observation, hit.conflicting_fact_id, True
                )
            hits.append(hit)
    return hits


def _check_one(w: World, sid: str, o: Observation) -> list[RuleHit]:
    out: list[RuleHit] = []
    who = w.name(o.subject_id)
    p = o.predicate

    def hit(
        rule: Rule, claim: str, fact: Fact | None = None, severity: Literal["low", "med", "high"] = "high"
    ) -> None:
        out.append(RuleHit(rule, sid, claim, severity, o, fact.id if fact else None))

    # ---- death: someone who is dead at this point in the story acts, speaks or holds things
    if p in ALIVE_EVENTS and isinstance(w.codex.get(o.subject_id), Character):
        status = w.current(o.subject_id, "status", sid)
        if status is not None and str(status.value) == "dead":
            hit(
                "temporal",
                f"{who} is dead by this point in the story (since {status.valid_from}) but acts here",
                status,
            )
            return out  # everything else about a dead character's action follows from this

    if p in STATIC:  # ---- single-valued facts that never change
        for f in w.any_time(o.subject_id, p):
            if str(f.value).strip().lower() != o.value.strip().lower():
                rule: Rule = "age_date" if p == "date_of_event" else "single_valued"
                hit(
                    rule,
                    f"{who}: {p.replace('_', ' ')} is “{o.value}” here but “{f.value}” in {f.valid_from or 'canon'}",
                    f,
                )
                break

    elif p == "age":  # ---- age vs birthdate and the scene's date
        char = w.codex.get(o.subject_id)
        born = _day(char.birthdate) if isinstance(char, Character) else None
        today = w.scene_date(sid)
        if born and today and o.value.isdigit():
            expected = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            if expected != int(o.value):
                hit(
                    "age_date",
                    f"{who} is {o.value} here, but born {born.isoformat()} makes them {expected} on {today.isoformat()}",
                )

    elif p == "condition":  # ---- destroyed / lost things don't come back
        prev = w.current(o.subject_id, "condition", sid)
        if prev is not None and str(prev.value) in TERMINAL_CONDITIONS and o.value not in TERMINAL_CONDITIONS:
            hit("object_state", f"{who} is {o.value} here but was {prev.value} in {prev.valid_from}", prev)

    elif p == "has_item":  # ---- possession without a transfer
        holder = w.current(o.value, "held_by", sid)
        if holder is not None and str(holder.value) != o.subject_id:
            hit(
                "object_state",
                f"{who} has {w.name(o.value)} here, but {w.name(str(holder.value))} has held it since "
                f"{holder.valid_from} and no handover is recorded",
                holder,
            )

    elif p == "mentions_fact":  # ---- knowledge: knows it, or witnessed it
        if not _knows(w, o.subject_id, o.value, sid):
            hit("knowledge", f"{who} refers to “{o.value}” but has not learned it by this point in the story")

    elif p == "location_at":  # ---- two places at once, or too far too fast
        here = w.at(sid)
        for f in w.any_time(o.subject_id, "location_at"):
            concurrent = f.valid_from and f.valid_from != sid and w.index.get(f.valid_from) == here
            if concurrent and str(f.value) != o.value:
                hit(
                    "presence",
                    f"{who} is at {w.name(o.value)} here and at {w.name(str(f.value))} in {f.valid_from}, "
                    "which happens at the same time",
                    f,
                )
                return out
        out += _travel(w, sid, o)

    elif p == "departs_on":  # ---- "left only this morning": departure vs arrival vs distance
        origin, _, when = o.value.partition("@")
        left, arrived = _day(when), w.scene_date(sid)
        card = w.cards.get(sid)
        need = w.min_days(origin, card.location_id) if card and card.location_id else None
        if left and arrived and need and (arrived - left).days < need:
            hit(
                "travel",
                f"{who} left {w.name(origin)} on {left.isoformat()} and is here on {arrived.isoformat()}; "
                f"the journey takes at least {need} days",
            )
        for f in w.any_time(o.subject_id, "departs_on"):
            if str(f.value).startswith(origin + "@") and str(f.value) != o.value:
                hit(
                    "travel",
                    f"{who} left {w.name(origin)} on {when} here but on {str(f.value).split('@')[1]} in {f.valid_from}",
                    f,
                )
                break
    return out


def _knows(w: World, char: str, ref: str, sid: str) -> bool:
    """Has `char` learned `ref` by this scene? Either a knows_fact record (learning in the same scene
    counts), or they were present when the referenced canon fact was established."""
    here = w.at(sid)
    for f in w.any_time(char, "knows_fact"):
        if str(f.value) == ref and w._start(f) <= here:
            return True
    if "." in ref:  # a canon state fact, "subject.predicate"
        subject, _, predicate = ref.partition(".")
        for f in w.any_time(subject, predicate):
            card = w.cards.get(f.valid_from or "")
            if card is not None and char in card.participants and w._start(f) <= here:
                return True
    return False


def _travel(w: World, sid: str, o: Observation) -> list[RuleHit]:
    """Could the character have reached this place? Uses dated departures and the travel table."""
    today = w.scene_date(sid)
    if today is None:
        return []
    for f in w.any_time(o.subject_id, "departs_on"):
        origin, _, when = str(f.value).partition("@")
        left = _day(when)
        need = w.min_days(origin, o.value)
        if left and need and w.top_location(origin) != w.top_location(o.value):
            first_possible = left + timedelta(days=need)
            if left <= today < first_possible or (today < left and w._start(f) < w.at(sid)):
                # here before they could have arrived — or here while canon still has them at the origin
                return [
                    RuleHit(
                        "presence",
                        sid,
                        f"{w.name(o.subject_id)} is at {w.name(o.value)} on {today.isoformat()}, "
                        f"but leaves {w.name(origin)} on {left.isoformat()} ({need} days away)",
                        "high",
                        o,
                        f.id,
                    )
                ]
    # no departure on record: compare with the nearest dated sightings elsewhere
    for f in w.any_time(o.subject_id, "location_at"):
        other = w.scene_date(f.valid_from) if f.valid_from else None
        need = w.min_days(str(f.value), o.value)
        if other and need and abs((today - other).days) < need and f.valid_from != sid:
            return [
                RuleHit(
                    "presence",
                    sid,
                    f"{w.name(o.subject_id)} is at {w.name(o.value)} on {today.isoformat()} and at "
                    f"{w.name(str(f.value))} on {other.isoformat()}; that journey takes {need} days",
                    "high",
                    o,
                    f.id,
                )
            ]
    return []


def check_promises(world: World, payoffs: list[Observation] | None = None) -> list[RuleHit]:
    """Book-level: promises still open at the end, and payoffs of things never set up."""
    hits: list[RuleHit] = []
    registry = world.store.get_promises()
    known = {p.id for p in registry.promises}
    for p in registry.promises:
        if p.status == PromiseStatus.OPEN and p.importance != "low" and not p.payoff_ref:
            hits.append(
                RuleHit(
                    "promise",
                    None,
                    f"promise {p.id} (“{p.description}”) is set up but never paid off",
                    "med",
                    conflicting_fact_id=None,
                )
            )
    for o in payoffs or []:
        if o.predicate == "pays_off" and o.value not in known:
            hits.append(
                RuleHit(
                    "promise",
                    o.scene_id,
                    f"“{o.value}” resolves the plot here but was never set up",
                    "med",
                    o,
                )
            )
    return hits
