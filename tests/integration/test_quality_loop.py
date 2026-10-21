"""Phase 6 agentic layer: Stage F (critique) and Stage G (revise) through the Conductor, on a scripted model.

Exit criteria exercised here: every finding has verifiable evidence; no canon change without
confirmation; a span edit never changes text outside its span; a revision is kept only if it fixes
what it was for without making anything worse.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from tests.conftest import populate

from inkforge.config import load_model_config
from inkforge.domain import models as m
from inkforge.domain.models import JobStatus
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse
from inkforge.models.router import ModelRouter
from inkforge.models.types import ChatRequest
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.quality import report
from inkforge.quality.revision import changed_ranges, outside, windows
from inkforge.storage.workspace import ProjectStore

SCENE = "ch01-sc02"
P1 = "Mira climbed to the lamp room. Her blue eyes stung in the smoke, and the glass hummed under her hand."
P2 = "Suddenly the wind changed. The shutters banged against the frame and the whole tower shook."
P3 = "“I will not leave the light,” Mira said. “Not tonight, not for anyone.”"
TEXT = "\n\n".join([P1, P2, P3])


def fix_all(passage: str, prompt: str) -> str:
    return (
        passage.replace("Suddenly the wind", "The wind")
        .replace("blue", "grey")
        .replace("Not tonight, not for anyone.", "Not tonight.")
    )


@dataclass
class Model:
    """Answers each kind of call the quality loop makes; records what it was asked."""

    adjudication: str = "yes"
    scores: int = 3
    rescored: int | None = None  # score given to revision candidates (None: same as `scores`)
    revise: Callable[[str, str], str] = fix_all
    extraction_fails: bool = False
    calls: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def __call__(self, req: ChatRequest) -> FakeResponse:
        user = req.messages[-1].content
        title = (req.json_schema or {}).get("title")
        self.calls.append(title or "prose")
        self.prompts.append(user)
        if title == "ExtractedFacts":
            if self.extraction_fails:
                return FakeResponse("not json")
            text = _between(user, "SCENE ", '"""')
            rows = []
            if "blue eyes" in text:
                quote = re.search(r"Her blue eyes \w+ in the smoke", text).group()
                rows.append(["char-mira", "eye_color", "blue", quote])
            return _json(
                {
                    "facts": [
                        dict(zip(("subject", "predicate", "value", "quote"), r, strict=True)) for r in rows
                    ]
                }
            )
        if title == "Adjudication":
            return _json(
                {
                    "conflict": self.adjudication,
                    "explanation": "Grey and blue are different colours.",
                    "confidence": 0.9,
                }
            )
        if title == "CraftReview":
            candidate = TEXT not in user  # a revision candidate, not the scene as written
            s = self.rescored if (candidate and self.rescored is not None) else self.scores
            dims = ("goal", "tension", "clarity", "pacing", "sensory", "dialogue", "show_tell", "ending")
            notes = [
                {"quote": "The shutters banged against the frame", "dimension": "sensory",
                 "problem": "Generic noise; nothing specific to this tower.", "suggestion": "Name the sound the iron makes."},
                {"quote": "The gulls screamed over the black water", "dimension": "sensory",
                 "problem": "A made-up line the scene doesn't contain.", "suggestion": "Cut it."},
            ]  # fmt: skip
            return _json({"scores": dict.fromkeys(dims, s), "notes": notes})
        if title == "VoiceReview":
            return _json({"notes": [{"quote": "Not tonight, not for anyone", "problem": "Too declarative for her plain diction.",
                                     "rewrite": "Not tonight."}]})  # fmt: skip
        if title is None:  # prose: a revision
            return FakeResponse(self.revise(_between(user, "PASSAGE\n", '"""'), user))
        raise AssertionError(f"unexpected call {title}")


def _json(v: object) -> FakeResponse:
    return FakeResponse(json.dumps(v))


def _between(text: str, start: str, quote: str) -> str:
    i = text.index(start)
    a = text.index(quote, i) + len(quote)
    return text[a : text.index(quote, a)]


@pytest.fixture
def s(store: ProjectStore) -> ProjectStore:
    populate(store)
    grey = store.facts.all()[0]  # char-mira eye_color grey, from ch01-sc01
    store.upsert("facts", grey.model_copy(update={"canon_status": m.CanonStatus.CANON}))
    store.save_draft(SCENE, TEXT)
    return store


def run(s: ProjectStore, model: Model, mode: str, **params) -> m.Job:
    c = Conductor(s, ModelRouter(load_model_config(), FakeAdapter(model)))
    return asyncio.run(c.run(c.create_job(stages_for(mode), {"scene": SCENE, **params})))


def text_of(s: ProjectStore) -> str:
    return s.read("draft", SCENE)


def by_detector(s: ProjectStore, prefix: str) -> list[m.Finding]:
    return [f for f in s.findings.all() if f.scene_id == SCENE and f.detector.startswith(prefix)]


