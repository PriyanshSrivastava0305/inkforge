"""Allowed-ID validation for model output (D6): the model may only reference IDs we gave it."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from inkforge.domain.ids import PATTERNS

_LABEL = {
    "character": "character",
    "location": "location",
    "item": "item",
    "lore": "lore entry",
    "chapter": "chapter",
    "scene": "scene",
    "promise": "promise",
    "fact": "fact",
    "act": "act",
}


def find_id_violations(value: Any, allowed: Mapping[str, Collection[str]], _path: str = "$") -> list[str]:
    """Every string in `value` that looks like an ID of a constrained kind must be in `allowed[kind]`.

    Returns repair-ready messages, e.g. `$.cards[0].pov_character_id: "char-bob" is not a known
    character id; use one of: char-mira, char-tom`.
    """
    errors: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            errors += find_id_violations(v, allowed, f"{_path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            errors += find_id_violations(v, allowed, f"{_path}[{i}]")
    elif isinstance(value, str):
        for kind, ok in allowed.items():
            if re.fullmatch(PATTERNS[kind], value) and value not in ok:
                choices = ", ".join(sorted(ok)) or "(none)"
                errors.append(
                    f'{_path}: "{value}" is not a known {_LABEL.get(kind, kind)} id; use one of: {choices}'
                )
    return errors
