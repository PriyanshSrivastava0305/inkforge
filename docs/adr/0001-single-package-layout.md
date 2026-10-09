# ADR-0001: Single `inkforge` package instead of a multi-package uv workspace

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** D17 (personal tool); supersedes the `packages/*` layout in `docs/03-architecture.md`

## Context

`03` proposed a uv workspace with ten packages (`packages/domain`, `packages/storage`, …), each with its own `pyproject.toml`. That pays off when components are versioned or published separately. InkForge is a personal tool (D17) with one developer and one deployable.

## Decision

One distribution, `inkforge`, in `src/inkforge/`, with one subpackage per component:

```
src/inkforge/{domain,storage,policy,models,prompts,pipeline,context,memory,quality,exporters}/
src/inkforge/cli.py          # Typer entry point (`inkforge`)
src/inkforge/api/            # FastAPI app (Phase 7)
apps/web/                    # React UI (Phase 7)
```

Component boundaries are kept by convention: `domain` imports nothing from InkForge; `storage`/`policy` import only `domain`; `pipeline` sits on top. An import-boundary test can be added in Phase 1 if it drifts.

## Alternatives considered

| Option | Why not |
|---|---|
| uv workspace, 10 packages | 10× packaging boilerplate, cross-package version pinning, slower `uv sync`; no benefit without separate releases |
| Flat modules (no subpackages) | Loses the component structure the plan is organized around |

## Consequences

- Imports are `from inkforge.storage import …` everywhere.
- `docs/03-architecture.md` component table maps `packages/<x>` → `src/inkforge/<x>`.
- Revisit only if a component gets reused outside InkForge.
