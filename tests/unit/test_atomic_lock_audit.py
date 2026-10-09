import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from inkforge.domain.common import Actor
from inkforge.policy.audit import AuditLog
from inkforge.policy.lock import ProjectLock, ProjectLockedError
from inkforge.storage import atomic
from inkforge.storage.atomic import atomic_write_text, remove_stale_temps


def test_atomic_write_replaces(tmp_path: Path):
    p = tmp_path / "a" / "f.txt"
    atomic_write_text(p, "one")
    atomic_write_text(p, "two — ünïcode\n")
    assert p.read_bytes() == "two — ünïcode\n".encode()  # exact bytes, LF kept
    assert not list(p.parent.glob(".*.tmp"))


def test_failed_replace_keeps_original_and_cleans_temp(tmp_path: Path, monkeypatch):
    p = tmp_path / "f.txt"
    atomic_write_text(p, "original")

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "new")
    assert p.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(".*.tmp"))


def test_transient_permission_error_is_retried(tmp_path: Path, monkeypatch):
    p = tmp_path / "f.txt"
    real, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("AV scanner has it open")
        real(src, dst)

    monkeypatch.setattr(atomic.os, "replace", flaky)
    atomic_write_text(p, "ok")
    assert p.read_text(encoding="utf-8") == "ok" and calls["n"] == 3


def test_remove_stale_temps(tmp_path: Path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / ".x.json.abc.tmp").write_text("partial")
    assert remove_stale_temps(tmp_path) == 1


# ---------------------------------------------------------------- lock


def test_lock_is_reentrant_and_exclusive(tmp_path: Path):
    a, b = ProjectLock(tmp_path, timeout=0.3), ProjectLock(tmp_path, timeout=0.3)
    with a, a:
        assert a.held
        with pytest.raises(ProjectLockedError):
            b.acquire()
    with b:
        assert b.held
    assert not a.held and not b.held


def test_lock_held_by_other_process_and_released_when_it_dies(tmp_path: Path):
    src = Path(__file__).parents[2] / "src"
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(src)!r})
        from pathlib import Path
        from inkforge.policy.lock import ProjectLock
        ProjectLock(Path({str(tmp_path)!r})).acquire()
        print("locked", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "locked"
        with pytest.raises(ProjectLockedError):
            ProjectLock(tmp_path, timeout=0.3).acquire()
    finally:
        proc.kill()  # simulated crash: no clean release
        proc.wait()
    deadline = time.monotonic() + 5
    lock = ProjectLock(tmp_path, timeout=5)
    lock.acquire()  # the OS released the dead process's lock
    lock.release()
    assert time.monotonic() < deadline


# ---------------------------------------------------------------- audit


def test_audit_append_only(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("card.save", "card:ch01-sc01", before_ref=None, after_ref="v1")
    log.record("card.approve", "card:ch01-sc01", actor=Actor.USER, via="--yes")
    first = (tmp_path / "audit.jsonl").read_bytes()
    log.record("draft.save", "draft:ch01-sc01")
    assert (tmp_path / "audit.jsonl").read_bytes().startswith(first)
    events = list(log)
    assert [e.operation for e in events] == ["card.save", "card.approve", "draft.save"]
    assert events[1].via == "--yes" and events[1].actor == Actor.USER
