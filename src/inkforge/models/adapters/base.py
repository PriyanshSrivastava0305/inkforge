"""Adapter contract plus the streaming loop shared by every HTTP adapter.

Adapters always stream internally, even when the caller wants one result: that is what makes
cancellation immediate and lets us tell "still reading a long prompt" from "stalled".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from inkforge.models.types import (
    CancelToken,
    ChatRequest,
    FinishReason,
    Generation,
    GenerationTimeout,
    RuntimeHealth,
)
from inkforge.system import awake_clock


@dataclass
class Chunk:
    """One parsed stream event. `done` carries final stats when the runtime provides them."""

    text: str = ""
    done: bool = False
    finish_reason: FinishReason | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    prompt_eval_s: float | None = None
    eval_s: float | None = None
    load_s: float | None = None


class Adapter(Protocol):
    name: str
    base_url: str

    async def chat(self, req: ChatRequest, cancel: CancelToken | None = None) -> Generation: ...
    async def health(self) -> RuntimeHealth: ...
    async def loaded_models(self) -> list[str]: ...
    async def unload(self, model: str) -> None: ...
    async def tokenize(self, model: str, text: str) -> list[int] | None: ...
    async def aclose(self) -> None: ...


def repeating_tail(text: str) -> str | None:
    """Return the unit if `text` ends with a pattern repeated over and over, else None.

    Small models under constrained decoding sometimes loop until max_tokens. On a CPU that costs
    minutes, so the stream is stopped as soon as the loop is obvious. Two tiers (both seen on
    llama3.2:1b, 2026-10-09):
      - short units (≤24 chars) repeated ≥12 times over ≥120 chars:  "- > - > - > …", endless newlines
      - long units (25–200 chars) repeated ≥4 times over ≥300 chars: a cycle of four names in a list
    """
    for period, reps in _PLAN:
        span = period * reps
        if len(text) < span:
            continue
        unit = text[-period:]
        if text[-span:] == unit * reps:
            return unit
    return None


_PLAN = [(p, max(12, -(-120 // p))) for p in range(1, 25)] + [
    (p, max(4, -(-300 // p))) for p in range(25, 201)
]


_SLICE_S = 1.0  # max real seconds per wait slice; awake-time deadlines are re-checked between slices


async def consume_stream(
    req: ChatRequest,
    open_stream: Callable[[], Awaitable[AsyncIterator[Chunk]]],
    close_stream: Callable[[], Awaitable[None]],
    cancel: CancelToken | None,
) -> Generation:
    """Drive a chunk stream with first-token / idle / total timeouts and cooperative cancellation.

    On cancel or timeout the stream is closed (the runtime stops generating) and whatever text
    arrived so far is returned with finish_reason "cancelled" — callers must never store it as a version.
    """
    t0 = awake_clock()
    parts: list[str] = []
    tail_len = 0
    gen = Generation(text="", finish_reason="stop", model=req.model)
    to = req.timeouts
    stream = await open_stream()
    it = stream.__aiter__()
    try:
        while True:
            # Deadlines are in *awake* seconds. We wait in short slices and re-check, because
            # asyncio's own timeouts count wall time: after a laptop sleep they would fire at wake
            # even though no awake time passed (2026-10-10 incident).
            waited_from = awake_clock()
            budget = to.idle_s if gen.first_token_s is not None else to.first_token_s
            next_chunk = asyncio.ensure_future(it.__anext__())
            cancel_wait = asyncio.ensure_future(cancel.wait()) if cancel else None
            waiters: set[asyncio.Future] = {next_chunk}
            if cancel_wait:
                waiters.add(cancel_wait)
            done: set[asyncio.Future] = set()
            timed_out: str | None = None
            while not done:
                now = awake_clock()
                if now - t0 >= to.total_s:
                    timed_out = f"exceeded total timeout {to.total_s:.0f}s"
                    break
                left = min(budget - (now - waited_from), to.total_s - (now - t0))
                if left <= 0:
                    phase = "first token" if gen.first_token_s is None else "next chunk"
                    timed_out = f"no {phase} within {budget:.0f}s"
                    break
                done, _ = await asyncio.wait(
                    waiters, timeout=min(left, _SLICE_S), return_when=asyncio.FIRST_COMPLETED
                )
            if cancel_wait and not cancel_wait.done():
                cancel_wait.cancel()
            if cancel_wait in done or (cancel and cancel.cancelled):
                next_chunk.cancel()
                gen.finish_reason = "cancelled"
                break
            if timed_out:
                next_chunk.cancel()
                raise GenerationTimeout(f"{req.model}: {timed_out}")
            try:
                chunk = next_chunk.result()
            except StopAsyncIteration:
                break
            if chunk.text:
                if gen.first_token_s is None:
                    gen.first_token_s = awake_clock() - t0
                parts.append(chunk.text)
                tail_len += len(chunk.text)
                if req.loop_guard and tail_len >= 64:
                    tail_len = 0
                    if (pattern := repeating_tail("".join(parts[-400:])[-1200:])) is not None:
                        gen.finish_reason = "repetition"
                        gen.error = f"degenerate repetition of {pattern!r}"
                        break
            for f in ("prompt_tokens", "output_tokens", "prompt_eval_s", "eval_s", "load_s"):
                if (v := getattr(chunk, f)) is not None:
                    setattr(gen, f, v)
            if chunk.done:
                gen.finish_reason = chunk.finish_reason or "stop"
                break
    finally:
        await close_stream()
    gen.text = "".join(parts)
    gen.wall_s = awake_clock() - t0
    if not gen.output_tokens:
        gen.output_tokens = len(parts)  # chunk count ≈ tokens when the runtime gives no stats
    return gen
