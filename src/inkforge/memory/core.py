"""CORE tier (docs/06): the small block that is always in the prompt. Deterministic — no model call.

Contents, in priority order (trimmed from the bottom when over budget):
  1. world rules (lore / location rules)
  2. present characters' current state, from canon facts valid at this scene's story time
  3. open high-importance promises
  4. the story-time anchor (where we are in the timeline)
  5. other characters' states, medium-importance promises

Fact validity uses *story order* from the Timeline (D20), so a flashback sees the world as it was
then. Only `canon` facts count; candidates in the review queue never reach a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inkforge.domain.codex import active_characters
from inkforge.domain.models import CanonStatus, Fact, Location, LoreItem, PromiseStatus, SceneCard
from inkforge.storage.workspace import ProjectStore

CORE_TOKEN_BUDGET = 800
SINGLE_VALUED = {
    "age",
    "eye_color",
    "hair",
    "height",
    "status",
    "location_at",
    "held_by",
    "located_at",
    "condition",
    "title_rank",
    "handedness",
}


@dataclass
class CoreLine:
    text: str
    priority: int  # lower = more important
    sources: list[str] = field(default_factory=list)


@dataclass
class CoreBlock:
    lines: list[CoreLine]
    dropped: int = 0

    def text(self) -> str:
        return "\n".join(f"- {ln.text}" for ln in self.lines)

    @property
    def sources(self) -> list[str]:
        return sorted({s for ln in self.lines for s in ln.sources})


def story_position(store: ProjectStore, scene_id: str) -> tuple[dict[str, int], int]:
    """(story index per scene, index of `scene_id`). A scene not yet on the timeline sits at the end."""
    tl = store.try_read("timeline")
    idx = tl.story_index() if tl else {}
    return idx, idx.get(scene_id, max(idx.values(), default=-1) + 1)


def facts_valid_at(store: ProjectStore, scene_id: str) -> list[Fact]:
    """Canon facts true when `scene_id` begins: established strictly earlier in story time and not ended.
    For single-valued predicates only the most recent value per subject survives."""
    idx, here = story_position(store, scene_id)
    live: list[tuple[int, Fact]] = []
    for f in store.facts.all():
        if f.canon_status != CanonStatus.CANON:
            continue
        start = -1 if f.valid_from is None else idx.get(f.valid_from)
        if start is None or start >= here:
            continue  # established at/after this scene, or its scene isn't on the timeline
        if f.valid_to is not None and (end := idx.get(f.valid_to)) is not None and end < here:
            continue
        live.append((start, f))
    latest: dict[tuple[str, str], tuple[int, Fact]] = {}
    multi: list[Fact] = []
    for start, f in live:
        if f.predicate in SINGLE_VALUED:
            key = (f.subject_id, f.predicate)
            if key not in latest or start >= latest[key][0]:
                latest[key] = (start, f)
        else:
            multi.append(f)
    return [f for _, f in sorted(latest.values(), key=lambda x: x[1].id)] + sorted(multi, key=lambda f: f.id)


def _fact_phrase(f: Fact, names: dict[str, str]) -> str:
    value = names.get(str(f.value), str(f.value))
    return f"{f.predicate.replace('_', ' ')}: {value}"


def build_core(
    store: ProjectStore, scene_id: str, count_tokens, *, budget: int = CORE_TOKEN_BUDGET
) -> CoreBlock:
    card: SceneCard | None = store.try_read("card", scene_id)
    present = set(card.participants) if card else set()
    chars = {c.id: c for c in active_characters(store)}
    codex = {e.id: e for e in store.list_codex()}
    names = {i: e.name for i, e in codex.items()}
    lines: list[CoreLine] = []

    for e in codex.values():  # 1. world rules
        if isinstance(e, LoreItem | Location) and e.rules:
            for rule in e.rules:
                lines.append(CoreLine(f"Rule ({e.name}): {rule}", 1, [f"codex:{e.id}"]))

    by_subject: dict[str, list[Fact]] = {}
    for f in facts_valid_at(store, scene_id):
        by_subject.setdefault(f.subject_id, []).append(f)
    items_here = set(card.items_present) if card else set()
    for subject, facts in sorted(by_subject.items()):  # 2 / 5. current states
        if subject not in names:
            continue
        relevant = (
            subject in present or subject in items_here or (card is not None and subject == card.location_id)
        )
        held_by_present = any(f.predicate == "held_by" and str(f.value) in present for f in facts)
        phrase = "; ".join(_fact_phrase(f, names) for f in facts)
        lines.append(
            CoreLine(
                f"{names[subject]} — {phrase}",
                2 if relevant or held_by_present else 5,
                [f"fact:{f.id}" for f in facts],
            )
        )

    for p in store.get_promises().promises:  # 3 / 5. promises
        if p.status == PromiseStatus.OPEN and p.importance != "low":
            lines.append(
                CoreLine(
                    f"Open promise ({p.id}): {p.description}",
                    3 if p.importance == "high" else 5,
                    [f"promise:{p.id}"],
                )
            )

    tl = store.try_read("timeline")  # 4. story-time anchor
    if tl and card:
        entry = next((e for e in tl.entries if e.scene_id == scene_id), None)
        when = (
            (entry.story_datetime or entry.label) if entry else (card.time.story_datetime or card.time.label)
        )
        if when:
            mode = "" if card.temporal_mode == "linear" else f" ({card.temporal_mode})"
            lines.append(CoreLine(f"Story time now: {when}{mode}", 4, ["timeline"]))

    for cid in sorted(present):  # essentials for present characters without any canon facts yet
        c = chars.get(cid)
        if c and cid not in by_subject and (c.goals or c.secrets):
            bits = ([f"wants {c.goals[0]}"] if c.goals else []) + (
                [f"hides {c.secrets[0]}"] if c.secrets else []
            )
            lines.append(CoreLine(f"{c.name} — {'; '.join(bits)}", 4, [f"codex:{cid}"]))

    lines.sort(key=lambda ln: (ln.priority, ln.text))
    kept: list[CoreLine] = []
    used = 0
    for ln in lines:
        cost = count_tokens("- " + ln.text) + 1
        if used + cost > budget:
            continue
        kept.append(ln)
        used += cost
    return CoreBlock(kept, dropped=len(lines) - len(kept))
