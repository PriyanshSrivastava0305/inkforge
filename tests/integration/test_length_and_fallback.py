"""8B findings (2026-10-13).

- The 8B writes one scene arc of ~750-850 words whatever length is requested (782 words when asked for
  1,680), so Stage D plans scenes at the prose profile's natural size instead of asking for more.
- Projects drafted before memory existed have no stored group digests: compose them from scene extracts.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio

import pytest

from inkforge.config import load_model_config
from inkforge.context.builder import DraftContextBuilder
from inkforge.domain.models import JobStatus
from inkforge.memory.digests import DigestStore
from inkforge.models.adapters.fake import FakeAdapter, auto_script
from inkforge.models.router import ModelRouter
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.prompts.registry import PromptRegistry
from inkforge.storage.workspace import ProjectStore

JOB = "job-20261013-000000-aaaa"


def router(**prose) -> ModelRouter:
    cfg = load_model_config()
    cfg.profiles["prose"] = cfg.profiles["prose"].model_copy(update=prose)
    return ModelRouter(cfg, FakeAdapter(auto_script))


def plan(store: ProjectStore, r: ModelRouter, **settings) -> None:
    store.update_project(settings=settings, target_length=settings.pop("_target", 9600))
    c = Conductor(store, r)
    job = asyncio.run(c.run(c.create_job(stages_for("plan"), {"idea": "Bottled letters.", "yes": True})))
    assert job.status == JobStatus.SUCCEEDED, job.error_summary


def test_default_prose_profile_plans_800_word_scenes():
    assert load_model_config().profiles["prose"].scene_words == 800


def test_scene_count_and_targets_follow_the_models_natural_scene_length(store: ProjectStore):
    # 9,600 words over 4 chapters = 2,400 per chapter → 3 scenes of 800 with the default profile
    plan(store, router(), chapters=4)
    scenes = [x for x in store.scenes_in_order() if x.startswith("ch01-")]
    assert len(scenes) == 3
    assert {store.read("card", x).target_length for x in scenes} == {800}


def test_a_model_that_writes_longer_scenes_gets_fewer_of_them(store: ProjectStore):
    plan(store, router(scene_words=1200), chapters=4)
    scenes = [x for x in store.scenes_in_order() if x.startswith("ch01-")]
    assert len(scenes) == 2 and store.read("card", scenes[0]).target_length == 1200


def test_explicit_scenes_per_chapter_still_wins(store: ProjectStore):
    plan(store, router(), chapters=4, scenes_per_chapter=2)
    assert len([x for x in store.scenes_in_order() if x.startswith("ch01-")]) == 2


def test_prompt_asks_for_the_cards_own_target(store: ProjectStore):
    plan(store, router(), chapters=4)
    built = DraftContextBuilder(store, router(), PromptRegistry()).build("ch01-sc01", job_id=JOB)
    assert "in about 800 words" in built.prompt.text()


@pytest.fixture
def drafted_without_memory(store: ProjectStore) -> ProjectStore:
    store.update_project(
        settings={"chapters": 3, "scenes_per_chapter": 2, "memory": False}, target_length=6000
    )
    c = Conductor(store, router())
    job = asyncio.run(
        c.run(c.create_job([*stages_for("plan"), "drafts"], {"idea": "Bottled letters.", "yes": True}))
    )
    assert job.status == JobStatus.SUCCEEDED
    return store


def test_group_digests_fall_back_to_scene_extracts_when_none_are_stored(drafted_without_memory: ProjectStore):
    """A project drafted with memory off: no digest files at all, yet later scenes still get the story so far."""
    s = drafted_without_memory
    assert not (s.dir / "memory" / "digests").exists()
    built = DraftContextBuilder(s, router(), PromptRegistry()).build("ch03-sc02", job_id=JOB)
    digests = built.sections["digests"]
    assert digests.disposition == "included" and digests.text
    assert "digest:scene:ch03-sc01" in digests.sources  # this chapter's earlier scene
    assert any(
        r.startswith(("digest:act:", "digest:chapter:")) for r in digests.sources
    )  # earlier chapters/acts
    assert DigestStore(s).stale()  # nothing was written: fallbacks are computed on the fly
