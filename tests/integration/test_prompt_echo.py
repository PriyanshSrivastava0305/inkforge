"""Prompt echo and near-copies (found on llama3.2:1b, 2026-10-12): the model repeated its context —
digests, the scene card and whole recalled passages — before writing, and paraphrased earlier text."""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio

import pytest

from inkforge.config import load_model_config
from inkforge.domain.models import JobStatus
from inkforge.memory.digests import DigestStore
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse, auto_script
from inkforge.models.router import ModelRouter
from inkforge.models.types import ChatRequest
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.pipeline.text import meta_leaks, near_copies, strip_prompt_echo
from inkforge.storage.workspace import ProjectStore

PROMPT = "\n".join(
    [
        "THE STORY SO FAR",
        "ch01: Ava found a weathered bottle with a yellowed parchment inside and felt uneasy.",
        "",
        "CHAPTER: Confrontation",
        "Purpose: Uncover the truth about her past",
        "",
        "REFERENCE NOTES",
        '[ch01-sc01] "As Ava stepped out onto the weathered boards, the salt air wrapped around her like a shawl."',
        "",
        "SCENE CARD",
        "POV: Ava Morales",
        "Want: to open the box",
    ]
)


def test_strip_prompt_echo_removes_copied_context_but_keeps_prose():
    draft = "\n".join(
        [
            "THE STORY SO FAR",
            "ch01: Ava found a weathered bottle with a yellowed parchment inside and felt uneasy.",
            "",
            "CHAPTER: Confrontation",
            "Purpose: Uncover the truth about her past",
            "",
            "As Ava stepped out onto the weathered boards, the salt air wrapped around her like a shawl.",
            "",
            "Ava lifted the lid. The hinges screamed.",
            "",
            '"Purpose: none," Ethan joked.',
            "",
            "She laughed anyway.",
        ]
    )
    clean, removed = strip_prompt_echo(draft, PROMPT)
    assert (
        clean
        == 'Ava lifted the lid. The hinges screamed.\n\n"Purpose: none," Ethan joked.\n\nShe laughed anyway.'
    )
    assert len(removed) == 5  # header, digest line, two chapter lines, one recalled passage
    assert strip_prompt_echo("Ava lifted the lid.", PROMPT) == ("Ava lifted the lid.", [])


def test_short_lines_and_mid_text_labels_survive():
    """Only long verbatim lines and labels in the echoed head are removed."""
    text = "Ava waited.\n\nPOV: Ava Morales\n\nWant: to open the box"
    clean, removed = strip_prompt_echo(text, PROMPT)
    assert clean == text and removed == []  # after real prose, short label-like lines are left alone
    assert meta_leaks("She paused.\n\nSCENE CARD\nmore") != []  # …but a surviving header is still reported


def test_near_copies_flags_paraphrase_of_shown_text():
    shown = [
        "As Ava stepped out onto the weathered boards, the salt air wrapped around her like a shawl. Gulls wheeled."
    ]
    text = (
        "Ava stepped out onto the weathered boards, the salt air wrapping around her like a shawl. She breathed in.\n\n"
        "Ethan was already at the rail, counting the swells under his breath as if they owed him money."
    )
    hits = near_copies(text, shown)
    assert len(hits) == 1 and hits[0][1] >= 0.8 and hits[0][0].startswith("Ava stepped out")
    assert (
        near_copies(
            text, ["A completely different passage about the Carrow records office and its noon closing."]
        )
        == []
    )


@pytest.fixture
def planned(store: ProjectStore) -> ProjectStore:
    store.update_project(settings={"chapters": 3, "scenes_per_chapter": 2}, target_length=6000)
    c = Conductor(store, ModelRouter(load_model_config("fast"), FakeAdapter(auto_script)))
    job = asyncio.run(c.run(c.create_job(stages_for("plan"), {"idea": "Bottled letters.", "yes": True})))
    assert job.status == JobStatus.SUCCEEDED
    asyncio.run(c.run(c.create_job(["drafts"], {"scene": "ch01-sc01"})))
    return store


def test_echoing_model_yields_clean_draft_and_clean_memory(planned: ProjectStore):
    s = planned
    new_prose = " ".join(f"Sentence {i} of genuinely new prose about the harbour." for i in range(90))

    def script(req: ChatRequest) -> FakeResponse:
        if req.json_schema:
            return auto_script(req)
        user = req.messages[-1].content
        return FakeResponse(
            user.split("Write this scene")[0] + "\n\n" + new_prose
        )  # repeats its whole context first

    events = []
    c = Conductor(
        s,
        ModelRouter(load_model_config("fast"), FakeAdapter(script)),
        on_event=lambda k, d: events.append((k, d)),
    )
    job = asyncio.run(c.run(c.create_job(["drafts"], {"scene": "ch01-sc02"})))
    assert job.status == JobStatus.SUCCEEDED
    draft = s.read("draft", "ch01-sc02")
    assert draft.startswith("Sentence 0 of genuinely new prose")
    for header in ("THE STORY SO FAR", "SCENE CARD", "CHAPTER:", "THE PREVIOUS SCENE ENDED", "POV:"):
        assert header not in draft, header
    echo = [d for k, d in events if k == "draft.check" and d["check"] == "prompt_echo"]
    assert echo and "copied from the prompt" in echo[0]["message"]
    assert not [d for k, d in events if k == "draft.check" and d["check"] == "meta_leak"]
    assert "SCENE CARD" not in DigestStore(s).get("scene", "ch01-sc02").text()
    assert s.meta("draft", "ch01-sc02").get(1).extra["prompt_ref"] == "draft.scene@4"
