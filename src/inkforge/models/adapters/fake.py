"""Fake adapter for tests and CI (D14): scripted responses, record/replay fixtures, simulated RAM limit.

FakeAdapter([FakeResponse('{"a": 1}'), FakeResponse("prose", finish_reason="length")])
FakeAdapter(lambda req: FakeResponse(...))
FakeAdapter.replay(Path("tests/fixtures/model"))       # recorded real outputs, keyed by request hash
RecordingAdapter(real_adapter, Path("tests/fixtures/model"))
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from inkforge.models.adapters.base import Adapter, Chunk, consume_stream
from inkforge.models.types import (
    CancelToken,
    ChatRequest,
    FinishReason,
    Generation,
    ModelError,
    ModelLoadError,
    RuntimeHealth,
)
from inkforge.storage.atomic import atomic_write_text
from inkforge.storage.hashing import sha256_json


def request_key(req: ChatRequest) -> str:
    """Stable fixture key: everything that affects the output, nothing that doesn't (timeouts, keep_alive)."""
    o = req.options
    return sha256_json(
        {
            "model": req.model,
            "messages": [m.as_dict() for m in req.messages],
            "schema": req.json_schema,
            "options": [
                o.num_ctx,
                o.max_tokens,
                o.temperature,
                o.top_p,
                o.repeat_penalty,
                o.seed,
                list(o.stop),
            ],
        }
    )[:24]


@dataclass
class FakeResponse:
    text: str
    finish_reason: FinishReason = "stop"
    prompt_tokens: int = 0
    output_tokens: int | None = None
    chunk_delay_s: float = 0.0
    error: str | None = None  # raise ModelError instead of answering
    load_error: bool = False


@dataclass
class FakeCall:
    request: ChatRequest
    response: FakeResponse
    generation: Generation | None = None


Script = Iterable[FakeResponse] | Callable[[ChatRequest], FakeResponse]


@dataclass
class FakeAdapter:
    script: Script = field(default_factory=list)
    max_resident: int | None = None  # simulate RAM: loading beyond this many models fails like Ollama's 500
    installed: tuple[str, ...] = ("llama3.1:8b-instruct-q4_K_M", "llama3.2:1b")
    fixtures: dict[str, FakeResponse] | None = None
    name: str = "fake"
    base_url: str = "fake://local"

    def __post_init__(self) -> None:
        self.calls: list[FakeCall] = []
        self.loaded: list[str] = []
        self.unloads: list[str] = []
        self._queue = None if callable(self.script) else list(self.script)

    @classmethod
    def replay(cls, directory: Path, **kw) -> FakeAdapter:
        fixtures = {
            p.stem: FakeResponse(**json.loads(p.read_text(encoding="utf-8"))["response"])
            for p in sorted(directory.glob("*.json"))
        }
        return cls(fixtures=fixtures, **kw)

    def _next(self, req: ChatRequest) -> FakeResponse:
        if self.fixtures is not None:
            key = request_key(req)
            if key not in self.fixtures:
                raise ModelError(f"no recorded fixture for request {key} (model {req.model})")
            return self.fixtures[key]
        if callable(self.script):
            return self.script(req)
        assert self._queue is not None
        if not self._queue:
            raise ModelError("FakeAdapter script exhausted")
        return self._queue.pop(0)

    async def chat(self, req: ChatRequest, cancel: CancelToken | None = None) -> Generation:
        if req.model not in self.installed:
            raise ModelError(f"model {req.model!r} not found")
        resp = self._next(req)
        call = FakeCall(req, resp)
        self.calls.append(call)
        if resp.load_error or (
            self.max_resident is not None
            and req.model not in self.loaded
            and len(self.loaded) >= self.max_resident
        ):
            raise ModelLoadError(f"could not load {req.model!r}: not enough memory (simulated)")
        if resp.error:
            raise ModelError(resp.error)
        if req.model not in self.loaded:
            self.loaded.append(req.model)
        words = resp.text.split(" ")
        pieces = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)] if resp.text else []

        async def stream() -> AsyncIterator[Chunk]:
            for p in pieces:
                if resp.chunk_delay_s:
                    await asyncio.sleep(resp.chunk_delay_s)
                yield Chunk(text=p)
            yield Chunk(
                done=True,
                finish_reason=resp.finish_reason,
                prompt_tokens=resp.prompt_tokens,
                output_tokens=resp.output_tokens if resp.output_tokens is not None else len(pieces),
            )

        async def open_stream() -> AsyncIterator[Chunk]:
            return stream()

        async def close_stream() -> None:
            return None

        call.generation = await consume_stream(req, open_stream, close_stream, cancel)
        return call.generation

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            reachable=True,
            adapter=self.name,
            base_url=self.base_url,
            version="fake",
            available_models=list(self.installed),
            loaded_models=list(self.loaded),
        )

    async def loaded_models(self) -> list[str]:
        return list(self.loaded)

    async def unload(self, model: str) -> None:
        self.unloads.append(model)
        if model in self.loaded:
            self.loaded.remove(model)

    async def tokenize(self, model: str, text: str) -> list[int] | None:
        return None

    async def aclose(self) -> None:
        return None


