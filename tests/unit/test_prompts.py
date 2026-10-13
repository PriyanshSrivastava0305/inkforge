from pathlib import Path

import pytest

from inkforge.prompts.registry import PromptError, PromptRegistry
from inkforge.prompts.skills import SkillError, SkillRegistry

CARD = {
    "location": "Lighthouse",
    "want": "w",
    "obstacle": "o",
    "concrete_event": "e",
    "change": "c",
    "exit_condition": "x",
}
CHAPTER = {"title": "T", "purpose": "p", "conflict": "c", "turning_point": "t", "outcome": "o"}
SKILL = SkillRegistry().get("three-act")
ACT = {"id": "act1", "purpose": "set up the storm", "turning_point": "the first letter"}
CONTEXTS: dict[tuple[str, int], dict] = {
    ("spec.generate", 1): {
        "form": "novella",
        "target_words": 24000,
        "idea": "Bottled letters predict storms.",
    },
    ("spec.generate", 2): {
        "form": "Novel",
        "target_length": 80000,
        "unit": "words",
        "idea": "Bottled letters.",
        "fixed": {"pov": "first"},
    },
    ("spec.enhance", 1): {"spec_json": "{}", "fixed": ["pov"]},
    ("design.generate", 1): {"spec_block": "SPEC", "structure": SKILL, "acts": 3, "known_cast": []},
    ("outline.chapters", 1): {"spec_block": "SPEC", "structure": None, "chapters": 3},
    ("outline.act", 1): {
        "spec_block": "SPEC",
        "design_block": "DESIGN",
        "cast_block": "CAST",
        "promises_block": "P",
        "act": ACT,
        "act_no": 1,
        "n_acts": 3,
        "n": 2,
        "earlier": [],
        "words": 3000,
        "unit": "chapter",
    },
    ("outline.enhance", 1): {"spec_block": "SPEC", "chapters": [("ch01", "One", "begin")]},
    ("cards.generate", 1): {
        "spec_block": "SPEC",
        "number": 1,
        "chapter": CHAPTER,
        "scenes": 2,
        "char_ids": ["char-a", "char-b"],
    },
    ("cards.chapter", 1): {
        "spec_block": "SPEC",
        "cast_block": "CAST",
        "promises_block": "P",
        "chapter": CHAPTER,
        "chapter_no": 1,
        "neighbours": {"previous": None, "next": "Two: x"},
        "locations": ["Lighthouse"],
        "earlier": ["ch01-sc01: storm"],
        "n": 2,
        "words": 1500,
    },
    ("draft.scene", 1): {
        "spec_block": "SPEC",
        "chapter_no": 1,
        "scene_no": 1,
        "card": CARD,
        "pov_name": "Ailsa",
        "participants": ["Ailsa"],
        "recap": "",
        "target_words": 450,
        "pov": "first",
        "tense": "past",
    },
    ("draft.continue", 1): {"original_request": "Write it.", "anchor": "…and then"},
    ("draft.scene", 2): {
        "spec_block": "SPEC",
        "codex_block": "- Ailsa",
        "chapter_block": "CHAPTER",
        "recap": "",
        "card_block": "CARD",
        "target_words": 1200,
        "pov": "third-limited",
        "tense": "past",
        "pov_name": "Ailsa",
    },
    ("draft.continue", 2): {
        "card_block": "CARD",
        "anchor": "…and then",
        "mid_sentence": True,
        "remaining_words": 300,
        "exit_condition": "the storm hits",
    },
    ("critique.scene", 1): {"text": "Some scene.", "max_findings": 5},
    ("draft.scene", 3): {
        "spec_block": "SPEC",
        "core_block": "- rule",
        "codex_block": "- Ailsa",
        "digest_block": "so far",
        "chapter_block": "",
        "recall_block": "[ch01-sc01] text",
        "recap": "…end",
        "card_block": "CARD",
        "target_words": 1200,
        "pov": "third-limited",
        "tense": "past",
        "pov_name": "Ailsa",
    },
    ("draft.scene", 4): {
        "spec_block": "SPEC",
        "core_block": "- rule",
        "codex_block": "- Ailsa",
        "digest_block": "so far",
        "chapter_block": "",
        "recall_block": "[ch01-sc01] text",
        "recap": "end",
        "card_block": "CARD",
        "target_words": 1200,
        "pov": "third-limited",
        "tense": "past",
        "pov_name": "Ailsa",
    },
    ("memory.digest", 1): {"kind": "scene", "target": "ch01-sc01", "limit": 120, "material": "Text."},
}


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_every_builtin_template_version_renders_deterministically():
    reg = PromptRegistry()
    assert {(t.id, t.version) for t in reg.all()} == set(CONTEXTS)  # a new template needs a context here
    for (pid, v), ctx in CONTEXTS.items():
        a, b = reg.render(pid, v, **ctx), reg.render(pid, v, **ctx)
        assert a.sha256 == b.sha256 and a.messages == b.messages, pid
        assert a.messages[0].role == "system" and a.messages[-1].role == "user", pid
        assert a.ref == f"{pid}@{v}"


