"""Versioned prompt templates with project overrides (docs/08 § Prompt & skill registry).

File: `templates/<stage>/<name>@<version>.md.j2`

    ---
    id: outline.chapters          # must equal "<stage>.<name>"
    version: 1                    # must equal the @version in the file name
    profile: planner
    description: ...
    ---
    [system]
    ...jinja...
    [user]
    ...jinja...

Rendering uses Jinja's *sandboxed* environment with StrictUndefined: project overrides are
author-editable text, so templates can't reach Python internals, and a missing variable is an error
rather than a silently empty prompt. Values (including skill bodies) are inserted verbatim and never
re-rendered, so text inside a skill or a manuscript can't inject template code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from inkforge.models.types import Message, Role
from inkforge.storage.hashing import sha256_text

BUILTIN_TEMPLATES = Path(__file__).parent / "templates"
_FILE = re.compile(r"^(?P<name>[a-z0-9_]+)@(?P<version>\d+)\.md\.j2$")
_SECTION = re.compile(r"^\[(system|user|assistant)\]\s*$", re.MULTILINE)


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: int
    profile: str
    description: str
    body: str
    path: Path
    source: str  # "builtin" | "project"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    ref: str
    profile: str
    messages: list[Message]
    sha256: str
    source: str

    def text(self) -> str:
        return "".join(f"[{m.role}]\n{m.content}\n" for m in self.messages)


def _parse(path: Path, source: str) -> PromptTemplate:
    m = _FILE.match(path.name)
    if not m:
        raise PromptError(f"bad template file name {path.name!r} (want <name>@<version>.md.j2)")
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise PromptError(f"{path}: missing YAML frontmatter")
    _, front, body = raw.split("---\n", 2)
    meta = yaml.safe_load(front) or {}
    expected_id = f"{path.parent.name}.{m['name']}"
    if meta.get("id") != expected_id:
        raise PromptError(f"{path}: frontmatter id {meta.get('id')!r} must be {expected_id!r}")
    if meta.get("version") != int(m["version"]):
        raise PromptError(f"{path}: frontmatter version {meta.get('version')!r} must be {m['version']}")
    if not _SECTION.search(body):
        raise PromptError(f"{path}: needs at least a [user] section")
    return PromptTemplate(
        id=expected_id,
        version=int(m["version"]),
        profile=meta.get("profile", "planner"),
        description=meta.get("description", ""),
        body=body,
        path=path,
        source=source,
        meta=meta,
    )


def _bullets(items: Any, prefix: str = "- ") -> str:
    return "\n".join(f"{prefix}{x}" for x in (items or []))


class PromptRegistry:
    def __init__(self, *, project_dir: Path | None = None, builtin_dir: Path = BUILTIN_TEMPLATES):
        self._templates: dict[tuple[str, int], PromptTemplate] = {}
        sources = [(builtin_dir, "builtin")]
        if project_dir is not None:
            sources.append((project_dir, "project"))  # later source wins on identical id@version
        for root, source in sources:
            if root.is_dir():
                for path in sorted(root.glob("*/*.md.j2")):
                    t = _parse(path, source)
                    self._templates[(t.id, t.version)] = t
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["bullets"] = _bullets

    def ids(self) -> list[str]:
        return sorted({k[0] for k in self._templates})

    def versions(self, prompt_id: str) -> list[int]:
        return sorted(v for (i, v) in self._templates if i == prompt_id)

    def get(self, prompt_id: str, version: int | None = None) -> PromptTemplate:
        versions = self.versions(prompt_id)
        if not versions:
            raise PromptError(f"unknown prompt {prompt_id!r}")
        v = version if version is not None else versions[-1]
        if (prompt_id, v) not in self._templates:
            raise PromptError(f"prompt {prompt_id!r} has no version {v} (have {versions})")
        return self._templates[(prompt_id, v)]

    def all(self) -> list[PromptTemplate]:
        return [self._templates[k] for k in sorted(self._templates)]

    def render(self, prompt_id: str, version: int | None = None, /, **context: Any) -> RenderedPrompt:
        t = self.get(prompt_id, version)
        try:
            rendered = self._env.from_string(t.body).render(**context)
        except Exception as e:  # jinja raises several types; surface them uniformly
            raise PromptError(f"{t.ref}: {type(e).__name__}: {e}") from e
        messages = _split(rendered)
        text = "".join(f"[{m.role}]\n{m.content}\n" for m in messages)
        return RenderedPrompt(
            ref=t.ref, profile=t.profile, messages=messages, sha256=sha256_text(text), source=t.source
        )


def _split(rendered: str) -> list[Message]:
    rendered = rendered.replace("\r\n", "\n")
    parts = _SECTION.split(rendered)
    messages = []
    for i in range(1, len(parts), 2):
        role: Role = parts[i]  # type: ignore[assignment]
        content = "\n".join(line.rstrip() for line in parts[i + 1].strip("\n").split("\n")).strip()
        if content:
            messages.append(Message(role, content))
    if not any(m.role == "user" for m in messages):
        raise PromptError("rendered prompt has no user message")
    return messages
