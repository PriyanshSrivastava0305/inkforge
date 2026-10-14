"""Load the golden continuity book (tests/golden/continuity_book/) into a real project.

`outline.yaml` gives the clean book: entities, scenes with dates and temporal modes, and the facts each
scene establishes (→ canon). `observations.yaml` says what each planted contradiction and each decoy
asserts. Rules are scored by running those observations against the canon.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from inkforge.domain import models as m
from inkforge.quality.rules import Observation
from inkforge.storage.workspace import ProjectStore

GOLDEN = Path(__file__).parents[1] / "golden" / "continuity_book"


@dataclass(frozen=True)
class Case:
    id: str
    rule: str  # the rule type a contradiction should trip; for decoys, what it looks like
    scene: str | None
    observations: list[Observation]
    note: str


def load_yaml() -> tuple[dict, dict]:
    outline = yaml.safe_load((GOLDEN / "outline.yaml").read_text(encoding="utf-8"))
    obs = yaml.safe_load((GOLDEN / "observations.yaml").read_text(encoding="utf-8"))
    return outline, obs


def build_continuity_book(store: ProjectStore) -> tuple[list[Case], list[Case]]:
    """Populate `store` with the clean book; return (contradictions, decoys)."""
    book, obs = load_yaml()
    s = store
    s.update_project(settings={"travel": book["entities"]["travel"], "secrets": book.get("secrets", {})})
    s.save(
        "spec",
        m.BookSpec(
            original=book["title"],
            logline="A glass restorer uncovers a forged deed.",
            pov=book["pov"],
            tense=book["tense"],
        ),
    )
    for c in book["entities"]["characters"]:
        s.save_codex(m.Character(id=c["id"], name=c["name"], role=c.get("role"), birthdate=str(c["born"])))
    for loc in book["entities"]["locations"]:
        s.save_codex(m.Location(id=loc["id"], name=loc["name"], parent_location=loc.get("parent")))
    for it in book["entities"]["items"]:
        s.save_codex(m.Item(id=it["id"], name=it["name"]))
    for lore in book["entities"]["lore"]:
        s.save_codex(m.LoreItem(id=lore["id"], name=lore["id"].removeprefix("lore-"), rules=[lore["rule"]]))

    scenes = book["scenes"]
    chapters = list(dict.fromkeys(sc["id"].split("-sc")[0] for sc in scenes))
    s.save(
        "outline",
        m.Outline(
            acts=[m.OutlineAct(id="act1", order=0)],
            chapters=[
                m.OutlineChapter(id=c, order=i, act_id="act1", title=c) for i, c in enumerate(chapters)
            ],
        ),
    )
    for c in chapters:
        s.save("chapter", m.ChapterPlan(id=c, act_id="act1", title=c, purpose=f"{c} of the golden book"))
    for sc in scenes:
        order = int(sc["id"].split("-sc")[1]) - 1
        s.save(
            "card",
            m.SceneCard(
                id=sc["id"],
                chapter_id=sc["id"].split("-sc")[0],
                order=order,
                pov_character_id=sc["pov"],
                location_id=sc["location"],
                participants=sc["participants"],
                items_present=sc.get("items_present", []),
                temporal_mode=sc.get("temporal_mode", "linear"),
                time=m.SceneTime(story_datetime=str(sc["when"])),
                concrete_event=sc["summary"],
            ),
        )

    # timeline: narrative order, except explicit placements; identical date-times are concurrent
    entries: list[m.TimelineEntry] = []
    seen_when: dict[str, str] = {}
    for sc in scenes:
        when = str(sc["when"])
        concurrent = seen_when.get(when) if len(when) > 10 else None
        entry = m.TimelineEntry(scene_id=sc["id"], story_datetime=when, concurrent_with=concurrent)
        if "timeline" in sc:
            at = next(i for i, e in enumerate(entries) if e.scene_id == sc["timeline"]["before"])
            entries.insert(at, entry)
        else:
            entries.append(entry)
        if len(when) > 10:
            seen_when.setdefault(when, sc["id"])
    s.save("timeline", m.Timeline(entries=entries))

    n = 0
    for sc in scenes:
        for subject, predicate, value in sc.get("establishes", []):
            n += 1
            s.upsert(
                "facts",
                m.Fact(
                    id=f"fact-{n:04d}",
                    subject_id=subject,
                    predicate=predicate,
                    value=str(value),
                    valid_from=sc["id"],
                    canon_status=m.CanonStatus.CANON,
                    origin="author",
                ),
            )
    s.save_promises(
        m.PromiseRegistry(
            promises=[
                m.PlotPromise(
                    id=p["id"],
                    description=p["description"],
                    setup_ref=p["setup"],
                    payoff_ref=p["payoff"],
                    status=m.PromiseStatus.PAID if p["payoff"] else m.PromiseStatus.OPEN,
                    importance="med",
                )
                for p in book["promises"]
            ]
        )
    )

    def cases(kind: str, source: list[dict], label: str) -> list[Case]:
        out = []
        for item in source:
            spec = obs[kind][item["id"]]
            observations = [
                Observation(spec["scene"], subj, pred, str(val), item.get("plant", ""))
                for subj, pred, val in spec["obs"]
            ]
            out.append(
                Case(
                    item["id"],
                    item[label],
                    spec["scene"],
                    observations,
                    item.get("plant") or item.get("why_not", ""),
                )
            )
        return out

    return cases("contradictions", book["contradictions"], "rule"), cases(
        "decoys", book["decoys"], "looks_like"
    )
