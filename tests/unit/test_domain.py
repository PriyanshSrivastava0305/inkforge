# Records are built from raw strings on purpose, as they would arrive from JSON; pydantic coerces them.
# pyright: reportArgumentType=false, reportAssignmentType=false
import pytest
from pydantic import ValidationError

from inkforge.domain import ids
from inkforge.domain import models as m
from inkforge.domain.common import SchemaRecord
from inkforge.domain.forms import get_form, load_forms

# One valid example per persisted record type; every SCHEMA_RECORD must appear here.
EXAMPLES: list[SchemaRecord] = [
    m.Project(
        id="the-glass-orchard",
        title="The Glass Orchard",
        form="novel",
        target_length=80000,
        counters={"fact": 3},
    ),
    m.BookSpec(
        original="idea",
        logline="A keeper reads tomorrow's storms.",
        pov="third-limited",
        tense="past",
        setting=m.Setting(place="Saltmere", time="1892"),
        themes=["fate", "family"],
        custom_fields={"k": "v"},
    ),
    m.StoryDesign(
        dramatic_question="Will she?",
        acts=[m.Act(id="act1", purpose="setup", turning_point="tp")],
        arcs=[m.Arc(character_id="char-mira", start_state="alone", end_state="belongs")],
        promise_ids=["prom-001"],
    ),
    m.PromiseRegistry(
        promises=[
            m.PlotPromise(
                id="prom-001",
                description="d",
                setup_ref="ch01-sc01",
                history=[m.PromiseHistory(actor="user", from_status=None, to_status="open")],
            )
        ]
    ),
    m.Outline(
        acts=[m.OutlineAct(id="act1", order=0)],
        chapters=[m.OutlineChapter(id="ch01", order=0, act_id="act1", title="T")],
    ),
    m.ChapterPlan(id="ch01", title="T", purpose="p", characters=["char-mira"], promises_touched=["prom-001"]),
    m.SceneCard(
        id="ch01-sc02",
        chapter_id="ch01",
        order=1,
        temporal_mode="flashback",
        time=m.SceneTime(story_datetime="1879-11-20"),
        participants=["char-mira"],
        items_present=["obj-key"],
        value_charge="+ → -",
        required_facts=["fact-0001"],
        slugline=m.Slugline(int_ext="INT.", location="LIGHTHOUSE", time_of_day="NIGHT"),
        page_estimate=1.5,
    ),
    m.Timeline(
        entries=[
            m.TimelineEntry(scene_id="ch01-sc01"),
            m.TimelineEntry(scene_id="ch01-sc02", concurrent_with="ch01-sc01"),
        ]
    ),
    m.Character(
        id="char-mira",
        name="Mira",
        aliases=["M"],
        voice_card=m.VoiceCard(verbal_tics=["well"]),
        knowledge=[m.KnowledgeEntry(fact_id="fact-0001", since="ch01-sc01")],
        relationships=[m.Relationship(other_id="char-tom", kind="sibling")],
    ),
    m.Location(id="loc-saltmere", name="Saltmere", rules=["fog at dawn"], parent_location="loc-coast"),
    m.LoreItem(id="lore-frost", name="Frost rule", rules=["glass shatters"]),
    m.Item(id="obj-brass-key", name="brass key", kind="key", promise_ids=["prom-001"]),
    m.Job(
        job_id="job-20261009-120000-abcd",
        project_id="p",
        stage="draft",
        status="running",
        input_hashes={"card": "x"},
    ),
    m.ContextManifest(
        job_id="job-20261009-120000-abcd",
        model="llama3.2:1b",
        context_limit=4096,
        budget_input=2800,
        output_reserved=1200,
        total_tokens=2000,
        sections=[m.ManifestSection(name="spec", tokens=100, disposition="included", prefix_hash="ab")],
    ),
]

PLAIN_EXAMPLES = [
    m.Fact(
        id="fact-0001",
        subject_id="obj-brass-key",
        predicate="held_by",
        value="char-tom",
        valid_from="ch04-sc01",
        source_ref=m.SourceRef(scene_id="ch04-sc01", version=2, char_span=(10, 40)),
        origin="extracted",
        confidence=0.8,
    ),
    m.Finding(
        id="find-0001",
        scene_id="ch01-sc01",
        category="continuity",
        severity="high",
        claim="c",
        detector="rule:single_valued",
        evidence=[m.Evidence(scene_id="ch01-sc01", version=1, span=(1, 5), quote="grey")],
    ),
    m.RevisionProposal(
        id="rev-0001", scene_id="ch01-sc01", base_version=1, instruction="fix", scores_before={"total": 3.0}
    ),
    m.AuditEvent(actor="user", operation="card.save", target="card:ch01-sc01", via="--yes"),
    m.SceneVersionMeta(scene_id="ch01-sc01", version=1, word_count=12, job_id="job-20261009-120000-abcd"),
]


def test_every_schema_record_has_an_example():
    assert {type(e) for e in EXAMPLES} == set(m.SCHEMA_RECORDS)


@pytest.mark.parametrize("record", EXAMPLES, ids=lambda r: type(r).__name__)
def test_schema_records_round_trip(record: SchemaRecord):
    text = record.to_json()
    assert f'"schema": "{record.SCHEMA}"' in text
    assert type(record).model_validate_json(text) == record


