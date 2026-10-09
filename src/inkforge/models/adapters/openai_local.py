"""OpenAI-compatible adapter for *local* servers: llama.cpp `llama-server`, LM Studio, KoboldCpp, vLLM.

Loopback is enforced by config (RuntimeConfig.base_url). `llamacpp` mode adds POST /tokenize.
Per-request context size can't be set on these servers; it is a server launch flag.
"""

from __future__ import annotations

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
    RuntimeHealth,
    RuntimeUnavailable,
)


def build_body(req: ChatRequest) -> dict[str, Any]:
    o = req.options
    body: dict[str, Any] = {
        "model": req.model,
        "messages": [m.as_dict() for m in req.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": o.max_tokens,
        "temperature": o.temperature,
    }
    if o.top_p is not None:
        body["top_p"] = o.top_p
    if o.seed is not None:
        body["seed"] = o.seed
    if o.repeat_penalty is not None:
        body["repeat_penalty"] = o.repeat_penalty  # llama.cpp extension; ignored elsewhere
    if o.stop:
        body["stop"] = list(o.stop)
    if req.json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": req.json_schema, "strict": True},
        }
    return body


def parse_sse(line: str) -> Chunk | None:
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return Chunk(done=True)
    d = json.loads(data)
    if "error" in d:
        raise ModelError(f"server error: {d['error']}")
    chunk = Chunk()
    if choices := d.get("choices"):
        choice = choices[0]
        chunk.text = (choice.get("delta") or {}).get("content") or ""
        if fr := choice.get("finish_reason"):
            chunk.finish_reason = "length" if fr == "length" else "stop"
    if usage := d.get("usage"):
        chunk.prompt_tokens = usage.get("prompt_tokens")
        chunk.output_tokens = usage.get("completion_tokens")
    if timings := d.get("timings"):  # llama.cpp extension
        chunk.prompt_eval_s = timings.get("prompt_ms", 0) / 1000
        chunk.eval_s = timings.get("predicted_ms", 0) / 1000
    return chunk


class OpenAILocalAdapter:
    def __init__(
        self, base_url: str, *, llamacpp: bool = False, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.llamacpp = llamacpp
        self.name = "llamacpp" if llamacpp else "openai-local"
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=httpx.Timeout(10.0, read=None)
        )

    async def chat(self, req: ChatRequest, cancel: CancelToken | None = None) -> Generation:
        resp: httpx.Response | None = None

        async def open_stream() -> AsyncIterator[Chunk]:
            nonlocal resp
            try:
                request = self._client.build_request("POST", "/v1/chat/completions", json=build_body(req))
                resp = await self._client.send(request, stream=True)
            except httpx.ConnectError as e:
                raise RuntimeUnavailable(f"server not reachable at {self.base_url}: {e}") from e
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                await resp.aclose()
                raise ModelError(f"HTTP {resp.status_code}: {body[:500]}")
            return _chunks(resp)

        async def close_stream() -> None:
            if resp is not None:
                await resp.aclose()

        return await consume_stream(req, open_stream, close_stream, cancel)

    async def health(self) -> RuntimeHealth:
        h = RuntimeHealth(reachable=False, adapter=self.name, base_url=self.base_url)
        try:
            r = await self._client.get("/v1/models")
            r.raise_for_status()
            h.available_models = sorted(m["id"] for m in r.json().get("data", []))
            h.loaded_models = list(h.available_models)
            h.reachable = True
        except (httpx.HTTPError, ValueError) as e:
            h.error = str(e) or type(e).__name__
        return h

    async def loaded_models(self) -> list[str]:
        return (await self.health()).loaded_models

    async def unload(self, model: str) -> None:
        return None  # model lifetime is managed by the server process

    async def tokenize(self, model: str, text: str) -> list[int] | None:
        if not self.llamacpp:
            return None
        r = await self._client.post("/tokenize", json={"content": text, "add_special": False})
        r.raise_for_status()
        return list(r.json()["tokens"])

    async def aclose(self) -> None:
        await self._client.aclose()


async def _chunks(resp: httpx.Response) -> AsyncIterator[Chunk]:
    """Merge the OpenAI-style tail (finish chunk, usage chunk, [DONE]) into one final done chunk."""
    final = Chunk(done=True, finish_reason="stop")
    async for line in resp.aiter_lines():
        chunk = parse_sse(line)
        if chunk is None:
            continue
        if chunk.text:
            yield Chunk(text=chunk.text)
        for f in ("finish_reason", "prompt_tokens", "output_tokens", "prompt_eval_s", "eval_s"):
            if (v := getattr(chunk, f)) is not None:
                setattr(final, f, v)
        if chunk.done:
            break
    yield final
