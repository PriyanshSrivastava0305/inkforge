"""Phase 3 exit criterion: kill the planner at random points 20 times; every resume must complete
with valid artifacts (docs/10 Phase 3, docs/11 Resume)."""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from inkforge.domain.models import JobStatus
from inkforge.storage.workspace import Workspace, verify_integrity

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "tests" / "support" / "plan_proc.py"
KILLS = int(os.environ.get("INKFORGE_CHAOS_KILLS", "20"))


def _job_status(project_dir: Path) -> str | None:
    jobs = sorted((project_dir / "runs").glob("*/job.json")) if (project_dir / "runs").exists() else []
    return json.loads(jobs[-1].read_text(encoding="utf-8"))["status"] if jobs else None


def test_kill_and_resume_twenty_times(tmp_path: Path):
    seed = int(os.environ.get("INKFORGE_CHAOS_SEED", time.time_ns() % 10_000))
    rng = random.Random(seed)
    ws = Workspace(tmp_path / "ws").ensure()
    store = ws.create_project("Chaos Orchard", "novel")
    store.update_project(settings={"chapters": 6, "scenes_per_chapter": 3})
    env = {
        **os.environ,
        "INKFORGE_FAKE": "auto",
        "INKFORGE_FAKE_DELAY": "0.003",
        "PYTHONPATH": str(ROOT / "src"),
    }
    cmd = [sys.executable, str(HELPER), str(ws.root), store.id]

    kills = 0
    while kills < KILLS:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(rng.uniform(0.6, 3.0))  # startup is ~0.5–1 s, so some kills land during import
        if proc.poll() is not None:
            err = proc.stderr.read().decode() if proc.stderr else ""
            assert proc.returncode == 0, f"seed {seed}: helper crashed on its own:\n{err}"
            if _job_status(store.dir) == JobStatus.SUCCEEDED:
                break  # finished before we could kill it this round; start a fresh job to keep killing
            continue
        proc.kill()
        proc.wait()
        kills += 1

    # final uninterrupted resume
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=300)
    assert proc.returncode == 0, f"seed {seed}: {proc.stderr.decode()[-2000:]}"
    assert _job_status(store.dir) == JobStatus.SUCCEEDED, f"seed {seed}"

    problems = verify_integrity(store)
    assert problems == [], f"seed {seed}: {problems}"
    outline = store.read("outline")
    assert len(outline.chapters) == 6
    scenes = store.scenes_in_order()
    assert len(scenes) == 18 and sorted(e.scene_id for e in store.read("timeline").entries) == sorted(scenes)
    job = json.loads(sorted((store.dir / "runs").glob("*/job.json"))[-1].read_text(encoding="utf-8"))
    assert all(s["status"] == "done" for s in job["steps"]) and len(job["steps"]) == 2 + 3 + 6
    dump = store.index.dump()
    store.index.path.unlink()
    store.index.rebuild()
    assert store.index.dump() == dump, f"seed {seed}: index diverged from files"
    print(f"chaos seed={seed} kills={kills}")
