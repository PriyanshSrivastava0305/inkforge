"""Ollama adapter: POST /api/chat (NDJSON stream), JSON-schema `format`, explicit unload via keep_alive=0."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from inkforge.models.adapters.base import Chunk, consume_stream
from inkforge.models.types import (
    CancelToken,
    ChatRequest,
    Generation,
    ModelError,
    ModelLoadError,
    RuntimeHealth,
    RuntimeUnavailable,
)

_NS = 1e9
_LOAD_HINTS = ("memory", "load", "llama runner", "out of", "allocate", "terminated")


def _options(req: ChatRequest) -> dict[str, Any]:
    o = req.options
    opts: dict[str, Any] = {"num_ctx": o.num_ctx, "num_predict": o.max_tokens, "temperature": o.temperature}
    for key, val in (("top_p", o.top_p), ("repeat_penalty", o.repeat_penalty), ("seed", o.seed)):
        if val is not None:
            opts[key] = val
    if o.stop:
        opts["stop"] = list(o.stop)
    return opts


def build_body(req: ChatRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": req.model,
        "messages": [m.as_dict() for m in req.messages],
        "stream": True,
        "options": _options(req),
    }
    if req.json_schema is not None:
        body["format"] = req.json_schema
    if req.keep_alive is not None:
        body["keep_alive"] = req.keep_alive
    return body


def parse_line(line: str) -> Chunk | None:
    if not line.strip():
        return None
    d = json.loads(line)
    if "error" in d:
        raise ModelError(f"ollama: {d['error']}")
    chunk = Chunk(text=d.get("message", {}).get("content", ""))
    if d.get("done"):
        chunk.done = True
        chunk.finish_reason = "length" if d.get("done_reason") == "length" else "stop"
        chunk.prompt_tokens = d.get("prompt_eval_count")
        chunk.output_tokens = d.get("eval_count")
        for src, dst in (
            ("prompt_eval_duration", "prompt_eval_s"),
            ("eval_duration", "eval_s"),
            ("load_duration", "load_s"),
        ):
            if src in d:
                setattr(chunk, dst, d[src] / _NS)
    return chunk


class OllamaAdapter:
    name = "ollama"

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        # No read timeout here: consume_stream enforces first-token / idle / total budgets.
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=httpx.Timeout(10.0, read=None)
        )

    async def chat(self, req: ChatRequest, cancel: CancelToken | None = None) -> Generation:
        resp: httpx.Response | None = None

        async def open_stream() -> AsyncIterator[Chunk]:
            nonlocal resp
            try:
                request = self._client.build_request("POST", "/api/chat", json=build_body(req))
                resp = await self._client.send(request, stream=True)
            except httpx.ConnectError as e:
                raise RuntimeUnavailable(f"Ollama not reachable at {self.base_url}: {e}") from e
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                await resp.aclose()
                msg = _error_text(body)
                if resp.status_code == 404:
                    raise ModelError(f"model {req.model!r} not found in Ollama: {msg}")
                if resp.status_code >= 500 and any(h in msg.lower() for h in _LOAD_HINTS):
                    raise ModelLoadError(
                        f"could not load {req.model!r} (likely RAM — unload other models): {msg}"
                    )
                raise ModelError(f"ollama HTTP {resp.status_code}: {msg}")
            return _chunks(resp)

        async def close_stream() -> None:
            if resp is not None:
                await resp.aclose()

        return await consume_stream(req, open_stream, close_stream, cancel)

    async def health(self) -> RuntimeHealth:
        h = RuntimeHealth(reachable=False, adapter=self.name, base_url=self.base_url)
        try:
            h.version = (await self._client.get("/api/version")).json().get("version")
            h.available_models = sorted(
                m["name"] for m in (await self._client.get("/api/tags")).json().get("models", [])
            )
            h.loaded_models = await self.loaded_models()
            h.reachable = True
        except (httpx.HTTPError, ValueError) as e:
            h.error = str(e) or type(e).__name__
        return h

    async def loaded_models(self) -> list[str]:
        r = await self._client.get("/api/ps")
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))

    async def unload(self, model: str, wait_s: float = 30.0) -> None:
        r = await self._client.post("/api/generate", json={"model": model, "keep_alive": 0})
        r.raise_for_status()
        deadline = asyncio.get_running_loop().time() + wait_s
        while model in await self.loaded_models():
            if asyncio.get_running_loop().time() > deadline:
                raise ModelError(f"{model} still loaded {wait_s:.0f}s after unload request")
            await asyncio.sleep(0.25)

    async def tokenize(self, model: str, text: str) -> list[int] | None:
        return None  # Ollama 0.34 has no tokenize endpoint (404, docs/08)

    async def aclose(self) -> None:
        await self._client.aclose()


async def _chunks(resp: httpx.Response) -> AsyncIterator[Chunk]:
    async for line in resp.aiter_lines():
        chunk = parse_line(line)
        if chunk is not None:
            yield chunk


def _error_text(body: str) -> str:
    try:
        return str(json.loads(body).get("error", body))
    except ValueError:
        return body[:500]
