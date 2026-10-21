"""Phase 6 slice 2: fact extraction with verified quotes, the review queue, canon ops, rule findings.

Exit criteria covered here: every finding has verifiable evidence; no canon change without confirmation.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio
import json

import pytest
from tests.conftest import populate

from inkforge.config import load_model_config
from inkforge.domain import models as m
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse
from inkforge.models.router import ModelRouter
from inkforge.quality import canon
from inkforge.quality.check import ContinuityChecker
from inkforge.quality.extract import FactExtractor, locate_quote
from inkforge.storage.workspace import ProjectStore

TEXT = (
    "Mira Vale set the brass key on the sill and looked up. Her blue eyes were tired, and her left hand "
    "ached from the glazing.\n\n“You kept it,” said Tom. He stood in the doorway of the lighthouse, "
    "rain on his coat.\n\nShe pressed the brass key into Tom's palm. “It was never mine to keep.”"
)


def facts(*rows: tuple[str, str, str, str]) -> FakeResponse:
    return FakeResponse(
        json.dumps(
            {"facts": [dict(zip(("subject", "predicate", "value", "quote"), r, strict=True)) for r in rows]}
        )
    )


def router(*responses: FakeResponse) -> ModelRouter:
    return ModelRouter(load_model_config(), FakeAdapter(list(responses)))


@pytest.fixture
def s(store: ProjectStore) -> ProjectStore:
    populate(store)
    grey = store.facts.all()[0]  # char-mira eye_color grey, from ch01-sc01
    store.upsert("facts", grey.model_copy(update={"canon_status": m.CanonStatus.CANON}))
    store.save_draft("ch01-sc02", TEXT)
    return store


def extract(s: ProjectStore, *rows, scene: str = "ch01-sc02"):
    return asyncio.run(FactExtractor(s, router(facts(*rows))).extract(scene))


# ---------------------------------------------------------------- quote verification
def test_quote_is_located_exactly_despite_quote_marks_and_spacing():
    start, end, ratio = locate_quote(  # type: ignore[misc]
        TEXT, '"You kept it," said   Tom.'
    )
    assert ratio == 1.0 and TEXT[start:end] == "You kept it,” said Tom"


def test_near_miss_quote_is_accepted_and_mapped_to_the_real_words():
    found = locate_quote(TEXT, "She pressed the brass key in to Tom's palm")
    assert found is not None and found[2] >= 0.95
    assert "pressed the brass key into Tom" in TEXT[found[0] : found[1]]


@pytest.mark.parametrize(
    "quote",
    [
        "Mira's eyes were green as bottle glass",
        "the key",
        "She pressed the silver locket into June's waiting palm",
    ],
)
def test_invented_or_trivial_quotes_are_not_found(quote):
    assert locate_quote(TEXT, quote) is None


# ---------------------------------------------------------------- extraction
def test_hallucinated_evidence_is_discarded(s):
    r = extract(s, ("char-mira", "hair", "red", "Her red hair was tied back with twine"))
    assert not r.observations and not r.candidates
    assert r.discarded[0].reason == "quote is not in the scene"


def test_a_real_quote_cannot_carry_an_invented_value(s):
    r = extract(s, ("char-mira", "eye_color", "green", "Her blue eyes were tired"))
    assert not r.observations and "not in the quoted words" in r.discarded[0].reason


def test_names_resolve_to_ids_and_unknown_entities_are_dropped(s):
    r = extract(
        s,
        ("obj-brass-key", "held_by", "Tommy", "She pressed the brass key into Tom's palm"),
        ("char-tom", "location_at", "the lighthouse", "He stood in the doorway of the lighthouse"),
        ("char-tom", "location_at", "the harbour office", "He stood in the doorway of the lighthouse"),
    )
    assert [(o.subject_id, o.predicate, o.value) for o in r.observations] == [
        ("obj-brass-key", "held_by", "char-tom"),
        ("char-tom", "location_at", "loc-lighthouse"),
    ]
    assert "not a known loc entity" in r.discarded[0].reason


def test_state_facts_are_queued_with_their_source_and_events_are_not(s):
    r = extract(
        s,
        ("char-mira", "handedness", "left", "her left hand ached from the glazing"),
        ("char-tom", "active", "speaks to Mira", "“You kept it,” said Tom"),
    )
    assert [o.predicate for o in r.observations] == [
        "handedness",
        "active",
        "location_at",
    ]  # presence derived
    assert [f.predicate for f in r.candidates] == ["handedness"]
    queued = s.review_queue.all()
    assert len(queued) == 1
    f = queued[0]
    assert (
        f.canon_status == m.CanonStatus.CANDIDATE and f.origin == "extracted" and f.valid_from == "ch01-sc02"
    )
    assert TEXT[f.source_ref.char_span[0] : f.source_ref.char_span[1]] == f.quote  # evidence is checkable


def test_extraction_never_writes_canon(s):
    before = s.facts.all()
    extract(s, ("char-mira", "handedness", "left", "her left hand ached from the glazing"))
    assert s.facts.all() == before


def test_what_canon_already_says_is_not_queued_again(s):
    canon.accept(
        s,
        extract(s, ("char-mira", "handedness", "left", "her left hand ached from the glazing"))
        .candidates[0]
        .id,
    )
    again = extract(s, ("char-mira", "handedness", "left", "her left hand ached from the glazing"))
    assert again.observations and not again.candidates and not canon.pending(s)


def test_re_extraction_replaces_the_scenes_pending_candidates_and_respects_rejections(s):
    first = extract(
        s,
        ("char-mira", "handedness", "left", "her left hand ached from the glazing"),
        ("char-tom", "location_at", "loc-lighthouse", "He stood in the doorway of the lighthouse"),
    )
    canon.reject(s, first.candidates[0].id)
    second = extract(
        s,
        ("char-mira", "handedness", "left", "her left hand ached from the glazing"),
        ("char-tom", "location_at", "loc-lighthouse", "He stood in the doorway of the lighthouse"),
    )
    assert [f.predicate for f in second.candidates] == ["location_at"]
    assert [f.predicate for f in canon.pending(s)] == ["location_at"]


def test_dream_scenes_feed_no_candidates_and_unreliable_ones_are_low_confidence(s):
    row = ("char-mira", "handedness", "left", "her left hand ached from the glazing")
    card = s.read("card", "ch01-sc02")
    s.save("card", card.model_copy(update={"temporal_mode": m.TemporalMode.DREAM}))
    assert not extract(s, row).candidates
    s.save("card", card.model_copy(update={"temporal_mode": m.TemporalMode.UNRELIABLE}))
    assert extract(s, row).candidates[0].confidence <= 0.5


def test_a_failed_model_call_extracts_nothing_and_says_so(s):
    bad = [FakeResponse("not json")] * 3
    r = asyncio.run(FactExtractor(s, router(*bad)).extract("ch01-sc02"))
    assert r.error and not r.observations and not canon.pending(s)


# ---------------------------------------------------------------- canon operations
def test_accepting_a_contradiction_is_refused_unless_forced(s):
    s.save_draft("ch02-sc01", TEXT)
    r = extract(s, ("char-mira", "eye_color", "blue", "Her blue eyes were tired"), scene="ch02-sc01")
    got = canon.accept(s, r.candidates[0].id)
    assert not got.ok and got.conflicts[0].rule == "single_valued"
    assert [f.value for f in s.facts.all() if f.predicate == "eye_color"] == ["grey"]
    assert canon.accept(s, r.candidates[0].id, force=True).ok


def test_accepting_a_handover_closes_the_previous_holder(s):
    s.upsert(
        "facts",
        m.Fact(
            id=s.mint("fact"),
            subject_id="obj-brass-key",
            predicate="held_by",
            value="char-mira",
            valid_from="ch01-sc01",
            canon_status=m.CanonStatus.CANON,
        ),
    )
    r = extract(s, ("obj-brass-key", "held_by", "char-tom", "She pressed the brass key into Tom's palm"))
    got = canon.accept(s, r.candidates[0].id)
    assert got.ok and len(got.closed) == 1
    old = s.facts.get(got.closed[0])
    assert old.value == "char-mira" and old.valid_to == "ch01-sc02"
    assert s.review_queue.get(got.fact.id) is None


def test_bulk_accept_takes_only_low_risk_candidates(s):
    extract(
        s,
        ("char-mira", "handedness", "left", "her left hand ached from the glazing"),
        ("char-tom", "location_at", "loc-lighthouse", "He stood in the doorway of the lighthouse"),
    )
    assert [f.predicate for f in canon.accept_low_risk(s)] == ["location_at"]
    assert [f.predicate for f in canon.pending(s)] == ["handedness"]


def test_only_pending_candidates_can_be_decided(s):
    with pytest.raises(canon.CanonError):
        canon.accept(s, "fact-9999")


# ---------------------------------------------------------------- findings
def check(s: ProjectStore, *rows, scene: str = "ch02-sc01"):
    return asyncio.run(ContinuityChecker(s, router(facts(*rows))).check(scene))


@pytest.fixture
def later(s: ProjectStore) -> ProjectStore:
    s.save_draft("ch02-sc01", TEXT)
    return s


def test_a_contradiction_becomes_a_finding_that_cites_the_scenes_own_words(later):
    r = check(later, ("char-mira", "eye_color", "blue", "Her blue eyes were tired"))
    (f,) = r.findings
    assert f.category == "continuity" and f.severity == "high" and f.detector == "rule:canon_single_valued"
    assert f.conflicting_fact_id and "grey" in f.claim and not f.is_hypothesis
    ev = f.evidence[0]
    text = later.read("draft", "ch02-sc01", version=ev.version)
    assert text[ev.span[0] : ev.span[1]] == ev.quote == "Her blue eyes were tired"
    assert later.findings.get(f.id) == f


def test_a_finding_also_cites_where_the_canon_fact_was_established(later):
    made = extract(later, ("char-mira", "handedness", "left", "her left hand ached from the glazing"))
    canon.accept(later, made.candidates[0].id)
    later.save_draft("ch02-sc01", "Mira signed the ledger with her right hand and said nothing.")
    r = check(later, ("char-mira", "handedness", "right", "signed the ledger with her right hand"))
    scenes = [e.scene_id for e in r.findings[0].evidence]
    assert scenes == ["ch02-sc01", "ch01-sc02"] and r.findings[0].evidence[1].quote.startswith(
        "her left hand"
    )


def test_rechecking_keeps_the_same_finding_and_marks_it_fixed_once_gone(later):
    row = ("char-mira", "eye_color", "blue", "Her blue eyes were tired")
    first = check(later, row).findings[0]
    assert check(later, row).findings[0].id == first.id
    later.save_draft("ch02-sc01", TEXT.replace("blue", "grey"))
    r = check(later, ("char-mira", "eye_color", "grey", "Her grey eyes were tired"))
    assert not r.findings and r.fixed == [first.id]
    assert later.findings.get(first.id).status == "fixed"


def test_a_finding_marked_intentional_is_not_raised_again(later):
    row = ("char-mira", "eye_color", "blue", "Her blue eyes were tired")
    f = check(later, row).findings[0]
    later.upsert("findings", f.model_copy(update={"status": "intentional"}))
    assert not check(later, row).findings
    assert later.findings.get(f.id).status == "intentional"


def test_a_failed_extraction_leaves_earlier_findings_alone(later):
    f = check(later, ("char-mira", "eye_color", "blue", "Her blue eyes were tired")).findings[0]
    bad = [FakeResponse("not json")] * 3
    r = asyncio.run(ContinuityChecker(later, router(*bad)).check("ch02-sc01"))
    assert r.extraction.error and later.findings.get(f.id).status == "open"


# ---------------------------------------------------------------- value grounding (found on llama3.2:1b)
@pytest.mark.parametrize(
    ("predicate", "value", "quoted", "ok"),
    [
        ("age", "21", "june, now twenty-one, folded her hands", True),
        ("age", "6", "june, a child of six, watched", True),
        ("age", "34", "june's fingers closed over it. when she looked up", False),
        ("date_of_event", "1892-10-14", "it was dated 14 october in his cramped hand", True),
        ("date_of_event", "1892-10-14", "it was dated 10 october in his cramped hand", False),
        ("status", "dead", "june found her father's body in the glasshouse", True),
        ("condition", "intact", "the silver pear glitters, untouched", True),
        ("condition", "destroyed", "the silver pear glitters, untouched", False),
        ("eye_color", "blue", "she met mira's blue eyes", True),
        ("hair", "red", "he was tired after the long road", False),
    ],
)
def test_literal_values_must_be_visible_in_the_quote(predicate, value, quoted, ok):
    from inkforge.quality.extract import grounded

    assert grounded(predicate, value, quoted) is ok


def test_whole_paragraph_quotes_are_rejected(s):
    long_quote = TEXT.split("\n\n")[0]  # 30-odd words is fine; the whole scene is not
    r = extract(s, ("char-mira", "handedness", "left", TEXT.replace("\n\n", " ")))
    assert r.discarded and "too long" in r.discarded[0].reason
    assert extract(s, ("char-mira", "handedness", "left", long_quote)).observations


# ---------------------------------------------------------------- 8B findings (2026-10-15)
def test_an_actor_is_placed_where_the_card_says_the_scene_happens(s):
    r = extract(s, ("char-tom", "active", "speaks to Mira", "“You kept it,” said Tom"))
    placed = [o for o in r.observations if o.predicate == "location_at"]
    assert [(o.subject_id, o.value) for o in placed] == [("char-tom", "loc-lighthouse")]
    assert not r.candidates  # derived, so checked but never proposed as canon


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (
            ("char-mira", "has_item", "obj-brass-key", "Her blue eyes were tired"),
            "does not name obj-brass-key",
        ),
        (
            ("obj-brass-key", "held_by", "char-tom", "He stood in the doorway of the lighthouse"),
            "does not name",
        ),
        (("char-tom", "age", "char-tom", "He stood in the doorway of the lighthouse"), "not a number"),
    ],
)
def test_a_quote_must_be_about_what_the_fact_is_about(s, row, why):
    r = extract(s, row)
    assert not r.observations and why in r.discarded[0].reason


def test_model_wording_is_mapped_to_the_controlled_values(s):
    s.save_draft("ch01-sc02", "The brass key lay whole on the sill, untouched since spring.")
    r = extract(s, ("obj-brass-key", "condition", "whole", "The brass key lay whole on the sill"))
    assert [(o.predicate, o.value) for o in r.observations] == [("condition", "intact")]


def test_a_secret_is_mentioned_only_if_the_quote_says_it(s):
    s.update_project(settings={"secrets": {"fact-key-copy": "Tom had a copy of the brass key made"}})
    s.upsert(
        "facts",
        m.Fact(
            id=s.mint("fact"),
            subject_id="char-tom",
            predicate="knows_fact",
            value="fact-key-copy",
            valid_from="ch01-sc01",
            canon_status=m.CanonStatus.CANON,
        ),
    )
    s.save_draft("ch01-sc02", "“I know about the copy of the key,” Mira said. “I lost this when I was nine.”")
    r = extract(
        s,
        ("char-mira", "mentions_fact", "fact-key-copy", "I know about the copy of the key"),
        ("char-mira", "mentions_fact", "fact-key-copy", "I lost this when I was nine"),
    )
    assert [o.quote for o in r.observations if o.predicate == "mentions_fact"] == [
        "I know about the copy of the key"
    ]
    assert "does not say what" in r.discarded[0].reason


def test_departures_need_a_named_place_and_a_stated_day(s):
    s.save_codex(m.Location(id="loc-carrow", name="Carrow"))
    card = s.read("card", "ch01-sc02")
    s.save("card", card.model_copy(update={"time": m.SceneTime(story_datetime="1892-10-14")}))
    s.save_draft("ch01-sc02", "“I left Carrow only that morning,” Tom said, “and I have not stopped since.”")
    r = extract(
        s,
        ("char-tom", "departs_on", "loc-carrow, 1892-10-14", "I left Carrow only that morning"),
        ("char-tom", "departs_on", "Carrow, 1892-10-11", "I left Carrow only that morning"),
    )
    assert [o.value for o in r.observations if o.predicate == "departs_on"] == ["loc-carrow@1892-10-14"]
    assert "does not give the date" in r.discarded[0].reason
