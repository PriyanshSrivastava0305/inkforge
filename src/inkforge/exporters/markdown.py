"""Markdown export (Stage H-lite, Phase 4).

    exports/<timestamp>/book.md + preflight.json            final: approved versions only, refused on blockers
    exports/<timestamp>-preview/book.md + preflight.json    preview: active versions, watermarked (D23)

Chapters in outline order, scenes in card order, scene breaks as a centred "* * *". Forms without a
chapter unit (short story) get no chapter headings. Every export carries a proofreading note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from inkforge.domain.common import Actor, utcnow
from inkforge.domain.forms import get_form
from inkforge.exporters.preflight import Mode, Preflight, preflight
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.workspace import ProjectStore

SCENE_BREAK = '\n\n<p align="center">* * *</p>\n\n'
WATERMARK = "> **DRAFT PREVIEW — contains unapproved text.** Not a release. Generated {ts}."
PROOF_NOTE = "<!-- Generated with InkForge from approved scene versions. A human proofread is required before publishing. -->"


class ExportBlocked(RuntimeError):
    def __init__(self, report: Preflight):
        super().__init__(
            "export blocked: " + "; ".join(report.blockers[:5]) + ("…" if len(report.blockers) > 5 else "")
        )
        self.report = report


@dataclass
class ExportResult:
    path: Path
    report: Preflight
    words: int


def render_markdown(store: ProjectStore, report: Preflight) -> str:
    project = store.project
    form = get_form(project.form)
    preview = report.mode == "preview"
    ts = f"{utcnow():%Y-%m-%d %H:%M} UTC"
    out = [f"# {project.title}", ""]
    if preview:
        out += [WATERMARK.format(ts=ts), ""]
    with_headings = "chapter" in form.units and len(report.chapters) > 1
    picks = {p.scene_id: p for p in report.scenes}
    first_block = True
    for n, (cid, title) in enumerate(report.chapters, 1):
        scenes = [p for p in report.scenes if p.chapter_id == cid and p.version is not None]
        if not scenes:
            continue
        if with_headings:
            out += [f"## Chapter {n}: {title}", ""]
        elif not first_block:
            out.append(SCENE_BREAK.strip("\n"))  # no headings: every section boundary is a scene break
            out.append("")
        first_block = False
        texts = [store.read("draft", p.scene_id, version=p.version).strip() for p in scenes]
        out.append(SCENE_BREAK.join(texts))
        out.append("")
        if preview:
            unapproved = [p.scene_id for p in scenes if not picks[p.scene_id].approved]
            if unapproved:
                out += [f"> _Preview: unapproved scenes in this chapter: {', '.join(unapproved)}_", ""]
    out += ["", PROOF_NOTE if not preview else WATERMARK.format(ts=ts), ""]
    return "\n".join(out)


def export_markdown(store: ProjectStore, *, mode: Mode = "final") -> ExportResult:
    report = preflight(store, mode)
    if mode == "final" and not report.ok:
        raise ExportBlocked(report)
    text = render_markdown(store, report)
    stamp = f"{utcnow():%Y%m%d-%H%M%S}" + ("-preview" if mode == "preview" else "")
    out_dir = store.path(f"exports/{stamp}")
    atomic_write_text(out_dir / "book.md", text)
    atomic_write_text(out_dir / "preflight.json", json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    words = sum(
        len(store.read("draft", p.scene_id, version=p.version).split()) for p in report.scenes if p.version
    )
    store.audit.record(
        "export.preview" if mode == "preview" else "export.final",
        store.id,
        actor=Actor.USER,
        after_ref=out_dir.relative_to(store.dir).as_posix(),
        detail={
            "format": "markdown",
            "scenes": [f"{p.scene_id}@v{p.version}" for p in report.scenes if p.version],
            "words": words,
        },
    )
    return ExportResult(out_dir / "book.md", report, words)
