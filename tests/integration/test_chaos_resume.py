"""Phase 3 exit criterion: kill the planner at random points 20 times; every resume must complete
with valid artifacts (docs/10 Phase 3, docs/11 Resume).

Windows note: a venv's python.exe is a *launcher* that runs the real interpreter as a child process,
so Popen.kill() only kills the launcher and leaves the worker running (found 2026-10-09: the first
version of this test mostly killed launchers). Kills here take down the whole process tree, and after
every kill the test proves the worker is gone by acquiring the project's run lock, which the OS only
releases when the holder dies.
"""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from inkforge.domain.models import JobStatus
from inkforge.policy.lock import ProjectLock, ProjectLockedError
from inkforge.storage.workspace import Workspace, verify_integrity

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "tests" / "support" / "plan_proc.py"
KILLS = int(os.environ.get("INKFORGE_CHAOS_KILLS", "20"))


def hard_kill(proc: subprocess.Popen) -> None:
    """Crash the worker and all its children (no cleanup handlers run)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    proc.wait(timeout=30)


def assert_worker_dead(project_dir: Path) -> None:
    lock = ProjectLock(project_dir, timeout=5.0, name=".run.lock")
    try:
        lock.acquire()
    except ProjectLockedError:
        pytest.fail("worker survived the kill: .run.lock is still held (orphaned process?)")
    lock.release()


def latest_job(project_dir: Path) -> dict | None:
    jobs = sorted((project_dir / "runs").glob("*/job.json")) if (project_dir / "runs").exists() else []
    return json.loads(jobs[-1].read_text(encoding="utf-8")) if jobs else None


def test_kill_and_resume_twenty_times(tmp_path: Path):
    seed = int(os.environ.get("INKFORGE_CHAOS_SEED", time.time_ns() % 10_000))
    rng = random.Random(seed)
    ws = Workspace(tmp_path / "ws").ensure()
    store = ws.create_project("Chaos Orchard", "novel")
    store.update_project(settings={"chapters": 6, "scenes_per_chapter": 3})
    env = {
        **os.environ,
        "INKFORGE_FAKE": "auto",
        "INKFORGE_FAKE_DELAY": "0.004",
        "PYTHONPATH": str(ROOT / "src"),
    }
    cmd = [sys.executable, str(HELPER), str(ws.root), store.id]

    kills = interrupted = finished_early = 0
    while kills < KILLS:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(rng.uniform(0.8, 3.0))
        if proc.poll() is not None:
            err = proc.stderr.read().decode() if proc.stderr else ""
            assert proc.returncode == 0, f"seed {seed}: worker failed on its own:\n{err[-3000:]}"
            finished_early += 1
            if finished_early > KILLS:
                pytest.fail(
                    f"seed {seed}: the job keeps finishing before it can be killed; slow the fake model down"
                )
            continue
        job = latest_job(store.dir)
        hard_kill(proc)
        assert_worker_dead(store.dir)
        kills += 1
        if job and job["status"] == "running":
            interrupted += 1

    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=600)
    assert proc.returncode == 0, f"seed {seed}: {proc.stderr.decode()[-3000:]}"
    job = latest_job(store.dir)
    assert job is not None and job["status"] == JobStatus.SUCCEEDED, f"seed {seed}"
    assert interrupted >= KILLS // 2, f"seed {seed}: only {interrupted}/{kills} kills hit a running job"

    problems = verify_integrity(store)
    assert problems == [], f"seed {seed}: {problems}"
    assert len(store.read("outline").chapters) == 6
    scenes = store.scenes_in_order()
    assert len(scenes) == 18 and sorted(e.scene_id for e in store.read("timeline").entries) == sorted(scenes)
    assert all(s["status"] == "done" for s in job["steps"]) and len(job["steps"]) == 2 + 3 + 6
    resumes = sum(
        1
        for line in (store.dir / "runs" / job["job_id"] / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"job.resume"' in line
    )
    assert resumes >= interrupted, f"seed {seed}: {resumes} resumes for {interrupted} interruptions"
    dump = store.index.dump()
    store.index.path.unlink()
    store.index.rebuild()
    assert store.index.dump() == dump, f"seed {seed}: index diverged from files"
    print(f"chaos seed={seed} kills={kills} interrupted_running_job={interrupted} resumes={resumes}")
