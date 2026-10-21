# pyright: reportInvalidTypeForm=false
"""Fact extraction (docs/07 § 2): a model proposes facts with the words that state them; code decides.

The model's output is never trusted on its own. Each proposed fact is kept only if
  1. its quote is found in the scene text (exactly, or ≥ 0.95 similar after normalising quotes and spaces),
  2. its subject is a Codex id we showed the model,
  3. a value that should be an entity resolves to one, and a value that should be literal (an eye
     colour, a rank) actually appears in the quoted words.
Everything else is discarded with a reason. Nothing here writes to canon: state facts go to the
**review queue** as candidates, and only an author action (`quality/canon.py`) makes them canon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field, create_model

from inkforge.domain.models import CanonStatus, CodexEntry, Fact, SourceRef
from inkforge.memory.effective import effective_text
from inkforge.models.router import GenerationCancelled, ModelRouter
from inkforge.models.types import CancelToken, ModelError
from inkforge.pipeline.proposals import _enum
from inkforge.prompts.registry import PromptRegistry
from inkforge.quality.rules import Observation, World
from inkforge.storage.workspace import ProjectStore

# state predicates may become canon once accepted; event predicates are only ever checked
STATE = (
    "eye_color",
    "hair",
    "height",
    "handedness",
    "title_rank",
    "age",
    "status",
    "location_at",
    "held_by",
    "located_at",
    "condition",
    "date_of_event",
    "departs_on",
)
EVENT = ("active", "has_item", "mentions_fact")
PERSON_PREDICATES = {
    "active",
    "has_item",
    "status",
    "age",
    "eye_color",
    "hair",
    "height",
    "handedness",
    "title_rank",
    "location_at",
    "departs_on",
    "mentions_fact",
}
SYNONYMS = {  # what the model writes → the controlled value
    "whole": "intact",
    "untouched": "intact",
    "undamaged": "intact",
    "unbroken": "intact",
    "shattered": "destroyed",
    "smashed": "destroyed",
    "ruined": "destroyed",
    "cracked": "broken",
    "chipped": "broken",
    "missing": "lost",
    "gone": "lost",
    "deceased": "dead",
    "killed": "dead",
    "wounded": "injured",
    "hurt": "injured",
    "left-handed": "left",
    "right-handed": "right",
}
ENTITY_VALUE = {"location_at": "loc-", "located_at": "loc-", "held_by": "char-", "has_item": "obj-"}
LITERAL_IN_QUOTE = {"eye_color", "hair", "handedness", "title_rank"}  # the value must be in the quoted words
MAX_QUOTE_WORDS = 40
ALLOWED_VALUES = {
    "status": {"dead", "injured"},  # "alive" is the default, not a fact worth recording
    "condition": {"intact", "broken", "lost", "destroyed"},
    "handedness": {"left", "right"},
}
SUBJECT_KIND = {"held_by": "obj-", "located_at": "obj-", "condition": "obj-"}  # others: any entity
FUZZY = 0.95
MAX_FACTS = 8


class FactProposal(BaseModel):
    subject: str
    predicate: str
    value: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=8, max_length=300)


@dataclass(frozen=True)
class Discarded:
    proposal: FactProposal
    reason: str


@dataclass
class Extraction:
    scene_id: str
    version: int
    observations: list[Observation] = field(default_factory=list)
    candidates: list[Fact] = field(default_factory=list)  # newly queued for review
    discarded: list[Discarded] = field(default_factory=list)
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)  # quote → where it is in the text
    error: str | None = None  # the model call failed; nothing was extracted


# ------------------------------------------------------------------ quote verification
_FOLD = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "…": "."})


def _normalise(text: str) -> tuple[str, list[int]]:
    """Lower-case, straight quotes, single spaces — plus, for each kept character, its original index."""
    out: list[str] = []
    at: list[int] = []
    space = True
    for i, ch in enumerate(text.translate(_FOLD)):
        if ch.isspace():
            if not space:
                out.append(" ")
                at.append(i)
            space = True
        else:
            out.append(ch.lower())
            at.append(i)
            space = False
    return "".join(out).rstrip(), at


def locate_quote(text: str, quote: str) -> tuple[int, int, float] | None:
    """Where `quote` is in `text` as (start, end, similarity), or None if it isn't really there."""
    hay, at = _normalise(text)
    needle = _normalise(quote.strip().strip('"').strip())[0].strip(" .,;:!?\"'")
    if len(needle.split()) < 3 or not hay:
        return None

    def span(a: int, b: int) -> tuple[int, int]:
        return at[a], at[min(b, len(at)) - 1] + 1

    i = hay.find(needle)
    if i >= 0:
        return (*span(i, i + len(needle)), 1.0)
    # near miss (a dropped comma, one changed word): anchor on the longest shared run, compare windows
    m = SequenceMatcher(None, hay, needle, autojunk=False).find_longest_match(0, len(hay), 0, len(needle))
    if m.size < max(12, len(needle) // 3):
        return None
    start = max(0, m.a - m.b)
    best: tuple[float, int, int] | None = None
    for a in range(max(0, start - 3), min(len(hay), start + 4)):
        for n in (len(needle) - 2, len(needle), len(needle) + 2):
            b = min(len(hay), a + n)
            ratio = SequenceMatcher(None, hay[a:b], needle, autojunk=False).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, a, b)
    if best is None or best[0] < FUZZY:
        return None
    return (*span(best[1], best[2]), round(best[0], 3))


