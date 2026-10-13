"""Context builder v2 for drafting (docs/06, Phase 5).

Sections are *rendered* stable-first so consecutive calls share a KV-cache prefix (D25):

    spec → CORE → codex → digests → chapter plan → RECALL → recap → scene card

and *trimmed* by priority when the prompt doesn't fit (docs/06 § Budgeting):

    RECALL passages, lowest-ranked first → digests (acts → earlier chapters → this chapter) →
    chapter plan → recap to ~150 tokens → codex to names + voice cards → ContextTooSmall

The scene card, the instructions and CORE (canon) are never trimmed. Memory comes from each scene's
effective version (D34). A missing or stale digest is replaced by an on-the-fly extract, so memory
can't block drafting. Every build returns a ContextManifest: what was included, trimmed or dropped,
from which sources, and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from inkforge.domain.codex import active_characters
from inkforge.domain.models import Character, ContextManifest, Location, ManifestSection, SceneCard
from inkforge.memory.core import build_core
from inkforge.memory.digests import LIMITS, DigestStore, Kind, extract_summary
from inkforge.memory.effective import effective_text, scenes_before
from inkforge.memory.recall import Passage, RecallIndex
from inkforge.models.router import ModelRouter
from inkforge.models.types import Message
from inkforge.pipeline.blocks import spec_block
from inkforge.prompts.registry import PromptRegistry, RenderedPrompt
from inkforge.storage.hashing import sha256_text
from inkforge.storage.workspace import ProjectStore

RECAP_TOKENS = 400
RECALL_LIMIT = 8
RENDER_ORDER = ("spec", "core", "codex", "digests", "chapter", "recall", "recap", "card")
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
        self.digests = DigestStore(store)
        self.recall = RecallIndex(store)

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

    # ------------------------------------------------------------ digests ("the story so far")
    def _digest_text(self, kind: Kind, target: str) -> str | None:
        """A fresh stored digest, else an on-the-fly extract (stale/missing digests never block drafting)."""
        d = self.digests.get(kind, target)
        if d is not None and self.digests.is_fresh(kind, target):
            return d.text()
        if kind == "scene":
            got = effective_text(self.store, target)
            return extract_summary(got[1], LIMITS["scene"]) if got else None
        return d.text() if d is not None else None  # a stale group digest still beats nothing

    def _digest_tiers(
        self, scene_id: str, chapter: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
        """(earlier acts, earlier chapters of this act, earlier scenes of this chapter) as (ref, line)."""
        s = self.store
        outline = s.try_read("outline")
        acts: list[tuple[str, str]] = []
        chapters: list[tuple[str, str]] = []
        if outline:
            order = outline.ordered_chapter_ids()
            act_of = {c.id: c.act_id for c in outline.chapters}
            here_act = act_of.get(chapter)
            before = order[: order.index(chapter)] if chapter in order else []
            seen_acts: list[str] = []
            for ch in before:
                a = act_of.get(ch)
                if a and a != here_act:
                    if a not in seen_acts:
                        seen_acts.append(a)
                elif (text := self._digest_text("chapter", ch)) is not None:
                    chapters.append((f"digest:chapter:{ch}", f"{ch}: {text}"))
            for a in seen_acts:
                text = self._digest_text("act", a)
                if text is not None:
                    acts.append((f"digest:act:{a}", f"{a}: {text}"))
                else:  # act digest not built yet: fall back to its chapters
                    for ch in before:
                        if act_of.get(ch) == a and (t := self._digest_text("chapter", ch)) is not None:
                            acts.append((f"digest:chapter:{ch}", f"{ch}: {t}"))
        scenes = []
        for sid in [x for x in scenes_before(s, scene_id) if x.startswith(f"{chapter}-")]:
            if (text := self._digest_text("scene", sid)) is not None:
                scenes.append((f"digest:scene:{sid}", f"{sid}: {text}"))
        return acts, chapters, scenes

    # ------------------------------------------------------------ build
    def build(
        self, scene_id: str, *, job_id: str, target_words: int | None = None, memory: bool = True
    ) -> BuiltContext:
        s = self.store
        card: SceneCard = s.read("card", scene_id)
        chars = {c.id: c for c in active_characters(s)}
        names = {cid: c.name for cid, c in chars.items()}
        loc = s.get_codex(card.location_id) if card.location_id else None
        location = loc if isinstance(loc, Location) else None
        present = [chars[p] for p in card.participants if p in chars]
        plan = s.read("chapter", card.chapter_id)
        target = target_words or card.target_length or 1500
        spec = s.read("spec")
        limit, reserve, budget = self.budget()

        recap = self._recap(scene_id)
        chapter_head = f"CHAPTER: {plan.title}\nPurpose: {plan.purpose}\nConflict: {plan.conflict}\nOutcome: {plan.outcome}"
        core = build_core(s, scene_id, self._count_text) if memory else None
        acts, chapters, scenes = self._digest_tiers(scene_id, card.chapter_id) if memory else ([], [], [])
        passages = (
            self.recall.retrieve(scene_id, limit=RECALL_LIMIT, exclude_text=recap.text) if memory else []
        )
        # the scene directly before this one is already quoted in the recap: don't spend recall on its ending
        prev_ref = recap.sources[0].split("@")[0].removeprefix("draft:") if recap.sources else None

        def digest_section(
            tiers: list[list[tuple[str, str]]], note: str | None = None, trimmed: bool = False
        ) -> Section:
            rows = [row for tier in tiers for row in tier]
            return Section(
                "digests",
                "\n".join(line for _, line in rows),
                [ref for ref, _ in rows],
                "trimmed" if trimmed and rows else ("included" if rows else "dropped"),
                note or (None if rows else "no earlier text yet"),
            )

        def recall_section(ps: list[Passage], note: str | None = None) -> Section:
            text = "\n\n".join(f'[{p.scene_id}] "{p.text}"' for p in ps)
            state = "included" if len(ps) == len(passages) and ps else ("trimmed" if ps else "dropped")
            return Section(
                "recall",
                text,
                [p.ref for p in ps],
                state,
                note or (None if ps else ("nothing relevant found" if memory else "memory off")),
            )

        sections: dict[str, Section] = {
            "spec": Section("spec", spec_block(spec), ["spec"]),
            "core": Section(
                "core",
                core.text() if core else "",
                core.sources if core else [],
                "included" if core and core.lines else "dropped",
                (f"{core.dropped} low-priority lines over the CORE budget" if core and core.dropped else None)
                if core and core.lines
                else "no canon facts, rules or promises yet",
            ),
            "codex": Section(
                "codex",
                "\n".join(character_entry(c, full=True) for c in present),
                [f"codex:{c.id}" for c in present] + ([f"codex:{location.id}"] if location else []),
            ),
            "digests": digest_section([acts, chapters, scenes]),
            "chapter": Section("chapter", chapter_head, [f"chapter:{plan.id}"]),
            "recall": recall_section(
                [p for p in passages if not (p.scene_id == prev_ref and p.text[-80:] in recap.text)]
            ),
            "recap": recap,
            "card": Section("card", card_block(card, names, location), [f"card:{scene_id}"]),
        }
        kept = [p for p in passages if not (p.scene_id == prev_ref and p.text[-80:] in recap.text)]

        def render() -> RenderedPrompt:
            return self.prompts.render(
                "draft.scene",
                4,
                spec_block=sections["spec"].text,
                core_block=sections["core"].text,
                codex_block=sections["codex"].text,
                digest_block=sections["digests"].text,
                chapter_block=sections["chapter"].text,
                recall_block=sections["recall"].text,
                recap=sections["recap"].text,
                card_block=sections["card"].text,
                target_words=target,
                pov=spec.pov or "third-limited",
                tense=spec.tense or "past",
                pov_name=names.get(card.pov_character_id or "", "the POV character"),
            )

        rp = render()
        total = self._count(rp.messages)

        def fits() -> bool:
            return total <= budget

        # ---- trimming ladder (docs/06). Scene card, instructions and CORE (canon) are never trimmed.
        while not fits() and kept:  # 1. lowest-ranked recalled passage first
            kept = kept[:-1]
            sections["recall"] = recall_section(kept, "lowest-ranked passages dropped to fit")
            rp = render()
            total = self._count(rp.messages)
        for tiers, why in (  # 2. digests: far past first (acts → earlier chapters → this chapter's scenes)
            ([chapters, scenes], "act digests dropped to fit"),
            ([chapters[-1:], scenes], "only the previous chapter and this chapter kept"),
            ([scenes], "only this chapter's scenes kept"),
            ([], "digests dropped to fit"),
        ):
            if fits():
                break
            new = digest_section(tiers, why, trimmed=True)
            if new.text == sections["digests"].text:
                continue
            sections["digests"] = new
            rp = render()
            total = self._count(rp.messages)
        steps = [
            ("chapter", lambda: "", "chapter plan dropped to fit"),
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
            if fits():
                break
            new_text = shrink()
            if new_text == sections[name].text:
                continue
            sections[name].text = new_text
            sections[name].disposition = "dropped" if not new_text else "trimmed"
            sections[name].reason = why
            rp = render()
            total = self._count(rp.messages)
        if not fits():
            raise ContextTooSmall(
                f"{scene_id}: prompt needs {total} tokens, budget is {budget} (num_ctx {limit}); "
                "raise num_ctx for the prose profile or shorten the scene card"
            )
        # ---- manifest: tokens and cumulative prefix hash per section, in render order (D25)
        running = ""
        out_sections = []
        for name in RENDER_ORDER:
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
        """Last ~400 tokens of the previous scene's effective text (narrative order, D34)."""
        for prev in reversed(scenes_before(self.store, scene_id)):
            got = effective_text(self.store, prev)
            if got is not None:
                eff, text = got
                return Section("recap", self._cut(text, RECAP_TOKENS, tail=True), [eff.ref])
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
