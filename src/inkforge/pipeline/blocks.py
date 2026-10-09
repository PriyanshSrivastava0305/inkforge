"""Deterministic text blocks shared by planning prompts.

Byte-stable on purpose (sorted, no timestamps): identical prefixes across calls let Ollama reuse its
KV cache, ~15× faster prompt reading on this CPU (D25). The full context builder arrives in Phase 4/5.
"""

from __future__ import annotations

from inkforge.domain.codex import active_characters
from inkforge.domain.models import BookSpec, Character, PromiseRegistry, StoryDesign
from inkforge.storage.workspace import ProjectStore


def spec_block(spec: BookSpec) -> str:
    lines = [
        "BOOK SPEC",
        f"Logline: {spec.logline}",
        f"Premise: {spec.premise}",
        f"Genre: {spec.genre}" + (f" ({', '.join(spec.subgenres)})" if spec.subgenres else ""),
        f"Audience: {spec.audience}. Tone: {spec.tone}.",
        f"POV: {spec.pov}, {spec.tense} tense.",
        f"Setting: {spec.setting.place}; {spec.setting.time}",
        f"Themes: {', '.join(spec.themes)}",
        f"Avoid: {', '.join(spec.content_boundaries) or 'none'}",
        f"Reader promises: {'; '.join(spec.narrative_promises)}",
    ]
    if spec.style_notes:
        lines.append(f"Style: {spec.style_notes}")
    return "\n".join(lines)


def design_block(design: StoryDesign) -> str:
    lines = ["STORY DESIGN", f"Dramatic question: {design.dramatic_question}"]
    if design.theme_statement:
        lines.append(f"Theme: {design.theme_statement}")
    for act in design.acts:
        lines.append(f"{act.id}: {act.purpose} — turning point: {act.turning_point}")
    if design.ending_constraints:
        lines.append(f"Ending must: {'; '.join(design.ending_constraints)}")
    return "\n".join(lines)


def _char_line(c: Character) -> str:
    bits = [f"{c.id}: {c.name}"]
    if c.role:
        bits.append(f"({c.role})")
    if c.goals:
        bits.append(f"wants {c.goals[0]}")
    if c.voice_card.diction:
        bits.append(f"voice: {c.voice_card.diction}")
    return "- " + " ".join(bits)


def cast_block(store: ProjectStore) -> str:
    chars = active_characters(store)
    return "CAST (use these ids)\n" + "\n".join(_char_line(c) for c in chars) if chars else "CAST: none yet"


def promises_block(registry: PromiseRegistry, *, only_open: bool = True) -> str:
    ps = [p for p in registry.promises if not only_open or p.status == "open"]
    if not ps:
        return "PROMISES: none"
    return "PROMISES TO KEEP (use these ids)\n" + "\n".join(
        f"- {p.id} [{p.importance}]: {p.description}" for p in ps
    )
