"""Domain records — the single contract between stages, storage, API and UI (docs/04).

Fields are optional where the plan is unsure; they tighten as stages land.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from inkforge.domain.common import Actor, ArtifactStatus, Record, SchemaRecord, utcnow
from inkforge.domain.ids import (
    ActId,
    ChapterId,
    CharacterId,
    CodexId,
    FactId,
    FindingId,
    ItemId,
    JobId,
    LocationId,
    LoreId,
    ProjectId,
    PromiseId,
    RevisionId,
    SceneId,
)

# ---------------------------------------------------------------- project


class Project(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/project@1"

    id: ProjectId
    title: str = Field(min_length=1)
    form: str
    genre: str | None = None
    status: ArtifactStatus = ArtifactStatus.DRAFT
    target_length: int | None = Field(default=None, gt=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    settings: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)  # last minted n per counter kind


# ---------------------------------------------------------------- planning (Stages A–C)


class Setting(Record):
    place: str | None = None
    time: str | None = None


class BookSpecFields(Record):
    premise: str | None = None
    logline: str | None = None
    genre: str | None = None
    subgenres: list[str] = Field(default_factory=list)
    audience: str | None = None
    tone: str | None = None
    pov: Literal["first", "third-limited", "omniscient", "second", "n/a"] | None = None
    tense: Literal["past", "present"] | None = None
    setting: Setting = Field(default_factory=Setting)
    themes: list[str] = Field(default_factory=list)
    content_boundaries: list[str] = Field(default_factory=list)
    comparable_titles: list[str] = Field(default_factory=list)
    target_length: int | None = Field(default=None, gt=0)
    narrative_promises: list[str] = Field(default_factory=list)
    style_notes: str | None = None
    custom_fields: dict[str, str] = Field(default_factory=dict)


class BookSpec(SchemaRecord, BookSpecFields):
    SCHEMA: ClassVar[str] = "inkforge/book-spec@1"

    original: str = Field(description="the author's raw idea, never overwritten")
    assumptions: list[str] = Field(default_factory=list)
    author_fields: list[str] = Field(
        default_factory=list,
        description="fields the author set explicitly; code re-applies them over model output",
    )


class Act(Record):
    id: ActId
    purpose: str
    turning_point: str | None = None


class Arc(Record):
    character_id: CharacterId
    start_state: str
    end_state: str
    beats: list[str] = Field(default_factory=list)


class StoryDesign(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/story-design@1"

    dramatic_question: str
    theme_statement: str | None = None
    reader_promise: str | None = None
    structure_template: str | None = None
    acts: list[Act] = Field(default_factory=list)
    arcs: list[Arc] = Field(default_factory=list)
    promise_ids: list[PromiseId] = Field(default_factory=list)  # registry is canon/promises.json (D22)
    ending_constraints: list[str] = Field(default_factory=list)


class PromiseStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    ABANDONED = "abandoned"
    INTENTIONAL_SURPRISE = "intentional-surprise"


class PromiseHistory(Record):
    ts: datetime = Field(default_factory=utcnow)
    actor: Actor
    from_status: PromiseStatus | None
    to_status: PromiseStatus


class PlotPromise(Record):
    id: PromiseId
    description: str
    setup_ref: str | None = None  # scene id or planned beat
    payoff_ref: str | None = None
    status: PromiseStatus = PromiseStatus.OPEN
    importance: Literal["low", "med", "high"] = "med"
    created_by_stage: str | None = None
    history: list[PromiseHistory] = Field(default_factory=list)


class PromiseRegistry(SchemaRecord):
    """canon/promises.json — the only promise store (D22)."""

    SCHEMA: ClassVar[str] = "inkforge/promises@1"

    promises: list[PlotPromise] = Field(default_factory=list)

    @field_validator("promises")
    @classmethod
    def _unique(cls, v: list[PlotPromise]) -> list[PlotPromise]:
        ids = [p.id for p in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate promise ids")
        return v


class OutlineAct(Record):
    id: ActId
    order: int = Field(ge=0)
    title: str | None = None


class OutlineChapter(Record):
    id: ChapterId
    order: int = Field(ge=0)
    act_id: ActId | None = None
    title: str


class Outline(SchemaRecord):
    """Structure only: ids, order, titles. Chapter content lives in per-chapter ChapterPlan files (D21)."""

    SCHEMA: ClassVar[str] = "inkforge/outline@1"

    acts: list[OutlineAct] = Field(default_factory=list)
    chapters: list[OutlineChapter] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Outline:
        ids = [c.id for c in self.chapters]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate chapter ids in outline")
        orders = [c.order for c in self.chapters]
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate chapter order values")
        act_ids = {a.id for a in self.acts}
        for c in self.chapters:
            if c.act_id is not None and c.act_id not in act_ids:
                raise ValueError(f"chapter {c.id} references unknown act {c.act_id}")
        return self

    def ordered_chapter_ids(self) -> list[str]:
        return [c.id for c in sorted(self.chapters, key=lambda c: c.order)]


class ChapterPlan(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/chapter-plan@1"

    id: ChapterId
    act_id: ActId | None = None
    title: str
    purpose: str
    conflict: str | None = None
    turning_point: str | None = None
    outcome: str | None = None
    causal_link_prev: str | None = None
    causal_link_next: str | None = None
    characters: list[CharacterId] = Field(default_factory=list)
    required_info: list[str] = Field(default_factory=list)
    pacing_note: str | None = None
    target_length: int | None = Field(default=None, gt=0)
    promises_touched: list[PromiseId] = Field(default_factory=list)


# ---------------------------------------------------------------- scenes (Stage D/E)


class TemporalMode(StrEnum):
    LINEAR = "linear"
    FLASHBACK = "flashback"
    FLASHFORWARD = "flashforward"
    DREAM = "dream"
    UNRELIABLE = "unreliable"


class SceneTime(Record):
    story_datetime: str | None = None  # ISO date or datetime; optional (docs/04 § Story time)
    time_of_day: str | None = None
    elapsed_since_prev: str | None = None
    label: str | None = None


class Slugline(Record):
    int_ext: Literal["INT.", "EXT.", "INT./EXT."]
    location: str
    time_of_day: str


class SceneCard(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/scene-card@1"

    id: SceneId
    chapter_id: ChapterId
    order: int = Field(ge=0)  # narrative order within the chapter
    pov_character_id: CharacterId | None = None
    location_id: LocationId | None = None
    time: SceneTime = Field(default_factory=SceneTime)
    temporal_mode: TemporalMode = TemporalMode.LINEAR
    participants: list[CharacterId] = Field(default_factory=list)
    items_present: list[ItemId] = Field(default_factory=list)
    want: str | None = None
    obstacle: str | None = None
    change: str | None = None
    story_value: str | None = None
    value_charge: str | None = Field(default=None, pattern=r"^[+-] *(->|→) *[+-]$")
    mood: str | None = None
    beats: list[str] = Field(default_factory=list)
    concrete_event: str | None = None
    turn: str | None = None
    exit_condition: str | None = None
    required_facts: list[FactId] = Field(default_factory=list)
    promises_touched: list[PromiseId] = Field(default_factory=list)
    target_length: int | None = Field(default=None, gt=0)
    notes: str | None = None
    slugline: Slugline | None = None  # script forms (Phase 9)
    page_estimate: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _chapter_prefix(self) -> SceneCard:
        if not self.id.startswith(f"{self.chapter_id}-"):
            raise ValueError(f"scene {self.id} does not belong to chapter {self.chapter_id}")
        return self


class SceneVersionMeta(Record):
    """Metadata for one draft version (the prose itself is draft.vN.md)."""

    scene_id: SceneId
    version: int = Field(ge=1)
    word_count: int = Field(ge=0)
    model_profile: str | None = None
    model_name: str | None = None
    prompt_ref: str | None = None  # prompt_id@version
    context_manifest_path: str | None = None
    job_id: JobId | None = None


class TimelineEntry(Record):
    scene_id: SceneId
    story_datetime: str | None = None
    label: str | None = None
    concurrent_with: SceneId | None = None


class Timeline(SchemaRecord):
    """Story (chronological) order; list order IS chronology (D20)."""

    SCHEMA: ClassVar[str] = "inkforge/timeline@1"

    entries: list[TimelineEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Timeline:
        ids = [e.scene_id for e in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("scene appears twice on the timeline")
        known = set(ids)
        for e in self.entries:
            if e.concurrent_with is not None and e.concurrent_with not in known:
                raise ValueError(f"{e.scene_id} concurrent_with unknown scene {e.concurrent_with}")
        return self

    def story_index(self) -> dict[str, int]:
        """Concurrent scenes share the index of the scene they're concurrent with."""
        idx: dict[str, int] = {}
        for i, e in enumerate(self.entries):
            idx[e.scene_id] = idx.get(e.concurrent_with, i) if e.concurrent_with else i
        return idx


