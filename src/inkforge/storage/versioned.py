"""Immutable versioned artifacts with active/approved pointers (docs/04 § Conventions, D8).

Layout for an artifact named `card` in `scenes/ch01-sc02/`:

    card.v1.json, card.v2.json      immutable content, never overwritten
    card.meta.json                  pointers + per-version records (rewritten atomically)

Crash safety: the content file is written before the meta file. A crash in between leaves an
orphan `vN` file that no pointer references; the next write skips past it, and `orphans()` lists it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from inkforge.domain.common import Actor, Record, SchemaRecord, utcnow
from inkforge.storage.atomic import atomic_write_text, write_new_file
from inkforge.storage.hashing import sha256_bytes


class VersionRecord(Record):
    version: int = Field(ge=1)
    sha256: str
    input_hashes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: Actor = Actor.SYSTEM
    parent: int | None = None
    note: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ArtifactMeta(SchemaRecord):
    SCHEMA: ClassVar[str] = "inkforge/artifact-meta@1"

    name: str
    ext: str
    active: int | None = None
    approved: int | None = None
    versions: list[VersionRecord] = Field(default_factory=list)

    def get(self, version: int) -> VersionRecord:
        for v in self.versions:
            if v.version == version:
                return v
        raise KeyError(f"{self.name} has no version {version}")


class ArtifactSlot:
    def __init__(self, directory: Path, name: str, ext: str):
        if not re.fullmatch(r"[a-z0-9_]+", name) or not re.fullmatch(r"[a-z]+", ext):
            raise ValueError(f"bad artifact name/ext {name!r}/{ext!r}")
        self.dir, self.name, self.ext = directory, name, ext
        self.meta_path = directory / f"{name}.meta.json"

    # ---- reading
    def exists(self) -> bool:
        return self.meta_path.is_file()

    def meta(self) -> ArtifactMeta:
        if not self.exists():
            return ArtifactMeta(name=self.name, ext=self.ext)
        return ArtifactMeta.model_validate_json(self.meta_path.read_text(encoding="utf-8"))

    def path_for(self, version: int) -> Path:
        return self.dir / f"{self.name}.v{version}.{self.ext}"

    def read_bytes(self, version: int | None = None) -> bytes:
        meta = self.meta()
        v = version if version is not None else meta.active
        if v is None:
            raise FileNotFoundError(f"{self.dir / self.name} has no active version")
        rec = meta.get(v)
        data = self.path_for(v).read_bytes()
        if sha256_bytes(data) != rec.sha256:
            raise ValueError(f"{self.path_for(v)} content does not match its recorded hash")
        return data

    def read_text(self, version: int | None = None) -> str:
        return self.read_bytes(version).decode("utf-8")

    def active_sha(self) -> str | None:
        meta = self.meta()
        return meta.get(meta.active).sha256 if meta.active is not None else None

    def orphans(self) -> list[Path]:
        known = {v.version for v in self.meta().versions}
        pat = re.compile(rf"^{re.escape(self.name)}\.v(\d+)\.{self.ext}$")
        return sorted(
            p
            for p in self.dir.glob(f"{self.name}.v*.{self.ext}")
            if (m := pat.match(p.name)) and int(m[1]) not in known
        )

    # ---- writing (callers hold the project lock)
    def write(
        self,
        data: bytes,
        *,
        input_hashes: dict[str, str] | None = None,
        created_by: Actor = Actor.SYSTEM,
        note: str | None = None,
        extra: dict[str, Any] | None = None,
        make_active: bool = True,
    ) -> VersionRecord:
        meta = self.meta()
        on_disk = [
            int(m[1])
            for p in self.dir.glob(f"{self.name}.v*.{self.ext}")
            if (m := re.search(r"\.v(\d+)\.", p.name))
        ]
        n = max([v.version for v in meta.versions] + on_disk + [0]) + 1
        rec = VersionRecord(
            version=n,
            sha256=sha256_bytes(data),
            input_hashes=dict(input_hashes or {}),
            created_by=created_by,
            parent=meta.active,
            note=note,
            extra=dict(extra or {}),
        )
        write_new_file(self.path_for(n), data)
        meta.versions.append(rec)
        if make_active:
            meta.active = n
        self._save(meta)
        return rec

    def set_active(self, version: int) -> None:
        meta = self.meta()
        meta.get(version)
        meta.active = version
        self._save(meta)

    def approve(self, version: int | None = None) -> int:
        meta = self.meta()
        v = version if version is not None else meta.active
        if v is None:
            raise ValueError(f"{self.name}: nothing to approve")
        meta.get(v)
        meta.approved = v
        self._save(meta)
        return v

    def unapprove(self) -> None:
        meta = self.meta()
        meta.approved = None
        self._save(meta)

    def _save(self, meta: ArtifactMeta) -> None:
        atomic_write_text(self.meta_path, meta.to_json())
