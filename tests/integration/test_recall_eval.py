"""Phase 5 exit: recall@context on the golden memory book, trimming under pressure, rebuildability."""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio
import json
from pathlib import Path

import pytest
from tests.support.memory_book import Planted, build_memory_book
from typer.testing import CliRunner

from inkforge.cli import app
from inkforge.config import load_model_config
from inkforge.context.builder import DraftContextBuilder
from inkforge.domain.models import JobStatus
from inkforge.memory.digests import DigestStore
from inkforge.memory.service import MemoryService
from inkforge.models.adapters.fake import FakeAdapter, auto_script
from inkforge.models.router import ModelRouter
from inkforge.models.tokens import TokenCounter, TokenizerCache
from inkforge.pipeline.conductor import Conductor
from inkforge.prompts.registry import PromptRegistry
from inkforge.storage.workspace import ProjectStore, Workspace

JOB = "job-20261012-000000-aaaa"


def router(script=auto_script, **prose) -> ModelRouter:
    cfg = load_model_config()
    if prose:
        cfg.profiles["prose"] = cfg.profiles["prose"].model_copy(update=prose)
    cache = TokenizerCache(Path.home() / "InkForge" / "cache")
    return ModelRouter(cfg, FakeAdapter(script), tokens=TokenCounter(cache, cfg.runtime.tokenizer_family))


@pytest.fixture(scope="module")
def memory_book(tmp_path_factory) -> tuple[ProjectStore, list[Planted]]:
    store = Workspace(tmp_path_factory.mktemp("membook") / "ws").create_project("Memory Book")
    plan = build_memory_book(store)
    asyncio.run(MemoryService(store, router()).update(use_model=False))
    return store, plan


def in_context(built, p: Planted, store: ProjectStore) -> bool:
    if p.sentence in built.prompt.text():
        return True  # the passage itself (RECALL or recap)
    if p.canon:  # or its canon line in CORE
        name = store.get_codex(p.entity).name
        want = p.canon[0].replace("_", " ")
        return any(
            name in ln.split(" — ")[0] and want in ln for ln in built.sections["core"].text.split("\n")
        )
    return False


def test_recall_at_context_meets_the_exit_target(memory_book):
    store, plan = memory_book
    b = DraftContextBuilder(store, router(), PromptRegistry())
    hits, tokens = 0, []
    for p in plan:
        built = b.build(p.needed_in, job_id=JOB)
        hits += in_context(built, p, store)
        tokens.append(built.manifest.total_tokens)
        assert built.manifest.total_tokens <= built.manifest.budget_input
    recall = hits / len(plan)
    print(
        f"recall@context {hits}/{len(plan)} = {recall:.0%}; mean prompt {sum(tokens) // len(tokens)} tokens"
    )
    assert recall >= 0.90  # docs/10 Phase 5 exit criterion


def test_naive_last_three_scenes_baseline_is_far_worse(memory_book):
    store, plan = memory_book
    order = store.scenes_in_order()
    hits = 0
    for p in plan:
        i = order.index(p.needed_in)
        hits += any(p.sentence in store.read("draft", x) for x in order[max(0, i - 3) : i])
    assert hits / len(plan) < 0.30


def test_memory_off_loses_the_facts(memory_book):
    store, plan = memory_book
    b = DraftContextBuilder(store, router(), PromptRegistry())
    hits = sum(in_context(b.build(p.needed_in, job_id=JOB, memory=False), p, store) for p in plan)
    assert hits <= 2  # only what the previous-scene recap happens to carry


