"""Confine every file operation to a project root (docs/03 § Security).

Rejects: absolute paths, drive letters, UNC paths, `..` components, NUL bytes, Windows reserved
device names, alternate data streams (`:`), trailing dots/spaces, and any symlink or junction on
the way from the root to the target — whether or not the target exists yet.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.IGNORECASE)
_BAD_CHARS = set('<>:"|?*') | {chr(c) for c in range(32)}


class PathEscapeError(PermissionError):
    """A path tried to leave (or could leave) the project root."""


def _components(rel: str) -> list[str]:
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise PathEscapeError(f"invalid path {rel!r}")
    win, posix = PureWindowsPath(rel), PurePosixPath(rel)
    if win.drive or win.root or posix.root or rel.startswith(("\\\\", "//")):
        raise PathEscapeError(f"absolute path not allowed: {rel!r}")
    parts = [p for p in re.split(r"[\\/]+", rel) if p not in ("", ".")]
    if not parts:
        raise PathEscapeError(f"empty path: {rel!r}")
    for part in parts:
        if part == "..":
            raise PathEscapeError(f"parent traversal not allowed: {rel!r}")
        if any(ch in _BAD_CHARS for ch in part):
            raise PathEscapeError(f"illegal character in {part!r}")
        if part.endswith((" ", ".")):
            raise PathEscapeError(f"trailing dot/space not allowed: {part!r}")
        if _RESERVED.match(part):
            raise PathEscapeError(f"reserved device name: {part!r}")
    return parts


def _is_link(p: Path) -> bool:
    return p.is_symlink() or p.is_junction()


def resolve_inside(root: Path, rel: str) -> Path:
    """Return the absolute path for `rel` under `root`, or raise PathEscapeError."""
    root_real = Path(os.path.realpath(root))
    if _is_link(Path(root)):
        raise PathEscapeError(f"project root is a link: {root}")
    cur = root_real
    for part in _components(rel):
        cur = cur / part
        if _is_link(cur):
            raise PathEscapeError(f"link in path not allowed: {cur}")
    final = Path(os.path.realpath(cur))
    if final != root_real and root_real not in final.parents:
        raise PathEscapeError(f"{rel!r} resolves outside the project root")
    return cur


def relative_to_root(root: Path, path: Path) -> str:
    """Inverse helper for display/audit: posix-style path relative to root (must be inside)."""
    rel = Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(root)))
    return rel.as_posix()
