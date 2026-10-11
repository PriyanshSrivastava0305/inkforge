"""Context builder v1 for drafting (docs/06, Phase 4).

Sections are *rendered* stable-first so consecutive calls share a KV-cache prefix (D25), but *trimmed*
by priority when the prompt doesn't fit:

    render order:  spec → codex (present characters + location) → chapter stand-in → recap → card
    trim order:    chapter stand-in (drop earlier events, then all) → recap (to ~150 tokens) →
                   codex (to name + voice card) → fail with "context too small"

The scene card and the output instructions are never trimmed. Before Phase 5 there are no digests,
CORE or RECALL: the chapter plan plus the earlier cards' one-line events stand in (docs/05 Stage E).
Every build returns a ContextManifest saying exactly what was included, trimmed or dropped, and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from inkforge.domain.codex import active_characters
from inkforge.domain.models import Character, ContextManifest, Location, ManifestSection, SceneCard
from inkforge.models.router import ModelRouter
from inkforge.models.types import Message
from inkforge.pipeline.blocks import spec_block
from inkforge.prompts.registry import PromptRegistry, RenderedPrompt
from inkforge.storage.hashing import sha256_text
from inkforge.storage.workspace import ProjectStore

RECAP_TOKENS = 400
RECAP_MIN_TOKENS = 150
SAFETY = 0.05
CHARS_PER_TOKEN = 4.5  # prose; only used to cut text to an approximate token length before re-counting


class ContextTooSmall(RuntimeError):
    pass


@dataclass
class Section:
    name: str
    text: str
    sources: list[str]
    disposition: Literal["included", "trimmed", "dropped"] = "included"
    reason: str | None = None
    tokens: int = 0


@dataclass
class BuiltContext:
    prompt: RenderedPrompt
    manifest: ContextManifest
    sections: dict[str, Section] = field(default_factory=dict)
    max_tokens: int = 0


def _voice_line(c: Character) -> str:
    v = c.voice_card
    bits = [b for b in (v.diction, v.sentence_length, v.vocabulary_level) if b]
    if v.verbal_tics:
        bits.append("tics: " + ", ".join(v.verbal_tics))
    if v.taboo_words:
        bits.append("never says: " + ", ".join(v.taboo_words))
    line = f"Voice: {'; '.join(bits)}" if bits else ""
    if v.sample_lines:
        line += "\n  Sample lines: " + " / ".join(f"“{s}”" for s in v.sample_lines[:3])
    return line


def character_entry(c: Character, *, full: bool) -> str:
    head = (
        f"{c.name}"
        + (f" ({c.role})" if c.role else "")
        + (f" — also called {', '.join(c.aliases)}" if c.aliases else "")
    )
    lines = [f"- {head}"]
    if full:
        for label, vals in (
            ("Wants", c.goals),
            ("Fears", c.fears),
            ("Hides", c.secrets),
            ("Traits", c.traits),
        ):
            if vals:
                lines.append(f"  {label}: {'; '.join(vals)}")
        if c.appearance:
            lines.append(f"  Looks: {c.appearance}")
    if voice := _voice_line(c):
        lines.append(f"  {voice}")
    return "\n".join(lines)


def card_block(card: SceneCard, names: dict[str, str], location: Location | None) -> str:
    who = ", ".join(names.get(p, p) for p in card.participants)
    lines = [
        f"POV: {names.get(card.pov_character_id or '', card.pov_character_id)}",
        f"Present: {who}",
        f"Place: {location.name if location else 'unspecified'}"
        + (f" — {location.description}" if location and location.description else ""),
        f"When: {card.time.label or 'continuous with the previous scene'}"
        + (f" ({card.temporal_mode})" if card.temporal_mode != "linear" else ""),
        f"Want: {card.want}",
        f"Obstacle: {card.obstacle}",
        f"What happens: {card.concrete_event}",
        f"Turn: {card.turn}",
        f"Change: {card.change}  ({card.story_value}: {card.value_charge})",
        f"Mood: {card.mood}",
    ]
    if card.beats:
        lines.append("Beats:\n" + "\n".join(f"  {i}. {b}" for i, b in enumerate(card.beats, 1)))
    lines.append(f"End the scene when: {card.exit_condition}")
    return "\n".join(lines)


class DraftContextBuilder:
    def __init__(
        self, store: ProjectStore, router: ModelRouter, prompts: PromptRegistry, *, profile: str = "prose"
    ):
        self.store, self.router, self.prompts, self.profile = store, router, prompts, profile

    def budget(self) -> tuple[int, int, int]:
        """(context limit, output reserve, input budget) — docs/06 § Budgeting."""
        p = self.router.profile(self.profile)
        limit = p.num_ctx
        reserve = max(p.min_output_tokens, int(limit * p.output_fraction))
        return limit, reserve, int(limit - reserve - limit * SAFETY)

    def _count(self, messages: list[Message]) -> int:
        return self.router.tokens.count_messages(messages, self.router.profile(self.profile).model).n

    def _count_text(self, text: str) -> int:
        return self.router.tokens.count(text, self.router.profile(self.profile).model).n

    def build(self, scene_id: str, *, job_id: str, target_words: int | None = None) -> BuiltContext:
        s = self.store
        card: SceneCard = s.read("card", scene_id)
        chars = {c.id: c for c in active_characters(s)}
        names = {cid: c.name for cid, c in chars.items()}
        loc = s.get_codex(card.location_id) if card.location_id else None
        location = loc if isinstance(loc, Location) else None
        present = [chars[p] for p in card.participants if p in chars]
        plan = s.read("chapter", card.chapter_id)
        target = target_words or card.target_length or 1500

        # chapter stand-in: plan + one-line events of earlier cards in this chapter (no digests before Phase 5)
        earlier = [sid for sid in s.scenes_in_order() if sid.startswith(f"{card.chapter_id}-")]
        earlier = earlier[: earlier.index(scene_id)]
        events = []
        for sid in earlier:
            c = s.try_read("card", sid)
            if c is not None and c.concrete_event:
                events.append(f"- {c.concrete_event}")
        chapter_head = f"CHAPTER: {plan.title}\nPurpose: {plan.purpose}\nConflict: {plan.conflict}\nOutcome: {plan.outcome}"
        sections = {
            "spec": Section("spec", spec_block(s.read("spec")), ["spec"]),
            "codex": Section(
                "codex",
                "\n".join(character_entry(c, full=True) for c in present),
                [f"codex:{c.id}" for c in present] + ([f"codex:{location.id}"] if location else []),
            ),
            "chapter": Section(
                "chapter",
                chapter_head + (("\nEarlier in this chapter:\n" + "\n".join(events)) if events else ""),
                [f"chapter:{plan.id}", *(f"card:{x}" for x in earlier)],
            ),
            "recap": self._recap(scene_id),
            "card": Section("card", card_block(card, names, location), [f"card:{scene_id}"]),
        }
        limit, reserve, budget = self.budget()
        spec = s.read("spec")

        def render() -> RenderedPrompt:
            return self.prompts.render(
                "draft.scene",
                2,
                spec_block=sections["spec"].text,
                codex_block=sections["codex"].text,
                chapter_block=sections["chapter"].text,
                recap=sections["recap"].text,
                card_block=sections["card"].text,
                target_words=target,
                pov=spec.pov or "third-limited",
                tense=spec.tense or "past",
                pov_name=names.get(card.pov_character_id or "", "the POV character"),
            )

        rp = render()
        total = self._count(rp.messages)
        # trimming ladder (docs/06): lowest priority first; scene card + instructions never trimmed
        steps = [
            ("chapter", lambda: chapter_head, "dropped earlier-scene events to fit"),
            ("chapter", lambda: "", "dropped chapter stand-in to fit"),
            (
                "recap",
                lambda: self._cut(sections["recap"].text, RECAP_MIN_TOKENS),
                f"recap cut to ~{RECAP_MIN_TOKENS} tokens",
            ),
            (
                "codex",
                lambda: "\n".join(character_entry(c, full=False) for c in present),
                "codex cut to names + voice cards",
            ),
        ]
        for name, shrink, why in steps:
            if total <= budget:
                break
            new_text = shrink()
            if new_text == sections[name].text:
                continue
            sections[name].text = new_text
            sections[name].disposition = "dropped" if not new_text else "trimmed"
            sections[name].reason = why
            rp = render()
            total = self._count(rp.messages)
        if total > budget:
            raise ContextTooSmall(
                f"{scene_id}: prompt needs {total} tokens, budget is {budget} (num_ctx {limit}); "
                "raise num_ctx for the prose profile or shorten the scene card"
            )
        # manifest: tokens and cumulative prefix hash per section, in render order
        running = ""
        out_sections = []
        for name in ("spec", "codex", "chapter", "recap", "card"):
            sec = sections[name]
            sec.tokens = self._count_text(sec.text) if sec.text else 0
            if not sec.text and sec.disposition == "included":
                sec.disposition, sec.reason = "dropped", sec.reason or "empty"
            running += sec.text
            out_sections.append(
                ManifestSection(
                    name=name,
                    source_refs=sec.sources,
                    tokens=sec.tokens,
                    disposition=sec.disposition,
                    reason=sec.reason,
                    prefix_hash=sha256_text(running)[:16],
                )
            )
        manifest = ContextManifest(
            job_id=job_id,
            model=self.router.profile(self.profile).model,
            context_limit=limit,
            budget_input=budget,
            output_reserved=reserve,
            sections=out_sections,
            total_tokens=total,
        )
        max_tokens = min(reserve, int(target * 1.33 * 1.3))
        return BuiltContext(rp, manifest, sections, max_tokens)

    def _recap(self, scene_id: str) -> Section:
        """Last ~400 tokens of the previous scene's active draft (narrative order)."""
        order = self.store.scenes_in_order()
        i = order.index(scene_id)
        for prev in reversed(order[:i]):
            m = self.store.meta("draft", prev)
            if m.active:
                text = self.store.read("draft", prev)
                return Section(
                    "recap", self._cut(text, RECAP_TOKENS, tail=True), [f"draft:{prev}@v{m.active}"]
                )
        return Section("recap", "", [], "dropped", "first scene: nothing before it")

    def _cut(self, text: str, tokens: int, *, tail: bool = True) -> str:
        if not text or self._count_text(text) <= tokens:
            return text
        n = int(tokens * CHARS_PER_TOKEN)
        cut = text[-n:] if tail else text[:n]
        if tail:  # start at a sentence or paragraph boundary
            for sep in ("\n\n", ". ", "! ", "? "):
                k = cut.find(sep)
                if 0 <= k < len(cut) // 3:
                    return "…" + cut[k + len(sep) :].lstrip()
        return "…" + cut.lstrip()
