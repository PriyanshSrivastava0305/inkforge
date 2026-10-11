# pyright: reportArgumentType=false, reportIndexIssue=false, reportOperatorIssue=false
"""Planning pipeline (Stages A–D) + Conductor on the Fake adapter."""

import asyncio
import json
from pathlib import Path

import pytest

from inkforge.config import load_model_config
from inkforge.domain.common import Staleness
from inkforge.domain.models import CodexStatus, JobStatus, Location
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse, SchemaExample, auto_script
from inkforge.models.router import ModelRouter
from inkforge.models.types import ChatRequest
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.pipeline.stage import gate_satisfied
from inkforge.storage.workspace import ProjectStore

IDEA = "A lighthouse keeper finds letters in bottles that predict storms."


@pytest.fixture
def plan_store(store: ProjectStore) -> ProjectStore:
    store.update_project(settings={"chapters": 4, "scenes_per_chapter": 2})
    return store


def conductor(store: ProjectStore, script=auto_script, **kw) -> Conductor:
    return Conductor(store, ModelRouter(load_model_config("fast"), FakeAdapter(script)), **kw)


def run(c: Conductor, stages, **params):
    return asyncio.run(c.run(c.create_job(stages, {"idea": IDEA, **params})))


# ---------------------------------------------------------------- whole mode


def test_plan_with_yes_end_to_end(plan_store: ProjectStore):
    s = plan_store
    job = run(conductor(s), stages_for("plan"), yes=True)
    assert job.status == JobStatus.SUCCEEDED, job.error_summary
    assert [st.key for st in job.steps] == [
        "spec",
        "design",
        "chapters:act1",
        "chapters:act2",
        "chapters:act3",
        "cards:ch01",
        "cards:ch02",
        "cards:ch03",
        "cards:ch04",
    ]
    assert s.read("outline").ordered_chapter_ids() == ["ch01", "ch02", "ch03", "ch04"]
    assert [c.act_id for c in sorted(s.read("outline").chapters, key=lambda c: c.order)] == [
        "act1",
        "act2",
        "act2",
        "act3",
    ]
    scenes = s.scenes_in_order()
    assert len(scenes) == 8 and sorted(e.scene_id for e in s.read("timeline").entries) == sorted(scenes)
    # --yes: planning gates approved and audited as such; candidates NOT accepted (D23)
    yes = {e.target for e in s.audit if e.via == "--yes"}
    assert {"spec", "design", "outline", "chapter:ch01"} <= yes
    assert (
        s.meta("card", "ch01-sc01").approved is None
    )  # scene_cards gate is off by default: nothing to answer
    assert all(e.codex_status == CodexStatus.CANDIDATE for e in s.list_codex())
    assert not any(e.operation.startswith("draft") for e in s.audit)  # nothing beyond planning
    # run records exist for every step
    run_dir = s.dir / "runs" / job.job_id
    assert (run_dir / "cards_ch01" / "prompt.txt").is_file() and (run_dir / "events.jsonl").is_file()


def test_gates_stop_the_job_until_approved(plan_store: ProjectStore):
    s = plan_store
    c = conductor(s)
    job = run(c, stages_for("plan"))
    assert (job.status, job.awaiting) == (JobStatus.AWAITING_APPROVAL, "spec")
    assert asyncio.run(c.resume(job.job_id)).awaiting == "spec"  # resuming without approving changes nothing
    for gate, kind in (("spec", "spec"), ("design", "design")):
        s.approve(kind)
        job = asyncio.run(c.resume(job.job_id))
        assert job.status == JobStatus.AWAITING_APPROVAL, gate
    assert job.awaiting == "outline"
    for kind, target in [("outline", None), *(("chapter", cid) for cid in s.chapter_ids())]:
        s.approve(kind, target)
    job = asyncio.run(c.resume(job.job_id))
    assert job.status == JobStatus.SUCCEEDED  # cards gate is off by default → nothing more to wait for
    assert sum(1 for st in job.steps if st.status == "done") == 9


def test_standalone_stage_requires_upstream_approval(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), ["spec"])
    job = run(conductor(s), ["design"])
    assert (job.status, job.awaiting) == (JobStatus.AWAITING_APPROVAL, "spec")
    s.approve("spec")
    assert run(conductor(s), ["design"]).status == JobStatus.SUCCEEDED


