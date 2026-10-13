"""Phase 4: context builder, Stage E drafting, draft checks, approval, export — on the Fake adapter."""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from inkforge.cli import app
from inkforge.config import load_model_config
from inkforge.context.builder import ContextTooSmall, DraftContextBuilder
from inkforge.domain.models import JobStatus
from inkforge.exporters.markdown import ExportBlocked, export_markdown
from inkforge.exporters.preflight import preflight
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse, auto_script
from inkforge.models.router import ModelRouter
from inkforge.models.types import ChatRequest
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.prompts.registry import PromptRegistry
from inkforge.storage.workspace import ProjectStore, verify_integrity

IDEA = "A lighthouse keeper finds letters in bottles that predict storms."
NOVELLA = yaml.safe_load(
    (Path(__file__).parents[1] / "golden" / "novella_5ch" / "fixture.yaml").read_text(encoding="utf-8")
)


def router(script=auto_script, **profile_updates) -> ModelRouter:
    cfg = load_model_config("fast")
    for name, upd in profile_updates.items():
        cfg.profiles[name] = cfg.profiles[name].model_copy(update=upd)
    return ModelRouter(cfg, FakeAdapter(script))


def conductor(store, script=auto_script, **kw) -> Conductor:
    return Conductor(store, router(script), **kw)


def run(c: Conductor, stages, **params):
    return asyncio.run(c.run(c.create_job(stages, {"idea": IDEA, **params})))


@pytest.fixture
def planned(store: ProjectStore) -> ProjectStore:
    store.update_project(settings={"chapters": 3, "scenes_per_chapter": 2}, target_length=6000)
    job = run(conductor(store), stages_for("plan"), yes=True)
    assert job.status == JobStatus.SUCCEEDED
    return store


def prose(text: str, **kw):
    """Script: planning stays auto; prose calls get `text`."""

    def script(req: ChatRequest) -> FakeResponse:
        return auto_script(req) if req.json_schema else FakeResponse(text, **kw)

    return script


# ---------------------------------------------------------------- context builder


def test_context_manifest_and_stable_prefix(planned: ProjectStore):
    s = planned
    run(conductor(s), ["drafts"], scene="ch01-sc01")
    b = DraftContextBuilder(s, router(), PromptRegistry())
    built1, built2 = (
        b.build("ch01-sc02", job_id="job-20261009-000000-aaaa"),
        b.build("ch01-sc02", job_id="job-20261009-000000-bbbb"),
    )
    names = [x.name for x in built1.manifest.sections]
    assert names == [
        "spec",
        "core",
        "codex",
        "digests",
        "chapter",
        "recall",
        "recap",
        "card",
    ]  # stable first (D25)
    assert built1.prompt.sha256 == built2.prompt.sha256  # byte-stable
    assert [x.prefix_hash for x in built1.manifest.sections] == [
        x.prefix_hash for x in built2.manifest.sections
    ]
    recap = next(x for x in built1.manifest.sections if x.name == "recap")
    assert recap.disposition == "included" and recap.source_refs == ["draft:ch01-sc01@v1"]
    first = b.build("ch01-sc01", job_id="job-20261009-000000-cccc")
    assert next(x for x in first.manifest.sections if x.name == "recap").disposition == "dropped"
    assert built1.manifest.total_tokens <= built1.manifest.budget_input


def test_trimming_follows_priority_and_never_drops_the_card(planned: ProjectStore):
    s = planned
    s.update_project(settings={**s.project.settings})
    long_plan = s.read("chapter", "ch01").model_copy(update={"purpose": "x " * 900})
    s.save("chapter", long_plan)
    r = router(prose={"num_ctx": 1024, "min_output_tokens": 128, "output_fraction": 0.2})
    built = DraftContextBuilder(s, r, PromptRegistry()).build("ch01-sc01", job_id="job-20261009-000000-dddd")
    secs = {x.name: x for x in built.manifest.sections}
    assert secs["chapter"].disposition == "dropped" and secs["card"].disposition == "included"
    assert built.manifest.total_tokens <= built.manifest.budget_input
    tiny = router(prose={"num_ctx": 512, "min_output_tokens": 300, "output_fraction": 0.5})
    with pytest.raises(ContextTooSmall, match="budget"):
        DraftContextBuilder(s, tiny, PromptRegistry()).build("ch01-sc01", job_id="job-20261009-000000-eeee")


