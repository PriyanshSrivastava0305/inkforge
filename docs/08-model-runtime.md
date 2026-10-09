# 08 — Model Runtime

## Router interface

```python
class ModelRouter:
    async def generate(
        self,
        messages,
        profile: str,
        max_tokens: int,
        temperature: float | None = None,
        stop: list[str] | None = None,
        stream: bool = False,
    ) -> Generation: ...

    async def generate_structured(
        self, messages, schema: type[BaseModel], profile: str, max_attempts: int = 3
    ) -> BaseModel: ...

    async def health(self) -> dict[str, RuntimeHealth]: ...  # reachable, models loaded, ctx size
    def context_length(self, profile: str) -> int: ...
    def count_tokens(self, text: str, profile: str) -> int: ...
```

`Generation` carries text, `finish_reason` (`stop|length|cancelled|error`), token counts, latency, model name/digest. `finish_reason == "length"` is what drives scene continuation.

## Adapters

| Adapter | Endpoint | Structured output | Notes |
|---|---|---|---|
| **Ollama** (default) | `http://127.0.0.1:11434/api/chat` | `format: <JSON schema>` | Installed (0.34.0). Set `num_ctx` explicitly per request — Ollama's default context is small. Read `done_reason` (`stop`/`length`) → `finish_reason`. No tokenize endpoint. |
| **llama.cpp server** | `http://127.0.0.1:8080/v1/chat/completions` | `response_format` JSON schema / GBNF grammar | Best control; exposes `/tokenize`. |
| **OpenAI-compatible (local only)** | configurable `127.0.0.1` URL | `response_format` if supported | Covers LM Studio, vLLM, KoboldCpp. URL validator rejects non-loopback hosts unless LAN mode is on. |
| **Fake** (tests) | in-process | deterministic fixtures | Replays recorded outputs; used by unit/integration tests and CI. |

## Structured output strategy

1. Send JSON Schema via the runtime's constrained decoding (Ollama `format`, llama.cpp grammar).
2. Parse with Pydantic. On failure: **repair** attempt with the error message appended; then lower temperature; then fail the job.
3. Keep schemas small and flat per call (e.g., scene cards for *one chapter*, not the whole book). Long lists are generated in batches.
4. All IDs in model output must be from an allowed set passed in the prompt; validator rejects unknown IDs.

## Profiles

`config/model-profiles.toml` — profiles are configuration, not code. One model serves all roles (decision D16).

**Allowed models (for now):** only the two already installed. No other models get pulled.

| Model | Used for |
|---|---|
| `llama3.1:8b-instruct-q4_K_M` | **Default** for planner / prose / critic: real runs and quality judgements |
| `llama3.2:1b` | **Fast plumbing tests**: real-model smoke runs where speed matters more than quality (pipeline wiring, schema/repair paths, truncation, resume) |
| Fake adapter | Unit/integration tests and CI (no model) |

```toml
# config/model-profiles.toml — default set
[runtime]
adapter = "ollama"
base_url = "http://127.0.0.1:11434"
max_loaded_models = 1          # 16 GB RAM: never keep 1B and 8B loaded together
keep_alive = "15m"

[profiles.planner]
model = "llama3.1:8b-instruct-q4_K_M"
num_ctx = 6144
temperature = 0.4
output_fraction = 0.3

[profiles.prose]
model = "llama3.1:8b-instruct-q4_K_M"
num_ctx = 6144
temperature = 0.85
top_p = 0.92
repeat_penalty = 1.08
min_output_tokens = 1500       # drafts continue via finish_reason=length, so no huge single call

[profiles.critic]
model = "llama3.1:8b-instruct-q4_K_M"
num_ctx = 6144
temperature = 0.1

[profiles.embedding]
model = "nomic-embed-text"     # already installed; still off by default (Phase 5 flag)
enabled = false

# config/model-profiles.fast.toml — `--profile-set fast` swaps every role to:
#   model = "llama3.2:1b", num_ctx = 4096
```

`num_ctx = 6144` is a starting value. On CPU, every extra context token costs prompt-reading time (see below), so the context budget is kept small on purpose.

### Your hardware (measured 2026-10-09)

