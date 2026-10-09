"""Structural checks for the golden fixtures, so they stay consistent as they grow."""

from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import yaml

GOLDEN = Path(__file__).parents[1] / "golden"
RULES = {
    "single_valued",
    "temporal",
    "knowledge",
    "presence",
    "object_state",
    "age_date",
    "promise",
    "travel",
}


@pytest.fixture(scope="module")
def book() -> dict:
    return yaml.safe_load((GOLDEN / "continuity_book" / "outline.yaml").read_text(encoding="utf-8"))


def _entity_ids(book: dict) -> set[str]:
    return {
        e["id"] for kind in book["entities"].values() if isinstance(kind, list) for e in kind if "id" in e
    }


def _when(s) -> date:
    return date.fromisoformat(str(s)[:10])


def test_counts(book):
    assert len(book["scenes"]) == 30
    assert len(book["contradictions"]) == 25
    assert len(book["decoys"]) == 10


def test_every_rule_type_covered(book):
    rules = Counter(c["rule"] for c in book["contradictions"])
    assert set(rules) == RULES


def test_unique_ids(book):
    ids = (
        [s["id"] for s in book["scenes"]]
        + [c["id"] for c in book["contradictions"]]
        + [d["id"] for d in book["decoys"]]
    )
    assert len(ids) == len(set(ids))


def test_references_resolve(book):
    scene_ids = {s["id"] for s in book["scenes"]}
    entities = _entity_ids(book)
    for s in book["scenes"]:
        assert s["pov"] in entities, s["id"]
        assert s["location"] in entities, s["id"]
        assert set(s["participants"]) <= entities, s["id"]
        assert s["pov"] in s["participants"], s["id"]
        assert set(s.get("items_present", [])) <= entities, s["id"]
        for subject, *_ in s.get("establishes", []):
            assert subject in entities or str(subject).startswith("fact-"), (s["id"], subject)
        if "timeline" in s:
            assert s["timeline"]["before"] in scene_ids
    for c in book["contradictions"] + book["decoys"]:
        assert c["scene"] is None or c["scene"] in scene_ids, c["id"]


def test_non_linear_scenes_are_placed(book):
    for s in book["scenes"]:
        if s.get("temporal_mode") in ("flashback", "flashforward"):
            assert "timeline" in s, f"{s['id']} needs an explicit timeline placement"


def test_linear_scenes_are_chronological(book):
    linear = [s for s in book["scenes"] if s.get("temporal_mode", "linear") in ("linear", "unreliable")]
    whens = [str(s["when"]) for s in linear]
    assert whens == sorted(whens)


def test_promises_have_setups(book):
    scene_ids = {s["id"] for s in book["scenes"]}
    for p in book["promises"]:
        assert p["setup"] in scene_ids
        assert p["payoff"] is None or p["payoff"] in scene_ids


def test_decoy_ages_are_consistent(book):
    chars = {c["id"]: c for c in book["entities"]["characters"]}
    scenes = {s["id"]: s for s in book["scenes"]}

    def age(cid: str, on: date) -> int:
        b = date.fromisoformat(str(chars[cid]["born"]))
        return on.year - b.year - ((on.month, on.day) < (b.month, b.day))

    assert age("char-june", _when(scenes["ch04-sc05"]["when"])) == 6  # decoy d02
    assert age("char-mira", _when(scenes["ch06-sc05"]["when"])) == 35
    assert age("char-mira", _when(scenes["ch01-sc01"]["when"])) == 34
    assert age("char-june", _when(scenes["ch01-sc02"]["when"])) == 19
    assert age("char-elias", _when(scenes["ch01-sc02"]["when"])) == 61
    assert age("char-elias", _when(scenes["ch04-sc05"]["when"])) == 48