def test_tight_budget_trims_by_priority_and_reports_it(memory_book):
    store, plan = memory_book
    tight = router(num_ctx=3072, min_output_tokens=900, output_fraction=0.3)
    target = plan[-1].needed_in
    built = DraftContextBuilder(store, tight, PromptRegistry()).build(target, job_id=JOB)
    secs = {x.name: x for x in built.manifest.sections}
    assert built.manifest.total_tokens <= built.manifest.budget_input
    assert secs["card"].disposition == "included" and secs["core"].disposition == "included"  # never trimmed
    assert secs["recall"].disposition in ("trimmed", "dropped") and secs["recall"].reason  # first thing to go
    full = DraftContextBuilder(store, router(), PromptRegistry()).build(target, job_id=JOB)
    full_recall = next(x for x in full.manifest.sections if x.name == "recall")
    assert len(secs["recall"].source_refs) < len(full_recall.source_refs)


def test_recalled_passages_are_citable(memory_book):
    store, plan = memory_book
    built = DraftContextBuilder(store, router(), PromptRegistry()).build(plan[0].needed_in, job_id=JOB)
    recall = next(x for x in built.manifest.sections if x.name == "recall")
    assert recall.source_refs
    for ref in recall.source_refs:  # draft:<scene>@v<version>#<start>-<end>
        scene, rest = ref.removeprefix("draft:").split("@v")
        version, span = rest.split("#")
        a, z = map(int, span.split("-"))
        assert store.read("draft", scene, version=int(version))[a:z] in built.prompt.text()


def test_memory_rebuild_reproduces_digests_and_recall(memory_book):
    store, _ = memory_book
    svc, ds = MemoryService(store, router()), DigestStore(store)
    before = {(k, t): ds.get(k, t).summary for k, t in ds.all_targets()}
    chunks = svc.recall.stats()
    asyncio.run(svc.rebuild(use_model=False))
    assert {(k, t): ds.get(k, t).summary for k, t in ds.all_targets()} == before
    assert svc.recall.stats() == chunks and svc.status().up_to_date


def test_drafting_writes_digests_and_later_scenes_see_them(store: ProjectStore):
    store.update_project(settings={"chapters": 3, "scenes_per_chapter": 2}, target_length=6000)
    c = Conductor(store, router())
    job = asyncio.run(
        c.run(
            c.create_job(
                ["spec", "design", "chapters", "cards", "drafts"], {"idea": "Bottled letters.", "yes": True}
            )
        )
    )
    assert job.status == JobStatus.SUCCEEDED
    ds = DigestStore(store)
    assert (
        ds.stale() == []
        and ds.get("scene", "ch01-sc01").method == "model"
        and ds.get("act", "act1") is not None
    )
    assert any(r.startswith("digest:scene:") for r in job.steps[-1].output_refs)
    extra = store.meta("draft", "ch03-sc02").get(1).extra
    assert extra["prompt_ref"] == "draft.scene@4"
    manifest = json.loads((store.dir / extra["context_manifest_path"]).read_text(encoding="utf-8"))
    digests = next(x for x in manifest["sections"] if x["name"] == "digests")
    assert digests["disposition"] == "included" and "digest:scene:ch03-sc01" in digests["source_refs"]
    assert any(r.startswith(("digest:act:", "digest:chapter:")) for r in digests["source_refs"])


def test_memory_cli(memory_book):
    store, _ = memory_book
    w = ["-p", store.id, "-w", str(store.dir.parent.parent)]
    r = CliRunner()
    out = r.invoke(app, ["memory", "status", *w])
    assert out.exit_code == 0 and "memory is up to date" in out.output and "CORE as of" in out.output
    store.save_draft("ch10-sc03", "A rewritten final scene about the brass key.")
    store.approve("draft", "ch10-sc03")
    out = r.invoke(app, ["memory", *w])
    assert "out of date" in out.output and "scene:ch10-sc03" in out.output
    out = r.invoke(app, ["memory", "update", "--extract", *w])
    assert out.exit_code == 0 and "scene:ch10-sc03 (extract" in out.output
    assert "memory is up to date" in r.invoke(app, ["memory", *w]).output
    assert r.invoke(app, ["memory", "explode", *w]).exit_code == 2
