"""Per-scene quality report: what was checked, on which version of the text, with which result.

`quality/reports/<scene>.json`. Derived data like digests: deleting it only means the next `check`
re-runs. Stage F uses it to skip scenes whose text and check set haven't changed; Stage G uses the
rubric scores as the "before" of keep-only-if-better.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from inkforge.domain.common import SchemaRecord
from inkforge.memory.effective import effective
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ProjectStore


class QualityReport(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/quality-report@1"

    scene_id: str
    version: int
    sha256: str
    families: list[str] = Field(default_factory=list)  # deterministic, continuity, craft, voice
    scores: dict[str, float] = Field(default_factory=dict)  # craft rubric, 1–5 per dimension
    open_findings: dict[str, int] = Field(default_factory=dict)  # by severity
    errors: list[str] = Field(default_factory=list)  # model calls that failed (the family didn't run)
    job_id: str | None = None

    @property
    def rubric_total(self) -> float | None:
        return sum(self.scores.values()) if self.scores else None


def path(store: ProjectStore, scene_id: str):
    return store.path(f"quality/reports/{scene_id}.json")


def load(store: ProjectStore, scene_id: str) -> QualityReport | None:
    p = path(store, scene_id)
    return QualityReport.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None


def save(store: ProjectStore, report: QualityReport) -> None:
    with store.lock:
        atomic_write_text(path(store, report.scene_id), report.to_json())


def is_fresh(store: ProjectStore, scene_id: str, families: list[str]) -> bool:
    """Checked already: same text, every requested family ran without error."""
    r, eff = load(store, scene_id), effective(store, scene_id)
    return (
        r is not None
        and eff is not None
        and r.sha256 == eff.sha256
        and set(families) <= set(r.families)
        and not r.errors
    )
