"""Project backup/restore as a zip with a hash manifest (docs/11 § Portability).

Excluded: the rebuildable index, the lock file, and earlier backups. Restore validates every
member name with the path guard (no zip-slip), verifies hashes, extracts to a temp dir, then
renames into place — a failed restore never leaves a half-written project.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from inkforge.domain.common import Actor, utcnow
from inkforge.policy.pathguard import PathEscapeError, resolve_inside
from inkforge.storage.hashing import sha256_bytes
from inkforge.storage.workspace import ProjectExistsError, ProjectStore, Workspace

MANIFEST = "inkforge-backup.json"
_EXCLUDE_TOP = {"index.sqlite", "index.sqlite.tmp", ".lock", "backups"}


def _files(project_dir: Path) -> list[Path]:
    out = []
    for p in sorted(project_dir.rglob("*")):
        rel = p.relative_to(project_dir)
        if p.is_file() and rel.parts[0] not in _EXCLUDE_TOP and not p.name.endswith(".tmp"):
            out.append(p)
    return out


def backup_project(store: ProjectStore, dest: Path | None = None) -> Path:
    with store.lock:
        stamp = f"{utcnow():%Y%m%d-%H%M%S}"
        dest = dest or store.path(f"backups/{store.id}-{stamp}.zip")
        dest.parent.mkdir(parents=True, exist_ok=True)
        files = {}
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in _files(store.dir):
                rel = p.relative_to(store.dir).as_posix()
                data = p.read_bytes()
                files[rel] = sha256_bytes(data)
                z.writestr(rel, data)
            manifest = {"project_id": store.id, "created": stamp, "files": files}
            z.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
        store.audit.record(
            "project.backup", store.id, actor=Actor.USER, after_ref=dest.name, detail={"files": len(files)}
        )
        return dest


def restore_project(workspace: Workspace, zip_path: Path, *, project_id: str | None = None) -> ProjectStore:
    workspace.ensure()
    with zipfile.ZipFile(zip_path) as z:
        manifest = json.loads(z.read(MANIFEST))
        pid = project_id or manifest["project_id"]
        target = resolve_inside(workspace.projects_dir, pid)
        if target.exists():
            raise ProjectExistsError(f"project {pid!r} already exists; restore under another id")
        staging = resolve_inside(workspace.projects_dir, f".restore-{pid}")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            names = set(z.namelist()) - {MANIFEST}
            if names != set(manifest["files"]):
                raise ValueError("backup contents do not match its manifest")
            for name in sorted(names):
                dest = resolve_inside(staging, name)  # rejects zip-slip, absolute, reserved names
                data = z.read(name)
                if sha256_bytes(data) != manifest["files"][name]:
                    raise ValueError(f"hash mismatch for {name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            if not (staging / "project.json").is_file():
                raise ValueError("backup has no project.json")
            staging.rename(target)
        except (ValueError, PathEscapeError, KeyError):
            shutil.rmtree(staging, ignore_errors=True)
            raise
    store = ProjectStore(target)
    if project_id and project_id != manifest["project_id"]:
        store.update_project(id=project_id)
    store.index.rebuild()
    store.audit.record("project.restore", store.id, actor=Actor.USER, detail={"from": zip_path.name})
    return store
