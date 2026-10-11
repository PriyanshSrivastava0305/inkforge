# pyright: reportArgumentType=false, reportCallIssue=false
"""Offline acceptance #1 (Phase 4): the real CLI, a real local model, and a network guard that fails
any non-loopback connection (tests/support/offline/sitecustomize.py).

    uv run pytest -m "ollama and slow" -s tests/integration/test_offline_acceptance.py

Defaults to the 5-chapter novella fixture on llama3.2:1b (wiring, ~20 min on this CPU). Set
INKFORGE_OFFLINE_PROFILE=default to run it on the 8B instead (hours; overnight). A true airplane-mode
run remains the release gate (docs/11); this test makes the "no network" claim checkable every time.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from inkforge.domain.ids import slugify
from inkforge.storage.workspace import Workspace, verify_integrity

pytestmark = [pytest.mark.ollama, pytest.mark.slow]

ROOT = Path(__file__).parents[2]
FIXTURE_NAME = os.environ.get("INKFORGE_OFFLINE_FIXTURE", "novella_5ch")  # or short_story
FIXTURE = yaml.safe_load(
    (ROOT / "tests" / "golden" / FIXTURE_NAME / "fixture.yaml").read_text(encoding="utf-8")
)
CHAPTERS = FIXTURE["settings"]["chapters"]
SCENES = CHAPTERS * FIXTURE["settings"]["scenes_per_chapter"]
PROFILE = os.environ.get("INKFORGE_OFFLINE_PROFILE", "fast")


@pytest.fixture(scope="module", autouse=True)
def ollama_up():
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=3)
    except httpx.HTTPError:
        pytest.skip("Ollama not reachable")


def test_full_run_offline(tmp_path: Path):
    ws_root = tmp_path / "ws"
    net_log = tmp_path / "network-attempts.log"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests" / "support" / "offline"), str(ROOT / "src")]),
        "INKFORGE_NET_LOG": str(net_log),
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("INKFORGE_FAKE", None)
    profile = [] if PROFILE == "default" else ["--profile-set", PROFILE]

    def cli(*args: str, timeout: int = 6 * 3600) -> str:
        p = subprocess.run(
            [sys.executable, "-m", "inkforge.cli", *args, "-w", str(ws_root)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        assert p.returncode == 0, f"{args}: {p.stdout[-3000:]}\n{p.stderr[-3000:]}"
        return p.stdout

    cli("init", FIXTURE["title"], "--form", FIXTURE["form"])
    store = Workspace(ws_root).open(slugify(FIXTURE["title"]))
    store.update_project(settings=FIXTURE["settings"], target_length=FIXTURE["target_length"])
    out = cli("run", "-p", store.id, "--mode", "full", "--yes", "--idea", FIXTURE["idea"], *profile)
    assert "succeeded" in out
    cli("approve", "-p", store.id, "drafts", "--all", "--confirm")
    out = cli("export", "-p", store.id)
    assert "exported final" in out

    assert not net_log.exists() or net_log.read_text(encoding="utf-8").strip() == "", net_log.read_text(
        encoding="utf-8"
    )
    scenes = store.scenes_in_order()
    assert len(store.read("outline").chapters) == CHAPTERS and len(scenes) == SCENES
    book = sorted((store.dir / "exports").glob("*/book.md"))
    final = [b for b in book if not b.parent.name.endswith("-preview")][-1].read_text(encoding="utf-8")
    positions = [
        final.index(store.read("draft", sid, version=store.meta("draft", sid).approved)[:80])
        for sid in scenes
    ]
    assert positions == sorted(positions), "export order differs from the outline"
    for sid in scenes:
        extra = store.meta("draft", sid).get(store.meta("draft", sid).active).extra
        assert (store.dir / extra["context_manifest_path"]).is_file()
    assert verify_integrity(store) == []
    report = {sid: store.meta("draft", sid).get(1).extra.get("word_count") for sid in scenes}
    (tmp_path / "words.json").write_text(json.dumps(report), encoding="utf-8")
    print(
        f"offline acceptance ok: fixture={FIXTURE_NAME} profile={PROFILE} words={sum(report.values())} export={len(final.split())} words"
    )