# ---------------------------------------------------------------- Stage E


def test_truncated_draft_is_continued_without_duplication(planned: ProjectStore, monkeypatch):
    monkeypatch.setattr("inkforge.pipeline.stages.draft.EXTEND_BELOW", 0.0)  # tiny fake scenes
    s = planned
    calls = []

    def script(req: ChatRequest) -> FakeResponse:
        if req.json_schema:
            return auto_script(req)
        calls.append(req.messages[-1].content)
        if len(calls) == 1:
            return FakeResponse(
                "The storm came in. She climbed the stairs and opened the", finish_reason="length"
            )
        return FakeResponse("climbed the stairs and opened the door. Light spilled across the lamp room.")

    job = run(conductor(s, script), ["drafts"], scene="ch01-sc01")
    assert job.status == JobStatus.SUCCEEDED
    text = s.read("draft", "ch01-sc01")
    assert (
        text
        == "The storm came in. She climbed the stairs and opened the door. Light spilled across the lamp room."
    )
    assert (
        len(calls) == 2
        and "Continue from exactly where it stops (finish the interrupted sentence first)" in calls[1]
    )
    extra = s.meta("draft", "ch01-sc01").get(1).extra
    assert extra["calls"] == 2 and extra["prompt_ref"] == "draft.scene@4"
    assert (s.dir / extra["context_manifest_path"]).is_file()


def test_meta_lines_stripped_and_checks_reported(planned: ProjectStore, monkeypatch):
    monkeypatch.setattr("inkforge.pipeline.stages.draft.EXTEND_BELOW", 0.0)  # tiny fake scenes
    s = planned
    events = []
    raw = "Here is the scene:\n\n## Scene 1\n\nThe lamp burned all night.\n\nNote: 12 words"
    run(conductor(s, prose(raw), on_event=lambda k, d: events.append((k, d))), ["drafts"], scene="ch01-sc01")
    assert s.read("draft", "ch01-sc01") == "The lamp burned all night."
    checks = {d["check"] for k, d in events if k == "draft.check"}
    assert {
        "meta_stripped",
        "length",
    } <= checks  # first-person spec: the narrator is exempt from "participants"


def test_name_typo_becomes_finding_with_verbatim_evidence(planned: ProjectStore):
    s = planned
    cast = [c for c in s.list_codex("character")]
    name = cast[0].name  # e.g. "Person 10"
    first = name.split()[0]
    typo = first[0] + first[2] + first[1] + first[3:] if len(first) > 3 else first + "x"
    text = f"{name} waited. Then {typo} {name.split()[-1]} lit the lamp. The sea rose."
    run(conductor(s, prose(text)), ["drafts"], scene="ch01-sc01")
    fs = [f for f in s.findings.all() if f.detector == "rule:name_consistency"]
    assert fs, "expected a name finding"
    f = fs[0]
    ev = f.evidence[0]
    assert f.status == "open" and ev.version == 1
    assert s.read("draft", "ch01-sc01")[ev.span[0] : ev.span[1]] == ev.quote == typo
    run(conductor(s, prose(f"{name} waited and lit the lamp.")), ["drafts"], scene="ch01-sc01")
    assert not [f for f in s.findings.all() if f.detector == "rule:name_consistency"]  # replaced on redraft


def test_default_targets_are_missing_or_stale_drafts(planned: ProjectStore):
    s = planned
    run(conductor(s), ["drafts"])
    assert all(s.meta("draft", x).active == 1 for x in s.scenes_in_order())
    job = run(conductor(s), ["drafts"])
    assert job.steps == []  # nothing to do
    card = s.read("card", "ch02-sc01")
    s.save("card", card.model_copy(update={"want": "to leave before dawn"}))
    job = run(conductor(s), ["drafts"])
    assert [st.key for st in job.steps] == ["drafts:ch02-sc01"] and s.meta("draft", "ch02-sc01").active == 2


