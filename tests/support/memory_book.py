"""Golden memory book (Phase 5 exit): 30 scenes, 25 planted facts, each needed by a later scene.

Built deterministically in code (no model): 10 chapters x 3 scenes of ~450 words. A planted fact is
one distinctive sentence in scene A; a later scene B (at least 3 scenes on, so the recap can't carry
it) has a card that involves the same entity. Twenty facts must come back through RECALL (the
passage); five are also canon facts and must come back through CORE.

    recall@context = planted facts whose sentence (or canon line) is in scene B's prompt / 25
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from inkforge.domain import models as m
from inkforge.storage.workspace import ProjectStore

CHARS = [
    ("char-mira", "Mira Vale"),
    ("char-tom", "Tom Vale"),
    ("char-elias", "Elias Crane"),
    ("char-june", "June Crane"),
    ("char-bram", "Bram Okafor"),
    ("char-nell", "Nell Pike"),
]
LOCS = [
    ("loc-orchard", "Glass Orchard"),
    ("loc-chapel", "Saltmere Chapel"),
    ("loc-harbor", "Harbor Wall"),
    ("loc-study", "Crane Study"),
    ("loc-carrow", "Carrow Records Office"),
    ("loc-tavern", "Anchor Tavern"),
]
ITEMS = [
    ("obj-brass-key", "brass key"),
    ("obj-ledger", "green ledger"),
    ("obj-locket", "silver locket"),
    ("obj-revolver", "service revolver"),
    ("obj-lantern", "storm lantern"),
    ("obj-deed", "orchard deed"),
    ("obj-compass", "whalebone compass"),
    ("obj-letter", "sealed letter"),
]

# (entity id, sentence planted in the early scene, canon fact or None)
PLANTED: list[tuple[str, str, tuple[str, str] | None]] = [
    ("obj-brass-key", "The brass key had a notch filed into its third tooth.", None),
    ("obj-ledger", "Every page of the green ledger was ruled in violet ink.", None),
    ("obj-locket", "Inside the silver locket lay a curl of copper hair.", None),
    ("obj-revolver", "The service revolver pulled a hand's width to the left.", None),
    ("obj-lantern", "The storm lantern leaked oil from a cracked brass collar.", None),
    ("obj-deed", "The orchard deed bore a notary stamp dated in the wrong century.", None),
    ("obj-compass", "The whalebone compass always pointed two degrees east of north.", None),
    ("obj-letter", "The sealed letter was closed with black wax and a thumbprint.", None),
    ("loc-orchard", "In the Glass Orchard the eleventh tree had never borne fruit.", None),
    ("loc-chapel", "The bell of Saltmere Chapel had been cast without a clapper.", None),
    ("loc-harbor", "Someone had carved a row of tally marks into the Harbor Wall.", None),
    ("loc-study", "The Crane Study smelled of camphor and scorched paper.", None),
    ("loc-carrow", "The Carrow Records Office closed at noon on market days.", None),
    ("loc-tavern", "The Anchor Tavern kept a drowned sailor's boots above the door.", None),
    ("char-nell", "Nell Pike counted every stair aloud, in a language nobody recognised.", None),
    ("char-bram", "Bram Okafor wrote only with his left hand since the winter fever.", None),
    ("char-june", "June Crane hummed the same four notes whenever she lied.", None),
    ("char-elias", "Elias Crane kept his late wife's spectacles in his breast pocket.", None),
    ("char-tom", "Tom Vale could not read a clock face, only the tide tables.", None),
    ("char-mira", "Mira Vale had sworn never to cut glass on a Sunday.", None),
    ("obj-brass-key", "The brass key passed into Bram's keeping that night.", ("held_by", "char-bram")),
    ("obj-ledger", "The green ledger was locked away in the Crane Study.", ("located_at", "loc-study")),
    ("char-elias", "By morning Elias Crane was dead.", ("status", "dead")),
    ("obj-lantern", "The storm lantern lay in pieces on the flagstones.", ("condition", "broken")),
    ("char-june", "June Crane's eyes were the pale blue of sea ice.", ("eye_color", "blue")),
]
_WORDS = [
    "the",
    "tide",
    "came",
    "in",
    "over",
    "the",
    "flats",
    "and",
    "the",
    "gulls",
    "wheeled",
    "above",
    "the",
    "grey",
    "water",
    "while",
    "lamps",
    "were",
    "lit",
    "along",
    "the",
    "quay",
    "and",
    "somebody",
    "laughed",
    "in",
    "a",
    "doorway",
    "as",
    "rain",
    "began",
    "again",
    "upon",
    "the",
    "slate",
    "roofs",
    "of",
    "the",
    "town",
    "and",
    "the",
    "wind",
    "carried",
    "salt",
    "up",
    "the",
    "lanes",
    "past",
    "shuttered",
    "windows",
    "where",
    "bread",
    "was",
    "cooling",
    "and",
    "a",
    "dog",
    "slept",
    "by",
    "the",
    "fire",
    "under",
    "a",
    "table",
    "set",
    "for",
    "two",
    "with",
    "pewter",
    "cups",
    "and",
    "a",
    "jug",
    "of",
    "dark",
    "ale",
    "that",
    "nobody",
    "had",
    "touched",
    "since",
    "supper",
]


@dataclass(frozen=True)
class Planted:
    index: int
    entity: str
    sentence: str
    planted_in: str
    needed_in: str
    canon: tuple[str, str] | None


def _filler(rng: random.Random, names: list[str], n_paras: int = 5) -> list[str]:
    paras = []
    for _ in range(n_paras):
        words = [rng.choice(_WORDS) for _ in range(rng.randint(60, 85))]
        for pos in sorted(rng.sample(range(5, len(words) - 5), 2)):
            words[pos] = rng.choice(names)
        sentences, cur = [], []
        for w in words:
            cur.append(w)
            if len(cur) >= rng.randint(9, 16):
                sentences.append(" ".join(cur).capitalize() + ".")
                cur = []
        if cur:
            sentences.append(" ".join(cur).capitalize() + ".")
        paras.append(" ".join(sentences))
    return paras


def build_memory_book(store: ProjectStore, *, seed: int = 7) -> list[Planted]:
    """Populate `store` with the 30-scene book and return the planted-fact plan."""
    rng = random.Random(seed)
    s = store
    s.save(
        "spec",
        m.BookSpec(
            original="memory book",
            logline="A glass restorer untangles a dead man's accounts.",
            premise="A forged deed, a drowned coast and a family of liars.",
            genre="gothic mystery",
            audience="adult",
            tone="brooding",
            pov="third-limited",
            tense="past",
            setting=m.Setting(place="Saltmere", time="1892"),
            themes=["debt", "inheritance"],
            content_boundaries=["none"],
            narrative_promises=["the forgery is exposed"],
        ),
    )
    for cid, name in CHARS:
        s.save_codex(
            m.Character(
                id=cid,
                name=name,
                role="protagonist" if cid == "char-mira" else "supporting",
                voice_card=m.VoiceCard(diction="plain"),
            )
        )
    for lid, name in LOCS:
        s.save_codex(m.Location(id=lid, name=name))
    for iid, name in ITEMS:
        s.save_codex(m.Item(id=iid, name=name))
    chapters = [f"ch{i:02d}" for i in range(1, 11)]
    s.save(
        "outline",
        m.Outline(
            acts=[
                m.OutlineAct(id="act1", order=0),
                m.OutlineAct(id="act2", order=1),
                m.OutlineAct(id="act3", order=2),
            ],
            chapters=[
                m.OutlineChapter(
                    id=c,
                    order=i,
                    act_id=("act1", "act2", "act2", "act3")[min(i // 3, 3)],
                    title=f"Chapter {i + 1}",
                )
                for i, c in enumerate(chapters)
            ],
        ),
    )
    for c in chapters:
        s.save(
            "chapter",
            m.ChapterPlan(
                id=c,
                title=c,
                purpose=f"what {c} is for",
                conflict="a quarrel",
                outcome="a cost",
                target_length=1500,
            ),
        )
    scenes = [f"{c}-sc{j:02d}" for c in chapters for j in (1, 2, 3)]
    # plan: fact k planted in an early scene, needed 3..14 scenes later
    plan: list[Planted] = []
    plant_at = sorted(rng.sample(range(0, 16), 16) + rng.sample(range(2, 16), 9))
    for k, (entity, sentence, canon) in enumerate(PLANTED):
        a = plant_at[k]
        b = min(len(scenes) - 1, a + rng.randint(3, 14))
        plan.append(Planted(k, entity, sentence, scenes[a], scenes[b], canon))
    needs: dict[str, list[Planted]] = {}
    plants: dict[str, list[Planted]] = {}
    for p in plan:
        needs.setdefault(p.needed_in, []).append(p)
        plants.setdefault(p.planted_in, []).append(p)

    names = [n.split()[0] for _, n in CHARS]
    for i, sid in enumerate(scenes):
        needed = needs.get(sid, [])
        cast = ["char-mira", rng.choice([c for c, _ in CHARS[1:]])]
        items, loc = [], rng.choice(LOCS)[0]
        for p in needed:
            if p.entity.startswith("char-") and p.entity not in cast:
                cast.append(p.entity)
            elif p.entity.startswith("obj-"):
                items.append(p.entity)
            elif p.entity.startswith("loc-"):
                loc = p.entity
        s.save(
            "card",
            m.SceneCard(
                id=sid,
                chapter_id=sid[:4],
                order=i % 3,
                pov_character_id="char-mira",
                location_id=loc,
                participants=cast,
                items_present=sorted(set(items)),
                want="to learn the truth",
                obstacle="someone lies",
                change="she knows more",
                value_charge="- -> +",
                concrete_event="a conversation turns",
                exit_condition="someone leaves",
                target_length=500,
            ),
        )
        paras = _filler(rng, names)
        for p in plants.get(sid, []):
            paras.insert(rng.randint(1, len(paras) - 1), p.sentence + " " + _filler(rng, names, 1)[0][:160])
        s.save_draft(sid, "\n\n".join(paras))
        s.approve("draft", sid)
    s.save(
        "timeline",
        m.Timeline(entries=[m.TimelineEntry(scene_id=x, label=f"day {i + 1}") for i, x in enumerate(scenes)]),
    )
    n = 0
    for p in plan:
        if p.canon:
            n += 1
            s.upsert(
                "facts",
                m.Fact(
                    id=f"fact-{n:04d}",
                    subject_id=p.entity,
                    predicate=p.canon[0],
                    value=p.canon[1],
                    valid_from=p.planted_in,
                    canon_status=m.CanonStatus.CANON,
                    origin="author",
                    quote=p.sentence,
                ),
            )
    return plan
