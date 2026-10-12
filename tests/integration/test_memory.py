"""Phase 5: effective versions (D34), DIGEST, CORE and RECALL tiers."""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from inkforge.config import load_model_config
from inkforge.domain import models as m
from inkforge.memory.core import build_core, facts_valid_at
from inkforge.memory.digests import Digester, DigestStore, clip_words, extract_summary
from inkforge.memory.effective import effective, scenes_before
from inkforge.memory.recall import RecallIndex, card_terms, chunk_text, keywords
from inkforge.memory.service import MemoryService
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse
from inkforge.models.router import ModelRouter
from inkforge.models.tokens import estimate
from inkforge.storage.workspace import ProjectStore

SCENES = {
    "ch01-sc01": "Mira Vale reached Saltmere at dusk.\n\nTom carried the trunk. Elias Crane handed Mira the brass key "
    "to the glasshouse and warned her never to open the central door.",
    "ch01-sc02": "The orchard glittered under frost.\n\nMira found a silver locket in the soil. It belonged to June. "
    "She put the locket in her coat pocket and told no one.",
    "ch02-sc01": "Tom rode the coach to Carrow.\n\nAt the records office he learned the deed to the orchard was forged. "
    "He wrote to Mira that night.",
    "ch02-sc02": "Constable Bram took the brass key from Mira as evidence.\n\nShe watched him lock it in his desk.",
    "ch03-sc01": "Snow fell on Saltmere.\n\nMira waited by the harbor wall for the coach from Carrow.",
}


@pytest.fixture
def book(store: ProjectStore) -> ProjectStore:
    s = store
    s.save(
        "spec",
        m.BookSpec(
            original="idea", logline="A restorer uncovers a forged deed.", pov="third-limited", tense="past"
        ),
    )
    s.save(
        "outline",
        m.Outline(
            acts=[m.OutlineAct(id="act1", order=0), m.OutlineAct(id="act2", order=1)],
            chapters=[
                m.OutlineChapter(id="ch01", order=0, act_id="act1", title="Arrival"),
                m.OutlineChapter(id="ch02", order=1, act_id="act1", title="The Deed"),
                m.OutlineChapter(id="ch03", order=2, act_id="act2", title="Snow"),
            ],
        ),
    )
    for cid in ("ch01", "ch02", "ch03"):
        s.save(
            "chapter",
            m.ChapterPlan(
                id=cid, act_id="act1" if cid != "ch03" else "act2", title=cid, purpose=f"{cid} purpose"
            ),
        )
    s.save_codex(m.Character(id="char-mira", name="Mira Vale"))
    s.save_codex(m.Character(id="char-tom", name="Tom Vale", aliases=["Tommy"]))
    s.save_codex(m.Character(id="char-bram", name="Bram Okafor"))
    s.save_codex(
        m.Location(id="loc-orchard", name="The Glass Orchard", rules=["Glass trees shatter in hard frost."])
    )
    s.save_codex(m.Item(id="obj-brass-key", name="brass key", kind="key"))
    s.save_codex(m.Item(id="obj-locket", name="silver locket"))
    for i, sid in enumerate(SCENES):
        parts = ["char-mira"] if sid != "ch02-sc01" else ["char-tom"]
        s.save(
            "card",
            m.SceneCard(
                id=sid,
                chapter_id=sid[:4],
                order=i % 2,
                pov_character_id=parts[0],
                participants=parts,
                concrete_event="something happens",
            ),
        )
    s.save(
        "timeline",
        m.Timeline(entries=[m.TimelineEntry(scene_id=x, label=f"day {i + 1}") for i, x in enumerate(SCENES)]),
    )
    for sid, text in SCENES.items():
        if sid != "ch03-sc01":
            s.save_draft(sid, text)
    s.save_promises(
        m.PromiseRegistry(
            promises=[
                m.PlotPromise(
                    id="prom-001", description="The locked central door of the glasshouse", importance="high"
                ),
                m.PlotPromise(id="prom-002", description="a minor rumour", importance="low"),
            ]
        )
    )
    return s


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- effective versions (D34)


def test_effective_prefers_approved_else_active(book: ProjectStore):
    e = effective(book, "ch01-sc01")
    assert (e.version, e.provisional) == (1, True)
    book.approve("draft", "ch01-sc01")
    book.save_draft("ch01-sc01", "A newer unapproved draft.")
    e = effective(book, "ch01-sc01")
    assert (e.version, e.provisional) == (1, False)  # approved v1 wins over active v2
    assert effective(book, "ch03-sc01") is None
    assert scenes_before(book, "ch02-sc01") == ["ch01-sc01", "ch01-sc02"]


