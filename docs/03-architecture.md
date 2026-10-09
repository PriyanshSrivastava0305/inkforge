# 03 — Architecture

Target diagram: [`reference/inkforge-local-architecture-v2.svg`](reference/inkforge-local-architecture-v2.svg). This page is the written version, with the decisions behind it.

## Layers

```
┌──────────────────────────── 01 EXPERIENCE ────────────────────────────┐
│ Story Workbench (graph) │ Manuscript Editor │ Codex │ CLI + Run Console │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ REST + SSE on 127.0.0.1
┌──────────────────────────── 02 APPLICATION CORE ──────────────────────┐
│ API Gateway (FastAPI) │ Conductor │ Context Builder + Prompt Registry │
│                       Policy, Audit & Workspace Guard                  │
└───────────────────────────────┬────────────────────────────────────────┘
┌──────────────────────────── 03 STORY PIPELINE ────────────────────────┐
│ A Spec → B Design → C Chapters → D Scene cards → E Draft →            │
│ F Critique ⇄ G Revise → H Approve & Export                            │
└───────────────────────────────┬────────────────────────────────────────┘
┌──────────────────────────── 04 SHARED SERVICES ───────────────────────┐
│ Continuity & Canon Engine │ Voice & Craft Critics │ Long-Book Memory  │
└───────────────────────────────┬────────────────────────────────────────┘
┌──────────────────────────── 05 LOCAL RUNTIME & DATA ──────────────────┐
│ Model Router (Ollama/llama.cpp) │ Workspace files + SQLite │ FTS5/FAISS│
│                          Offline boundary                              │
└────────────────────────────────────────────────────────────────────────┘
```

## Components

| Component | Responsibility | Package | Phase |
|---|---|---|---|
| **Domain** | Pydantic models, IDs, invariants, form profiles | `packages/domain` | 1 |
| **Workspace store** | Read/write JSON + Markdown, atomic writes, versions, SQLite index, rebuild | `packages/storage` | 1 |
| **Policy & Audit guard** | Path sandbox, append-only audit log, confirmation gates, backups | `packages/policy` | 1 |
| **Model router** | One `generate()` / `generate_structured()` interface over Ollama and llama.cpp | `packages/models` | 2 |
| **Prompt & skill registry** | Versioned prompt templates and skills (frontmatter + markdown) | `packages/prompts` | 2 |
| **Conductor** | Stage state machine, jobs, retries, pause/resume/cancel, approval gates | `packages/pipeline` | 3 |
| **Stages A–H** | One module per stage; typed in/out | `packages/pipeline/stages` | 3, 4, 6, 10 |
| **Context builder** | Assemble prompts within a token budget; write context manifests | `packages/context` | 4 (basic), 5 (full) |
| **Memory** | CORE/DIGEST/RECALL, FTS5, optional embeddings | `packages/memory` | 5 |
| **Quality** | Fact extraction, deterministic checks, critics, revision proposals | `packages/quality` | 6 |
| **Exporters** | Markdown, DOCX, EPUB, Fountain, PDF + preflight | `packages/exporters` | 4 (MD), 9, 10 |
| **API** | FastAPI app, REST + SSE event stream | `apps/api` | 7 |
| **Web** | React + Vite UI | `apps/web` | 7–8 |
| **CLI** | Typer commands mirroring the API | `cli/` | 1 onward |

## Data flow (one generation)

1. Author triggers a stage (UI button or `inkforge run-stage …`).
2. **Conductor** creates a `Job`, checks the stage's input artifacts exist and are approved/valid, records input hashes.
3. **Context builder** pulls Book Spec, CORE facts, the relevant plan/scene card, Codex entries, retrieved passages and recap, trims to budget, writes a **context manifest**.
4. **Prompt registry** renders the stage's versioned template with that context.
5. **Model router** calls the configured local runtime for the stage's profile (planner / prose / critic).
6. **Stage** validates output against its schema. Invalid → repair/retry (bounded). Still invalid → job fails, last valid artifact untouched, error surfaced.
7. **Workspace store** writes a *new version* atomically; SQLite index updated; **audit** event appended; **checkpoint** recorded.
8. Conductor emits events (SSE / console). If the stage has an approval gate, the job stops in `awaiting_approval`.

## Trust model

| Actor | Trusted for | Not trusted for |
|---|---|---|
| Deterministic code | Paths, schemas, IDs, versions, checkpoints, audit, export assembly | — |
| LLM | Proposals: plans, prose, critiques | Facts, canon, file paths, IDs, anything executable |
| Author | Canon, approvals, resolutions | — |
| Indexes, summaries | Speed | Truth — always rebuildable from source files |

Consequences: model output never chooses a file path or an ID (IDs are minted by code; the model refers to entities by the IDs we gave it, and unknown IDs are rejected). Model-proposed canon facts land in a **review queue**. Generated text is never executed.

## Stack (and why)

