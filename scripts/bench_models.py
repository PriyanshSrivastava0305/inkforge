"""Mini-book benchmark — thin wrapper around `inkforge bench` (Phase 2 moved the harness into the package).

    uv run python scripts/bench_models.py            # default profile set (llama3.1 8B, ~25 min on this CPU)
    uv run python scripts/bench_models.py fast       # llama3.2:1b (~5 min)

The Phase 0 reports (docs/benchmarks/2026-10-09-minibook-*.md) came from the pre-router version of this
script; reports from the router are named *-minibook-router-*.md.
"""

from __future__ import annotations

import asyncio
import sys

from inkforge.bench import run_bench
from inkforge.models.router import ModelRouter


async def main(profile_set: str | None) -> None:
    router = ModelRouter.from_config(profile_set)
    try:
        await run_bench(router, profile_set=profile_set)
    finally:
        await router.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