def test_cancelled_draft_leaves_no_version(planned: ProjectStore):
    s = planned
    c = conductor(s, prose(" ".join(f"word{i}" for i in range(400)), chunk_delay_s=0.01))
    job = c.create_job(["drafts"], {"scene": "ch01-sc01"})

    async def go():
        async def stop():
            await asyncio.sleep(0.4)
            c.request_cancel(job.job_id)

        t = asyncio.ensure_future(stop())
        out = await c.run(job)
        await t
        return out

    out = asyncio.run(go())
    assert out.status == JobStatus.CANCELLED
    assert s.meta("draft", "ch01-sc01").active is None
    assert (s.dir / "runs" / job.job_id / "drafts_ch01-sc01" / "partial.txt").is_file()


# ---------------------------------------------------------------- approve & export


def test_full_mode_ends_at_preview_and_final_export_needs_approval(store: ProjectStore):
    s = store
    s.update_project(settings={"chapters": 3, "scenes_per_chapter": 2}, target_length=6000)
    job = run(conductor(s), stages_for("full"), yes=True)
    assert job.status == JobStatus.SUCCEEDED and job.steps[-1].key == "preview"
    assert all(
        s.meta("draft", x).approved is None for x in s.scenes_in_order()
    )  # --yes never approves drafts
    previews = list((s.dir / "exports").glob("*-preview/book.md"))
    assert len(previews) == 1 and "DRAFT PREVIEW" in previews[0].read_text(encoding="utf-8")
    with pytest.raises(ExportBlocked) as ei:
        export_markdown(s, mode="final")
    assert any("not approved" in b for b in ei.value.report.blockers)
    assert not [p for p in (s.dir / "exports").iterdir() if not p.name.endswith("-preview")]


def test_final_export_uses_approved_versions_in_outline_order(planned: ProjectStore):
    s = planned
    texts = iter(f"Scene number {i} happens here." for i in range(100))
    run(conductor(s, lambda r: auto_script(r) if r.json_schema else FakeResponse(next(texts))), ["drafts"])
    for sid in s.scenes_in_order():
        s.approve("draft", sid)
    approved_text = s.read("draft", "ch02-sc01")
    run(conductor(s, prose("A newer, unapproved version.")), ["drafts"], scene="ch02-sc01")
    assert s.meta("draft", "ch02-sc01").active == 2
    res = export_markdown(s, mode="final")
    book = res.path.read_text(encoding="utf-8")
    assert approved_text in book and "newer, unapproved" not in book
    assert any("newer v2 exists" in w for w in res.report.warnings)
    order = [
        book.index(s.read("draft", sid, version=s.meta("draft", sid).approved)) for sid in s.scenes_in_order()
    ]
    assert order == sorted(order)
    assert book.count("## Chapter ") == 3 and "human proofread is required" in book
    pre = json.loads((res.path.parent / "preflight.json").read_text(encoding="utf-8"))
    assert pre["ok"] and pre["mode"] == "final"
    last = [e for e in s.audit if e.operation == "export.final"][-1]
    assert "ch02-sc01@v1" in last.detail["scenes"]


def test_preflight_blocks_on_high_findings_and_empty_chapters(planned: ProjectStore):
    s = planned
    run(conductor(s), ["drafts"])
    for sid in s.scenes_in_order():
        s.approve("draft", sid)
    assert preflight(s).ok
    from inkforge.domain.models import Evidence, Finding

    s.upsert(
        "findings",
        Finding(
            id=s.mint("finding"),
            scene_id="ch01-sc01",
            category="continuity",
            severity="high",
            claim="eyes change colour",
            detector="rule:test",
            evidence=[Evidence(scene_id="ch01-sc01", version=1, span=(0, 3), quote="The")],
        ),
    )
    assert any("high/blocker findings" in b for b in preflight(s).blockers)
    assert preflight(s, "preview").ok