| Layer | Choice | Reason | Alternatives considered |
|---|---|---|---|
| Backend language | Python 3.12 | NLP/model tooling, Pydantic, your existing stack | TypeScript (AuthorAgent, OpenWrite) — weaker ML ecosystem |
| Env/packaging | `uv` + `pyproject.toml` (workspace) | Fast, reproducible, lockfile | Poetry |
| API | FastAPI + SSE (`sse-starlette`) | Typed, async, OpenAPI for the frontend | Litestar |
| CLI | Typer + Rich | Same models as API; nice console | Click |
| Schemas | Pydantic v2 | Shared contract; JSON Schema for structured output | attrs |
| Metadata DB | SQLite via SQLModel, Alembic migrations | Single file, local, FTS5 built in | DuckDB |
| Search | SQLite FTS5; optional `sentence-transformers` + FAISS | Lexical first; semantic when hardware allows | Chroma |
| Model runtime | Ollama first; llama.cpp server adapter (OpenAI-compatible) | Easy local install (you already have Ollama); GGUF; grammar-constrained JSON | LM Studio (also OpenAI-compatible — free via same adapter) |
| Frontend | React + TypeScript + Vite, TanStack Query, Tailwind + shadcn/ui | Fast, well-known | Svelte |
| Graph | React Flow (`@xyflow/react`) + ELK/dagre auto-layout | Node graphs with custom nodes | Cytoscape |
| Editor | Tiptap (prose); CodeMirror with Fountain mode (script) | Rich prose editing; plain-text screenplay editing | Lexical |
| Exports | python-docx, ebooklib, Pandoc optional; screenplay PDF via own Fountain renderer (ReportLab/WeasyPrint) | Pure-Python, offline | afterwriting (Node) |
| Tests | pytest, hypothesis, Playwright (UI), vitest (web units) | — | — |
| Lint/type | ruff, mypy/pyright, biome | — | — |

## Repository layout

One distribution with a subpackage per component ([ADR-0001](adr/0001-single-package-layout.md)). In the component table above, `packages/<x>` means `src/inkforge/<x>`.

```text
storywriter/                    # repo root (local git only)
├── src/inkforge/
│   ├── domain/                 # Pydantic models, IDs, form profiles, invariants
│   ├── storage/                # workspace FS, atomic writes, versions, SQLite index
│   ├── policy/                 # path guard, audit log, confirmations, backups
│   ├── models/                 # router, Ollama + llama.cpp adapters, structured output
│   ├── prompts/                # versioned templates + skills/ (SKILL.md)
│   ├── pipeline/               # conductor, jobs, checkpoints, stages/
│   ├── context/                # context builder, token budgeting, manifests
│   ├── memory/                 # CORE/DIGEST/RECALL, FTS5, embeddings
│   ├── quality/                # fact extraction, checks, critics, revision
│   ├── exporters/              # md, docx, epub, fountain, pdf, preflight
│   ├── api/                    # FastAPI app (Phase 7)
│   ├── config.py               # TOML config loader; loopback + model allow-list enforced
│   └── cli.py                  # Typer app: `inkforge …`
├── apps/web/                   # React + Vite UI (Phase 7)
├── config/
│   ├── defaults.toml
│   ├── model-profiles.toml     # 8B default (D16)
│   └── model-profiles.fast.toml  # 1B plumbing set
├── tests/{unit,integration,golden,e2e}/
├── scripts/                    # smoke_ollama, build_tokenizer, bench_models
├── docs/                       # this plan, adr/, benchmarks/, reference/ (build guide + diagrams)
├── pyproject.toml              # single uv project
└── README.md
```

User data lives **outside** the repo, default `%USERPROFILE%\InkForge\workspace` (configurable). `.gitignore` covers `workspace/`, model caches, logs, exports, local config overrides.

## Concurrency & jobs

- One **job runner** process inside the API (asyncio task queue). Jobs persist to SQLite (`jobs` table) *and* a checkpoint JSON, so a crash loses at most the in-flight model call.
- Default concurrency: 1 generation at a time per model (local GPUs don't parallelize well); critics can run in parallel if the runtime reports spare slots.
- Cancellation: cooperative; the router aborts the HTTP stream to the runtime.
- The CLI can run jobs in-process (no API needed) — important for Phases 1–6.

## Security & offline (summary — details in the build guide §11)

- Bind API and runtime to `127.0.0.1`; LAN only by explicit flag.
- No telemetry, update checks, remote providers or web tools in the code path. A CI test greps dependencies and config for remote endpoints.
- Path guard: every file op resolves against the project root; reject `..`, absolute paths, symlink/junction escapes (Windows junctions included).
- Atomic writes (`write temp → fsync → os.replace`), backup snapshot before bulk operations.
- Append-only JSONL audit log per project.
- Never execute generated content; skills are templates only, no code.
- Offline acceptance test runs with the network disabled (Windows: firewall rule / disabled adapter; Linux CI: `unshare -n` or Docker `--network none`).
