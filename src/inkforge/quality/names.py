"""Deterministic draft checks: name/alias consistency (the Phase 4 continuity rule), participants
present, unknown capitalized names (docs/05 Stage E, docs/07 § 1).

Every finding carries verbatim evidence: scene, version, character span and the quote itself.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from inkforge.domain.models import Character, CodexEntry

_WORD = re.compile(r"\b[A-Z][a-zA-Z'’-]{2,}\b")
# Capitalized words that are never names on their own (sentence starters, titles, days…).
_COMMON = set(
    [
        "The",
        "A",
        "An",
        "And",
        "But",
        "Or",
        "So",
        "Yet",
        "For",
        "Nor",
        "If",
        "Then",
        "When",
        "While",
        "Where",
        "What",
        "Who",
        "Why",
        "How",
        "Which",
        "This",
        "That",
        "These",
        "Those",
        "He",
        "She",
        "It",
        "They",
        "We",
        "You",
        "I",
        "His",
        "Her",
        "Its",
        "Their",
        "Our",
        "Your",
        "My",
        "Mine",
        "Him",
        "Them",
        "Us",
        "Me",
        "Not",
        "No",
        "Yes",
        "Oh",
        "Ah",
        "Still",
        "Even",
        "Just",
        "There",
        "Here",
        "Now",
        "Once",
        "Again",
        "After",
        "Before",
        "Above",
        "Below",
        "Inside",
        "Outside",
        "Upon",
        "With",
        "Without",
        "Into",
        "Onto",
        "From",
        "Over",
        "Under",
        "Mr",
        "Mrs",
        "Ms",
        "Miss",
        "Dr",
        "Sir",
        "Lady",
        "Lord",
        "Captain",
        "Father",
        "Mother",
        "Aunt",
        "Uncle",
        "God",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Every",
        "Each",
        "Some",
        "Any",
        "All",
        "None",
        "Something",
        "Nothing",
        "Someone",
        "Everyone",
        "Maybe",
        "Perhaps",
        "Only",
        "Then",
        "Later",
        "Soon",
        "Finally",
        "Instead",
        "Something",
        "Somewhere",
        "Nobody",
        "Everything",
        "Anything",
        "Why",
        "Because",
        "Although",
        "Though",
        "Whatever",
        "Whoever",
    ]
)


@dataclass(frozen=True)
class NameHit:
    span: tuple[int, int]
    found: str
    expected: str
    entity_id: str


def _base(word: str) -> str:
    """Drop a possessive suffix: "James's" -> "James", "Ross'" -> "Ross" (not rstrip, which eats letters)."""
    return re.sub(r"['’]s$|['’]$", "", word)


_CONTRACTION = re.compile(r"['’](?:d|m|ll|ve|re|t)$|n['’]t$", re.IGNORECASE)


def _is_contraction(word: str) -> bool:
    """ "I'd", "She'll", "Don't", "It's" (when the stem is a pronoun) are not names."""
    return bool(_CONTRACTION.search(word)) or _base(word) in _COMMON


def _edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Damerau (optimal string alignment) distance: a swapped pair of letters counts as one edit —
    the commonest name typo ("Astird" for "Astrid"). Exits early above `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
        if min(d[i]) > limit:
            return limit + 1
    return d[len(a)][len(b)]


def _name_parts(entry: CodexEntry) -> dict[str, str]:
    """Every name token we know for an entity → its canonical display name."""
    parts: dict[str, str] = {}
    for full in (entry.name, *entry.aliases):
        for tok in re.findall(r"[A-Za-z'’-]{3,}", full):
            if tok[0].isupper():
                parts[tok] = full
    return parts


def name_inconsistencies(text: str, entries: list[CodexEntry]) -> list[NameHit]:
    """Capitalized words within edit distance 1–2 of a known name/alias token but not equal to any
    known token: likely misspellings ("Astird" for "Astrid"). Distance 2 only for tokens ≥ 7 letters."""
    known: dict[str, tuple[str, str]] = {}
    for e in entries:
        for tok, full in _name_parts(e).items():
            known[tok] = (full, e.id)
    known_lower = {k.lower() for k in known}
    hits = []
    for m in _WORD.finditer(text):
        if _is_contraction(m.group()):
            continue
        w = _base(m.group())
        if w in known or w.lower() in known_lower or w in _COMMON or len(w) < 4:
            continue
        for tok, (_full, eid) in known.items():
            limit = 2 if len(tok) >= 7 else 1
            if tok[0].lower() == w[0].lower() and _edit_distance(w.lower(), tok.lower(), limit) <= limit:
                hits.append(NameHit((m.start(), m.start() + len(w)), w, tok, eid))
                break
    return hits


def mentions(text: str, entry: CodexEntry) -> bool:
    lowered = text.lower()
    return any(tok.lower() in lowered for tok in _name_parts(entry)) or entry.name.lower() in lowered


def missing_participants(text: str, participants: list[Character]) -> list[Character]:
    return [c for c in participants if not mentions(text, c)]


def unknown_names(text: str, entries: list[CodexEntry], min_count: int = 2) -> list[str]:
    """Capitalized words used ≥ min_count times mid-sentence that aren't in the Codex: likely new
    characters or places the model invented. Reported as candidates, never errors (docs/05)."""
    known = {tok.lower() for e in entries for tok in _name_parts(e)}
    counts: Counter[str] = Counter()
    for m in _WORD.finditer(text):
        before = text[max(0, m.start() - 2) : m.start()]
        if re.search(r"[.!?\"“]\s*$|^\s*$", before) and m.start() > 0:
            continue  # sentence-initial: capitalized anyway
        if _is_contraction(m.group()):
            continue
        w = _base(m.group())
        if w.isupper():
            continue  # shouted words and stray headings are not names
        if w not in _COMMON and w.lower() not in known and len(w) >= 3:
            counts[w] += 1
    return sorted(w for w, n in counts.items() if n >= min_count)
