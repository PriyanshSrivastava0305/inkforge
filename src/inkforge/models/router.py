"""ModelRouter: the one way the rest of InkForge talks to a model (docs/08).

- Profiles (planner / prose / critic) come from config; models must be on the allow-list (D16).
- One generation in flight per runtime; other models are unloaded first when RAM allows only one.
- `generate_structured`: schema-constrained decoding → Pydantic validation → allowed-ID check →
  repair attempt with the errors → lower temperature → fail. The last valid artifact is never touched here.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from inkforge.config import ModelConfig, ModelProfile, load_app_config, load_model_config
from inkforge.models.adapters.base import Adapter
from inkforge.models.allowed import find_id_violations
from inkforge.models.recorder import RunRecorder
from inkforge.models.tokens import TokenCounter, TokenizerCache
from inkforge.models.types import (
    CancelToken,
    ChatRequest,
    Generation,
    Message,
    ModelError,
    ModelLoadError,
    ModelNotAllowed,
    RuntimeHealth,
    SamplingOptions,
    Timeouts,
)


class GenerationCancelled(ModelError):
    def __init__(self, generation: Generation):
        super().__init__("generation cancelled")
        self.generation = generation


@dataclass
class Attempt:
    generation: Generation
    errors: list[str]
    temperature: float
    max_tokens: int


@dataclass
class StructuredResult[T: BaseModel]:
    value: T
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def first_try(self) -> bool:
        return len(self.attempts) == 1


class StructuredOutputError(ModelError):
    def __init__(self, schema: str, attempts: list[Attempt]):
        last = attempts[-1].errors if attempts else []
        super().__init__(f"{schema}: no valid output after {len(attempts)} attempts; last errors: {last[:3]}")
        self.attempts = attempts


def make_adapter(config: ModelConfig) -> Adapter:
    rt = config.runtime
    if os.environ.get("INKFORGE_FAKE") == "auto":  # test hook: schema-valid fake output, no model needed
        from inkforge.models.adapters.fake import FakeAdapter, auto_script

        delay = float(os.environ.get("INKFORGE_FAKE_DELAY", "0"))
        return FakeAdapter(lambda req: _with_delay(auto_script(req), delay))
    if rt.adapter == "ollama":
        from inkforge.models.adapters.ollama import OllamaAdapter

        return OllamaAdapter(rt.base_url)
    if rt.adapter in ("llamacpp", "openai-local"):
        from inkforge.models.adapters.openai_local import OpenAILocalAdapter

        return OpenAILocalAdapter(rt.base_url, llamacpp=rt.adapter == "llamacpp")
    from inkforge.models.adapters.fake import FakeAdapter

    return FakeAdapter()


def _with_delay(resp, delay: float):
    resp.chunk_delay_s = delay
    return resp


class ModelRouter:
    def __init__(self, config: ModelConfig, adapter: Adapter, *, tokens: TokenCounter | None = None):
        self.config, self.adapter = config, adapter
        self.tokens = tokens or TokenCounter(None, config.runtime.tokenizer_family)
        self._inflight = asyncio.Lock()

    @classmethod
    def from_config(
        cls, profile_set: str | None = None, *, adapter: Adapter | None = None, cache_root: Path | None = None
    ) -> ModelRouter:
        config = load_model_config(profile_set)
        cache = TokenizerCache(cache_root or load_app_config().cache_root)
        return cls(
            config,
            adapter or make_adapter(config),
            tokens=TokenCounter(cache, config.runtime.tokenizer_family),
        )

    # ------------------------------------------------------------ profiles & sizing
    def profile(self, name: str) -> ModelProfile:
        if name not in self.config.profiles:
            raise KeyError(f"unknown profile {name!r}; have {sorted(self.config.profiles)}")
        p = self.config.profiles[name]
        if not p.enabled:
            raise ModelNotAllowed(f"profile {name!r} is disabled")
        if p.model not in self.config.runtime.allowed_models:
            raise ModelNotAllowed(f"{p.model!r} is not on the allow-list")
        return p

    def context_length(self, profile: str) -> int:
        return self.profile(profile).num_ctx

    def default_max_tokens(self, profile: str) -> int:
        p = self.profile(profile)
        return max(p.min_output_tokens, int(p.num_ctx * p.output_fraction))

    def count_tokens(self, text: str, profile: str) -> int:
        return self.tokens.count(text, self.profile(profile).model).n

    # ------------------------------------------------------------ runtime
    async def health(self) -> RuntimeHealth:
        return await self.adapter.health()

    async def ensure_model(self, model: str) -> list[str]:
        """Unload other resident models when the RAM budget allows only `max_loaded_models`."""
        limit = self.config.runtime.max_loaded_models
        loaded = await self.adapter.loaded_models()
        others = [m for m in loaded if m != model]
        unloaded = []
        if model not in loaded and len(others) >= limit:
            for m in others[: len(others) - limit + 1]:
                await self.adapter.unload(m)
                unloaded.append(m)
        return unloaded

    async def aclose(self) -> None:
        await self.adapter.aclose()

    # ------------------------------------------------------------ generation
    def _request(
        self,
        messages: list[Message],
        p: ModelProfile,
        *,
        max_tokens: int,
        temperature: float,
        stop: Collection[str],
        json_schema: dict[str, Any] | None,
    ) -> ChatRequest:
        return ChatRequest(
            model=p.model,
            messages=tuple(messages),
            options=SamplingOptions(
                num_ctx=p.num_ctx,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=p.top_p,
                repeat_penalty=p.repeat_penalty,
                seed=p.seed,
                stop=tuple(stop),
            ),
            json_schema=json_schema,
            keep_alive=self.config.runtime.keep_alive,
            timeouts=Timeouts(p.first_token_timeout_s, p.idle_timeout_s, p.timeout_s),
        )

    async def generate(
        self,
        messages: list[Message],
        profile: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Collection[str] = (),
        json_schema: dict[str, Any] | None = None,
        cancel: CancelToken | None = None,
    ) -> Generation:
        p = self.profile(profile)
        req = self._request(
            messages,
            p,
            max_tokens=max_tokens or self.default_max_tokens(profile),
            temperature=p.temperature if temperature is None else temperature,
            stop=stop,
            json_schema=json_schema,
        )
        async with self._inflight:
            await self.ensure_model(p.model)
            try:
                return await self.adapter.chat(req, cancel)
            except ModelLoadError:
                # Something else got loaded meanwhile (or RAM is tight): free everything, try once more.
                for m in await self.adapter.loaded_models():
                    await self.adapter.unload(m)
                return await self.adapter.chat(req, cancel)

    async def generate_structured[T: BaseModel](
        self,
        messages: list[Message],
        schema: type[T],
        profile: str,
        *,
        max_attempts: int = 3,
        allowed_ids: Mapping[str, Collection[str]] | None = None,
        validate: Callable[[T], list[str]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cancel: CancelToken | None = None,
        recorder: RunRecorder | None = None,
    ) -> StructuredResult[T]:
        p = self.profile(profile)
        base_temp = p.temperature if temperature is None else temperature
        tokens = max_tokens or self.default_max_tokens(profile)
        json_schema = schema.model_json_schema()
        convo = list(messages)
        attempts: list[Attempt] = []
        for n in range(1, max_attempts + 1):
            temp = base_temp if n < 3 else max(0.1, base_temp / 2) if base_temp > 0.1 else base_temp
            gen = await self.generate(
                convo, profile, max_tokens=tokens, temperature=temp, json_schema=json_schema, cancel=cancel
            )
            if gen.finish_reason == "cancelled":
                if recorder:
                    recorder.attempt(n, gen, ["cancelled"], temperature=temp)
                raise GenerationCancelled(gen)
            value, errors = self._check(gen, schema, allowed_ids, validate)
            attempts.append(Attempt(gen, errors, temp, tokens))
            repair = None if not errors else _repair_message(errors, truncated=gen.finish_reason == "length")
            if recorder:
                recorder.attempt(n, gen, errors, temperature=temp, repair_prompt=repair)
            if value is not None:
                return StructuredResult(value, attempts)
            assert repair is not None
            convo = [*messages, Message("assistant", gen.text), Message("user", repair)]
            if gen.finish_reason == "length":
                tokens = min(int(tokens * 1.5), p.num_ctx // 2)
        raise StructuredOutputError(schema.__name__, attempts)

    @staticmethod
    def _check[T: BaseModel](
        gen: Generation,
        schema: type[T],
        allowed_ids: Mapping[str, Collection[str]] | None,
        validate: Callable[[T], list[str]] | None,
    ) -> tuple[T | None, list[str]]:
        if gen.finish_reason == "length":
            return None, [f"output was cut off after {gen.output_tokens} tokens before the JSON was complete"]
        if gen.finish_reason == "repetition":
            return None, [
                f"output got stuck repeating {gen.error.split('of ', 1)[-1] if gen.error else 'a pattern'}; "
                "write short, normal values for every field"
            ]
        try:
            raw = json.loads(gen.text)
        except json.JSONDecodeError as e:
            return None, [f"not valid JSON: {e.msg} at position {e.pos}"]
        errors = find_id_violations(raw, allowed_ids) if allowed_ids else []
        try:
            value = schema.model_validate(raw)
        except ValidationError as e:
            errors += [f"{'.'.join(map(str, x['loc'])) or '$'}: {x['msg']}" for x in e.errors()[:8]]
            return None, errors
        if validate:
            errors += validate(value)
        return (None, errors) if errors else (value, [])


def _repair_message(errors: list[str], *, truncated: bool) -> str:
    lines = "\n".join(f"- {e}" for e in errors[:10])
    tail = "Be more concise so the whole JSON fits." if truncated else "Fix exactly these problems."
    return f"Your previous output was not accepted:\n{lines}\n{tail} Return only the corrected JSON, nothing else."
