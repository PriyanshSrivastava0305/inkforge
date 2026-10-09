"""Craft skills: `skills/<name>/SKILL.md` = YAML frontmatter + Markdown body (AuthorAgent-style).

Skills are *data injected into prompts*, never code: the loader reads text and nothing else.
Project skills in `<project>/skills/` shadow built-ins with the same name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

BUILTIN_SKILLS = Path(__file__).parent / "skills"
Kind = Literal["structure", "genre", "style"]


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    kind: Kind
    description: str
    body: str
    version: int = 1
    forms: tuple[str, ...] = ()  # empty = any form
    stages: tuple[str, ...] = ()
    source: str = "builtin"
    beats: tuple[str, ...] = field(default=())

    def applies_to(self, form: str) -> bool:
        return not self.forms or form in self.forms


def _load(path: Path, source: str) -> Skill:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise SkillError(f"{path}: missing frontmatter")
    _, front, body = raw.split("---\n", 2)
    meta = yaml.safe_load(front) or {}
    name = path.parent.name
    if meta.get("name") != name:
        raise SkillError(f"{path}: frontmatter name {meta.get('name')!r} must match folder {name!r}")
    if meta.get("kind") not in ("structure", "genre", "style"):
        raise SkillError(f"{path}: kind must be structure|genre|style")
    return Skill(
        name=name,
        title=meta.get("title", name),
        kind=meta["kind"],
        description=meta.get("description", ""),
        body=body.strip(),
        version=int(meta.get("version", 1)),
        forms=tuple(meta.get("forms", [])),
        stages=tuple(meta.get("stages", [])),
        source=source,
        beats=tuple(meta.get("beats", [])),
    )


class SkillRegistry:
    def __init__(self, *, project_dir: Path | None = None, builtin_dir: Path = BUILTIN_SKILLS):
        self._skills: dict[str, Skill] = {}
        for root, source in ((builtin_dir, "builtin"), (project_dir, "project")):
            if root is not None and root.is_dir():
                for p in sorted(root.glob("*/SKILL.md")):
                    s = _load(p, source)
                    self._skills[s.name] = s

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise SkillError(f"unknown skill {name!r}; have {sorted(self._skills)}")
        return self._skills[name]

    def list(self, kind: Kind | None = None, form: str | None = None) -> list[Skill]:
        return [
            s
            for s in sorted(self._skills.values(), key=lambda s: s.name)
            if (kind is None or s.kind == kind) and (form is None or s.applies_to(form))
        ]