# ---------------------------------------------------------------- codex


class VoiceCard(Record):
    diction: str | None = None
    sentence_length: str | None = None
    verbal_tics: list[str] = Field(default_factory=list)
    vocabulary_level: str | None = None
    taboo_words: list[str] = Field(default_factory=list)
    sample_lines: list[str] = Field(default_factory=list)


class Relationship(Record):
    other_id: CharacterId
    kind: str
    since: SceneId | None = None


class KnowledgeEntry(Record):
    fact_id: FactId
    since: SceneId | None = None


class CodexStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CodexEntry(SchemaRecord):
    id: CodexId
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    codex_status: CodexStatus = CodexStatus.ACCEPTED
    first_introduced: SceneId | None = None
    source_refs: list[str] = Field(default_factory=list)


class Character(CodexEntry):
    SCHEMA: ClassVar[str] = "inkforge/character@1"

    id: CharacterId
    role: str | None = None
    birthdate: str | None = None
    age: int | None = Field(default=None, ge=0)
    appearance: str | None = None
    goals: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    voice_card: VoiceCard = Field(default_factory=VoiceCard)
    knowledge: list[KnowledgeEntry] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    arc_ref: str | None = None


class Location(CodexEntry):
    SCHEMA: ClassVar[str] = "inkforge/location@1"

    id: LocationId
    rules: list[str] = Field(default_factory=list)
    parent_location: LocationId | None = None