Intel i7-1165G7 (4C/8T, Iris Xe), **16 GB RAM, no discrete GPU**, so inference runs on the CPU. Ollama 0.34.0. Smoke test: 2–3 runs per model ([benchmarks/2026-10-09-smoke.md](benchmarks/2026-10-09-smoke.md)).

| | llama3.2:1b | llama3.1:8b Q4_K_M |
|---|---|---|
| Schema-valid JSON (BookSpec-lite via `format`) | 3/3 | 2/2 |
| `done_reason="length"` on truncation | ✓ | ✓ |
| Generation | ~10 tok/s | **~3 tok/s** |
| Prompt eval, cold → cached prefix | ~50 → ~500 tok/s | **~10–12 → ~170 tok/s** |
| Load time | ~8 s | ~20–35 s |
| Chars/token (JSON / prose) | 3.7 / 4.6 | 3.7 / 4.5 |
| Quality notes | Valid but generic (characters named by role, e.g. "Lighthouse Keeper") | Named characters, coherent logline, decent prose |

**What this means for the design**

- **A 2,000-word scene is about 15–20 min with the 8B model** (≈2,700 output tokens at 3 tok/s, plus prompt reading). A 40-scene novel is an overnight-plus job, so checkpoints and resume are essential.
- **Prompt reading is the hidden cost.** Cold, 3,000 prompt tokens take about 4–5 min. Ollama reuses the KV cache for an *identical prefix*, though, and that is ~15× faster. So the context builder puts stable sections **first** (system → output contract → spec → CORE → Codex) and per-scene sections last, which maximizes prefix reuse ([06](06-memory-and-context.md#context-builder)).
- **One model in RAM at a time.** With the 8B loaded, about 0.8 GB of RAM was free. Loading the 8B while the 1B was still resident gave an HTTP 500. The router must (a) unload the other model (`keep_alive: 0`) before switching, (b) turn load failures into a clear "not enough memory" error, and (c) `doctor` should report free RAM.
- **No `/api/tokenize` on Ollama 0.34** (404 confirmed). Token counting uses the Llama-3 tokenizer built **offline from Ollama's own GGUF** (`scripts/build_tokenizer.py` → `~/InkForge/cache/tokenizers/llama3.json`); it matches Ollama's prompt counts exactly (+1 BOS) and serves both models. Fallback: ~3.7 chars/token for JSON, ~4.5 for prose, plus a 10% margin.
- Structured output is reliable at both sizes for small, flat schemas. Keep the per-call-small-schema rule.

Larger models or a GPU tier are out of scope until the hardware changes. The setup wizard (Phase 11) just verifies these two models are present.

## Prompt & skill registry

- `packages/prompts/templates/<stage>/<name>@<version>.md.j2` — Jinja2 with YAML frontmatter (`id, version, profile, output_schema, description`).
- `packages/prompts/skills/<skill>/SKILL.md` — AuthorAgent-style craft skills: structure templates (three-act, save-the-cat, hero's journey, kishōtenketsu, 8-sequence), genre packs (mystery fair-play rules, romance beats, thriller pacing), style guides. Skills are *data injected into prompts*, never code.
- Project-level overrides in `<project>/prompts/` shadow defaults (author can tune without touching the app).
- Every run records `prompt_id@version` and a hash of the rendered prompt.
- Prompt regression: changing a template version triggers golden-fixture runs against the fake + one real model (Phase 2 tooling).

## Operations

- **Health:** startup checks runtime reachable, models present, context length; UI shows red/green.
- **Timeouts:** per profile (default 600 s for prose), with streaming heartbeat.
- **Queue:** single in-flight generation per runtime by default.
- **Cancellation:** abort stream; job → `cancelled`; partial output saved to `runs/<job>/partial.txt` but never becomes a version.
- **Metrics:** tokens/s, latency, retries, schema failure rate — written to `runs/` and summarized by `inkforge stats`.

## Benchmark harness (Phase 0)

`scripts/bench_models.py` runs a fixed mini-book (spec → 3 chapters → 6 scene cards → 2 scene drafts → 1 critique) against each candidate model and records: schema-valid rate, tokens/s, wall time, VRAM peak, and a blind author rating of the two scenes. Output table goes into [`docs/benchmarks/`](benchmarks/)`<date>.md`.
