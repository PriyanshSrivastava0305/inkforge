"""DIGEST tier (docs/06): scene (≤120 words), chapter (≤250) and act (≤400) summaries.

Derived data: `memory/digests/{scenes,chapters,acts}/<id>.json`, each stamped with the hash of what it
was made from, so staleness is a comparison, not bookkeeping. Deleting the folder loses nothing —
`inkforge memory rebuild` regenerates it from the manuscript.

A digest is written by the model when one is available. If that call fails (or `use_model=False`), a
deterministic extract stands in, marked `method="extract"`, so memory can never block drafting.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from inkforge.domain.common import SchemaRecord
from inkforge.memory.effective import effective, effective_text
from inkforge.models.router import GenerationCancelled, ModelRouter
from inkforge.models.types import CancelToken, ModelError
from inkforge.prompts.registry import PromptRegistry
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.hashing import sha256_json
from inkforge.storage.workspace import ProjectStore

Kind = Literal["scene", "chapter", "act"]
LIMITS: dict[str, int] = {"scene": 120, "chapter": 250, "act": 400}
_DIR = {"scene": "scenes", "chapter": "chapters", "act": "acts"}
_SENT = re.compile(r"(?<=[.!?…])[\"'”’)]*\s+")


class Digest(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/digest@1"

    kind: Kind
    target: str
    summary: str
    changes: list[str] = Field(default_factory=list)  # state changes worth carrying forward
    source_hash: str
    source_refs: list[str] = Field(default_factory=list)
    method: Literal["model", "extract"] = "extract"
    provisional: bool = False  # built from text that isn't approved yet (D34)

    def text(self) -> str:
        return self.summary + ("" if not self.changes else " Changes: " + "; ".join(self.changes))


class DigestProposal(BaseModel):
    summary: str = Field(min_length=20, description="what happens, in plain past tense")
    changes: list[str] = Field(
        default_factory=list, max_length=5, description="who learned, gained, lost or decided what"
    )


def clip_words(text: str, limit: int) -> str:
    """Cut to `limit` words at a sentence boundary when possible."""
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    cut = " ".join(words[:limit])
    ends = [m.end() for m in re.finditer(r"[.!?…][\"'”’)]*", cut)]
    return (cut[: ends[-1]] if ends and ends[-1] > len(cut) * 0.5 else cut + "…").strip()


def extract_summary(text: str, limit: int) -> str:
    """Deterministic stand-in: opening sentences plus the final sentence."""
    sentences = [s.strip() for s in _SENT.split(text.replace("\n", " ")) if s.strip()]
    if not sentences:
        return ""
    head: list[str] = []
    for s in sentences[:-1]:
        if len(" ".join([*head, s]).split()) > limit * 0.7:
            break
        head.append(s)
    tail = sentences[-1] if len(sentences) > 1 else ""
    joined = " ".join(head) + (" … " + tail if tail and tail not in head else "")
    return clip_words(joined or sentences[0], limit)


class DigestStore:
    def __init__(self, store: ProjectStore):
        self.store = store

    def path(self, kind: Kind, target: str):
        return self.store.path(f"memory/digests/{_DIR[kind]}/{target}.json")

    def get(self, kind: Kind, target: str) -> Digest | None:
        p = self.path(kind, target)
        return Digest.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None

    def save(self, d: Digest) -> None:
        with self.store.lock:
            atomic_write_text(self.path(d.kind, d.target), d.to_json())

    def clear(self) -> None:
        import shutil

        with self.store.lock:
            shutil.rmtree(self.store.path("memory/digests"), ignore_errors=True)

    # ---- structure
    def chapter_scenes(self, chapter: str) -> list[str]:
        return [s for s in self.store.scenes_in_order() if s.startswith(f"{chapter}-")]

    def act_chapters(self, act: str) -> list[str]:
        outline = self.store.try_read("outline")
        return (
            [c.id for c in sorted(outline.chapters, key=lambda c: c.order) if c.act_id == act]
            if outline
            else []
        )

    def acts(self) -> list[str]:
        outline = self.store.try_read("outline")
        return [a.id for a in sorted(outline.acts, key=lambda a: a.order)] if outline else []

    # ---- what a digest is made from → its source hash (None = nothing to digest yet)
    def source(self, kind: Kind, target: str) -> tuple[str, list[str], bool] | None:
        if kind == "scene":
            eff = effective(self.store, target)
            return (eff.sha256, [eff.ref], eff.provisional) if eff else None
        members = self.chapter_scenes(target) if kind == "chapter" else self.act_chapters(target)
        child: Kind = "scene" if kind == "chapter" else "chapter"
        parts = [(m, self.source(child, m)) for m in members]
        have = [(m, src) for m, src in parts if src is not None]
        if not have:
            return None
        return (
            sha256_json([[m, src[0]] for m, src in have]),
            [f"{child}:{m}" for m, _ in have],
            any(src[2] for _, src in have),
        )

    def is_fresh(self, kind: Kind, target: str) -> bool:
        d, src = self.get(kind, target), self.source(kind, target)
        return d is not None and src is not None and d.source_hash == src[0]

    def all_targets(self) -> list[tuple[Kind, str]]:
        """Everything that could have a digest, in dependency order (scenes → chapters → acts)."""
        s = self.store
        out: list[tuple[Kind, str]] = [("scene", x) for x in s.scenes_in_order()]
        outline = s.try_read("outline")
        if outline:
            for c in outline.ordered_chapter_ids():
                out.append(("chapter", c))
            for a in self.acts():
                out.append(("act", a))
        return out

    def stale(self) -> list[tuple[Kind, str]]:
        return [
            (k, t) for k, t in self.all_targets() if self.source(k, t) is not None and not self.is_fresh(k, t)
        ]


class Digester:
    """Builds digests; with a router it asks the model, otherwise (or on failure) it extracts."""

    def __init__(
        self, store: ProjectStore, router: ModelRouter | None = None, prompts: PromptRegistry | None = None
    ):
        self.store, self.router = store, router
        self.prompts = prompts or PromptRegistry(project_dir=store.dir / "prompts")
        self.digests = DigestStore(store)

    def _material(self, kind: Kind, target: str) -> str:
        if kind == "scene":
            got = effective_text(self.store, target)
            return got[1] if got else ""
        child: Kind = "scene" if kind == "chapter" else "chapter"
        members = (
            self.digests.chapter_scenes(target) if kind == "chapter" else self.digests.act_chapters(target)
        )
        lines = []
        for m in members:
            d = self.digests.get(child, m)
            if d is not None:
                lines.append(f"{m}: {d.text()}")
        return "\n".join(lines)

    async def build(
        self, kind: Kind, target: str, *, use_model: bool = True, cancel: CancelToken | None = None
    ) -> Digest | None:
        src = self.digests.source(kind, target)
        if src is None:
            return None
        material = self._material(kind, target)
        if not material.strip():
            return None
        limit = LIMITS[kind]
        summary, changes, method = "", [], "extract"
        if use_model and self.router is not None:
            try:
                rp = self.prompts.render(
                    "memory.digest", kind=kind, target=target, limit=limit, material=material
                )
                res = await self.router.generate_structured(
                    rp.messages,
                    DigestProposal,
                    rp.profile,
                    max_tokens=int(limit * 1.33 * 2) + 120,
                    cancel=cancel,
                )
                summary, changes, method = clip_words(res.value.summary, limit), res.value.changes, "model"
            except GenerationCancelled:
                raise
            except ModelError:
                summary = ""  # fall through to the extract
        if not summary:
            summary = extract_summary(material, limit)
        d = Digest(
            kind=kind,
            target=target,
            summary=summary,
            changes=list(changes),
            source_hash=src[0],
            source_refs=src[1],
            method=method,
            provisional=src[2],
        )  # type: ignore[arg-type]
        self.digests.save(d)
        return d

    async def update(
        self,
        *,
        use_model: bool = True,
        only: list[tuple[Kind, str]] | None = None,
        cancel: CancelToken | None = None,
        on_built: Any = None,
    ) -> list[Digest]:
        """Rebuild whatever is stale, children before parents."""
        built = []
        for kind, target in self.digests.all_targets():
            if only is not None and (kind, target) not in only:
                continue
            if self.digests.source(kind, target) is None or self.digests.is_fresh(kind, target):
                continue
            d = await self.build(kind, target, use_model=use_model, cancel=cancel)
            if d is not None:
                built.append(d)
                if on_built:
                    on_built(d)
        return built

    async def after_scene(
        self, scene_id: str, *, use_model: bool = True, cancel: CancelToken | None = None
    ) -> list[Digest]:
        """After a scene's text changes: its digest, then its chapter's and act's if every member has text."""
        chapter = scene_id.split("-sc")[0]
        todo: list[tuple[Kind, str]] = [("scene", scene_id)]
        if all(effective(self.store, s) for s in self.digests.chapter_scenes(chapter)):
            todo.append(("chapter", chapter))
            outline = self.store.try_read("outline")
            act = next((c.act_id for c in outline.chapters if c.id == chapter), None) if outline else None
            if act and all(
                effective(self.store, s)
                for ch in self.digests.act_chapters(act)
                for s in self.digests.chapter_scenes(ch)
            ):
                todo.append(("act", act))
        return await self.update(use_model=use_model, only=todo, cancel=cancel)