# ------------------------------------------------------------------ value grounding
# A real quote can still carry an invented value ("June is 34", quoting a line with no age in it), so
# literal values must be visible in the quoted words. Entity values (ids) and events are not checked here.
_UNITS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_STATE_WORDS = {
    "dead": r"dead|died|dies|death|body|corpse|killed|drowned|buried|late",
    "injured": r"injur|wound|hurt|bleed|bled|blood|broken|limp",
    "destroyed": r"destroy|shatter|smash|broke|ruin|burn|gone",
    "broken": r"broke|crack|shatter|smash|chip",
    "lost": r"lost|missing|gone|vanish",
    "intact": r"intact|whole|untouched|unbroken|undamaged|unharmed|perfect",
}


def numbers_in(text: str) -> set[int]:
    """Numbers written as digits or words (up to 99): "twenty-one", "21", "six"."""
    found = {int(d) for d in re.findall(r"\b\d{1,3}\b", text)}
    for m in re.finditer(
        r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ]([a-z]+))?\b", text
    ):
        unit = _UNITS.index(m.group(2)) if m.group(2) in _UNITS[1:10] else 0
        found.add(_TENS[m.group(1)] + unit)
    found |= {i for i, w in enumerate(_UNITS) if re.search(rf"\b{w}\b", text)}
    return found


def grounded(predicate: str, value: str, quoted: str) -> bool:
    """Is `value` actually stated in the (normalised, lower-case) quoted words?"""
    if predicate in LITERAL_IN_QUOTE:
        return all(re.search(rf"\b{re.escape(w)}\b", quoted) for w in value.lower().split())
    if predicate == "age":
        return value.isdigit() and int(value) in numbers_in(quoted)
    if predicate in ("status", "condition"):
        return bool(re.search(rf"\b(?:{_STATE_WORDS.get(value, re.escape(value))})", quoted))
    if predicate == "date_of_event":
        _, mo, d = (int(x) for x in value.split("-"))
        month = rf"\b(?:{_MONTHS[mo - 1]}|{_MONTHS[mo - 1][:3]}|0?{mo})\b"
        return bool(re.search(rf"\b0?{d}(?:st|nd|rd|th)?\b", quoted) and re.search(month, quoted))
    return True


# ------------------------------------------------------------------ extraction
_STOP = {"the", "from", "with", "of", "and", "old", "new", "little", "great", "house", "room"}