def test_each_stage_runs_alone_from_saved_inputs(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), stages_for("plan"), yes=True)
    before = s.meta("card", "ch01-sc01").active
    job = run(conductor(s), ["cards"], chapter="ch03")
    assert job.status == JobStatus.SUCCEEDED and [st.key for st in job.steps] == ["cards:ch03"]
    assert s.meta("card", "ch01-sc01").active == before  # other chapters untouched
    assert s.meta("card", "ch03-sc01").active == 2


# ---------------------------------------------------------------- Stage A


def test_author_fields_win_over_model_and_survive_enhance(plan_store: ProjectStore):
    s = plan_store
    events = []
    c = conductor(s, on_event=lambda k, d: events.append((k, d)))
    run(
        c,
        ["spec"],
        author_fields={
            "pov": "third-limited",
            "tense": "present",
            "target_length": 60000,
            "setting_place": "Orkney",
        },
    )
    spec = s.read("spec")
    assert (spec.pov, spec.tense, spec.target_length, spec.setting.place) == (
        "third-limited",
        "present",
        60000,
        "Orkney",
    )
    assert spec.original == IDEA and set(spec.author_fields) >= {
        "pov",
        "tense",
        "target_length",
        "setting_place",
    }
    run(c, ["spec"], enhance=True, idea=None)
    spec2 = s.read("spec")
    assert s.meta("spec").active == 2 and (spec2.pov, spec2.tense, spec2.setting.place) == (
        "third-limited",
        "present",
        "Orkney",
    )
    assert spec2.original == IDEA
    assert any(k == "spec.diff" for k, _ in events)


def test_failed_stage_keeps_last_valid_artifact(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), ["spec"])
    job = run(conductor(s, [FakeResponse("not json")] * 3), ["spec"])
    assert job.status == JobStatus.FAILED and "no valid output" in job.error_summary
    assert s.meta("spec").active == 1
    assert (s.dir / "runs" / job.job_id / "spec" / "raw_output.txt").is_file()


# ---------------------------------------------------------------- Stage B


def test_design_repairs_missing_protagonist(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), ["spec"])
    s.approve("spec")
    calls = {"n": 0}

    def script(req: ChatRequest) -> FakeResponse:
        if (req.json_schema or {}).get("title") != "DesignProposal":
            return auto_script(req)
        calls["n"] += 1
        v = SchemaExample(req.json_schema).build()
        if calls["n"] == 1:
            for ch in v["characters"]:
                ch["role"] = "ally"
        return FakeResponse(json.dumps(v))

    c = conductor(s, script)
    job = run(c, ["design"])
    assert job.status == JobStatus.SUCCEEDED and calls["n"] == 2
    meta = json.loads((s.dir / "runs" / job.job_id / "design" / "meta.json").read_text(encoding="utf-8"))
    assert "protagonist" in meta["attempts"][0]["errors"][0]
    d = s.read("design")
    assert d.promise_ids and all(p.startswith("prom-") for p in d.promise_ids)
    assert {a.id for a in d.acts} == {"act1", "act2", "act3"}


def test_rerunning_design_reuses_cast_and_replaces_unreferenced_promises(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), ["spec", "design"], yes=True)
    cast1, proms1 = sorted(e.id for e in s.list_codex("character")), [p.id for p in s.get_promises().promises]
    run(conductor(s), ["design"])
    assert sorted(e.id for e in s.list_codex("character")) == cast1  # same names → same ids, no duplicates
    proms2 = [p.id for p in s.get_promises().promises]
    assert not set(proms1) & set(proms2) and len(proms2) == len(proms1)


# ---------------------------------------------------------------- Stage C


def test_regenerating_an_act_keeps_chapter_ids_and_stales_only_its_cards(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), stages_for("plan"), yes=True)
    act2 = [c.id for c in s.read("outline").chapters if c.act_id == "act2"]

    def script(req):  # make act2's new plans differ from the old ones
        r = auto_script(req)
        if (req.json_schema or {}).get("title") == "ActPlan":
            r.text = r.text.replace("purpose ", "new purpose ")
        return r

    run(conductor(s, script), ["chapters"], act="act2")
    assert [c.id for c in s.read("outline").chapters if c.act_id == "act2"] == act2
    assert all(s.meta("chapter", c).active == 2 for c in act2)
    assert s.staleness("card", f"{act2[0]}-sc01") == Staleness.STALE
    assert s.staleness("card", "ch01-sc01") == Staleness.FRESH