# ---------------------------------------------------------------- DIGEST


def test_extract_and_clip():
    text = "Mira arrived at dawn. She met Elias. He gave her the brass key. They argued about debts. She left angry."
    assert extract_summary(text, 12) == "Mira arrived at dawn. She met Elias. … She left angry."
    assert len(clip_words("word " * 300, 120).split()) <= 121
    assert extract_summary("", 50) == ""


def test_digests_without_model_then_staleness_follows_the_text(book: ProjectStore):
    ds, dg = DigestStore(book), Digester(book)
    built = run(dg.update(use_model=False))
    kinds = [(d.kind, d.target) for d in built]
    assert kinds == [
        ("scene", "ch01-sc01"),
        ("scene", "ch01-sc02"),
        ("scene", "ch02-sc01"),
        ("scene", "ch02-sc02"),
        ("chapter", "ch01"),
        ("chapter", "ch02"),
        ("act", "act1"),
    ]  # children before parents; ch03 has no text
    assert all(d.method == "extract" and d.provisional for d in built)
    assert ds.stale() == []
    book.save_draft("ch01-sc02", "Entirely new text about the locket.")
    assert ds.stale() == [
        ("scene", "ch01-sc02"),
        ("chapter", "ch01"),
        ("act", "act1"),
    ]  # hash chain goes stale upward
    book.approve("draft", "ch02-sc01")
    assert ("scene", "ch02-sc01") not in ds.stale()  # same text, now approved: still fresh


def test_model_digest_with_extract_fallback(book: ProjectStore):
    good = json.dumps(
        {
            "summary": "Mira arrived in Saltmere and received the brass key from Elias.",
            "changes": ["Mira holds the brass key"],
        }
    )
    r = ModelRouter(
        load_model_config("fast"),
        FakeAdapter([FakeResponse(good), FakeResponse("x"), FakeResponse("x"), FakeResponse("x")]),
    )
    dg = Digester(book, r)
    d1 = run(dg.build("scene", "ch01-sc01"))
    assert d1.method == "model" and "brass key" in d1.text() and d1.source_refs == ["draft:ch01-sc01@v1"]
    d2 = run(dg.build("scene", "ch01-sc02"))  # three bad attempts → extract stands in, nothing raised
    assert d2.method == "extract" and d2.summary


def test_after_scene_builds_chapter_and_act_only_when_complete(book: ProjectStore):
    dg = Digester(book)
    run(dg.update(use_model=False))
    book.save_draft("ch03-sc01", SCENES["ch03-sc01"])
    built = run(dg.after_scene("ch03-sc01", use_model=False))
    assert [(d.kind, d.target) for d in built] == [
        ("scene", "ch03-sc01"),
        ("chapter", "ch03"),
        ("act", "act2"),
    ]


# ---------------------------------------------------------------- CORE


def test_facts_valid_at_story_time(book: ProjectStore):
    s = book

    def f(i, subj, pred, val, a, b=None):
        return m.Fact(
            id=f"fact-000{i}",
            subject_id=subj,
            predicate=pred,
            value=val,
            valid_from=a,
            valid_to=b,
            canon_status="canon",
        )

    s.upsert("facts", f(1, "obj-brass-key", "held_by", "char-mira", "ch01-sc01", "ch02-sc02"))
    s.upsert("facts", f(2, "obj-brass-key", "held_by", "char-bram", "ch02-sc02"))
    s.upsert("facts", f(3, "char-mira", "eye_color", "grey", None))
    s.upsert(
        "facts",
        m.Fact(
            id="fact-0004",
            subject_id="char-mira",
            predicate="eye_color",
            value="blue",
            valid_from="ch01-sc01",
        ),
    )  # candidate

    def at(sid):
        return {(x.subject_id, x.predicate): x.value for x in facts_valid_at(s, sid)}

    assert at("ch01-sc01") == {
        ("char-mira", "eye_color"): "grey"
    }  # the key fact starts *in* sc01, so not yet true
    assert at("ch02-sc01")[("obj-brass-key", "held_by")] == "char-mira"
    assert at("ch03-sc01")[("obj-brass-key", "held_by")] == "char-bram"
    assert at("ch03-sc01")[("char-mira", "eye_color")] == "grey"  # candidates never count