class LoreItem(CodexEntry):
    SCHEMA: ClassVar[str] = "inkforge/lore@1"

    id: LoreId
    rules: list[str] = Field(default_factory=list)


class Item(CodexEntry):
    SCHEMA: ClassVar[str] = "inkforge/item@1"

    id: ItemId
    kind: Literal["weapon", "document", "key", "artifact", "vehicle", "other"] = "other"
    significance: Literal["prop", "plot", "macguffin"] = "plot"
    unique: bool = True
    promise_ids: list[PromiseId] = Field(default_factory=list)


CODEX_MODELS: dict[str, type[CodexEntry]] = {
    "character": Character,
    "location": Location,
    "lore": LoreItem,
    "item": Item,
}


# ---------------------------------------------------------------- canon & quality


class SourceRef(Record):
    scene_id: SceneId
    version: int = Field(ge=1)
    char_span: tuple[int, int]

    @field_validator("char_span")
    @classmethod
    def _span(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 0 or v[1] < v[0]:
            raise ValueError("char_span must be 0 <= start <= end")
        return v


class CanonStatus(StrEnum):
    CANDIDATE = "candidate"
    CANON = "canon"
    REJECTED = "rejected"
    RETCONNED = "retconned"


class Fact(Record):
    id: FactId
    subject_id: CodexId
    predicate: str = Field(min_length=1)
    value: str | int | float | bool
    valid_from: SceneId | None = None  # resolved to story_index via the Timeline (D20)
    valid_to: SceneId | None = None
    source_ref: SourceRef | None = None
    quote: str | None = None
    origin: Literal["author", "extracted"] = "author"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    canon_status: CanonStatus = CanonStatus.CANDIDATE


class Evidence(Record):
    scene_id: SceneId
    version: int = Field(ge=1)
    span: tuple[int, int]
    quote: str


class Finding(Record):
    id: FindingId
    scene_id: SceneId
    category: Literal[
        "continuity",
        "timeline",
        "knowledge",
        "voice",
        "motivation",
        "pacing",
        "clarity",
        "prose",
        "promise",
        "format",
    ]
    severity: Literal["low", "med", "high", "blocker"]
    claim: str
    evidence: list[Evidence] = Field(default_factory=list)
    conflicting_fact_id: FactId | None = None
    affected_entities: list[CodexId] = Field(default_factory=list)
    detector: str = Field(pattern=r"^(rule|critic):[a-z0-9_-]+$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_hypothesis: bool = False
    status: Literal["open", "accepted", "dismissed", "intentional", "fixed"] = "open"
    resolution_note: str | None = None

    @model_validator(mode="after")
    def _evidence_or_hypothesis(self) -> Finding:
        if not self.evidence and not self.is_hypothesis:
            raise ValueError("a finding without evidence must be marked is_hypothesis (docs/07)")
        return self


class RevisionProposal(Record):
    id: RevisionId
    scene_id: SceneId
    base_version: int = Field(ge=1)
    finding_ids: list[FindingId] = Field(default_factory=list)
    instruction: str
    diff_path: str | None = None
    candidate_version: int | None = Field(default=None, ge=1)
    scores_before: dict[str, float] = Field(default_factory=dict)
    scores_after: dict[str, float] = Field(default_factory=dict)
    decision: Literal["pending", "accepted", "rejected", "auto-discarded"] = "pending"


# ---------------------------------------------------------------- runs & audit


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStep(Record):
    """One unit of work (a stage for one target). `done` steps are the checkpoint: resume skips them."""

    stage: str
    target: str | None = None
    status: StepStatus = StepStatus.PENDING
    output_refs: list[str] = Field(default_factory=list)
    attempts: int = Field(default=0, ge=0)
    error: str | None = None
    finished_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.stage}:{self.target}" if self.target else self.stage


class Job(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/job@1"

    job_id: JobId
    project_id: ProjectId
    stage: str  # mode or single stage name, e.g. "plan" or "cards"
    target_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    output_refs: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    events_path: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # mode, until, yes, profile_set, stage options
    steps: list[JobStep] = Field(default_factory=list)
    awaiting: str | None = None  # gate name when status == awaiting_approval


class AuditEvent(Record):
    ts: datetime = Field(default_factory=utcnow)
    actor: Actor
    operation: str
    target: str
    before_ref: str | None = None
    after_ref: str | None = None
    job_id: JobId | None = None
    via: str | None = None  # e.g. "--yes" (D23)
    detail: dict[str, Any] = Field(default_factory=dict)


class ManifestSection(Record):
    name: str
    source_refs: list[str] = Field(default_factory=list)
    tokens: int = Field(ge=0)
    disposition: Literal["included", "trimmed", "dropped"]
    reason: str | None = None
    prefix_hash: str | None = None


class ContextManifest(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/context-manifest@1"

    job_id: JobId
    model: str
    context_limit: int = Field(gt=0)
    budget_input: int = Field(ge=0)
    output_reserved: int = Field(ge=0)
    sections: list[ManifestSection] = Field(default_factory=list)
    total_tokens: int = Field(ge=0)


SCHEMA_RECORDS: tuple[type[SchemaRecord], ...] = (
    Project,
    BookSpec,
    StoryDesign,
    PromiseRegistry,
    Outline,
    ChapterPlan,
    SceneCard,
    Timeline,
    Character,
    Location,
    LoreItem,
    Item,
    Job,
    ContextManifest,
)