def _mentions(e: CodexEntry) -> list[str]:
    """Words that suggest the text is about `e`: full name, aliases, a character's first name, and any
    distinctive word of a thing's or place's name ("Tom's letter from Carrow" → letter, carrow).
    Over-including an entity only costs a line of prompt; missing one loses its facts."""
    names = [e.name, *e.aliases]
    if e.id.startswith("char-"):
        names.append(e.name.split()[0])
    else:
        core = re.sub(r"\(.*?\)", " ", e.name.lower())
        words = re.findall(r"[a-z]+(?:'[a-z]+)?", core)
        names += [w for w in words if len(w) > 3 and w not in _STOP and "'" not in w]
    return [n.lower() for n in names if len(n) > 2]


class FactExtractor:
    def __init__(self, store: ProjectStore, router: ModelRouter, prompts: PromptRegistry | None = None):
        self.store, self.router = store, router
        self.prompts = prompts or PromptRegistry(project_dir=store.dir / "prompts")

    # -- what the model is shown
    def entities(self, scene_id: str, text: str) -> list[CodexEntry]:
        """Entities on the scene card, plus any other Codex entry named in the text."""
        card = self.store.try_read("card", scene_id)
        wanted: set[str] = set()
        if card is not None:
            wanted |= {*card.participants, *card.items_present, card.pov_character_id, card.location_id} - {
                None
            }
        low = text.lower()
        out = []
        for e in self.store.list_codex():
            if e.id.startswith("lore-"):
                continue
            if e.id in wanted or any(re.search(rf"\b{re.escape(n)}\b", low) for n in _mentions(e)):
                out.append(e)
        return out

    def secrets(self) -> list[tuple[str, str]]:
        """Facts some character had to learn (`knows_fact` values) as (label, description), so the model
        can spot references. A label may be a sentence already, or a key into `settings["secrets"]`."""
        described: dict[str, str] = self.store.project.settings.get("secrets", {})
        seen = dict.fromkeys(
            str(f.value)
            for f in self.store.facts.all()
            if f.predicate == "knows_fact" and f.canon_status == CanonStatus.CANON
        )
        return [(label, described.get(label, label)) for label in seen]

    def _when(self, scene_id: str) -> str:
        card = self.store.try_read("card", scene_id)
        return str(card.time.story_datetime or "") if card is not None else ""

    @staticmethod
    def schema(entity_ids: list[str], secrets: list[str]) -> type[BaseModel]:
        predicates = [*STATE, *(p for p in EVENT if p != "mentions_fact" or secrets)]
        fact = create_model(
            "ExtractedFact",
            subject=(_enum(entity_ids, "char-none"), ...),
            predicate=(_enum(predicates, "active"), ...),
            value=(str, Field(min_length=1, max_length=80)),
            quote=(str, Field(min_length=8, max_length=300, description="exact words from the scene")),
        )
        return create_model(
            "ExtractedFacts", facts=(list[fact], Field(default_factory=list, max_length=MAX_FACTS))
        )

    # -- the call
    async def extract(
        self, scene_id: str, *, cancel: CancelToken | None = None, queue: bool = True
    ) -> Extraction:
        got = effective_text(self.store, scene_id)
        if got is None:
            raise ValueError(f"{scene_id} has no draft to extract facts from")
        eff, text = got
        result = Extraction(scene_id, eff.version)
        entities = self.entities(scene_id, text)
        if not entities:
            return result
        secrets = self.secrets()
        labels = [label for label, _ in secrets]
        rp = self.prompts.render(
            "facts.extract",
            scene_id=scene_id,
            when=self._when(scene_id),
            text=text,
            entities=entities,
            secrets=secrets,
            max_facts=MAX_FACTS,
        )
        try:
            res = await self.router.generate_structured(
                rp.messages,
                self.schema([e.id for e in entities], labels),
                rp.profile,
                max_tokens=MAX_FACTS * 90 + 80,
                temperature=0.1,
                cancel=cancel,
            )
        except GenerationCancelled:
            raise
        except ModelError as e:
            result.error = str(e)
            return result
        proposals = [
            FactProposal.model_validate(p, from_attributes=True) for p in getattr(res.value, "facts", [])
        ]
        self.verify(result, text, proposals, entities, secrets)
        if queue:
            self._queue(result)
        return result

    # -- the checks
    def verify(
        self,
        result: Extraction,
        text: str,
        proposals: list[FactProposal],
        entities: list[CodexEntry],
        secrets: list[tuple[str, str]],
    ) -> None:
        known = {e.id: e for e in entities}
        about = dict(secrets)
        card = self.store.try_read("card", result.scene_id)
        mode = str(card.temporal_mode) if card else "linear"
        seen: set[tuple[str, str, str]] = set()
        for p in proposals:
            reason, value = self._judge(p, known, list(about))
            found = locate_quote(text, p.quote) if reason is None else None
            if reason is None and found is None:
                reason = "quote is not in the scene"
            if reason is None and found is not None:
                quoted = _normalise(text[found[0] : found[1]])[0]
                if len(quoted.split()) > MAX_QUOTE_WORDS:
                    reason = "quote is too long to point at the words that state the fact"
                elif not grounded(p.predicate, value, quoted):
                    reason = f"“{value}” is not in the quoted words"
                else:
                    reason = self._anchored(p.predicate, p.subject, value, quoted, about, result.scene_id)
            if reason is None and (p.subject, p.predicate, value) in seen:
                reason = "duplicate"
            if reason is not None or found is None:
                result.discarded.append(Discarded(p, reason or "quote is not in the scene"))
                continue
            seen.add((p.subject, p.predicate, value))
            start, end, ratio = found
            quote = text[start:end]
            result.spans[quote] = (start, end)
            result.observations.append(Observation(result.scene_id, p.subject, p.predicate, value, quote))
            if p.predicate in STATE and mode != "dream":  # dreams never feed canon (docs/07)
                confidence = 0.8 if ratio == 1.0 else 0.6
                result.candidates.append(
                    Fact(
                        id="fact-0000",  # minted when queued
                        subject_id=p.subject,
                        predicate=p.predicate,
                        value=value,
                        valid_from=result.scene_id,
                        source_ref=SourceRef(
                            scene_id=result.scene_id, version=result.version, char_span=(start, end)
                        ),
                        quote=quote,
                        origin="extracted",
                        confidence=min(confidence, 0.5) if mode == "unreliable" else confidence,
                    )
                )
        self._derive_presence(result, card)

    def _derive_presence(self, result: Extraction, card: Any) -> None:
        """Someone who acts in a scene is where the scene happens (the card says where). Checked, never
        made canon: the model often reports what a character does but not where they are."""
        if card is None or not card.location_id:
            return
        placed = {o.subject_id for o in result.observations if o.predicate == "location_at"}
        for o in list(result.observations):
            if o.predicate == "active" and o.subject_id not in placed:
                placed.add(o.subject_id)
                result.observations.append(
                    Observation(result.scene_id, o.subject_id, "location_at", card.location_id, o.quote)
                )

    def _anchored(
        self, predicate: str, subject: str, value: str, quoted: str, about: dict[str, str], scene_id: str
    ) -> str | None:
        """Does the quote mention what the fact is about? A quote that never names the thing, place or
        secret can't be evidence for it (8B: "has the locket", quoting a line about stove-pipes)."""

        def named(entity_id: str) -> bool:
            e = self.store.get_codex(entity_id)
            return e is None or any(re.search(rf"\b{re.escape(n)}", quoted) for n in _mentions(e))

        if predicate in ("held_by", "located_at", "condition", "date_of_event") and not named(subject):
            return f"the quote does not name {subject}"
        if predicate in ("has_item", "located_at") and not named(value):
            return f"the quote does not name {value}"
        if predicate == "mentions_fact":
            words = {w[:5] for w in re.findall(r"[a-z]+", about.get(value, value).lower()) if len(w) > 2}
            words -= {
                "the",
                "and",
                "was",
                "had",
                "has",
                "his",
                "her",
                "for",
                "not",
                "but",
                "are",
                "that",
                "with",
                "from",
            }
            if len([w for w in words if re.search(rf"\b{w}", quoted)]) < 2:
                return f"the quote does not say what {value} is"
        if predicate == "departs_on":
            place, _, day = value.partition("@")
            if not named(place):
                return f"the quote does not name {place}"
            d = int(day[-2:])
            same_day = day == self._when(scene_id)[:10]
            if not (
                re.search(rf"\b0?{d}(?:st|nd|rd|th)?\b", quoted)
                or (same_day and re.search(r"\b(?:this|that) morning\b|\btoday\b|\bat dawn\b", quoted))
            ):
                return f"the quote does not give the date {day}"
        return None

    def _judge(self, p: FactProposal, known: dict[str, Any], secrets: list[str]) -> tuple[str | None, str]:
        """(reason to discard or None, normalised value)."""
        value = p.value.strip().strip(".").strip()
        if p.subject not in known:
            return "unknown subject", value
        need = SUBJECT_KIND.get(p.predicate)
        if need and not p.subject.startswith(need):
            return f"{p.predicate} needs an item as its subject", value
        if p.predicate in PERSON_PREDICATES and not p.subject.startswith("char-"):
            return f"{p.predicate} needs a character as its subject", value
        prefix = ENTITY_VALUE.get(p.predicate)
        if prefix:
            resolved = self._resolve(value, prefix)
            if resolved is None:
                return f"“{value}” is not a known {prefix.rstrip('-')} entity", value
            return None, resolved
        if p.predicate == "mentions_fact":
            match = next((s for s in secrets if s.lower() == value.lower()), None)
            return (None, match) if match else ("not one of the listed facts", value)
        if p.predicate == "departs_on":  # "loc-carrow, 1892-10-14" → "loc-carrow@1892-10-14"
            day = re.search(r"\d{4}-\d{2}-\d{2}", value)
            place = self._resolve(re.sub(r"[,;@ ]*\d{4}-\d{2}-\d{2}[,; ]*", " ", value).strip(), "loc-")
            if day is None or place is None:
                return "departure needs a known place and a YYYY-MM-DD date", value
            return None, f"{place}@{day.group()}"
        value = SYNONYMS.get(value.lower(), value.lower())
        if p.predicate in ALLOWED_VALUES and value not in ALLOWED_VALUES[p.predicate]:
            return f"“{value}” is not a {p.predicate} value", value
        if p.predicate == "age":
            digits = re.search(r"\d{1,3}", value)
            return (None, digits.group()) if digits else ("age is not a number", value)
        if p.predicate == "date_of_event" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "date is not YYYY-MM-DD", value
        return None, value

    def _resolve(self, value: str, prefix: str) -> str | None:
        """An id, or a name/alias, of a Codex entity of the right kind (any entity, not only those shown)."""
        low = value.lower().strip()
        pool = [e for e in self.store.list_codex() if e.id.startswith(prefix)]
        for e in pool:
            if low == e.id or low in {e.name.lower(), *(a.lower() for a in e.aliases)}:
                return e.id
        hits = [e.id for e in pool if low and (low in e.name.lower() or e.name.lower() in low)]
        return hits[0] if len(hits) == 1 else None

    # -- the review queue
    def _queue(self, result: Extraction) -> None:
        """Replace this scene's pending candidates with the new ones; skip what canon or a past decision covers."""
        s = self.store
        for old in s.review_queue.all():
            if old.valid_from == result.scene_id and old.canon_status == CanonStatus.CANDIDATE:
                s.remove("review_queue", old.id)

        def key(f: Fact) -> tuple[str, str, str, str | None]:
            return (f.subject_id, f.predicate, str(f.value).lower(), f.valid_from)

        decided = {key(f) for f in s.review_queue.all()}  # rejected earlier: don't ask again
        world = World(s)
        queued: list[Fact] = []
        for f in result.candidates:
            now = world.current(f.subject_id, f.predicate, result.scene_id, inclusive=True)
            if key(f) in decided or (now is not None and str(now.value).lower() == str(f.value).lower()):
                continue  # canon already says so at this point in the story
            f = f.model_copy(update={"id": s.mint("fact")})
            s.upsert("review_queue", f)
            decided.add(key(f))
            queued.append(f)
        result.candidates = queued