def test_core_block_contents_priorities_and_budget(book: ProjectStore):
    s = book
    s.upsert(
        "facts",
        m.Fact(
            id="fact-0001",
            subject_id="obj-brass-key",
            predicate="held_by",
            value="char-mira",
            valid_from="ch01-sc01",
            canon_status="canon",
        ),
    )
    core = build_core(s, "ch02-sc02", estimate)
    text = core.text()
    assert "Rule (The Glass Orchard): Glass trees shatter in hard frost." in text
    assert "brass key — held by: Mira Vale" in text  # ids resolved to names; held by a present character
    assert "Open promise (prom-001)" in text and "prom-002" not in text  # low importance left out
    assert "Story time now: day 4" in text
    assert "fact:fact-0001" in core.sources
    tiny = build_core(s, "ch02-sc02", estimate, budget=25)
    assert tiny.dropped > 0 and tiny.lines[0].priority == 1  # rules survive longest


# ---------------------------------------------------------------- RECALL


@settings(max_examples=100, deadline=None)
@given(st.lists(st.text(alphabet="ab .!\n", min_size=0, max_size=300), max_size=12).map("\n\n".join))
def test_chunks_are_exact_slices(text):
    for a, b, chunk in chunk_text(text):
        assert text[a:b] == chunk and chunk.strip() == chunk and chunk


def test_keywords_and_card_terms(book: ProjectStore):
    assert keywords("The locked central door of the glasshouse") == [
        "glasshouse",
        "central",
        "locked",
        "door",
    ]
    card = book.read("card", "ch02-sc02").model_copy(
        update={"items_present": ["obj-brass-key"], "promises_touched": ["prom-001"]}
    )
    terms = card_terms(book, card)
    assert {"mira", "vale", "brass", "key", "glasshouse"} <= set(terms)


def test_recall_finds_the_passage_and_only_looks_backwards(book: ProjectStore):
    s = book
    s.save("card", s.read("card", "ch02-sc02").model_copy(update={"items_present": ["obj-locket"]}))
    idx = RecallIndex(s)
    assert idx.sync() == {"indexed": 4, "removed": 0, "chunks": 4}
    assert idx.sync()["indexed"] == 0  # nothing changed
    hits = idx.retrieve("ch02-sc02")
    top = hits[0]
    assert top.scene_id == "ch01-sc02" and "silver locket" in top.text
    assert (
        s.read("draft", top.scene_id, version=top.version)[top.start : top.end] == top.text
    )  # citable offsets
    assert all(
        h.scene_id in ("ch01-sc01", "ch01-sc02", "ch02-sc01") for h in hits
    )  # never the scene itself or later
    assert idx.retrieve("ch01-sc01") == []  # first scene: nothing before it


def test_recall_follows_effective_text_and_survives_deletion(book: ProjectStore):
    s = book
    idx = RecallIndex(s)
    idx.sync()
    s.save_draft("ch01-sc02", "Mira found a brass compass in the soil instead.")
    assert idx.sync()["indexed"] == 1
    assert not any("locket" in p.text for p in idx.search(["locket"], scenes=["ch01-sc02"]))
    assert any("compass" in p.text for p in idx.search(["compass"], scenes=["ch01-sc02"]))
    idx.path.write_bytes(b"corrupt")
    assert idx.sync()["indexed"] == 4  # rebuilt from files
    assert (
        idx.retrieve(
            "ch02-sc02",
            exclude_text="Tom rode the coach to Carrow. At the records office he learned the deed",
        )
        != []
    )


# ---------------------------------------------------------------- service


def test_status_update_rebuild(book: ProjectStore):
    svc = MemoryService(book)
    st0 = svc.status()
    assert (st0.scenes_with_text, st0.provisional, st0.up_to_date) == (4, 4, False)
    run(svc.update(use_model=False))
    st1 = svc.status()
    assert st1.up_to_date and st1.extract_digests == st1.digests_fresh == 7 and st1.recall_chunks == 4
    before = {
        (k, t): DigestStore(book).get(k, t).summary
        for k, t in DigestStore(book).all_targets()
        if DigestStore(book).get(k, t)
    }
    run(svc.rebuild(use_model=False))
    after = {
        (k, t): DigestStore(book).get(k, t).summary
        for k, t in DigestStore(book).all_targets()
        if DigestStore(book).get(k, t)
    }
    assert before == after  # rebuild reproduces memory from files
    assert (
        json.loads((book.dir / "memory" / "core.json").read_text(encoding="utf-8"))["as_of_scene"]
        == "ch03-sc01"
    )
