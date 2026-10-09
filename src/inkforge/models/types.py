"""Runtime-neutral request/response types for model calls (docs/08 § Router interface)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length", "cancelled", "error", "repetition"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class SamplingOptions:
    num_ctx: int
    max_tokens: int
    temperature: float
    top_p: float | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True)
class Timeouts:
    first_token_s: float = 900
    idle_s: float = 180
    total_s: float = 1800


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[Message, ...]
    options: SamplingOptions
    json_schema: dict[str, Any] | None = None
    keep_alive: str | None = None
    timeouts: Timeouts = field(default_factory=Timeouts)
    loop_guard: bool = True  # stop the stream on degenerate repetition (adapters/base.repeating_tail)


@dataclass
class Generation:
    text: str
    finish_reason: FinishReason
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    prompt_eval_s: float = 0.0
    eval_s: float = 0.0
    load_s: float = 0.0
    wall_s: float = 0.0
    first_token_s: float | None = None
    error: str | None = None

    @property
    def tokens_per_s(self) -> float:
        return self.output_tokens / self.eval_s if self.eval_s else 0.0

    @property
    def prompt_tokens_per_s(self) -> float:
        return self.prompt_tokens / self.prompt_eval_s if self.prompt_eval_s else 0.0


@dataclass
class RuntimeHealth:
    reachable: bool
    adapter: str
    base_url: str
    version: str | None = None
    available_models: list[str] = field(default_factory=list)
    loaded_models: list[str] = field(default_factory=list)
    error: str | None = None


class CancelToken:
    """Cooperative cancellation shared between the caller and an in-flight generation."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


# ---------------------------------------------------------------- errors


class ModelError(RuntimeError):
    """Base for runtime failures surfaced to the conductor."""


class RuntimeUnavailable(ModelError):
    pass


class ModelNotAllowed(ModelError):
    pass


class ModelLoadError(ModelError):
    """Usually RAM: on 16 GB the 8B leaves ~1 GB free (docs/08 § Your hardware)."""


class GenerationTimeout(ModelError):
    pass
