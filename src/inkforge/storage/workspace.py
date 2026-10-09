"""Workspace and per-project store: the only code that touches project files (docs/04 § Workspace layout).

Every mutating call: takes the project lock, resolves paths through the path guard, writes
atomically, appends an audit event and refreshes the SQLite index for what changed.
"""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, TypeVar

from inkforge.domain import ids
from inkforge.domain.common import Actor, SchemaRecord, Staleness, utcnow
from inkforge.domain.forms import get_form
from inkforge.domain.models import (
    CODEX_MODELS,
    BookSpec,
    ChapterPlan,
    CodexEntry,
    Fact,
    Finding,
    Job,
    Outline,
    Project,
    PromiseRegistry,
    SceneCard,
    SceneVersionMeta,
    StoryDesign,
    Timeline,
)
from inkforge.policy.audit import AuditLog
from inkforge.policy.lock import ProjectLock
from inkforge.policy.pathguard import resolve_inside
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.hashing import sha256_json, sha256_text
from inkforge.storage.tables import JsonlTable
from inkforge.storage.versioned import ArtifactMeta, ArtifactSlot, VersionRecord

M = TypeVar("M", bound=SchemaRecord)

ArtifactKind = Literal["spec", "design", "outline", "chapter", "card", "draft", "timeline"]
CODEX_DIRS = {"character": "characters", "location": "locations", "lore": "lore", "item": "items"}
_PREFIX_KIND = {"char": "character", "loc": "location", "lore": "lore", "obj": "item"}
_SINGLETONS: dict[str, tuple[str, str, type[SchemaRecord]]] = {
    "spec": ("spec", "book_spec", BookSpec),
    "design": ("design", "story_design", StoryDesign),
    "outline": ("outline", "outline", Outline),
    "timeline": ("timeline", "timeline", Timeline),
}


class ProjectExistsError(FileExistsError):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.projects_dir = self.root / "projects"

    def ensure(self) -> Workspace:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        return self

    def project_ids(self) -> list[str]:
        if not self.projects_dir.exists():
            return []
        return sorted(p.name for p in self.projects_dir.iterdir() if (p / "project.json").is_file())

    def create_project(self, title: str, form: str = "novel", *, slug: str | None = None) -> ProjectStore:
        prof = get_form(form)
        if not prof.available:
            raise ValueError(f"form {form!r} is not available yet")
        pid = slug or ids.slugify(title)
        if not ids.is_valid("project", pid):
            raise ValueError(f"invalid project id {pid!r}")
        self.ensure()
        pdir = resolve_inside(self.projects_dir, pid)
        if pdir.exists():
            raise ProjectExistsError(f"project {pid!r} already exists")
        pdir.mkdir()
        target = prof.default_target_words or prof.default_target_pages
        store = ProjectStore(pdir)
        with store.lock:
            project = Project(id=pid, title=title, form=form, target_length=target)
            atomic_write_text(pdir / "project.json", project.to_json())
            store.audit.record("project.create", pid, actor=Actor.USER, detail={"title": title, "form": form})
            store.save_promises(PromiseRegistry(), actor=Actor.SYSTEM)
        store.index.rebuild()
        return store

    def open(self, project_id: str) -> ProjectStore:
        pdir = resolve_inside(self.projects_dir, project_id)
        if not (pdir / "project.json").is_file():
            raise FileNotFoundError(f"no project {project_id!r} in {self.projects_dir}")
        return ProjectStore(pdir)


