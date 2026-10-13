"""Phase 6 exit criterion (rules half): the canon conflict rules scored on the golden continuity book.

25 planted contradictions and 10 decoys (things that look wrong but are not: a flashback, a dream, an
unreliable account, an on-page handover). Target: recall >= 80%, decoy false positives <= 20%.
Observations are given by the fixture; extracting them from prose is a later slice.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

from collections import Counter

import pytest
from tests.support.continuity_book import Case, build_continuity_book, load_yaml

from inkforge.quality.rules import Observation, RuleHit, World, check_promises, check_scene
from inkforge.storage.workspace import ProjectStore


@pytest.fixture
def book(store: ProjectStore) -> tuple[World, list[Case], list[Case]]:
    contradictions, decoys = build_continuity_book(store)
    return World(store), contradictions, decoys


def run(world: World, case: Case) -> list[RuleHit]:
    hits = check_scene(world, case.scene, case.observations) if case.scene else []
    hits += [h for h in check_promises(world, case.observations) if h.scene_id == case.scene]
    return [h for h in hits if not h.is_hypothesis]


def test_recall_on_planted_contradictions(book):
    world, contradictions, _ = book
    missed = [c.id for c in contradictions if not run(world, c)]
    recall = 1 - len(missed) / len(contradictions)
    assert recall >= 0.8, f"recall {recall:.0%}; missed {missed}"
    assert not missed, f"regression: previously 25/25, now missing {missed}"


def test_each_contradiction_trips_the_rule_it_was_planted_for(book):
    world, contradictions, _ = book
    wrong = {
        c.id: [h.rule for h in run(world, c)]
        for c in contradictions
        if c.rule not in [h.rule for h in run(world, c)]
    }
    assert not wrong


def test_every_rule_type_is_exercised(book):
    world, contradictions, _ = book
    seen = Counter(h.rule for c in contradictions for h in run(world, c))
    assert set(seen) == {
        "single_valued",
        "temporal",
        "knowledge",
        "presence",
        "object_state",
        "age_date",
        "promise",
        "travel",
    }


def test_decoy_false_positives_stay_under_the_limit(book):
    world, _, decoys = book
    flagged = [d.id for d in decoys if run(world, d)]
    assert len(flagged) / len(decoys) <= 0.2, flagged
    # d09 ("sea-coloured" vs blue) needs judgement, not string comparison: left for model adjudication
    assert flagged == ["d09"]


def test_unreliable_account_is_a_low_severity_hypothesis_not_a_finding(book):
    world, _, _ = book
    assert world.mode("ch05-sc04") == "unreliable"
    said = Observation(
        "ch05-sc04", "char-elias", "active", "was seen at the window", "He was at the window, I swear it."
    )
    hits = check_scene(
        world, "ch05-sc04", [said]
    )  # Elias is dead by then; in a linear scene this is a finding
    assert hits and all(h.is_hypothesis and h.severity == "low" for h in hits)


def test_every_hit_carries_its_evidence(book):
    world, contradictions, _ = book
    for c in contradictions:
        for h in run(world, c):
            assert h.claim
            if h.scene_id is not None:
                assert h.observation is not None and h.observation.quote  # the planted sentence


def test_the_clean_book_trips_nothing(book):
    """Each scene's own established facts, replayed as observations, must not conflict with canon."""
    world, _, _ = book
    outline, _ = load_yaml()
    hits = []
    for sc in outline["scenes"]:
        obs = [Observation(sc["id"], a, b, str(c)) for a, b, c in sc.get("establishes", [])]
        hits += [(sc["id"], h.rule, h.claim) for h in check_scene(world, sc["id"], obs)]
    assert hits == []
