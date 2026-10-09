"""Form profiles: unit names, hierarchy, output kind and exporters per form (docs/04 § Form profiles)."""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import Field

from inkforge.domain.common import Record

FORMS_DIR = Path(__file__).parent / "forms"


class FormProfile(Record):
    id: str
    label: str
    units: list[str] = Field(min_length=1)  # top-down, e.g. ["act", "chapter", "scene"]
    output: Literal["prose", "script"]
    default_target_words: int | None = None
    default_target_pages: int | None = None
    exporters: list[str] = Field(min_length=1)
    optional_exporters: list[str] = Field(default_factory=list)
    available: bool = True  # False = stub until its phase ships (script forms → Phase 9)

    @property
    def unit(self) -> str:
        """The unit a chapter plan describes (the level above scenes)."""
        return self.units[-2] if len(self.units) >= 2 else self.units[-1]


@cache
def load_forms() -> dict[str, FormProfile]:
    forms = {}
    for path in sorted(FORMS_DIR.glob("*.toml")):
        with path.open("rb") as f:
            prof = FormProfile.model_validate(tomllib.load(f))
        if prof.id != path.stem:
            raise ValueError(f"{path.name}: id {prof.id!r} must match file name")
        forms[prof.id] = prof
    return forms


def get_form(form_id: str) -> FormProfile:
    forms = load_forms()
    if form_id not in forms:
        raise KeyError(f"unknown form {form_id!r}; known: {sorted(forms)}")
    return forms[form_id]