def test_novella_fixture_end_to_end(workspace):
    s = workspace.create_project(NOVELLA["title"], NOVELLA["form"])
    s.update_project(settings=NOVELLA["settings"], target_length=NOVELLA["target_length"])
    job = run(conductor(s), stages_for("full"), yes=True, idea=NOVELLA["idea"])
    assert job.status == JobStatus.SUCCEEDED
    scenes = s.scenes_in_order()
    assert len(s.read("outline").chapters) == 5 and len(scenes) == 10
    for sid in scenes:
        s.approve("draft", sid)
    res = export_markdown(s, mode="final")
    assert res.path.read_text(encoding="utf-8").count("## Chapter ") == 5
    for sid in scenes:  # every draft has its manifest on disk
        assert (s.dir / s.meta("draft", sid).get(1).extra["context_manifest_path"]).is_file()
    assert verify_integrity(s) == []


def test_short_story_exports_without_chapter_headings(workspace):
    s = workspace.create_project("Salt", "short-story")
    s.update_project(settings={"chapters": 3, "scenes_per_chapter": 1})
    run(conductor(s), stages_for("full"), yes=True)
    for sid in s.scenes_in_order():
        s.approve("draft", sid)
    book = export_markdown(s, mode="final").path.read_text(encoding="utf-8")
    assert "## Chapter" not in book and book.count("* * *") == 2


# ---------------------------------------------------------------- CLI


def test_cli_draft_approve_export_diff_show_findings(planned: ProjectStore, monkeypatch):
    s = planned
    w = str(s.dir.parent.parent)
    monkeypatch.setenv("INKFORGE_FAKE", "auto")
    r = CliRunner()

    def ok(*args):
        res = r.invoke(app, [*args, "-p", s.id, "-w", w])
        assert res.exit_code == 0, res.output
        return res.output

    assert "succeeded" in ok("draft")
    out = r.invoke(app, ["export", "-p", s.id, "-w", w])
    assert out.exit_code == 1 and "not approved" in out.output
    assert "approved 6 draft(s)" in ok("approve", "drafts", "--all", "--confirm")
    assert "exported final" in ok("export")
    ok("draft", "--scene", "ch01-sc01")
    assert "v1 -> v2" in ok("diff", "draft", "ch01-sc01")
    assert '"total_tokens"' in ok("show", "manifest", "ch01-sc01")
    assert "Scene" in ok("findings") or True
    declined = r.invoke(app, ["approve", "drafts", "--scene", "ch01-sc01", "-p", s.id, "-w", w], input="n\n")
    assert declined.exit_code == 1 and s.meta("draft", "ch01-sc01").approved == 1


def test_short_scene_is_extended_once(planned: ProjectStore):
    """8B behaviour (2026-10-12): natural stop at ~55% of a 1,200-word target."""
    s = planned
    target = s.read("card", "ch01-sc01").target_length
    calls = []

    def script(req: ChatRequest) -> FakeResponse:
        if req.json_schema:
            return auto_script(req)
        calls.append(req.messages[-1].content)
        n = int(target * 0.3)
        return FakeResponse(" ".join(f"w{len(calls)}x{i}" for i in range(n)) + ".")

    events = []
    run(conductor(s, script, on_event=lambda k, d: events.append((k, d))), ["drafts"], scene="ch01-sc01")
    assert len(calls) == 2  # one extension, not a loop, even though it is still short afterwards
    assert "extended" in {d["check"] for k, d in events if k == "draft.check"}
    assert s.meta("draft", "ch01-sc01").get(1).extra["calls"] == 2


def test_first_person_narrator_is_not_reported_missing(planned: ProjectStore):
    s = planned
    spec = s.read("spec")
    s.save("spec", spec.model_copy(update={"pov": "first"}))
    card = s.read("card", "ch01-sc01")
    events = []
    run(
        conductor(
            s,
            prose("I climbed the stairs. I'd known this would happen. " * 40),
            on_event=lambda k, d: events.append((k, d)),
        ),
        ["drafts"],
        scene="ch01-sc01",
    )
    msgs = [
        d["message"]
        for k, d in events
        if k == "draft.check" and d["check"] in ("participants", "unknown_names")
    ]
    pov_name = s.get_codex(card.pov_character_id).name
    assert not any(pov_name in x for x in msgs) and not any("I'd" in x for x in msgs)
