"""Phase 6 CLI: check / revise / polish jobs, the fact review queue, lessons — and that canon changes
only through an explicit author command."""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

import pytest
from tests.conftest import populate
from typer.testing import CliRunner

from inkforge.cli import app
from inkforge.config import load_model_config
from inkforge.context.builder import DraftContextBuilder
from inkforge.domain import models as m
from inkforge.models.adapters.fake import FakeAdapter, auto_script
from inkforge.models.router import ModelRouter
from inkforge.prompts.registry import PromptRegistry
from inkforge.quality import lessons, report
from inkforge.storage.workspace import ProjectStore

SCENE = "ch01-sc02"
TEXT = (
    "Mira climbed to the lamp room. Suddenly the wind changed and the shutters banged.\n\n"
    "“I will not leave the light,” Mira said. “Not tonight.”"
)


@pytest.fixture
def s(store: ProjectStore, monkeypatch) -> ProjectStore:
    monkeypatch.setenv("INKFORGE_FAKE", "auto")
    populate(store)
    store.save_draft(SCENE, TEXT)
    return store


def cli(s: ProjectStore, *args: str, code: int = 0) -> str:
    res = CliRunner().invoke(app, [*args, "-p", s.id, "-w", str(s.dir.parent.parent)])
    assert res.exit_code == code, res.output
    return res.output


def candidate(s: ProjectStore, **kw) -> m.Fact:
    f = m.Fact(id=s.mint("fact"), subject_id="char-mira", predicate="eye_color", value="blue", valid_from=SCENE,
               quote="blue eyes", origin="extracted", **kw)  # fmt: skip
    s.upsert("review_queue", f)
    return f


def test_check_runs_as_a_job_and_writes_a_report(s):
    out = cli(s, "check", "--scene", SCENE)
    assert "succeeded" in out
    r = report.load(s, SCENE)
    assert r is not None and "deterministic" in r.families
    assert any(f.detector == "rule:slop" for f in s.findings.all())
    assert "nothing to do" in cli(s, "check", "--scene", SCENE) or "succeeded" in cli(
        s, "check", "--scene", SCENE
    )


def test_check_only_some_families(s):
    cli(s, "check", "--scene", SCENE, "--only", "deterministic")
    assert report.load(s, SCENE).families == ["deterministic"]


def test_facts_accept_refuses_a_contradiction_without_force(s):
    grey = s.facts.all()[0]
    s.upsert("facts", grey.model_copy(update={"canon_status": m.CanonStatus.CANON}))
    f = candidate(s)
    out = cli(s, "facts", "accept", f.id, code=1)
    assert "contradicts canon" in out and "grey" in out
    assert [x.value for x in s.facts.all() if x.predicate == "eye_color"] == ["grey"]
    assert "is canon" in cli(s, "facts", "accept", f.id, "--force")


def test_facts_reject_and_retcon(s):
    f = candidate(s)
    assert f.id in cli(s, "facts", "list")
    cli(s, "facts", "reject", f.id)
    assert s.review_queue.get(f.id).canon_status == m.CanonStatus.REJECTED
    g = candidate(s, confidence=0.8)
    g2 = g.model_copy(update={"predicate": "handedness", "value": "left", "quote": "left hand"})
    s.upsert("review_queue", g2)
    cli(s, "facts", "accept", g2.id)
    cli(s, "facts", "retcon", g2.id)
    assert s.facts.get(g2.id).canon_status == m.CanonStatus.RETCONNED
    cli(s, "facts", "accept", "fact-9999", code=1)


def test_every_canon_change_is_an_audited_user_action(s):
    f = candidate(s)
    cli(s, "facts", "accept", f.id)
    ops = [e for e in s.audit if e.target == f.id and e.operation.startswith("facts.")]
    assert ops and all(e.actor == "user" for e in ops)


def test_findings_accept_then_revise(s):
    cli(s, "check", "--scene", SCENE, "--only", "deterministic")
    slop = next(f for f in s.findings.all() if f.detector == "rule:slop" and f.scene_id == SCENE)
    cli(s, "findings", "--accept", slop.id)
    assert s.findings.get(slop.id).status == "accepted"
    out = cli(s, "revise", "--scene", SCENE, "--no-rubric", "--attempts", "1")
    assert "succeeded" in out
    after = s.findings.get(slop.id)
    # the fake model writes unrelated prose: the edit is refused and the finding left for the author
    assert after.status == "accepted" and after.resolution_note.startswith("needs author")
    assert s.read("draft", SCENE) == TEXT


def test_polish_needs_a_scope(s):
    assert "needs --chapter or --scene" in cli(s, "polish", code=2)


def test_lessons_are_suggested_added_shown_to_drafting_and_removed(s):
    for i, sid in enumerate(("ch01-sc01", "ch01-sc02", "ch02-sc01")):
        s.upsert("findings", m.Finding(
            id=s.mint("finding"), scene_id=sid, category="prose", severity="low", claim="stock phrase “suddenly”",
            detector="rule:slop", evidence=[m.Evidence(scene_id=sid, version=1, span=(0, 4), quote=f"q{i}xx")],
        ))  # fmt: skip
    out = cli(s, "lessons", "list")
    assert "Avoid the phrase “suddenly”." in out
    assert "lesson-001" in cli(s, "lessons", "add", "Avoid the phrase “suddenly”.")
    assert lessons.load(s).lessons[0].source == "rule:slop:suddenly"
    assert (
        "Avoid the phrase" not in cli(s, "lessons", "list").split("Suggested")[-1]
        if "Suggested" in cli(s, "lessons", "list")
        else True
    )
    router = ModelRouter(load_model_config(), FakeAdapter(auto_script))
    prompt = DraftContextBuilder(s, router, PromptRegistry()).build(
        "ch02-sc01", job_id="job-20261022-000000-aaaa"
    )
    assert "Style rules (from the author):\n- Avoid the phrase “suddenly”." in prompt.prompt.text()
    cli(s, "lessons", "remove", "lesson-001")
    prompt = DraftContextBuilder(s, router, PromptRegistry()).build(
        "ch02-sc01", job_id="job-20261022-000000-aaaa"
    )
    assert "Style rules" not in prompt.prompt.text()
    cli(s, "lessons", "remove", "lesson-001", code=1)
