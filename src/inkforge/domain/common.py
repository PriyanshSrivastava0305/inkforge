"""Shared building blocks for domain records: base model, schema versioning, enums, timestamps."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Record(BaseModel):
    """Strict base for every persisted record: unknown fields are errors, not silently dropped."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


class SchemaRecord(Record):
    """A record stored as its own file. Carries `"schema": "inkforge/<name>@<version>"` on disk."""

    SCHEMA: ClassVar[str]

    schema_id: str = Field(default="", alias="schema")

    @model_validator(mode="before")
    @classmethod
    def _default_schema(cls, data: Any) -> Any:
        if isinstance(data, dict) and "schema" not in data and "schema_id" not in data:
            return {**data, "schema": cls.SCHEMA}
        return data

    @model_validator(mode="after")
    def _check_schema(self) -> SchemaRecord:
        if self.schema_id != self.SCHEMA:
            # Older versions are migrated by storage before validation (docs/04 § Migrations).
            raise ValueError(f"schema {self.schema_id!r} does not match {self.SCHEMA!r}")
        return self

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"
    ARCHIVED = "archived"


class Actor(StrEnum):
    USER = "user"
    SYSTEM = "system"


class Staleness(StrEnum):
    FRESH = "fresh"
    REVIEW = "review"  # only soft (neighbour-context) inputs changed
    STALE = "stale"  # a hard declared input changed
