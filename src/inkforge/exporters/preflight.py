"""Export preflight (docs/05 Stage H): what would go into the manuscript, and what blocks a final export.

Final export uses each scene's *approved* version and is refused while any blocker remains.
A preview export uses the *active* version of every scene and only lists the problems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from inkforge.domain.common import Staleness
from inkforge.storage.workspace import ProjectStore

Mode = Literal["final", "preview"]


@dataclass
class ScenePick:
    scene_id: str
    chapter_id: str
    version: int | None  # None: nothing to export for this scene
    approved: bool
    stale: bool


@dataclass
class Preflight:
    mode: Mode
    chapters: list[tuple[str, str]]  # (chapter id, title) in outline order
    scenes: list[ScenePick]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "scenes": [s.__dict__ for s in self.scenes],
            "chapters": [{"id": c, "title": t} for c, t in self.chapters],
        }


def preflight(store: ProjectStore, mode: Mode = "final") -> Preflight:
    s = store
    outline = s.try_read("outline")
    if outline is None:
        return Preflight(mode, [], [], ["no outline yet — run the planning stages first"])
    ids = [c.id for c in outline.chapters]
    chapters = [(c.id, c.title) for c in sorted(outline.chapters, key=lambda c: c.order)]
    pf = Preflight(mode, chapters, [])
    if len(ids) != len(set(ids)):
        pf.blockers.append("duplicate chapter ids in the outline")
    by_chapter: dict[str, list[str]] = {c: [] for c, _ in chapters}
    for sid in s.scenes_in_order():
        ch = sid.split("-sc")[0]
        if ch in by_chapter:
            by_chapter[ch].append(sid)
        else:
            pf.warnings.append(f"{sid} belongs to {ch}, which is not in the outline — skipped")
    for ch, title in chapters:
        if not by_chapter[ch]:
            (pf.blockers if mode == "final" else pf.warnings).append(f"{ch} ({title}) has no scenes")
        for sid in by_chapter[ch]:
            m = s.meta("draft", sid)
            approved = m.approved is not None
            version = m.approved if mode == "final" else m.active
            stale = version is not None and s.staleness("draft", sid, version) == Staleness.STALE
            pf.scenes.append(ScenePick(sid, ch, version, approved, stale))
            if m.active is None:
                (pf.blockers if mode == "final" else pf.warnings).append(f"{sid}: no draft")
            elif not approved:
                (pf.blockers if mode == "final" else pf.warnings).append(f"{sid}: not approved")
            if stale:
                pf.warnings.append(
                    f"{sid}: v{version} is stale (its card or a voice card changed since it was written)"
                )
            if mode == "final" and approved and m.active != m.approved:
                pf.warnings.append(f"{sid}: exporting approved v{m.approved}; a newer v{m.active} exists")
    open_findings = [f for f in s.findings.all() if f.status == "open"]
    high = [f for f in open_findings if f.severity in ("high", "blocker")]
    if high:
        (pf.blockers if mode == "final" else pf.warnings).append(f"{len(high)} open high/blocker findings")
    if open_findings and not high:
        pf.warnings.append(f"{len(open_findings)} open findings (low/med)")
    open_promises = [p for p in s.get_promises().promises if p.status == "open" and p.importance == "high"]
    if open_promises:
        pf.warnings.append("open high-importance promises: " + ", ".join(p.id for p in open_promises))
    return pf
