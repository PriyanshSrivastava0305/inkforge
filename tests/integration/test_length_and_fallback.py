"""8B findings (2026-10-13): long scenes come out short → ask for more; projects drafted before memory
existed have no stored group digests → compose them from scene extracts."""
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


@pytest.fixture
def drafted(store: ProjectStore) -> ProjectStore:
    store.update_project(
        settings={"chapters": 3, "scenes_per_chapter": 2, "memory": False}, target_length=6000
    )
    c = Conductor(store, router())
    job = asyncio.run(
        c.run(c.create_job([*stages_for("plan"), "drafts"], {"idea": "Bottled letters.", "yes": True}))
    )
    assert job.status == JobStatus.SUCCEEDED
    return store


def test_default_profile_asks_for_more_on_long_scenes():
    assert load_model_config().profiles["prose"].long_scene_factor == 1.4


def test_long_scene_factor_changes_the_request_not_the_target(drafted: ProjectStore):
    s = drafted
    card = s.read("card", "ch02-sc01")
    assert card.target_length == 1000
    b = DraftContextBuilder(s, router(long_scene_factor=1.4, long_scene_words=700), PromptRegistry())
    built = b.build("ch02-sc01", job_id=JOB)
    assert "in about 1400 words" in built.prompt.text()
    assert built.max_tokens == min(b.budget()[1], int(1400 * 1.33 * 1.3))
    plain = DraftContextBuilder(s, router(long_scene_factor=1.0), PromptRegistry()).build(
        "ch02-sc01", job_id=JOB
    )
    assert "in about 1000 words" in plain.prompt.text()


def test_short_scenes_are_not_inflated(drafted: ProjectStore):
    s = drafted
    card = s.read("card", "ch02-sc01")
    s.save("card", card.model_copy(update={"target_length": 450}))
    built = DraftContextBuilder(s, router(long_scene_factor=1.4), PromptRegistry()).build(
        "ch02-sc01", job_id=JOB
    )
    assert "in about 450 words" in built.prompt.text()


def test_group_digests_fall_back_to_scene_extracts_when_none_are_stored(drafted: ProjectStore):
    """A project drafted with memory off: no digest files at all, yet later scenes still get the story so far."""
    s = drafted
    assert not (s.dir / "memory" / "digests").exists()
    built = DraftContextBuilder(s, router(), PromptRegistry()).build("ch03-sc02", job_id=JOB)
    digests = built.sections["digests"]
    assert digests.disposition == "included" and digests.text
    assert "digest:scene:ch03-sc01" in digests.sources  # this chapter's earlier scene
    assert any(
        r.startswith(("digest:act:", "digest:chapter:")) for r in digests.sources
    )  # earlier chapters/acts
    assert DigestStore(s).stale()  # nothing was written: fallbacks are computed on the fly
