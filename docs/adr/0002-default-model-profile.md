# ADR-0002: Default model profile — llama3.1 8B for every role, llama3.2 1B for plumbing

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** D16 (models), D17 (personal tool), D18 (strictly local), D25 (prefix caching)

## Context

Phase 0 asked for a benchmark-backed default. D16 already limits the choice to the two installed models. The open question was whether the 8B is usable on this CPU-only laptop (i7-1165G7, 16 GB RAM) and whether the 1B is good enough for anything beyond wiring tests.

Evidence: [smoke test](../benchmarks/2026-10-09-smoke.md), mini-book benchmark on [8B](../benchmarks/2026-10-09-minibook-llama3.1_8b-instruct-q4_K_M.md) and [1B](../benchmarks/2026-10-09-minibook-llama3.2_1b.md).

| | llama3.1:8b-instruct-q4_K_M | llama3.2:1b |
|---|---|---|
| Structured calls valid first try (mini-book) | 6/6 | 6/6 |
| Mini-book wall time (spec → 3 ch → 6 cards → 2 × ~450-word drafts → critique) | 23.3 min | 5.5 min |
| Generation speed | 2.7–3.2 tok/s | 10–11 tok/s |
| Prompt reading, cold → reused prefix | ~11 → 16–24 tok/s observed in-pipeline | ~50 → ~110 tok/s |
| Critique quotes found in the scene (after folding quote marks) | 5/5 | 1/1 |
| Quality (spec, outline, characters) | Named cast, coherent three-act outline, readable first-person prose | Generic, role-named characters, thin outlines |

## Decision

- `config/model-profiles.toml`: `llama3.1:8b-instruct-q4_K_M` for planner, prose and critic (`num_ctx` 6144).
- `config/model-profiles.fast.toml`: `llama3.2:1b` for every role. Used only for real-model plumbing tests, never to judge quality.
- One model resident at a time. Unload before switching (verified clean 1B → 8B → 1B).

## Consequences

- At ~3 tok/s a ~450-word scene takes ~4 min, so a 2,000-word scene takes ~15–20 min, as projected. Long runs are batch jobs.
- **Critique prompt needs work (Phase 6).** Both models put the quote into `claim` (5/5 on 8B) instead of stating a problem. Fix it with field order and naming (`quote` first, then `problem`, then `suggestion`) plus a worked example.
- **Quote verification must fold quote marks.** The 8B swapped `"` for `'` inside JSON strings, which made 3 genuine quotes look hallucinated until the matcher folded `" ' “ ” ‘ ’` and dashes. Phase 6 evidence checks use the same normalization.
- Revisit only if the hardware changes or a model on the allow-list changes.
