# pyright: reportInvalidTypeForm=false
# (this module builds Literal enums of allowed IDs at runtime, which static checkers can't follow)
"""Model-facing output schemas ("proposals") for Stages A–D.

These are what the LLM fills in. They are NOT domain records: they carry no minted IDs. Code
converts a proposal into records, minting every new ID (D6). Wherever the model must refer to
something that already exists, the schema offers an enum of the allowed IDs, so constrained
decoding can't invent one (lesson from Phase 2: free-text references loop or drift on small models).

Every free-text field has a minimum length: constrained decoding otherwise emits "" for hard fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, create_model

Text = Annotated[str, Field(min_length=3)]
Short = Annotated[str, Field(min_length=2, max_length=80)]
Charge = Annotated[str, Field(pattern=r"^[+-] *(->|→) *[+-]$")]


def _enum(values: list[str], fallback: str) -> Any:
    """Literal of allowed values; a single sentinel when there are none (JSON Schema needs ≥1 enum value)."""
    return Literal[tuple(values or [fallback])]  # type: ignore[valid-type]


# ---------------------------------------------------------------- Stage A


class SpecProposal(BaseModel):
    premise: Text
    logline: str = Field(min_length=20)
    genre: Text
    subgenres: list[Short] = Field(default_factory=list, max_length=3)
    audience: Text
    tone: Text
    pov: Literal["first", "third-limited", "omniscient", "second"]
    tense: Literal["past", "present"]
    setting_place: Text
    setting_time: Text
    themes: list[Short] = Field(min_length=2, max_length=4)
    content_boundaries: list[Short] = Field(
        min_length=1, max_length=5, description='things to avoid, or ["none"]'
    )
    comparable_titles: list[Short] = Field(default_factory=list, max_length=3)
    narrative_promises: list[Text] = Field(
        min_length=1, max_length=4, description="what the reader is promised"
    )
    style_notes: Text
    assumptions: list[Text] = Field(
        min_length=1, max_length=6, description="choices you made that the idea didn't state"
    )


class SpecChange(BaseModel):
    field: Literal[
        "premise",
        "logline",
        "tone",
        "themes",
        "narrative_promises",
        "style_notes",
        "setting_place",
        "setting_time",
        "audience",
    ]
    why: Text


class SpecEnhancement(BaseModel):
    critique: list[Text] = Field(min_length=1, max_length=5)
    changes: list[SpecChange] = Field(max_length=6)
    improved: SpecProposal


# ---------------------------------------------------------------- Stage B


class CharacterProposal(BaseModel):
    name: str = Field(min_length=2, max_length=60, description="a proper personal name")
    role: Literal[
        "protagonist", "antagonist", "deuteragonist", "mentor", "ally", "love interest", "foil", "supporting"
    ]
    goal: Text
    fear: Text
    secret: Text
    voice: Text = Field(description="how they talk: diction, rhythm, tics")
    arc_start: Text
    arc_end: Text


class ActProposal(BaseModel):
    purpose: Text
    turning_point: Text


class PromiseProposal(BaseModel):
    description: Text = Field(description="a setup the reader will expect to be paid off")
    importance: Literal["low", "med", "high"]
    setup_hint: Text
    payoff_hint: Text


def design_model(n_acts: int) -> type[BaseModel]:
    return create_model(
        "DesignProposal",
        dramatic_question=(str, Field(min_length=10)),
        theme_statement=(Text, ...),
        reader_promise=(Text, ...),
        acts=(list[ActProposal], Field(min_length=n_acts, max_length=n_acts)),
        characters=(list[CharacterProposal], Field(min_length=2, max_length=6)),
        promises=(list[PromiseProposal], Field(min_length=1, max_length=6)),
        ending_constraints=(list[Text], Field(min_length=1, max_length=4)),
    )


# ---------------------------------------------------------------- Stage C


def act_plan_model(n: int, char_ids: list[str], promise_ids: list[str]) -> type[BaseModel]:
    char_id = _enum(char_ids, "char-none")
    prom_id = _enum(promise_ids, "prom-000")
    chapter = create_model(
        "ChapterProposal",
        title=(Short, ...),
        purpose=(Text, ...),
        conflict=(Text, ...),
        turning_point=(Text, ...),
        outcome=(Text, ...),
        causal_link_prev=(Text, Field(description="how the previous chapter causes this one")),
        causal_link_next=(Text, Field(description="how this chapter causes the next")),
        characters=(list[char_id], Field(min_length=1, max_length=max(1, len(char_ids)))),
        promises_touched=(list[prom_id], Field(default_factory=list, max_length=max(1, len(promise_ids)))),
        pacing_note=(Text, ...),
    )
    return create_model("ActPlan", chapters=(list[chapter], Field(min_length=n, max_length=n)))


class OutlineEdit(BaseModel):
    title: Short
    purpose: Text


def outline_enhance_model(n: int) -> type[BaseModel]:
    return create_model(
        "OutlineEnhancement",
        critique=(list[Text], Field(min_length=1, max_length=5)),
        chapters=(list[OutlineEdit], Field(min_length=n, max_length=n)),
    )


# ---------------------------------------------------------------- Stage D


def cards_model(
    n: int, char_ids: list[str], promise_ids: list[str], earlier_scene_ids: list[str]
) -> type[BaseModel]:
    char_id = _enum(char_ids, "char-none")
    prom_id = _enum(promise_ids, "prom-000")
    place = _enum(["none", *earlier_scene_ids], "none")
    card = create_model(
        "SceneCardProposal",
        pov_character_id=(char_id, ...),
        participants=(list[char_id], Field(min_length=1, max_length=max(1, len(char_ids)))),
        location=(Short, Field(description="place name; reuse a known location name when it fits")),
        time_label=(Short, Field(description='e.g. "dawn, next day"')),
        temporal_mode=(Literal["linear", "flashback", "flashforward", "dream", "unreliable"], ...),
        happens_after=(
            place,
            Field(
                description='for flashbacks/flashforwards: the earlier scene it follows in story time; else "none"'
            ),
        ),
        want=(Text, ...),
        obstacle=(Text, ...),
        change=(Text, ...),
        story_value=(Short, Field(description="the value at stake, e.g. trust, safety, hope")),
        value_charge=(Charge, Field(description='how the value moves, e.g. "+ -> -"')),
        mood=(Short, ...),
        beats=(list[Text], Field(min_length=2, max_length=6)),
        concrete_event=(Text, ...),
        turn=(Text, ...),
        exit_condition=(Text, ...),
        promises_touched=(list[prom_id], Field(default_factory=list, max_length=max(1, len(promise_ids)))),
    )
    return create_model("ChapterScenes", cards=(list[card], Field(min_length=n, max_length=n)))
