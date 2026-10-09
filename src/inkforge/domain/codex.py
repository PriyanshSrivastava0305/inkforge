"""Codex queries shared by stages: which entities are usable, and name → id resolution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from inkforge.domain.models import Character, CodexStatus, Location

if TYPE_CHECKING:
    from inkforge.storage.workspace import ProjectStore


def active_characters(store: ProjectStore) -> list[Character]:
    """Accepted and candidate characters (candidates are usable before review; rejected ones never are)."""
    return [
        c
        for c in store.list_codex("character")
        if isinstance(c, Character) and c.codex_status != CodexStatus.REJECTED
    ]


def active_locations(store: ProjectStore) -> list[Location]:
    return [
        c
        for c in store.list_codex("location")
        if isinstance(c, Location) and c.codex_status != CodexStatus.REJECTED
    ]


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    n = re.sub(r"^(the|a|an) ", "", n.strip())
    return re.sub(r"\s+", " ", n).strip()


def find_by_name(entities: list, name: str):
    """Match on normalized name or alias ("The Lighthouse" == "lighthouse")."""
    target = _norm(name)
    for e in entities:
        if target in {_norm(e.name), *(_norm(a) for a in e.aliases)}:
            return e
    return None
