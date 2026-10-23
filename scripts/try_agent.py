"""One-command trial of the whole agent: idea → plan → draft → check → polish → preview, on a tiny story.

    uv run python scripts/try_agent.py fake       # scripted fake model: ~10 s, proves the plumbing
    uv run python scripts/try_agent.py fast       # llama3.2:1b: ~15–30 min on this laptop
    uv run python scripts/try_agent.py default    # llama3.1 8B: ~1–2 h (plug the laptop in)

Options: --idea "…"   --scenes N (default 2)   --keep (leave the project in ~/InkForge for inspection)

Planning gates are answered automatically (--yes); everything else follows the normal rules: drafts are
not approved, canon is untouched (extracted facts only reach the review queue), and the run ends at a
watermarked preview. Prints a report and exits non-zero if any stage failed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IDEA = (
    "A lighthouse keeper on a cold northern coast finds letters in bottles that describe storms "
    "the day before they arrive, and must decide whether to warn the village that distrusts her."
)
STAGES = ["spec", "design", "chapters", "cards", "drafts", "critique", "revise", "preview"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", choices=["fake", "fast", "default"])
    ap.add_argument("--idea", default=IDEA)
    ap.add_argument("--scenes", type=int, default=1, help="scenes per chapter (3 chapters: one per act)")
    ap.add_argument("--keep", action="store_true", help="keep the project under ~/InkForge/trials")
    args = ap.parse_args()
    if args.model == "fake":
        os.environ["INKFORGE_FAKE"] = "auto"  # must be set before the router is built

    from inkforge.models.router import ModelRouter
    from inkforge.pipeline.conductor import Conductor
    from inkforge.storage.workspace import Workspace

    base = (
        Path.home() / "InkForge" / "trials" if args.keep else Path(tempfile.mkdtemp(prefix="inkforge-trial-"))
    )
    ws = Workspace(base).ensure()
    store = ws.create_project(f"trial {time.strftime('%Y%m%d-%H%M%S')}", "novel")
    words = 800 if args.model != "fast" else 500
    store.update_project(
        settings={"chapters": 3, "scenes_per_chapter": args.scenes, "digest_model": args.model != "fast"},
        target_length=words * args.scenes * 3,
    )

    timings: dict[str, float] = {}
    started: dict[str, float] = {}
    errors: list[str] = []

    def on_event(kind: str, rec: dict) -> None:
        now = time.monotonic()
        if kind == "stage.start":
            started[rec["stage"]] = now
            print(f"\n▶ {rec['stage']}  ({len(rec.get('targets') or [])} step(s))", flush=True)
        elif kind == "stage.done":
            timings[rec["stage"]] = now - started.get(rec["stage"], now)
        elif kind == "step.done":
            print(f"   ✓ {rec['step']}  {rec.get('seconds', 0):.0f}s", flush=True)
        elif kind == "revise.attempt":
            verdict = "kept" if rec["ok"] else f"refused: {rec['reason']}"
            print(f"   · revision {rec['revision']} try {rec['attempt']}: {verdict}", flush=True)
        elif kind == "problem" and rec.get("level") == "warning":
            print(f"   ! {rec['message'][:140]}", flush=True)
        elif kind == "job.failed":
            errors.append(rec["error"])

    async def go():
        router = ModelRouter.from_config(None if args.model in ("fake", "default") else args.model)
        c = Conductor(store, router, on_event=on_event)
        try:
            job = c.create_job(STAGES, {"idea": args.idea, "yes": True, "accept": ["high", "med"]})
            return await c.run(job), router.profile("prose").model
        finally:
            await router.aclose()

    t0 = time.monotonic()
    job, model = asyncio.run(go())
    total = time.monotonic() - t0

    s = store
    scenes = s.scenes_in_order()
    findings = [f for f in s.findings.all() if not f.is_hypothesis]
    by_status = Counter(f.status for f in findings)
    by_detector = Counter(
        f.detector.split(":")[0] + ":" + f.detector.split(":")[1].split("_")[0] for f in findings
    )
    revisions = s.revisions.all()
    kept = [r for r in revisions if r.decision == "accepted"]
    preview = sorted(s.dir.glob("exports/**/*.md"))
    needs_author = [f for f in findings if (f.resolution_note or "").startswith("needs author")]

    print("\n" + "=" * 72)
    print(f"model {model if args.model != 'fake' else 'fake (scripted)'} — job {job.job_id}: {job.status}"
          f" in {total / 60:.1f} min")  # fmt: skip
    if job.error_summary:
        print(f"ERROR: {job.error_summary}")
    print(f"plan:      {len(scenes)} scene(s): {', '.join(scenes)}")
    for sid in scenes:
        m = s.meta("draft", sid)
        if m.active:
            text = s.read("draft", sid)
            print(f"draft:     {sid} v{m.active}  {len(text.split())} words  (versions: {len(m.versions)})")
    print(f"findings:  {len(findings)} — {dict(by_status)}")
    print(f"           by kind: {dict(by_detector)}")
    print(
        f"revisions: {len(kept)} kept, {len(revisions) - len(kept)} refused; {len(needs_author)} finding(s) need you"
    )
    print(
        f"facts:     {len(s.review_queue.all())} in the review queue, {len(s.facts.all())} canon (unchanged by the run)"
    )
    print("time:      " + ", ".join(f"{k} {v:.0f}s" for k, v in timings.items()))
    if preview:
        print(f"preview:   {preview[-1]}")
    print(f"project:   {s.dir}" + ("" if args.keep else "  (temporary — use --keep to inspect later)"))

    ok = (
        job.status == "succeeded"
        and len(scenes) == 3 * args.scenes
        and all(s.meta("draft", x).active for x in scenes)
    )
    print("RESULT:    " + ("PASS — every stage ran end to end" if ok else "FAIL"))
    if not args.keep and ok and args.model == "fake":
        shutil.rmtree(base, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
