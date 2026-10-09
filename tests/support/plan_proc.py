"""Subprocess body for the kill/resume chaos test: run (or resume) a plan job on the fake adapter."""

import asyncio
import os
import sys
from pathlib import Path

os.environ["INKFORGE_FAKE"] = "auto"
os.environ.setdefault("INKFORGE_FAKE_DELAY", "0.002")

from inkforge.models.router import ModelRouter
from inkforge.pipeline.conductor import Conductor, stages_for
from inkforge.storage.workspace import Workspace


async def main(ws_root: str, project: str) -> None:
    store = Workspace(Path(ws_root)).open(project)
    router = ModelRouter.from_config("fast")
    conductor = Conductor(store, router)
    job = conductor.latest_job()
    if job is None:
        job = conductor.create_job(
            stages_for("plan"), {"idea": "Bottled letters predict storms.", "yes": True}
        )
        await conductor.run(job)
    else:
        await conductor.resume(job.job_id)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