def events(s: ProjectStore, job: m.Job, kind: str) -> list[dict]:
    lines = s.path(f"runs/{job.job_id}/events.jsonl").read_text(encoding="utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["type"] == kind]


# ================================================================ Stage F
def test_check_raises_evidence_linked_findings_from_every_family(s):
    job = run(s, Model(), "check")
    assert job.status == JobStatus.SUCCEEDED, job.error_summary
    (canon,) = by_detector(s, "rule:canon_")
    assert "grey" in canon.claim and canon.severity == "high"
    assert [f.claim for f in by_detector(s, "rule:slop")] == ["stock phrase “suddenly”"]
    (craft,) = by_detector(s, "critic:craft_")  # the invented quote was dropped
    assert craft.evidence[0].quote == "The shutters banged against the frame"
    (voice,) = by_detector(s, "critic:voice")
    assert voice.affected_entities == ["char-mira"] and "Not tonight." in voice.claim
    text = text_of(s)
    for f in s.findings.all():
        if f.scene_id == SCENE:
            for ev in f.evidence:
                if ev.scene_id == SCENE:
                    assert text[ev.span[0] : ev.span[1]] == ev.quote  # verifiable evidence
    r = report.load(s, SCENE)
    assert r.families == ["deterministic", "continuity", "craft", "voice"] and r.rubric_total == 24
    assert events(s, job, "critique.dropped")[0]["reasons"][0].startswith("quote not in scene")


def test_check_never_changes_canon_or_text(s):
    before_facts, before_text = s.facts.all(), text_of(s)
    run(s, Model(), "check")
    assert s.facts.all() == before_facts and text_of(s) == before_text


def test_an_unchanged_scene_is_not_checked_again_but_an_edited_one_is(s):
    run(s, Model(), "check")
    again = Model()
    job = run(s, again, "check")
    assert job.status == JobStatus.SUCCEEDED and again.calls == []
    s.save_draft(SCENE, TEXT.replace("lamp room", "lantern gallery"))
    third = Model()
    run(s, third, "check")
    assert "CraftReview" in third.calls


def test_a_failed_extraction_is_a_warning_and_the_scene_is_retried_next_time(s):
    job = run(s, Model(extraction_fails=True), "check")
    assert job.status == JobStatus.SUCCEEDED
    assert any("fact extraction failed" in p["message"] for p in events(s, job, "problem"))
    assert report.load(s, SCENE).errors
    retry = Model()
    run(s, retry, "check")
    assert "ExtractedFacts" in retry.calls and by_detector(s, "rule:canon_")


@pytest.mark.parametrize(("verdict", "expect"), [("no", None), ("unclear", True), ("yes", False)])
def test_adjudication_decides_free_text_conflicts(s, verdict, expect):
    run(s, Model(adjudication=verdict), "check")
    found = by_detector(s, "rule:canon_")
    if expect is None:
        assert found == []
    else:
        assert found[0].is_hypothesis is expect


def test_a_narrower_check_runs_only_what_was_asked(s):
    model = Model()
    run(s, model, "check", checks=["deterministic"])
    assert model.calls == [] and by_detector(s, "rule:slop") and not by_detector(s, "critic:")


# ================================================================ Stage G
def accept(s: ProjectStore, prefix: str) -> list[m.Finding]:
    out = []
    for f in by_detector(s, prefix):
        s.upsert("findings", f.model_copy(update={"status": "accepted"}))
        out.append(f)
    return out


def test_a_kept_revision_changes_only_its_passage(s):
    run(s, Model(), "check")
    (slop,) = accept(s, "rule:slop")
    s.approve("draft", SCENE)
    base_version = s.meta("draft", SCENE).active
    job = run(s, Model(), "revise")
    assert job.status == JobStatus.SUCCEEDED, job.error_summary
    new = text_of(s)
    assert new == "\n\n".join([P1, P2.replace("Suddenly the wind", "The wind"), P3])
    meta = s.meta("draft", SCENE)
    assert meta.active == base_version + 1 and meta.approved == base_version  # never auto-approved
    assert s.findings.get(slop.id).status == "fixed"
    (rev,) = s.revisions.all()
    assert (
        rev.decision == "accepted" and rev.candidate_version == meta.active and rev.finding_ids == [slop.id]
    )
    assert s.path(rev.diff_path).is_file()
    base = s.read("draft", SCENE, version=base_version)
    p2 = base.index(P2)
    assert outside(base, new, p2, p2 + len(P2)) == []  # exit criterion: nothing outside the span


def test_a_rewrite_of_the_whole_scene_is_rejected_and_retried_then_left_for_the_author(s):
    run(s, Model(), "check")
    (slop,) = accept(s, "rule:slop")
    before, version = text_of(s), s.meta("draft", SCENE).active
    model = Model(revise=lambda passage, prompt: (TEXT + "\n\n") * 3)
    job = run(s, model, "revise")
    assert job.status == JobStatus.SUCCEEDED
    assert text_of(s) == before and s.meta("draft", SCENE).active == version
    assert model.calls.count("prose") == 2  # max_attempts
    assert "Your previous attempt was rejected" in model.prompts[-1]  # the reason went back to the model
    f = s.findings.get(slop.id)
    assert f.status == "accepted" and f.resolution_note.startswith("needs author: revision failed")
    assert s.revisions.all()[0].decision == "auto-discarded"


def test_a_revision_that_does_not_fix_the_issue_is_not_kept(s):
    run(s, Model(), "check")
    accept(s, "rule:slop")
    job = run(
        s, Model(revise=lambda passage, prompt: passage.replace("whole tower", "entire tower")), "revise"
    )
    assert job.status == JobStatus.SUCCEEDED and text_of(s) == TEXT
    reasons = [e["reason"] for e in events(s, job, "revise.attempt")]
    assert reasons and all(r.startswith("still detected") for r in reasons)


def test_a_revision_that_reads_worse_is_not_kept(s):
    run(s, Model(), "check")
    accept(s, "rule:slop")
    job = run(s, Model(scores=4, rescored=2), "revise")
    assert text_of(s) == TEXT
    assert "reads worse" in events(s, job, "revise.attempt")[-1]["reason"]


def test_a_continuity_fix_is_confirmed_by_re_extracting_facts(s):
    run(s, Model(), "check")
    (c,) = accept(s, "rule:canon_")
    run(s, Model(), "revise")
    assert "grey eyes" in text_of(s) and s.findings.get(c.id).status == "fixed"
    s2 = s.findings.get(c.id)
    assert s2.resolution_note.startswith(f"revised in v{s.meta('draft', SCENE).active}")


def test_a_continuity_edit_that_keeps_the_contradiction_is_rejected(s):
    run(s, Model(), "check")
    accept(s, "rule:canon_")
    job = run(s, Model(revise=lambda passage, prompt: passage.replace("stung in", "watered in")), "revise")
    assert text_of(s) == TEXT
    assert any("still contradicts canon" in (e["reason"] or "") for e in events(s, job, "revise.attempt"))


def test_findings_that_are_not_local_edits_go_to_the_author(s):
    card = s.read("card", SCENE)
    s.save("card", card.model_copy(update={"target_length": 1500}))
    run(s, Model(), "check")
    (length,) = accept(s, "rule:length")
    model = Model()
    run(s, model, "revise")
    assert "prose" not in model.calls
    assert s.findings.get(length.id).resolution_note.startswith("needs author: not a local edit")


def test_polish_fixes_med_and_high_findings_in_specialist_order(s):
    model = Model()
    job = run(s, model, "polish", accept=["high", "med"])
    assert job.status == JobStatus.SUCCEEDED, job.error_summary
    passes = [e["pass"] for e in events(s, job, "revise.pass")]
    assert passes == ["continuity", "voice"]  # slop and craft findings here are low severity
    text = text_of(s)
    assert "grey eyes" in text and "“Not tonight.”" in text and "Suddenly" in text
    assert by_detector(s, "rule:slop")[0].status == "open"  # low: untouched
    assert all(f.status == "fixed" for f in by_detector(s, "rule:canon_") + by_detector(s, "critic:voice"))
    assert [r.decision for r in s.revisions.all()] == ["accepted", "accepted"]
    assert s.meta("draft", SCENE).active == 4  # v2 + one attributable version per kept edit


def test_revision_never_touches_canon(s):
    before = s.facts.all()
    run(s, Model(), "polish", accept=["high", "med"])
    assert s.facts.all() == before


# ================================================================ span guard (pure)
def test_changed_ranges_and_the_span_guard():
    a = "one two three four five"
    b = "one two THREE four five"
    (rng,) = changed_ranges(a, b)
    assert a[rng[0] : rng[1]] == "three"
    assert outside(a, b, 8, 13) == [] and outside(a, b, 0, 7) != []
    assert outside(a, a + " six", 0, len(a)) == []  # an insertion at the end of the span is inside it


def test_findings_in_one_paragraph_share_a_window():
    f1 = m.Finding(id="find-0001", scene_id=SCENE, category="prose", severity="low", claim="a", detector="rule:slop",
                   evidence=[m.Evidence(scene_id=SCENE, version=1, span=(0, 4), quote="Mira")])  # fmt: skip
    f2 = f1.model_copy(update={"id": "find-0002", "evidence": [m.Evidence(scene_id=SCENE, version=1, span=(40, 50),
                                                                         quote=TEXT[40:50])]})  # fmt: skip
    gone = f1.model_copy(update={"id": "find-0003", "evidence": [m.Evidence(scene_id=SCENE, version=1, span=(0, 4),
                                                                           quote="Nobody wrote this")]})  # fmt: skip
    ws, lost = windows(TEXT, [f1, f2, gone])
    assert len(ws) == 1 and (ws[0].start, ws[0].end) == (0, len(P1)) and lost == [gone]
    assert re.fullmatch(r"\S.*\S", TEXT[ws[0].start : ws[0].end])
