"""Atomic file writes: temp file in the same dir → fsync → os.replace (docs/03 § Security).

On Windows, os.replace can transiently fail with PermissionError when an antivirus or
indexer has the target open; we retry briefly before giving up.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

_RETRIES = 20
_DELAY = 0.05


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(_RETRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == _RETRIES - 1:
                    raise
                time.sleep(_DELAY)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    # newline="" semantics: we write exactly the bytes we were given (LF), never CRLF-translated.
    atomic_write_bytes(path, text.encode("utf-8"))


def write_new_file(path: Path, data: bytes) -> None:
    """Create a file that must not already exist (immutable artifact versions)."""
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable file {path}")
    atomic_write_bytes(path, data)


def _fsync_dir(d: Path) -> None:
    if os.name == "nt":  # directories can't be opened for fsync on Windows; NTFS journals the rename
        return
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_stale_temps(directory: Path) -> int:
    """Delete `.*.tmp` leftovers from an interrupted write. Returns how many were removed."""
    n = 0
    for p in directory.rglob(".*.tmp"):
        with contextlib.suppress(OSError):
            p.unlink()
            n += 1
    return n
