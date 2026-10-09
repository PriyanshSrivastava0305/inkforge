from pathlib import Path

import pytest

from inkforge.domain import models as m
from inkforge.storage.workspace import ProjectStore, Workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws").ensure()


@pytest.fixture
def store(workspace: Workspace) -> ProjectStore:
    return workspace.create_project("The Glass Orchard", "novel")


def populate(s: ProjectStore) -> ProjectStore:
    """A small but complete project: spec → design → 3-chapter outline → cards → drafts, codex, canon."""
    s.save(
        "spec",
        m.BookSpec(
            original="Letters in bottles predict storms.", logline="A keeper reads tomorrow's storms."
        ),
    )
    s.save("design", m.StoryDesign(dramatic_question="Will Mira stop the storm?", promise_ids=["prom-001"]))
    s.save(
        "outline",
        m.Outline(
            acts=[m.OutlineAct(id="act1", order=0)],
            chapters=[
                m.OutlineChapter(id="ch01", order=0, act_id="act1", title="Arrival"),
                m.OutlineChapter(id="ch02", order=1, act_id="act1", title="The Bottles"),
                m.OutlineChapter(id="ch03", order=2, act_id="act1", title="Frost"),
            ],
        ),
    )
    for cid, purpose, outcome in (
        ("ch01", "arrive", "hired"),
        ("ch02", "find bottles", "first letter"),
        ("ch03", "frost", "trees shatter"),
    ):
        s.save("chapter", m.ChapterPlan(id=cid, act_id="act1", title=cid, purpose=purpose, outcome=outcome))
    s.save_codex(m.Character(id="char-mira", name="Mira Vale", voice_card=m.VoiceCard(diction="plain")))
    s.save_codex(m.Character(id="char-tom", name="Tom Vale", aliases=["Tommy"]))
    s.save_codex(m.Location(id="loc-lighthouse", name="The Lighthouse"))
    s.save_codex(m.Item(id="obj-brass-key", name="brass key", kind="key"))
    for sid, order, parts in (
        ("ch01-sc01", 0, ["char-mira", "char-tom"]),
        ("ch01-sc02", 1, ["char-mira"]),
        ("ch02-sc01", 0, ["char-mira"]),
    ):
        s.save(
            "card",
            m.SceneCard(
                id=sid,
                chapter_id=sid[:4],
                order=order,
                pov_character_id="char-mira",
                location_id="loc-lighthouse",
                participants=parts,
                value_charge="+ -> -",
            ),
        )
    s.save_draft("ch01-sc01", "Mira and Tom climbed the lighthouse stairs as the storm came in.")
    s.save_draft("ch01-sc02", "The first bottle was green glass, warm from the sun.")
    s.save(
        "timeline",
        m.Timeline(entries=[m.TimelineEntry(scene_id=x) for x in ("ch01-sc01", "ch01-sc02", "ch02-sc01")]),
    )
    s.save_promises(
        m.PromiseRegistry(promises=[m.PlotPromise(id="prom-001", description="the locked glasshouse")])
    )
    s.upsert(
        "facts",
        m.Fact(
            id=s.mint("fact"),
            subject_id="char-mira",
            predicate="eye_color",
            value="grey",
            valid_from="ch01-sc01",
        ),
    )
    s.upsert(
        "findings",
        m.Finding(
            id=s.mint("finding"),
            scene_id="ch01-sc01",
            category="prose",
            severity="low",
            claim="weather opening",
            detector="rule:slop",
            evidence=[m.Evidence(scene_id="ch01-sc01", version=1, span=(0, 4), quote="Mira")],
        ),
    )
    s.save_job(
        m.Job(job_id="job-20261009-120000-abcd", project_id=s.id, stage="draft", target_id="ch01-sc01")
    )
    s.approve("draft", "ch01-sc01")
    return s


@pytest.fixture
def populated(store: ProjectStore) -> ProjectStore:
    return populate(store)
