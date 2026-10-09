"""Stable, prefixed IDs minted by code — never by the model (docs/04 § Conventions, D6/D7).

IDs are never renumbered: ordering lives in explicit `order` fields / the outline.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated

from pydantic import StringConstraints

SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"

PATTERNS: dict[str, str] = {
    "chapter": r"ch\d{2,3}",
    "scene": r"ch\d{2,3}-sc\d{2,3}",
    "act": r"act\d{1,2}",
    "character": rf"char-{SLUG}",
    "location": rf"loc-{SLUG}",
    "item": rf"obj-{SLUG}",
    "lore": rf"lore-{SLUG}",
    "promise": r"prom-\d{3,}",
    "fact": r"fact-\d{4,}",
    "finding": r"find-\d{4,}",
    "revision": r"rev-\d{4,}",
    "job": r"job-\d{8}-\d{6}-[0-9a-f]{4}",
    "project": SLUG,
}


def _p(kind: str) -> StringConstraints:
    return StringConstraints(pattern=f"^{PATTERNS[kind]}$")


# Spelled out (not built in a loop) so type checkers see real type aliases.
ChapterId = Annotated[str, _p("chapter")]
SceneId = Annotated[str, _p("scene")]
ActId = Annotated[str, _p("act")]
CharacterId = Annotated[str, _p("character")]
LocationId = Annotated[str, _p("location")]
ItemId = Annotated[str, _p("item")]
LoreId = Annotated[str, _p("lore")]
PromiseId = Annotated[str, _p("promise")]
FactId = Annotated[str, _p("fact")]
FindingId = Annotated[str, _p("finding")]
RevisionId = Annotated[str, _p("revision")]
JobId = Annotated[str, _p("job")]
ProjectId = Annotated[str, _p("project")]
CodexId = Annotated[
    str,
    StringConstraints(
        pattern=f"^(?:{PATTERNS['character']}|{PATTERNS['location']}|{PATTERNS['item']}|{PATTERNS['lore']})$"
    ),
]

CODEX_PREFIX = {"character": "char", "location": "loc", "item": "obj", "lore": "lore"}
COUNTER_PREFIX = {"promise": ("prom", 3), "fact": ("fact", 4), "finding": ("find", 4), "revision": ("rev", 4)}


def is_valid(kind: str, value: str) -> bool:
    return re.fullmatch(PATTERNS[kind], value) is not None


def slugify(text: str, max_len: int = 48) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def unique(base: str, existing: Iterable[str]) -> str:
    taken = set(existing)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def codex_id(kind: str, name: str, existing: Iterable[str]) -> str:
    """`char-mira`, `char-mira-2` on collision."""
    return unique(f"{CODEX_PREFIX[kind]}-{slugify(name, 40)}", existing)


def counter_id(kind: str, n: int) -> str:
    prefix, width = COUNTER_PREFIX[kind]
    return f"{prefix}-{n:0{width}d}"


def chapter_id(existing: Iterable[str]) -> str:
    """Next unused chapter number. Gaps left by deletions are not reused."""
    nums = [int(c[2:]) for c in existing if is_valid("chapter", c)]
    return f"ch{(max(nums, default=0) + 1):02d}"


def scene_id(chapter: str, existing: Iterable[str]) -> str:
    nums = [int(s.split("-sc")[1]) for s in existing if is_valid("scene", s) and s.startswith(f"{chapter}-")]
    return f"{chapter}-sc{(max(nums, default=0) + 1):02d}"


def job_id(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"job-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