class ProjectStore:
    def __init__(self, project_dir: Path):
        from inkforge.storage.index import ProjectIndex  # local import: index depends on this module

        self.dir = Path(project_dir)
        self.lock = ProjectLock(self.dir)
        self.audit = AuditLog(self.dir / "audit.jsonl")
        self.index = ProjectIndex(self)

    # ------------------------------------------------------------ helpers
    def path(self, rel: str) -> Path:
        return resolve_inside(self.dir, rel)

    @contextlib.contextmanager
    def _writing(self) -> Iterator[None]:
        with self.lock:
            yield

    @property
    def project(self) -> Project:
        return Project.model_validate_json((self.dir / "project.json").read_text(encoding="utf-8"))

    @property
    def id(self) -> str:
        return self.dir.name

    def update_project(self, **changes: Any) -> Project:
        with self._writing():
            p = self.project.model_copy(update={**changes, "updated_at": utcnow()})
            p = Project.model_validate(p.model_dump(by_alias=True))
            atomic_write_text(self.dir / "project.json", p.to_json())
            self.audit.record("project.update", self.id, actor=Actor.USER, detail={"fields": sorted(changes)})
            return p

    def mint(self, kind: str) -> str:
        """Next counter id (`fact-0001`, `prom-001`, …), persisted in project.json."""
        with self._writing():
            p = self.project
            n = p.counters.get(kind, 0) + 1
            p.counters[kind] = n
            atomic_write_text(self.dir / "project.json", p.to_json())
            return ids.counter_id(kind, n)

    # ------------------------------------------------------------ versioned artifacts
    def slot(self, kind: ArtifactKind, target: str | None = None) -> ArtifactSlot:
        if kind in _SINGLETONS:
            d, name, _ = _SINGLETONS[kind]
            return ArtifactSlot(self.path(d), name, "json")
        if target is None:
            raise ValueError(f"{kind} needs a target id")
        if kind == "chapter":
            if not ids.is_valid("chapter", target):
                raise ValueError(f"bad chapter id {target!r}")
            return ArtifactSlot(self.path(f"outline/chapters/{target}"), "plan", "json")
        if kind in ("card", "draft"):
            if not ids.is_valid("scene", target):
                raise ValueError(f"bad scene id {target!r}")
            return ArtifactSlot(self.path(f"scenes/{target}"), kind, "json" if kind == "card" else "md")
        raise ValueError(f"unknown artifact kind {kind!r}")

    def _model_for(self, kind: ArtifactKind) -> type[SchemaRecord]:
        if kind in _SINGLETONS:
            return _SINGLETONS[kind][2]
        return {"chapter": ChapterPlan, "card": SceneCard}[kind]

    def read(self, kind: ArtifactKind, target: str | None = None, version: int | None = None) -> Any:
        slot = self.slot(kind, target)
        if kind == "draft":
            return slot.read_text(version)
        return self._model_for(kind).model_validate_json(slot.read_text(version))

    def try_read(self, kind: ArtifactKind, target: str | None = None) -> Any | None:
        slot = self.slot(kind, target)
        return self.read(kind, target) if slot.exists() and slot.meta().active is not None else None

    def meta(self, kind: ArtifactKind, target: str | None = None) -> ArtifactMeta:
        return self.slot(kind, target).meta()

    def save(
        self,
        kind: ArtifactKind,
        content: SchemaRecord | str,
        *,
        target: str | None = None,
        actor: Actor = Actor.SYSTEM,
        note: str | None = None,
        extra: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> VersionRecord:
        """Write a new immutable version and make it active. Inputs are hashed for staleness."""
        if kind == "draft":
            if not isinstance(content, str):
                raise TypeError("draft content is Markdown text")
            data = content.encode("utf-8")
        else:
            model = self._model_for(kind)
            if not isinstance(content, model):
                raise TypeError(f"{kind} expects {model.__name__}")
            data = content.to_json().encode("utf-8")
            target = self._target_of(kind, content, target)
        with self._writing():
            slot = self.slot(kind, target)
            inputs = self.declared_inputs(kind, target, content)
            before = slot.meta().active
            rec = slot.write(data, input_hashes=inputs, created_by=actor, note=note, extra=extra)
            self.audit.record(
                f"{kind}.save",
                self._ref(kind, target),
                actor=actor,
                before_ref=f"v{before}" if before else None,
                after_ref=f"v{rec.version}",
                job_id=job_id,
            )
            self.index.refresh(kind, target)
            return rec

    def save_draft(
        self, scene_id: str, text: str, meta: SceneVersionMeta | None = None, **kw: Any
    ) -> VersionRecord:
        extra = (meta or SceneVersionMeta(scene_id=scene_id, version=1, word_count=0)).model_dump(mode="json")
        extra["word_count"] = len(text.split())
        extra.pop("version", None)
        return self.save("draft", text, target=scene_id, extra=extra, **kw)

    def approve(
        self,
        kind: ArtifactKind,
        target: str | None = None,
        version: int | None = None,
        *,
        via: str | None = None,
    ) -> int:
        with self._writing():
            slot = self.slot(kind, target)
            before = slot.meta().approved
            v = slot.approve(version)
            self.audit.record(
                f"{kind}.approve",
                self._ref(kind, target),
                actor=Actor.USER,
                before_ref=f"v{before}" if before else None,
                after_ref=f"v{v}",
                via=via,
            )
            self.index.refresh(kind, target)
            return v

    def set_active(self, kind: ArtifactKind, target: str | None, version: int) -> None:
        with self._writing():
            slot = self.slot(kind, target)
            before = slot.meta().active
            slot.set_active(version)
            self.audit.record(
                f"{kind}.set_active",
                self._ref(kind, target),
                actor=Actor.USER,
                before_ref=f"v{before}",
                after_ref=f"v{version}",
            )
            self.index.refresh(kind, target)

    @staticmethod
    def _ref(kind: str, target: str | None) -> str:
        return f"{kind}:{target}" if target else kind

    @staticmethod
    def _target_of(kind: str, content: SchemaRecord, target: str | None) -> str | None:
        own = getattr(content, "id", None) if kind in ("chapter", "card") else None
        if own is not None and target is not None and own != target:
            raise ValueError(f"target {target!r} does not match content id {own!r}")
        return own or target

    # ------------------------------------------------------------ listing
    def chapter_ids(self) -> list[str]:
        d = self.dir / "outline" / "chapters"
        return sorted(p.name for p in d.iterdir() if (p / "plan.meta.json").is_file()) if d.exists() else []

    def scene_ids(self) -> list[str]:
        d = self.dir / "scenes"
        if not d.exists():
            return []
        return sorted(
            p.name
            for p in d.iterdir()
            if (p / "card.meta.json").is_file() or (p / "draft.meta.json").is_file()
        )

    def scenes_in_order(self) -> list[str]:
        """Narrative order: outline chapter order, then card `order`."""
        outline = self.try_read("outline")
        chapter_order = {c: i for i, c in enumerate(outline.ordered_chapter_ids())} if outline else {}
        keyed = []
        for sid in self.scene_ids():
            card = self.try_read("card", sid)
            ch = sid.split("-sc")[0]
            keyed.append((chapter_order.get(ch, 10**6), ch, card.order if card else 10**6, sid))
        return [k[-1] for k in sorted(keyed)]

    # ------------------------------------------------------------ staleness (docs/04 § Staleness)
    def declared_inputs(self, kind: str, target: str | None, content: Any = None) -> dict[str, str]:
        refs: list[str]
        if kind == "spec" or kind == "timeline":
            refs = []
        elif kind == "design":
            refs = ["spec"]
        elif kind == "outline":
            refs = ["spec", "design"]
        elif kind == "chapter":
            refs = ["design", f"outline-entry:{target}", f"~prev:{target}", f"~next:{target}"]
        elif kind == "card":
            card = content if isinstance(content, SceneCard) else self.try_read("card", target)
            refs = [f"chapter:{target.split('-sc')[0]}"] if target else []
            if card is not None:
                ents = [card.pov_character_id, card.location_id, *card.participants]
                refs += sorted({f"codex:{e}" for e in ents if e})
        elif kind == "draft":
            card = self.try_read("card", target)
            refs = [f"card:{target}"]
            if card is not None:
                refs += sorted({f"voice:{c}" for c in card.participants})
        else:
            raise ValueError(f"unknown kind {kind!r}")
        return {r: self.current_hash(r) for r in refs}

    def current_hash(self, ref: str) -> str:
        """Hash of the current state of an input reference ("" when it doesn't exist)."""
        name, _, arg = ref.lstrip("~").partition(":")
        if name in _SINGLETONS:
            return self.slot(name).active_sha() or ""  # type: ignore[arg-type]
        if name == "chapter":
            return self.slot("chapter", arg).active_sha() or ""
        if name == "card":
            return self.slot("card", arg).active_sha() or ""
        if name == "outline-entry":
            outline = self.try_read("outline")
            entry = next((c for c in outline.chapters if c.id == arg), None) if outline else None
            return sha256_json(entry.model_dump(exclude={"order"})) if entry else ""
        if name in ("prev", "next"):
            # Neighbour token "<chapter>@<active version>" ("@0" = no plan yet; "" = no neighbour).
            # Compared by _soft_changed(), which looks at the neighbour's purpose/outcome.
            outline = self.try_read("outline")
            order = outline.ordered_chapter_ids() if outline else []
            if arg not in order:
                return ""
            j = order.index(arg) + (-1 if name == "prev" else 1)
            if not 0 <= j < len(order):
                return ""
            return f"{order[j]}@{self.slot('chapter', order[j]).meta().active or 0}"
        if name == "codex":
            path = self._codex_path(arg)
            return sha256_text(path.read_text(encoding="utf-8")) if path.is_file() else ""
        if name == "voice":
            entry = self.get_codex(arg)
            return sha256_json(getattr(entry, "voice_card", None)) if entry else ""
        raise ValueError(f"unknown input ref {ref!r}")

    def staleness(
        self, kind: ArtifactKind, target: str | None = None, version: int | None = None
    ) -> Staleness:
        meta = self.meta(kind, target)
        v = version if version is not None else meta.active
        if v is None:
            return Staleness.FRESH
        result = Staleness.FRESH
        for ref, recorded in meta.get(v).input_hashes.items():
            current = self.current_hash(ref)
            if current == recorded:
                continue
            if not ref.startswith("~"):
                return Staleness.STALE
            if self._soft_changed(recorded, current):
                result = Staleness.REVIEW
        return result

    def _soft_changed(self, recorded: str, current: str) -> bool:
        """Neighbour context changed? Different neighbour → yes. Same neighbour → compare purpose/outcome
        against the baseline version (its first plan if none existed yet when we were written, since
        chapters are generated in sequence and a neighbour appearing is not a change)."""
        if not recorded or not current:
            return False  # no neighbour then or now (first/last chapter, or appended later)
        (rid, rv), (cid, cv) = (t.rsplit("@", 1) for t in (recorded, current))
        if rid != cid:
            return True  # reordered / inserted
        base_v, cur_v = int(rv) or 1, int(cv)
        if cur_v == 0 or cur_v == base_v:
            return False
        slot = self.slot("chapter", cid)
        base, cur = (ChapterPlan.model_validate_json(slot.read_text(x)) for x in (base_v, cur_v))
        return (base.purpose, base.outcome) != (cur.purpose, cur.outcome)

    # ------------------------------------------------------------ codex
    def _codex_path(self, entity_id: str) -> Path:
        kind = _PREFIX_KIND.get(entity_id.split("-", 1)[0])
        if kind is None or not ids.is_valid(kind, entity_id):
            raise ValueError(f"not a codex id: {entity_id!r}")
        return self.path(f"codex/{CODEX_DIRS[kind]}/{entity_id}.json")

    def get_codex(self, entity_id: str) -> CodexEntry | None:
        path = self._codex_path(entity_id)
        if not path.is_file():
            return None
        model = CODEX_MODELS[_PREFIX_KIND[entity_id.split("-", 1)[0]]]
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    def list_codex(self, kind: str | None = None) -> list[CodexEntry]:
        kinds = [kind] if kind else list(CODEX_DIRS)
        out: list[CodexEntry] = []
        for k in kinds:
            d = self.dir / "codex" / CODEX_DIRS[k]
            if d.exists():
                out += [
                    CODEX_MODELS[k].model_validate_json(p.read_text(encoding="utf-8"))
                    for p in sorted(d.glob("*.json"))
                ]
        return out

    def new_codex_id(self, kind: str, name: str) -> str:
        return ids.codex_id(kind, name, (e.id for e in self.list_codex(kind)))

    def save_codex(self, entry: CodexEntry, *, actor: Actor = Actor.USER, job_id: str | None = None) -> None:
        with self._writing():
            path = self._codex_path(entry.id)
            before = sha256_text(path.read_text(encoding="utf-8"))[:12] if path.is_file() else None
            text = entry.to_json()
            atomic_write_text(path, text)
            self.audit.record(
                "codex.save",
                entry.id,
                actor=actor,
                before_ref=before,
                after_ref=sha256_text(text)[:12],
                job_id=job_id,
            )
            self.index.refresh("codex", entry.id)

    def delete_codex(self, entity_id: str, *, actor: Actor = Actor.USER) -> None:
        with self._writing():
            path = self._codex_path(entity_id)
            if path.is_file():
                path.unlink()
                self.audit.record("codex.delete", entity_id, actor=actor)
                self.index.refresh("codex", entity_id)

    # ------------------------------------------------------------ canon, promises, quality, jobs
    @property
    def facts(self) -> JsonlTable[Fact]:
        return JsonlTable(self.path("canon/facts.jsonl"), Fact)

    @property
    def review_queue(self) -> JsonlTable[Fact]:
        return JsonlTable(self.path("canon/review_queue.jsonl"), Fact)

    @property
    def findings(self) -> JsonlTable[Finding]:
        return JsonlTable(self.path("quality/findings.jsonl"), Finding)

    def upsert(
        self,
        table: Literal["facts", "review_queue", "findings"],
        record: Fact | Finding,
        *,
        actor: Actor = Actor.SYSTEM,
    ) -> None:
        with self._writing():
            replaced = getattr(self, table).upsert(record)
            self.audit.record(f"{table}.{'update' if replaced else 'add'}", record.id, actor=actor)
            self.index.refresh(table, record.id)

    def get_promises(self) -> PromiseRegistry:
        path = self.path("canon/promises.json")
        return (
            PromiseRegistry.model_validate_json(path.read_text(encoding="utf-8"))
            if path.is_file()
            else PromiseRegistry()
        )

    def save_promises(self, registry: PromiseRegistry, *, actor: Actor = Actor.USER) -> None:
        with self._writing():
            atomic_write_text(self.path("canon/promises.json"), registry.to_json())
            self.audit.record(
                "promises.save", "promises", actor=actor, detail={"count": len(registry.promises)}
            )
            self.index.refresh("promises", None)

    def save_job(self, job: Job) -> None:
        with self._writing():
            atomic_write_text(self.path(f"runs/{job.job_id}/job.json"), job.to_json())
            self.index.refresh("job", job.job_id)

    def jobs(self) -> list[Job]:
        d = self.dir / "runs"
        if not d.exists():
            return []
        return [Job.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*/job.json"))]

    # ------------------------------------------------------------ maintenance
    def delete_scene(self, scene_id: str, *, actor: Actor = Actor.USER) -> None:
        """Archive-by-move: scene folder goes to backups/deleted/, never hard-deleted."""
        with self._writing():
            src = self.path(f"scenes/{scene_id}")
            if not src.exists():
                return
            dest = self.path(f"backups/deleted/{scene_id}-{utcnow():%Y%m%d%H%M%S}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            self.audit.record(
                "scene.delete", scene_id, actor=actor, after_ref=dest.relative_to(self.dir).as_posix()
            )
            self.index.refresh("card", scene_id)


# ---------------------------------------------------------------- integrity & repair


def all_slots(store: ProjectStore) -> list[tuple[str, str | None, ArtifactSlot]]:
    out: list[tuple[str, str | None, ArtifactSlot]] = [(k, None, store.slot(k)) for k in _SINGLETONS]  # type: ignore[arg-type]
    out += [("chapter", c, store.slot("chapter", c)) for c in store.chapter_ids()]
    for sid in store.scene_ids():
        out += [("card", sid, store.slot("card", sid)), ("draft", sid, store.slot("draft", sid))]
    return out


def repair(store: ProjectStore) -> dict[str, list[str]]:
    """Clean up after a crash: delete temp files, quarantine orphan versions (content written, metadata
    never updated) to backups/orphans/. Nothing is deleted outright except temp files."""
    from inkforge.storage.atomic import remove_stale_temps

    report: dict[str, list[str]] = {"temps": [], "orphans": []}
    with store.lock:
        n = remove_stale_temps(store.dir)
        report["temps"] = [str(n)] if n else []
        for _, _, slot in all_slots(store):
            for p in slot.orphans():
                rel = p.relative_to(store.dir)
                dest = store.dir / "backups" / "orphans" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                report["orphans"].append(rel.as_posix())
        if report["orphans"]:
            store.audit.record("project.repair", store.id, detail=report)
    return report


def verify_integrity(store: ProjectStore) -> list[str]:
    """Every problem that would count as corruption: unreadable metadata, missing or altered version
    files, dangling pointers, orphans, temp files, records that fail validation, broken references."""
    problems: list[str] = []
    for kind, target, slot in all_slots(store):
        ref = f"{kind}:{target}" if target else kind
        if not slot.exists():
            continue
        try:
            meta = slot.meta()
        except Exception as e:
            problems.append(f"{ref}: unreadable meta ({e})")
            continue
        versions = {v.version for v in meta.versions}
        for ptr in ("active", "approved"):
            v = getattr(meta, ptr)
            if v is not None and v not in versions:
                problems.append(f"{ref}: {ptr} v{v} not among versions")
        for v in sorted(versions):
            try:
                data = slot.read_text(v)
                if kind != "draft":
                    store._model_for(kind).model_validate_json(data)  # type: ignore[arg-type]
            except Exception as e:
                problems.append(f"{ref} v{v}: {type(e).__name__}: {e}")
        problems += [f"{ref}: orphan {p.name}" for p in slot.orphans()]
    problems += [f"temp file {p.relative_to(store.dir).as_posix()}" for p in store.dir.rglob(".*.tmp")]
    outline = store.try_read("outline")
    if outline:
        problems += [
            f"outline: {c.id} has no plan"
            for c in outline.chapters
            if store.meta("chapter", c.id).active is None
        ]
    tl = store.try_read("timeline")
    if tl:
        known = set(store.scene_ids())
        problems += [f"timeline: unknown scene {e.scene_id}" for e in tl.entries if e.scene_id not in known]
    try:
        store.project  # noqa: B018
        store.get_promises()
        for e in store.list_codex():
            e.model_validate(e.model_dump(by_alias=True))
    except Exception as e:
        problems.append(f"project/codex/promises: {type(e).__name__}: {e}")
    return problems