def test_outline_checks_report_untouched_promises_and_missing_protagonist(plan_store: ProjectStore):
    s = plan_store
    events = []
    run(
        conductor(s, on_event=lambda k, d: events.append((k, d))),
        stages_for("plan", until="chapters"),
        yes=True,
    )
    problems = [d["message"] for k, d in events if k == "problem" and d["stage"] == "chapters"]
    assert any("words/chapter" in p for p in problems)  # 4 chapters for an 80k novel


# ---------------------------------------------------------------- Stage D


def test_flashback_placement_and_value_charge_repair(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), stages_for("plan", until="cards"), yes=True)
    n = {"cards": 0}

    def script(req):
        if (req.json_schema or {}).get("title") != "ChapterScenes":
            return auto_script(req)
        n["cards"] += 1
        v = SchemaExample(req.json_schema).build()
        if n["cards"] == 1:
            v["cards"][0]["value_charge"] = "+ -> +"  # doesn't change → repair
        v["cards"][1]["temporal_mode"] = "flashback"
        v["cards"][1]["happens_after"] = "ch01-sc01"
        v["cards"][0]["location"] = "the Location 1"  # name variant of an existing candidate location
        return FakeResponse(json.dumps(v))

    job = run(conductor(s, script), ["cards"], chapter="ch03")
    assert job.status == JobStatus.SUCCEEDED and n["cards"] == 2
    order = [e.scene_id for e in s.read("timeline").entries]
    assert order.index("ch03-sc02") == order.index("ch01-sc01") + 1  # flashback sits right after its anchor
    assert s.read("card", "ch03-sc02").temporal_mode == "flashback"
    loc_ids = [e.id for e in s.list_codex("location")]
    assert s.read("card", "ch03-sc01").location_id in loc_ids and len(loc_ids) == len(set(loc_ids))
    assert isinstance(s.get_codex(s.read("card", "ch03-sc01").location_id), Location)


def test_fewer_scenes_on_regeneration_archives_the_extras(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), stages_for("plan"), yes=True)
    s.update_project(settings={"chapters": 4, "scenes_per_chapter": 1})
    run(conductor(s), ["cards"], chapter="ch02")
    assert [x for x in s.scene_ids() if x.startswith("ch02")] == ["ch02-sc01"]
    assert "ch02-sc02" not in {e.scene_id for e in s.read("timeline").entries}
    assert list((s.dir / "backups" / "deleted").glob("ch02-sc02-*"))


# ---------------------------------------------------------------- cancel, resume, crash adoption


def test_cancel_file_then_resume(plan_store: ProjectStore):
    s = plan_store

    def slow(req):
        r = auto_script(req)
        r.chunk_delay_s = 0.01
        return r

    c = conductor(s, slow)
    job = c.create_job(stages_for("plan"), {"idea": IDEA, "yes": True})

    async def go():
        async def cancel_later():
            while not any(st.status == "done" for st in c.load_job(job.job_id).steps):
                await asyncio.sleep(0.05)
            c.request_cancel(job.job_id)

        t = asyncio.ensure_future(cancel_later())
        out = await c.run(job)
        await t
        return out

    out = asyncio.run(go())
    assert out.status == JobStatus.CANCELLED
    done_before = sum(1 for st in out.steps if st.status == "done")
    assert 1 <= done_before < 9
    final = asyncio.run(conductor(s).resume(job.job_id))
    assert final.status == JobStatus.SUCCEEDED and sum(1 for st in final.steps if st.status == "done") == 9


def test_resume_adopts_work_persisted_before_a_lost_checkpoint(plan_store: ProjectStore):
    s = plan_store
    c = conductor(s)
    job = run(c, stages_for("plan"), yes=True)
    # simulate a crash between "cards for ch04 written" and "checkpoint saved"
    jf = s.dir / "runs" / job.job_id / "job.json"
    data = json.loads(jf.read_text(encoding="utf-8"))
    data["status"] = "running"
    data["steps"][-1]["status"] = "pending"
    jf.write_text(json.dumps(data), encoding="utf-8")
    before = s.meta("card", "ch04-sc01").active
    fa_calls_before = len(c.router.adapter.calls)  # type: ignore[attr-defined]
    out = asyncio.run(c.resume(job.job_id))
    assert out.status == JobStatus.SUCCEEDED
    assert s.meta("card", "ch04-sc01").active == before  # adopted, not regenerated
    assert len(c.router.adapter.calls) == fa_calls_before  # type: ignore[attr-defined]


