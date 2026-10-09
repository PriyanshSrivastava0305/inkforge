"""The whole mini-book pipeline (router + registry + recorder + bench) on the Fake adapter — no model."""

import asyncio
import json
from pathlib import Path

from inkforge.bench import run_bench
from inkforge.config import load_model_config
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse
from inkforge.models.router import ModelRouter
from inkforge.models.types import ChatRequest

SPEC = {
    "logline": "A keeper reads tomorrow's storms in her own hand.",
    "genre": "gothic",
    "tone": "brooding",
    "pov": "third-limited",
    "tense": "past",
    "themes": ["fate", "memory"],
    "characters": [
        {"name": "Ailsa Reid", "role": "keeper", "want": "the truth", "voice": "terse"},
        {"name": "Tom Reid", "role": "brother", "want": "to leave", "voice": "wry"},
    ],
}
CHAPTER = {
    "title": "Arrival",
    "purpose": "meet the keeper",
    "conflict": "storm vs duty",
    "turning_point": "first bottle",
    "outcome": "she reads it",
}
CARD = {
    "pov_character_id": "char-ailsa-reid",
    "participants": ["char-ailsa-reid", "char-tom-reid"],
    "location": "lamp room",
    "want": "to read the letter",
    "obstacle": "Tom mocks her",
    "change": "doubt to dread",
    "value_charge": "+ -> -",
    "concrete_event": "she finds the bottle",
    "exit_condition": "the storm arrives",
}
SCENE = "Ailsa climbed the stairs. The bottle was green and cold. Tom laughed at her from the gallery."


def script(req: ChatRequest) -> FakeResponse:
    title = (req.json_schema or {}).get("title")
    if title == "BookSpec":
        return FakeResponse(json.dumps(SPEC))
    if title == "Outline":
        return FakeResponse(json.dumps({"chapters": [CHAPTER] * 3}))
    if title == "SceneCards":
        # first cards call names an unknown id once → exercises the allowed-id repair path
        bad = (
            not any(m.role == "assistant" for m in req.messages) and "CHAPTER 1:" in req.messages[-1].content
        )
        card = {**CARD, "pov_character_id": "char-nobody"} if bad else CARD
        return FakeResponse(json.dumps({"cards": [card, CARD]}))
    if title == "Critique":
        return FakeResponse(
            json.dumps(
                {
                    "findings": [
                        {
                            "quote": "The bottle was green and cold.",
                            "category": "prose",
                            "severity": "low",
                            "problem": "Flat sensory detail.",
                            "suggestion": "Name the kind of glass.",
                        },
                        {
                            "quote": "Tom laughed at her from the gallery",
                            "category": "voice",
                            "severity": "med",
                            "problem": "Tom's mockery is unmotivated here.",
                            "suggestion": "Seed his doubt earlier.",
                        },
                    ]
                }
            )
        )
    if "Continue directly" in req.messages[-1].content:
        return FakeResponse(" She opened it.")
    return FakeResponse(SCENE, finish_reason="length" if "scene 1)" in req.messages[-1].content else "stop")


def test_bench_end_to_end_on_fake(tmp_path: Path):
    router = ModelRouter(load_model_config("fast"), FakeAdapter(script))
    report = asyncio.run(
        run_bench(router, profile_set="fast", out_root=tmp_path / "out", report_dir=tmp_path / "docs")
    )
    text = report.read_text(encoding="utf-8")
    assert "| Structured calls valid first try | 5/6 |" in text  # the planted bad id needed one repair
    assert "| Structured calls valid after repair | 6/6 |" in text
    assert "| Critique quotes found in scene | 2/2 |" in text
    assert "`cards.generate@1`" in text and "char-nobody" in text
    run_dir = next((tmp_path / "out").iterdir()) / "runs"
    assert {p.name for p in run_dir.iterdir()} >= {
        "spec",
        "outline",
        "cards_ch1",
        "draft_ch1_sc1",
        "critique",
    }
    draft = (run_dir.parent / "draft_ch1_sc1.md").read_text(encoding="utf-8")
    assert draft.endswith("She opened it.")  # continuation after finish_reason=length was joined
    meta = json.loads((run_dir / "cards_ch1" / "meta.json").read_text(encoding="utf-8"))
    assert meta["prompt_ref"] == "cards.generate@1" and len(meta["attempts"]) == 2
