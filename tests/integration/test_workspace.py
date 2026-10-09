import json
import zipfile
from pathlib import Path

import pytest

from inkforge.domain import models as m
from inkforge.domain.common import Actor, Staleness
from inkforge.policy.pathguard import PathEscapeError
from inkforge.storage.backup import MANIFEST, backup_project, restore_project
from inkforge.storage.workspace import ProjectExistsError, ProjectStore, Workspace

# ---------------------------------------------------------------- project lifecycle


def test_create_and_open(workspace: Workspace, store: ProjectStore):
    assert workspace.project_ids() == ["the-glass-orchard"]
    p = workspace.open("the-glass-orchard").project
    assert (p.title, p.form, p.target_length) == ("The Glass Orchard", "novel", 80000)
    with pytest.raises(ProjectExistsError):
        workspace.create_project("The Glass Orchard")
    with pytest.raises(ValueError, match="not available"):
        workspace.create_project("Script", "screenplay")
    with pytest.raises(PathEscapeError):
        workspace.open("../elsewhere")


def test_counters_mint_unique_ids(store: ProjectStore):
    got = [store.mint("fact") for _ in range(3)] + [store.mint("promise")]
    assert got == ["fact-0001", "fact-0002", "fact-0003", "prom-001"]
    assert store.project.counters == {"fact": 3, "promise": 1}


def test_save_rejects_mismatched_target(store: ProjectStore):
    with pytest.raises(ValueError, match="does not match"):
        store.save("chapter", m.ChapterPlan(id="ch01", title="t", purpose="p"), target="ch02")
    with pytest.raises(TypeError):
        store.save("chapter", m.BookSpec(original="x"))


def test_audit_trail(populated: ProjectStore):
    ops = [e.operation for e in populated.audit]
    assert ops[0] == "project.create"
    for op in (
        "spec.save",
        "outline.save",
        "chapter.save",
        "card.save",
        "draft.save",
        "codex.save",
        "facts.add",
        "findings.add",
        "promises.save",
        "draft.approve",
    ):
        assert op in ops, op
    approve = next(e for e in populated.audit if e.operation == "draft.approve")
    assert (approve.actor, approve.target, approve.after_ref) == (Actor.USER, "draft:ch01-sc01", "v1")


# ---------------------------------------------------------------- staleness (docs/04 table, D21)


def _plan(cid: str, purpose: str, outcome: str = "o") -> m.ChapterPlan:
    return m.ChapterPlan(id=cid, act_id="act1", title=cid, purpose=purpose, outcome=outcome)


def test_everything_fresh_after_populate(populated: ProjectStore):
    s = populated
    for kind, target in [
        ("design", None),
        ("outline", None),
        ("chapter", "ch01"),
        ("chapter", "ch02"),
        ("card", "ch01-sc01"),
        ("draft", "ch01-sc01"),
    ]:
        assert s.staleness(kind, target) == Staleness.FRESH, (kind, target)


def test_editing_one_chapter_stales_only_its_cards(populated: ProjectStore):
    s = populated
    s.save("chapter", _plan("ch02", "find bottles", "first letter"), actor=Actor.USER)  # same content → fresh
    assert s.staleness("card", "ch02-sc01") == Staleness.FRESH
    s.save("chapter", _plan("ch01", "arrive by sea", "hired"), actor=Actor.USER)
    assert s.staleness("card", "ch01-sc01") == Staleness.STALE
    assert s.staleness("card", "ch01-sc02") == Staleness.STALE
    assert s.staleness("card", "ch02-sc01") == Staleness.FRESH
    assert s.staleness("chapter", "ch03") == Staleness.FRESH  # not a neighbour of ch01


def test_neighbour_purpose_change_is_review_not_stale(populated: ProjectStore):
    s = populated
    s.save("chapter", _plan("ch02", "find bottles in the reeds", "first letter"))
    assert s.staleness("chapter", "ch01") == Staleness.REVIEW
    assert s.staleness("chapter", "ch03") == Staleness.REVIEW
    assert s.staleness("card", "ch01-sc01") == Staleness.FRESH  # cards of other chapters untouched


def test_reorder_and_retitle(populated: ProjectStore):
    s = populated
    o = s.read("outline")
    swapped = [c.model_copy(update={"order": {"ch01": 1, "ch02": 0}.get(c.id, c.order)}) for c in o.chapters]
    s.save("outline", o.model_copy(update={"chapters": swapped}))
    # reorder: nothing stale (neighbour context may ask for review)
    for cid in ("ch01", "ch02", "ch03"):
        assert s.staleness("chapter", cid) in (Staleness.FRESH, Staleness.REVIEW)
    assert s.staleness("card", "ch01-sc01") == Staleness.FRESH
    assert s.scenes_in_order()[0] == "ch02-sc01"
    # retitle ch03 → its own outline entry changed → ch03 plan is stale
    o = s.read("outline")
    s.save(
        "outline",
        o.model_copy(
            update={
                "chapters": [
                    c.model_copy(update={"title": "Hard Frost"}) if c.id == "ch03" else c for c in o.chapters
                ]
            }
        ),
    )
    assert s.staleness("chapter", "ch03") == Staleness.STALE
    assert s.staleness("chapter", "ch01") != Staleness.STALE