class RecordingAdapter:
    """Wraps a real adapter and saves each completed generation as a replayable fixture."""

    def __init__(self, inner: Adapter, directory: Path):
        self.inner, self.dir = inner, directory
        self.name, self.base_url = f"recording({inner.name})", inner.base_url

    async def chat(self, req: ChatRequest, cancel: CancelToken | None = None) -> Generation:
        gen = await self.inner.chat(req, cancel)
        if gen.finish_reason != "cancelled":
            resp = FakeResponse(gen.text, gen.finish_reason, gen.prompt_tokens, gen.output_tokens)
            payload = {
                "model": req.model,
                "messages": [m.as_dict() for m in req.messages],
                "response": asdict(resp),
            }
            atomic_write_text(
                self.dir / f"{request_key(req)}.json", json.dumps(payload, indent=2, ensure_ascii=False)
            )
        return gen

    async def health(self) -> RuntimeHealth:
        return await self.inner.health()

    async def loaded_models(self) -> list[str]:
        return await self.inner.loaded_models()

    async def unload(self, model: str) -> None:
        await self.inner.unload(model)

    async def tokenize(self, model: str, text: str) -> list[int] | None:
        return await self.inner.tokenize(model, text)

    async def aclose(self) -> None:
        await self.inner.aclose()


# ---------------------------------------------------------------- auto mode: valid output for any schema

_PATTERN_EXAMPLES = {r"^[+-] *(->|→) *[+-]$": "+ -> -"}


class SchemaExample:
    """Builds a valid instance of a JSON schema: enums take their first value, lists their minimum
    length (at least 1), strings are unique and long enough. Deterministic for a given schema."""

    def __init__(self, schema: dict):
        self.root, self.n = schema, 0

    def build(self) -> object:
        return self._value(self.root, "root")

    def _resolve(self, s: dict) -> dict:
        while "$ref" in s:
            s = self.root["$defs"][s["$ref"].split("/")[-1]]
        if "anyOf" in s:
            s = next(x for x in s["anyOf"] if x.get("type") != "null")
            return self._resolve(s)
        return s

    def _value(self, s: dict, name: str) -> object:
        s = self._resolve(s)
        if "enum" in s:
            return s["enum"][0]
        if "const" in s:
            return s["const"]
        t = s.get("type")
        if t == "object":
            return {k: self._value(v, k) for k, v in s.get("properties", {}).items()}
        if t == "array":
            return [self._value(s["items"], name) for _ in range(max(1, s.get("minItems", 0)))]
        if t == "integer":
            return max(s.get("minimum", 1), 1)
        if t == "number":
            return float(max(s.get("minimum", 1), 1))
        if t == "boolean":
            return False
        if pattern := s.get("pattern"):
            return _PATTERN_EXAMPLES.get(pattern, "x")
        self.n += 1
        text = f"{name.replace('_', ' ')} {self.n}" if name != "name" else f"Person {self.n}"
        while len(text) < s.get("minLength", 0):
            text += " more"
        return text[: s["maxLength"]] if "maxLength" in s else text


def auto_script(req: ChatRequest) -> FakeResponse:
    """FakeAdapter(auto_script): schema-valid JSON for structured calls, short prose otherwise."""
    if req.json_schema is not None:
        return FakeResponse(json.dumps(SchemaExample(req.json_schema).build()))
    return FakeResponse("The keeper climbed the stairs while the storm gathered over the drowned coast.")
