"""Deterministic checks (docs/07 § 1) as evidence-linked findings. Cheap, no model, run on every check.

| detector              | finds                                                        | severity |
|-----------------------|--------------------------------------------------------------|----------|
| rule:repetition       | a phrase repeated 3+ times in the scene; 3+ sentences in a    | low      |
|                       | row with the same opening word; a long phrase copied from    |          |
|                       | the previous scene                                           |          |
| rule:slop             | stock phrases / AI tells (configurable), dash overuse        | low      |
| rule:meta             | model talk, headings, bracketed instructions left in prose   | med      |
| rule:pov              | first-person narration in a third-person book (and back)     | high     |
| rule:tense            | a narration paragraph in the wrong tense                     | med      |
| rule:length           | scene far from its card's target                             | low      |
| rule:participants     | a character on the card who is never named                   | med      |

Dialogue is excluded from the POV and tense checks: characters speak in any person and tense.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator

from inkforge.domain.codex import active_characters
from inkforge.domain.models import Evidence, Finding
from inkforge.memory.effective import effective_text, scenes_before
from inkforge.pipeline.text import meta_leaks, word_count
from inkforge.quality.names import missing_participants
from inkforge.storage.workspace import ProjectStore

DETECTORS = (
    "rule:repetition",
    "rule:slop",
    "rule:meta",
    "rule:pov",
    "rule:tense",
    "rule:length",
    "rule:participants",
)
SLOP = (
    "a testament to",
    "sent shivers down",
    "shivers down her spine",
    "shivers down his spine",
    "let out a breath she didn't know",
    "let out a breath he didn't know",
    "breath she didn't know she was holding",
    "breath he didn't know he was holding",
    "the weight of the world",
    "in that moment",
    "little did she know",
    "little did he know",
    "a dance of",
    "tapestry of",
    "palpable",
    "barely above a whisper",
    "eyes widened in",
    "time seemed to stand still",
    "it was as if",
    "suddenly",
    "a mix of",
    "couldn't help but",
)
LENGTH_TOLERANCE = 0.35
DASHES_PER_1000 = 12
_STOP = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
    ]
)
_QUOTE = re.compile(r"[“\"][^”\"]*[”\"]")
_PRESENT = re.compile(r"\b(?:is|are|am|says|does|has|walks|looks|turns|stands|sits|goes|comes|takes|knows)\b")
_PAST = re.compile(r"\b(?:was|were|said|did|had|walked|looked|turned|stood|sat|went|came|took|knew)\b")
_FIRST = re.compile(r"\b(?:I|me|my|myself)\b")


def _finding(scene: str, version: int, text: str, span: tuple[int, int], detector: str, category: str,
             severity: str, claim: str, confidence: float = 0.9) -> Finding:  # fmt: skip
    return Finding(
        id="find-0000",
        scene_id=scene,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        claim=claim,
        evidence=[Evidence(scene_id=scene, version=version, span=span, quote=text[span[0] : span[1]])],
        detector=detector,
        confidence=confidence,
    )


def paragraphs(text: str) -> Iterator[tuple[int, str]]:
    pos = 0
    for para in re.split(r"\n\s*\n", text):
        start = text.find(para, pos)
        pos = start + len(para)
        if para.strip():
            yield start, para


def sentences(text: str) -> Iterator[tuple[int, int]]:
    for m in re.finditer(r"[^.!?…\n]+[.!?…]*[\"”’)]*", text):
        if m.group().strip():
            s = m.start() + len(m.group()) - len(m.group().lstrip())
            yield s, m.end()


def _narration(para: str) -> str:
    return _QUOTE.sub(lambda m: " " * len(m.group()), para)  # keep offsets


# ------------------------------------------------------------------ checks
def repetition(scene: str, version: int, text: str, previous: str | None) -> list[Finding]:
    out: list[Finding] = []
    words = [(m.start(), m.end(), m.group().lower()) for m in re.finditer(r"[A-Za-z']+", text)]
    grams: dict[tuple[str, ...], list[tuple[int, int]]] = {}
    for i in range(len(words) - 3):
        g = tuple(w for _, _, w in words[i : i + 4])
        if sum(w in _STOP for w in g) >= 3:
            continue
        grams.setdefault(g, []).append((words[i][0], words[i + 3][1]))
    reported: set[tuple[str, ...]] = set()
    for g, spans in grams.items():
        if len(spans) >= 3 and not any(set(g) <= set(r) for r in reported):
            reported.add(g)
            out.append(_finding(scene, version, text, spans[-1], "rule:repetition", "prose", "low",
                                f"“{' '.join(g)}” appears {len(spans)} times in this scene"))  # fmt: skip
    sents = list(sentences(_narration(text)))
    run: list[tuple[int, int]] = []
    last = None
    for s, e in sents:
        first = re.match(r"\W*([A-Za-z']+)", text[s:e])
        word = first.group(1).lower() if first else None
        if word and word == last and word not in ("i",):
            run.append((s, e))
        else:
            if len(run) >= 3:
                out.append(_finding(scene, version, text, (run[0][0], run[-1][1]), "rule:repetition", "prose", "low",
                                    f"{len(run)} sentences in a row open with “{last}”"))  # fmt: skip
            run = [(s, e)] if word else []
        last = word
    if len(run) >= 3:
        out.append(_finding(scene, version, text, (run[0][0], run[-1][1]), "rule:repetition", "prose", "low",
                            f"{len(run)} sentences in a row open with “{last}”"))  # fmt: skip
    if previous:
        prev = re.sub(r"\s+", " ", previous.lower())
        for s, e in sentences(text):
            chunk = re.sub(r"\s+", " ", text[s:e].lower()).strip()
            if len(chunk.split()) >= 8 and chunk in prev:
                out.append(_finding(scene, version, text, (s, e), "rule:repetition", "prose", "med",
                                    "this sentence is copied from the previous scene"))  # fmt: skip
    return out


def slop(scene: str, version: int, text: str, phrases: tuple[str, ...] | list[str] = SLOP) -> list[Finding]:
    out = []
    for phrase in phrases:
        m = re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE)
        if m:
            n = len(re.findall(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE))
            times = f" ({n}×)" if n > 1 else ""
            out.append(_finding(scene, version, text, (m.start(), m.end()), "rule:slop", "prose", "low",
                                f"stock phrase “{phrase}”{times}", 0.7))  # fmt: skip
    dashes = [m.span() for m in re.finditer(r"—|--", text)]
    words = max(1, word_count(text))
    if len(dashes) * 1000 / words > DASHES_PER_1000 and len(dashes) >= 4:
        out.append(_finding(scene, version, text, dashes[len(dashes) // 2], "rule:slop", "prose", "low",
                            f"{len(dashes)} dashes in {words} words — heavy dash use", 0.6))  # fmt: skip
    return out


def meta(scene: str, version: int, text: str) -> list[Finding]:
    out = []
    for pos, snippet in meta_leaks(text):
        eol = text.find("\n", pos)
        end = min(pos + 80, len(text) if eol < 0 else eol)
        out.append(_finding(scene, version, text, (pos, end), "rule:meta", "format", "med",
                            f"model or prompt text left in the prose: {snippet[:60]!r}"))  # fmt: skip
    for m in re.finditer(r"\[[^\]\n]{3,80}\]", text):
        out.append(_finding(scene, version, text, m.span(), "rule:meta", "format", "med",
                            "bracketed note or instruction left in the prose"))  # fmt: skip
    return out


def pov_and_tense(scene: str, version: int, text: str, pov: str, tense: str) -> list[Finding]:
    out = []
    for start, para in paragraphs(text):
        narr = _narration(para)
        first = list(_FIRST.finditer(narr))
        if pov != "first" and len(first) >= 2:
            m = first[0]
            out.append(_finding(scene, version, text, (start + m.start(), start + m.end()), "rule:pov", "prose", "high",
                                f"first-person narration (“{m.group()}”) in a {pov} book", 0.7))  # fmt: skip
        present, past = len(_PRESENT.findall(narr.lower())), len(_PAST.findall(narr.lower()))
        wrong, right = (present, past) if tense == "past" else (past, present)
        if wrong >= 3 and wrong > 2 * right:
            pat = _PRESENT if tense == "past" else _PAST
            m = pat.search(narr.lower())
            assert m is not None
            out.append(_finding(scene, version, text, (start + m.start(), start + m.end()), "rule:tense", "prose", "med",
                                f"this paragraph's narration is in the {'present' if tense == 'past' else 'past'} "
                                f"tense; the book is in the {tense} tense", 0.6))  # fmt: skip
    return out


def length(scene: str, version: int, text: str, target: int | None) -> list[Finding]:
    if not target or not text.strip():
        return []
    words = word_count(text)
    if abs(words - target) <= target * LENGTH_TOLERANCE:
        return []
    last = list(sentences(text))[-1]
    return [_finding(scene, version, text, last, "rule:length", "pacing", "low",
                     f"{words} words against a target of {target}", 0.9)]  # fmt: skip


def check_text(store: ProjectStore, scene: str, version: int, text: str) -> list[Finding]:
    """All deterministic findings for one scene text (the effective version, or a revision candidate)."""
    spec = store.try_read("spec")
    card = store.try_read("card", scene)
    pov = spec.pov if spec else "third-limited"
    tense = spec.tense if spec else "past"
    before = scenes_before(store, scene)
    prev = effective_text(store, before[-1]) if before else None
    found = [
        *repetition(scene, version, text, prev[1] if prev else None),
        *slop(scene, version, text, store.project.settings.get("slop_phrases", SLOP)),
        *meta(scene, version, text),
        *pov_and_tense(scene, version, text, pov, tense),
        *length(scene, version, text, card.target_length if card else None),
    ]
    if card is not None:
        skip = {card.pov_character_id} if pov == "first" else set()
        present = [c for c in active_characters(store) if c.id in card.participants and c.id not in skip]
        for c in missing_participants(text, present):
            first = next(sentences(text), (0, min(len(text), 80)))
            found.append(_finding(scene, version, text, first, "rule:participants", "clarity", "med",
                                  f"{c.name} is on the scene card but never named in the scene", 0.8))  # fmt: skip
    return found


def counts(found: list[Finding]) -> Counter[str]:
    return Counter(f.detector for f in found)