def test_codex_and_voice_changes(populated: ProjectStore):
    s = populated
    tom = s.get_codex("char-tom")
    assert tom is not None
    s.save_codex(tom.model_copy(update={"aliases": ["Tommy", "T"]}))
    assert s.staleness("card", "ch01-sc01") == Staleness.STALE  # Tom participates
    assert s.staleness("card", "ch01-sc02") == Staleness.FRESH  # Tom not in this scene
    assert s.staleness("draft", "ch01-sc01") == Staleness.FRESH  # aliases aren't the voice card
    mira = s.get_codex("char-mira")
    assert mira is not None
    s.save_codex(mira.model_copy(update={"voice_card": m.VoiceCard(diction="ornate")}))
    assert s.staleness("draft", "ch01-sc01") == Staleness.STALE
    assert s.staleness("draft", "ch01-sc02") == Staleness.STALE


def test_card_change_stales_draft_and_older_versions_remain(populated: ProjectStore):
    s = populated
    card = s.read("card", "ch01-sc02")
    s.save("card", card.model_copy(update={"want": "to leave"}), actor=Actor.USER)
    assert s.staleness("draft", "ch01-sc02") == Staleness.STALE
    assert s.meta("card", "ch01-sc02").active == 2
    assert s.read("card", "ch01-sc02", version=1).want is None


# ---------------------------------------------------------------- index (Phase 1 exit criterion)


def test_incremental_index_equals_rebuild(populated: ProjectStore):
    s = populated
    s.save_draft("ch01-sc01", "A second draft about the lighthouse and the reeds.")
    s.set_active("draft", "ch01-sc01", 1)
    s.delete_codex("obj-brass-key")
    s.delete_scene("ch02-sc01")
    incremental = s.index.dump()
    s.index.path.unlink()
    s.index.rebuild()
    assert s.index.dump() == incremental
    assert {r[0] for r in incremental["scenes"]} == {"ch01-sc01", "ch01-sc02"}
    assert "obj-brass-key" not in {r[0] for r in incremental["codex"]}


def test_reindex_after_delete_reproduces_queries(populated: ProjectStore):
    s = populated
    q = "SELECT s.id, s.narrative_pos, s.story_index, s.approved_draft, s.word_count FROM scenes s ORDER BY narrative_pos"
    before = (
        s.index.query(q),
        s.index.search_text("lighthouse"),
        s.index.query("SELECT id, name FROM codex ORDER BY id"),
    )
    s.index.path.unlink()
    after = (
        s.index.query(q),
        s.index.search_text("lighthouse"),
        s.index.query("SELECT id, name FROM codex ORDER BY id"),
    )
    assert before == after
    assert before[1] == [("ch01-sc01", 1)]


def test_corrupt_index_is_rebuilt(populated: ProjectStore):
    s = populated
    expected = s.index.dump()
    s.index.path.write_bytes(b"not a database")
    assert s.index.dump() == expected


def test_delete_scene_archives_instead_of_deleting(populated: ProjectStore):
    s = populated
    s.delete_scene("ch01-sc02")
    assert "ch01-sc02" not in s.scene_ids()
    archived = list((s.dir / "backups" / "deleted").glob("ch01-sc02-*"))
    assert len(archived) == 1 and (archived[0] / "draft.v1.md").is_file()


# ---------------------------------------------------------------- backup / restore (portability)


def _tree(d: Path) -> dict[str, bytes]:
    skip = {"index.sqlite", ".lock", "backups", "audit.jsonl"}
    return {
        p.relative_to(d).as_posix(): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file() and p.relative_to(d).parts[0] not in skip
    }


def test_backup_restore_round_trip(workspace: Workspace, populated: ProjectStore, tmp_path: Path):
    z = backup_project(populated)
    other = Workspace(tmp_path / "other-machine").ensure()
    restored = restore_project(other, z)
    assert _tree(restored.dir) == _tree(populated.dir)
    assert restored.index.dump() == populated.index.dump()
    with pytest.raises(ProjectExistsError):
        restore_project(other, z)


def test_restore_under_new_id(workspace: Workspace, populated: ProjectStore):
    restored = restore_project(workspace, backup_project(populated), project_id="orchard-copy")
    assert restored.project.id == "orchard-copy" and workspace.project_ids() == [
        "orchard-copy",
        "the-glass-orchard",
    ]


def _rewrite_zip(src: Path, dst: Path, mutate) -> Path:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        items = {n: zin.read(n) for n in zin.namelist()}
        for n, data in mutate(items).items():
            zout.writestr(n, data)
    return dst


def test_restore_rejects_zip_slip(workspace: Workspace, populated: ProjectStore, tmp_path: Path):
    def evil(items):
        man = json.loads(items[MANIFEST])
        items["../../escaped.txt"] = b"pwned"
        from inkforge.storage.hashing import sha256_bytes

        man["files"]["../../escaped.txt"] = sha256_bytes(b"pwned")
        items[MANIFEST] = json.dumps(man).encode()
        return items

    bad = _rewrite_zip(backup_project(populated), tmp_path / "evil.zip", evil)
    with pytest.raises(PathEscapeError):
        restore_project(workspace, bad, project_id="evil")
    assert (
        not (workspace.root / "escaped.txt").exists()
        and not (workspace.projects_dir / "escaped.txt").exists()
    )
    assert "evil" not in workspace.project_ids()
    assert not list(workspace.projects_dir.glob(".restore-*"))


def test_restore_rejects_tampered_content(workspace: Workspace, populated: ProjectStore, tmp_path: Path):
    def tamper(items):
        items["scenes/ch01-sc01/draft.v1.md"] = b"rewritten"
        return items

    bad = _rewrite_zip(backup_project(populated), tmp_path / "tampered.zip", tamper)
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_project(workspace, bad, project_id="tampered")
    assert "tampered" not in workspace.project_ids()
