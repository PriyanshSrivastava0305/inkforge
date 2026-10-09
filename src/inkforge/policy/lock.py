"""Cross-process project write lock (CLI and API must not write the same project at once).

Uses an OS byte-range lock on `<project>/.lock`: `msvcrt.locking` on Windows, `fcntl.flock` elsewhere.
The OS releases it if the process dies, so there are no stale locks after a crash.
Re-entrant within one ProjectLock instance.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import TracebackType


class ProjectLockedError(RuntimeError):
    pass


class ProjectLock:
    def __init__(self, project_dir: Path, timeout: float = 10.0, name: str = ".lock"):
        self.path = project_dir / name
        self.timeout = timeout
        self._fd: int | None = None
        self._depth = 0

    def acquire(self) -> None:
        if self._depth:
            self._depth += 1
            return
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise ProjectLockedError(
                        f"{self.path.name} in {self.path.parent} is held by another process"
                    ) from None
                time.sleep(0.1)
        self._fd, self._depth = fd, 1

    def release(self) -> None:
        if not self._depth:
            return
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            try:
                _unlock(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def held(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(
        self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