def test_only_one_conductor_per_project(plan_store: ProjectStore):
    from inkforge.policy.lock import ProjectLock, ProjectLockedError

    with ProjectLock(plan_store.dir, name=".run.lock"), pytest.raises(ProjectLockedError):
        run(conductor(plan_store), ["spec"])


def test_gate_satisfied_needs_every_artifact(plan_store: ProjectStore):
    s = plan_store
    run(conductor(s), stages_for("plan", until="chapters"), yes=True)
    arts = [("outline", None), *(("chapter", c) for c in s.chapter_ids())]
    assert gate_satisfied(s, arts)
    s.save("chapter", s.read("chapter", "ch02").model_copy(update={"purpose": "edited by hand"}))
    assert not gate_satisfied(s, arts)  # new active version isn't approved yet


def test_edit_cli_saves_user_version_and_reports_staleness(
    plan_store: ProjectStore, tmp_path: Path, monkeypatch
):
    from typer.testing import CliRunner

    from inkforge.cli import app

    s = plan_store
    run(conductor(s), stages_for("plan"), yes=True)
    script = tmp_path / "ed.py"
    script.write_text(
        "import json,sys\np=sys.argv[1]\nd=json.load(open(p,encoding='utf-8'))\nd['purpose']='A sharper purpose'\n"
        "json.dump(d,open(p,'w',encoding='utf-8'))\n",
        encoding="utf-8",
    )
    import sys

    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    res = CliRunner().invoke(app, ["edit", "-p", s.id, "chapter", "ch02", "-w", str(s.dir.parent.parent)])
    assert res.exit_code == 0, res.output
    assert "saved chapter:ch02 v2 (by you)" in res.output and "card:ch02-sc01: fresh → stale" in res.output
    assert s.read("chapter", "ch02").purpose == "A sharper purpose"
    last = [e for e in s.audit if e.operation == "chapter.save"][-1]
    assert last.actor == "user"


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("Act 2: Confrontation", "Confrontation"),
        ("Chapter 3: The Bottle", "The Bottle"),
        ("CHAPTER ONE — Salt", "Salt"),
        ("The Act of Mercy", "The Act of Mercy"),
        ("Part-time Keeper", "Part-time Keeper"),
        ("Setup: Turning Point", "Setup: Turning Point"),
    ],
)
def test_chapter_titles_lose_numbering_labels(raw, clean):
    """Seen on llama3.2:1b (2026-10-09 smoke run); numbering comes from outline order."""
    from inkforge.pipeline.stages.chapters import clean_title

    assert clean_title(raw) == clean


def test_value_charge_normalized():
    from inkforge.pipeline.stages.cards import charge_changes, normalize_charge

    assert normalize_charge("+   ->-") == "+ -> -" and normalize_charge("- → +") == "- -> +"
    assert [charge_changes(x) for x in ("+ -> -", "- → +", "+ -> +", "+->-")] == [True, True, False, True]


def test_persistent_duplicate_purposes_become_a_warning_not_a_failure(plan_store: ProjectStore):
    """Real llama3.2:1b behaviour (2026-10-10): repeated a duplicate chapter purpose after every repair."""
    s = plan_store
    run(conductor(s), stages_for("plan", until="design"), yes=True)
    events = []

    def script(req):
        if (req.json_schema or {}).get("title") != "ActPlan":
            return auto_script(req)
        v = SchemaExample(req.json_schema).build()
        for ch in v["chapters"]:
            ch["purpose"] = "the visions grow stronger"
        return FakeResponse(json.dumps(v))

    job = run(conductor(s, script, on_event=lambda k, d: events.append((k, d))), ["chapters"])
    assert job.status == JobStatus.SUCCEEDED
    warns = [d["message"] for k, d in events if k == "problem" and d.get("target") == "act2"]
    assert any("share the purpose" in w for w in warns)
