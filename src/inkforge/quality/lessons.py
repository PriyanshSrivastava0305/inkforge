"""Lessons (docs/07 § Learning loop): a finding that keeps coming back becomes a style rule for drafting.

Suggestions are computed, never applied: when the same kind of finding appears in 3+ of the last 10
checked scenes, `suggest()` proposes a one-line rule. Only the author adds it (`inkforge lessons add`).
Active lessons are shown to the drafting model with the book spec, so they change future drafts —
visibly: they live in `style/lessons.json`, every change is audited, and `inkforge lessons` lists them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import ClassVar

from pydantic import Field

from inkforge.domain.common import Actor, Record, SchemaRecord, utcnow
from inkforge.domain.models import Character, Finding
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ProjectStore

WINDOW = 10  # look at the last N checked scenes
MIN_SCENES = 3  # a pattern must show up in this many of them
GENERIC = {
    "rule:repetition": "Vary sentence openings and don't repeat phrases within a scene.",
    "rule:pov": "Never slip into first-person narration.",
    "rule:meta": "Write only the story: no headings, notes or bracketed instructions.",
    "critic:craft_sensory": "Ground every scene in specific, concrete sensory detail.",
    "critic:craft_show_tell": "Show feelings through action and dialogue rather than naming them.",
    "critic:craft_tension": "Keep a live question or threat in every scene.",
    "critic:craft_pacing": "Cut summary; stay in the moment for the scene's key exchange.",
    "critic:craft_dialogue": "Let dialogue carry subtext; characters rarely say exactly what they mean.",
    "critic:craft_clarity": "Make who is where, and who is speaking, clear in every paragraph.",
    "critic:craft_ending": "End scenes on a turn or a question, not a summary.",
    "critic:craft_goal": "Make each scene's goal visible early and resolve or turn it by the end.",
}


class Lesson(Record):
    id: str = Field(pattern=r"^lesson-\d{3,}$")
    text: str = Field(min_length=3, max_length=200)
    source: str | None = None  # the detector (and key) it came from, if suggested
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    active: bool = True


class LessonBook(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/lessons@1"

    lessons: list[Lesson] = Field(default_factory=list)

    def active(self) -> list[Lesson]:
        return [x for x in self.lessons if x.active]


def _path(store: ProjectStore):
    return store.path("style/lessons.json")


def load(store: ProjectStore) -> LessonBook:
    p = _path(store)
    return LessonBook.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else LessonBook()


def _save(store: ProjectStore, book: LessonBook) -> None:
    with store.lock:
        atomic_write_text(_path(store), book.to_json())


def add(store: ProjectStore, text: str, *, source: str | None = None, actor: Actor = Actor.USER) -> Lesson:
    book = load(store)
    n = max((int(x.id.split("-")[1]) for x in book.lessons), default=0) + 1
    lesson = Lesson(id=f"lesson-{n:03d}", text=text.strip(), source=source)
    book.lessons.append(lesson)
    _save(store, book)
    store.audit.record("lessons.add", lesson.id, actor=actor, detail={"text": lesson.text})
    return lesson


def remove(store: ProjectStore, lesson_id: str, *, actor: Actor = Actor.USER) -> bool:
    """Deactivate (kept on file, so the history of what drafting was told stays visible)."""
    book = load(store)
    for i, x in enumerate(book.lessons):
        if x.id == lesson_id and x.active:
            book.lessons[i] = x.model_copy(update={"active": False})
            _save(store, book)
            store.audit.record("lessons.remove", lesson_id, actor=actor)
            return True
    return False


def drafting_rules(store: ProjectStore) -> list[str]:
    return [x.text for x in load(store).active()]


def _key(f: Finding) -> tuple[str, str]:
    if f.detector == "rule:slop":
        m = re.search(r"“([^”]+)”", f.claim)
        return f.detector, (m.group(1) if m else "")
    if f.detector == "critic:voice":
        return f.detector, (f.affected_entities[0] if f.affected_entities else "")
    if f.detector == "rule:tense":
        return f.detector, ("past" if "in the past tense" in f.claim else "present")
    return f.detector, ""


def suggest(store: ProjectStore) -> list[tuple[str, str, int]]:
    """(rule text, source, scenes) for patterns in 3+ of the last 10 checked scenes, not already lessons."""
    order = store.scenes_in_order()
    findings = [f for f in store.findings.all() if f.status != "dismissed" and not f.is_hypothesis]
    checked = [x for x in order if any(f.scene_id == x for f in findings)][-WINDOW:]
    scenes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in findings:
        if f.scene_id in checked:
            scenes[_key(f)].add(f.scene_id)
    have = {x.source for x in load(store).lessons}
    names = {c.id: c for c in store.list_codex() if isinstance(c, Character)}
    spec = store.try_read("spec")
    out = []
    for (detector, key), where in sorted(scenes.items(), key=lambda kv: -len(kv[1])):
        if len(where) < MIN_SCENES:
            continue
        source = f"{detector}:{key}" if key else detector
        if source in have:
            continue
        if detector == "rule:slop" and key:
            text = f"Avoid the phrase “{key}”."
        elif detector == "rule:tense":
            text = f"Keep the narration in the {spec.tense if spec else 'past'} tense."
        elif detector == "critic:voice" and key in names:
            c = names[key]
            how = c.voice_card.diction or "their own voice"
            text = f"{c.name} always speaks in character: {how}."
        elif detector in GENERIC:
            text = GENERIC[detector]
        else:
            continue
        out.append((text, source, len(where)))
    return out