@pytest.mark.parametrize("record", PLAIN_EXAMPLES, ids=lambda r: type(r).__name__)
def test_plain_records_round_trip(record):
    assert type(record).model_validate_json(record.model_dump_json()) == record


def test_wrong_schema_version_rejected():
    data = EXAMPLES[5].model_dump(mode="json", by_alias=True)
    data["schema"] = "inkforge/chapter-plan@99"
    with pytest.raises(ValidationError, match="does not match"):
        m.ChapterPlan.model_validate(data)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        m.ChapterPlan.model_validate({"id": "ch01", "title": "t", "purpose": "p", "surprise": 1})


@pytest.mark.parametrize(
    "bad",
    [
        lambda: m.SceneCard(id="ch02-sc01", chapter_id="ch01", order=0),  # wrong chapter prefix
        lambda: m.SceneCard(id="ch01-sc01", chapter_id="ch01", order=0, value_charge="up"),
        lambda: m.Finding(
            id="find-0001",
            scene_id="ch01-sc01",
            category="voice",
            severity="low",
            claim="c",
            detector="critic:voice",
        ),
        lambda: m.Outline(
            chapters=[
                m.OutlineChapter(id="ch01", order=0, title="a"),
                m.OutlineChapter(id="ch01", order=1, title="b"),
            ]
        ),
        lambda: m.Outline(
            chapters=[
                m.OutlineChapter(id="ch01", order=0, title="a"),
                m.OutlineChapter(id="ch02", order=0, title="b"),
            ]
        ),
        lambda: m.Outline(chapters=[m.OutlineChapter(id="ch01", order=0, act_id="act9", title="a")]),
        lambda: m.Timeline(
            entries=[m.TimelineEntry(scene_id="ch01-sc01"), m.TimelineEntry(scene_id="ch01-sc01")]
        ),
        lambda: m.Timeline(entries=[m.TimelineEntry(scene_id="ch01-sc01", concurrent_with="ch09-sc09")]),
        lambda: m.PromiseRegistry(
            promises=[
                m.PlotPromise(id="prom-001", description="a"),
                m.PlotPromise(id="prom-001", description="b"),
            ]
        ),
        lambda: m.Fact(
            id="fact-0001", subject_id="ch01-sc01", predicate="p", value="v"
        ),  # subject must be a codex id
        lambda: m.Character(id="loc-x", name="x"),
        lambda: m.SourceRef(scene_id="ch01-sc01", version=1, char_span=(5, 2)),
    ],
)
def test_invariants(bad):
    with pytest.raises(ValidationError):
        bad()


def test_finding_without_evidence_ok_as_hypothesis():
    f = m.Finding(
        id="find-0001",
        scene_id="ch01-sc01",
        category="voice",
        severity="low",
        claim="c",
        detector="critic:voice",
        is_hypothesis=True,
    )
    assert f.evidence == []


def test_timeline_story_index_with_concurrency():
    t = m.Timeline(
        entries=[
            m.TimelineEntry(scene_id="ch02-sc01"),
            m.TimelineEntry(scene_id="ch01-sc01"),
            m.TimelineEntry(scene_id="ch01-sc02", concurrent_with="ch01-sc01"),
        ]
    )
    idx = t.story_index()
    assert idx["ch02-sc01"] < idx["ch01-sc01"] == idx["ch01-sc02"]


# ---------------------------------------------------------------- ids


@pytest.mark.parametrize(
    "text,slug",
    [
        ("The Glass Orchard", "the-glass-orchard"),
        ("Émilie’s Café!", "emilies-cafe"),
        ("  --  ", "untitled"),
        ("東京", "untitled"),
    ],
)
def test_slugify(text, slug):
    assert ids.slugify(text) == slug


def test_codex_ids_unique_and_valid():
    a = ids.codex_id("character", "Mira Vale", [])
    b = ids.codex_id("character", "Mira Vale", [a])
    c = ids.codex_id("character", "Mira Vale", [a, b])
    assert (a, b, c) == ("char-mira-vale", "char-mira-vale-2", "char-mira-vale-3")
    assert all(ids.is_valid("character", x) for x in (a, b, c))
    assert ids.codex_id("item", "Brass Key", []) == "obj-brass-key"


def test_chapter_and_scene_ids_never_reuse_gaps():
    assert ids.chapter_id([]) == "ch01"
    assert ids.chapter_id(["ch01", "ch03"]) == "ch04"  # ch02 deleted earlier: not reused
    assert ids.scene_id("ch03", ["ch03-sc01", "ch03-sc04", "ch02-sc09"]) == "ch03-sc05"
    assert ids.scene_id("ch05", []) == "ch05-sc01"


def test_counter_and_job_ids():
    assert ids.counter_id("fact", 7) == "fact-0007"
    assert ids.counter_id("promise", 12) == "prom-012"
    assert ids.is_valid("job", ids.job_id())


# ---------------------------------------------------------------- forms


def test_forms():
    forms = load_forms()
    assert {"novel", "short-story", "screenplay", "tv-episode", "stage-play"} <= set(forms)
    assert get_form("novel").unit == "chapter" and get_form("screenplay").unit == "sequence"
    assert get_form("novel").available and not get_form("screenplay").available
    assert (
        "fdx" not in get_form("screenplay").exporters and "fdx" in get_form("screenplay").optional_exporters
    )
    with pytest.raises(KeyError):
        get_form("comic")
