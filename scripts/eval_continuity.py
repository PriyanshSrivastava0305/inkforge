"""End-to-end continuity evaluation on a local model: passage → fact extraction → quote check → rules.

    uv run python scripts/eval_continuity.py fast        # llama3.2:1b
    uv run python scripts/eval_continuity.py             # default profile (llama3.1 8B)
    uv run python scripts/eval_continuity.py fast c01 d09   # only some cases

Each planted contradiction (c01–c25, except book-level c23) and decoy (d01–d10) in the golden continuity
book has a short passage in tests/golden/continuity_book/passages.yaml. A contradiction counts as found
when a non-hypothesis rule hit of the planted rule type comes out of the passage; a decoy is a false
positive when any non-hypothesis hit above low severity does. Reports go to docs/benchmarks/.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.support.continuity_book import GOLDEN, build_continuity_book  # noqa: E402

from inkforge.models.router import ModelRouter  # noqa: E402
from inkforge.quality.check import ContinuityChecker  # noqa: E402
from inkforge.storage.workspace import Workspace  # noqa: E402
from inkforge.system import keep_awake  # noqa: E402


async def main(profile_set: str | None, only: list[str]) -> None:
    passages = yaml.safe_load((GOLDEN / "passages.yaml").read_text(encoding="utf-8"))
    store = Workspace(Path(tempfile.mkdtemp()) / "ws").create_project("The Glass Orchard")
    contradictions, decoys = build_continuity_book(store)
    router = ModelRouter.from_config(profile_set)
    checker = ContinuityChecker(store, router)
    rows = []
    cases = [("c", c) for c in contradictions] + [("d", d) for d in decoys]
    try:
        with keep_awake():
            for kind, case in cases:
                text = passages["contradictions" if kind == "c" else "decoys"].get(case.id)
                if (only and case.id not in only) or text is None or case.scene is None:
                    continue
                store.save_draft(case.scene, text.strip())
                t0 = time.monotonic()
                r = await checker.check(case.scene)
                hits = [
                    h for h in r.hits if not h.is_hypothesis
                ]  # c24 (payoff never set up) needs a model judgement: not extracted yet
                ok = (
                    any(h.rule == case.rule for h in hits)
                    if kind == "c"
                    else not any(h.severity != "low" for h in hits)
                )
                row = {
                    "case": case.id,
                    "kind": "contradiction" if kind == "c" else "decoy",
                    "rule": case.rule,
                    "scene": case.scene,
                    "ok": ok,
                    "seconds": round(time.monotonic() - t0, 1),
                    "error": r.extraction.error,
                    "observations": [
                        [o.subject_id, o.predicate, o.value, o.quote] for o in r.extraction.observations
                    ],
                    "discarded": [
                        [d.proposal.subject, d.proposal.predicate, d.proposal.value, d.reason]
                        for d in r.extraction.discarded
                    ],
                    "hits": [[h.rule, h.severity, h.is_hypothesis, h.claim] for h in r.hits],
                }
                rows.append(row)
                mark = "OK  " if ok else ("MISS" if kind == "c" else "FP  ")
                print(
                    f"{mark} {case.id} {case.rule:13} {row['seconds']:6.1f}s obs={len(row['observations'])} "
                    f"drop={len(row['discarded'])} {('ERR ' + str(row['error'])[:60]) if row['error'] else ''}",
                    flush=True,
                )
                if only:  # debugging a few cases: show what the model said and why it was kept or dropped
                    for o in row["observations"]:
                        print("     kept   ", o)
                    for d in row["discarded"]:
                        print("     dropped", d)
                    for h in row["hits"]:
                        print("     hit    ", h)
    finally:
        await router.aclose()

    c_rows = [r for r in rows if r["kind"] == "contradiction"]
    d_rows = [r for r in rows if r["kind"] == "decoy"]
    found = sum(r["ok"] for r in c_rows)
    fps = sum(not r["ok"] for r in d_rows)
    kept = sum(len(r["observations"]) for r in rows)
    dropped = sum(len(r["discarded"]) for r in rows)
    model = router.profile("planner").model
    summary = {
        "model": model,
        "date": date.today().isoformat(),
        "recall": f"{found}/{len(c_rows)}",
        "decoy_false_positives": f"{fps}/{len(d_rows)}",
        "observations_kept": kept,
        "proposals_discarded": dropped,
        "minutes": round(sum(r["seconds"] for r in rows) / 60, 1),
    }
    print(json.dumps(summary, indent=2))
    if not only:
        out = (
            ROOT
            / "docs"
            / "benchmarks"
            / f"{date.today().isoformat()}-continuity-{(profile_set or 'default')}.json"
        )
        out.write_text(
            json.dumps({"summary": summary, "cases": rows}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print("report:", out.relative_to(ROOT))


if __name__ == "__main__":
    args = sys.argv[1:]
    profile = args.pop(0) if args and args[0] in ("fast", "default") else None
    asyncio.run(main(None if profile == "default" else profile, args))
