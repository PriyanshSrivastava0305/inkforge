# 11 — Testing & Acceptance

## Test layers

| Layer | Tooling | Model | Runs |
|---|---|---|---|
| Unit | pytest, hypothesis | none | every commit (local pre-commit, Windows) |
| Integration (pipeline) | pytest | **Fake adapter** (record/replay) | every commit |
| Golden evaluations | pytest + custom scorers | Fake + real | Fake on commit; `llama3.2:1b` on demand; `llama3.1:8b` overnight/manual |
| Web unit | vitest | — | every commit |
| E2E | Playwright | Fake | every PR |
| Offline acceptance | scripted run, network disabled | `llama3.1:8b-instruct-q4_K_M` | before each milestone/release (overnight) |
| Benchmarks | `scripts/bench_models.py` | real | on model/prompt changes |

The **Fake adapter** is what makes the project testable: recorded model outputs keyed by prompt hash, plus hand-written fixtures for malformed output (bad JSON, unknown IDs, truncated text, hallucinated quotes).

## Golden fixtures (`tests/golden/`)

| Fixture | Purpose | Phase |
|---|---|---|
| `mini_book/` | Benchmark: spec → 3 chapters → 6 cards → 2 scenes → critique | 0 |
| `novella_5ch/` | End-to-end MVP run and export ordering | 4 |
| `continuity_book/` | 30 scenes, 25 planted contradictions across all rule types + 10 decoys | 0 (outline), 6 |
| `memory_book/` | Dependent-fact chains for recall@context | 5 |
| `short_film/` | ~15-page screenplay, Fountain correctness | 9 |
| `migrations/` | Old-schema projects | 10 |

## Acceptance criteria (release gate, from the build guide §13 plus additions)

| Test | Pass condition |
|---|---|
| **Offline** | After setup, with networking disabled: startup, project creation, planning, drafting, critique, revision, export all succeed |
| **Network** | No remote endpoints in config/code paths; socket monitor sees no non-loopback connections during the offline run |
| **Resume** | Kill at 50 random points across a long run → resume completes, zero corrupted or orphaned artifacts |
| **Stage isolation** | Each stage A–H runs alone from saved inputs |
| **Canon** | Contradictions produce evidence-linked findings; no canon change without confirmation |
| **Path safety** | Traversal, absolute paths, symlinks, Windows junctions rejected |
| **Context** | Overlong context trimmed by priority; manifest records included/trimmed/dropped |
| **Export** | Chapter order = approved outline; no unapproved version included; preflight lists all blockers |
| **Portability** | Backup → restore on another machine with same app version → identical project |
| **Reindex** | Deleting SQLite + indexes and rebuilding yields identical query results |
| **Script format** | Script exports parse with zero Fountain errors |

## Metrics tracked over time (`inkforge stats`, CI artifacts)

Schema-valid rate, retries per stage, tokens/s, scene draft time, contradiction recall/precision, critic precision on calibration set, average craft rubric per chapter, findings per 1k words, export preflight blockers.

## Definition of done (any task)

Code + tests + docs updated (the relevant page in this folder) + CHANGELOG entry + no new lint/type errors + works on Windows.