def test_structure_skill_is_included_verbatim():
    reg, skills = PromptRegistry(), SkillRegistry()
    sk = skills.get("save-the-cat")
    rp = reg.render("outline.chapters", spec_block="S", structure=sk, chapters=3)
    assert "Save the Cat" in rp.messages[-1].content and "Dark night of the soul" in rp.messages[-1].content


def test_missing_variable_is_an_error():
    with pytest.raises(PromptError, match="undefined"):
        PromptRegistry().render("spec.generate", 1, form="novel")


def test_project_override_and_versions(tmp_path: Path):
    write(
        tmp_path,
        "spec/generate@1.md.j2",
        "---\nid: spec.generate\nversion: 1\nprofile: planner\n---\n[user]\nmine {{ idea }}\n",
    )
    write(
        tmp_path,
        "spec/generate@2.md.j2",
        "---\nid: spec.generate\nversion: 2\nprofile: planner\n---\n[user]\nv2 {{ idea }}\n",
    )
    reg = PromptRegistry(project_dir=tmp_path)
    assert reg.versions("spec.generate") == [1, 2]
    assert reg.render("spec.generate", idea="X").messages[0].content == "v2 X"
    v1 = reg.render("spec.generate", 1, idea="X")
    assert v1.messages[0].content == "mine X" and v1.source == "project" and v1.ref == "spec.generate@1"
    with pytest.raises(PromptError):
        reg.get("spec.generate", 9)


@pytest.mark.parametrize(
    "rel,text,match",
    [
        ("spec/Bad Name.md.j2", "---\nid: x\nversion: 1\n---\n[user]\nx", "bad template file name"),
        ("spec/x@1.md.j2", "---\nid: spec.y\nversion: 1\n---\n[user]\nx", "must be 'spec.x'"),
        ("spec/x@2.md.j2", "---\nid: spec.x\nversion: 1\n---\n[user]\nx", "must be 2"),
        ("spec/x@1.md.j2", "[user]\nx", "frontmatter"),
        ("spec/x@1.md.j2", "---\nid: spec.x\nversion: 1\n---\nno sections", "user"),
    ],
)
def test_malformed_templates_rejected(tmp_path: Path, rel, text, match):
    write(tmp_path, rel, text)
    with pytest.raises(PromptError, match=match):
        PromptRegistry(project_dir=tmp_path, builtin_dir=tmp_path / "none")


def test_sandbox_blocks_python_internals(tmp_path: Path):
    write(
        tmp_path,
        "evil/x@1.md.j2",
        "---\nid: evil.x\nversion: 1\n---\n[user]\n{{ ''.__class__.__mro__[1].__subclasses__() }}\n",
    )
    reg = PromptRegistry(project_dir=tmp_path, builtin_dir=tmp_path / "none")
    with pytest.raises(PromptError, match=r"SecurityError|unsafe"):
        reg.render("evil.x")


def test_values_are_not_re_rendered():
    """Text from a manuscript or skill that looks like template code stays literal."""
    rp = PromptRegistry().render("critique.scene", text="{{ 7*7 }} {% if true %}x{% endif %}", max_findings=1)
    assert "{{ 7*7 }} {% if true %}x{% endif %}" in rp.messages[-1].content


def test_sections_split_and_whitespace_normalized(tmp_path: Path):
    write(
        tmp_path,
        "t/x@1.md.j2",
        "---\nid: t.x\nversion: 1\n---\n[system]\n  sys  \n\n[user]\nline one   \nline two\n\n[assistant]\n\n",
    )
    msgs = PromptRegistry(project_dir=tmp_path, builtin_dir=tmp_path / "none").render("t.x").messages
    assert [(m.role, m.content) for m in msgs] == [("system", "sys"), ("user", "line one\nline two")]


# ---------------------------------------------------------------- skills


def test_builtin_skills():
    reg = SkillRegistry()
    assert [s.name for s in reg.list(kind="structure")] == ["heros-journey", "save-the-cat", "three-act"]
    stc = reg.get("save-the-cat")
    assert len(stc.beats) == 15 and stc.applies_to("novel") and "design" in stc.stages


def test_project_skill_shadows_builtin(tmp_path: Path):
    write(
        tmp_path,
        "three-act/SKILL.md",
        "---\nname: three-act\nkind: structure\ntitle: Mine\n---\nMy own acts.",
    )
    write(tmp_path, "noir/SKILL.md", "---\nname: noir\nkind: genre\nforms: [novel]\n---\nRain. Neon. Regret.")
    reg = SkillRegistry(project_dir=tmp_path)
    assert reg.get("three-act").body == "My own acts." and reg.get("three-act").source == "project"
    assert [s.name for s in reg.list(kind="genre", form="screenplay")] == []
    with pytest.raises(SkillError):
        reg.get("cozy")


def test_bad_skill_rejected(tmp_path: Path):
    write(tmp_path, "x/SKILL.md", "---\nname: y\nkind: structure\n---\nbody")
    with pytest.raises(SkillError, match="must match folder"):
        SkillRegistry(project_dir=tmp_path)
    write(tmp_path, "x/SKILL.md", "---\nname: x\nkind: plugin\n---\nbody")
    with pytest.raises(SkillError, match="kind"):
        SkillRegistry(project_dir=tmp_path)
