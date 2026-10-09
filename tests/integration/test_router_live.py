"""Router against real local Ollama on the fast model (`uv run pytest -m ollama`)."""

import asyncio

import httpx
import pytest
from pydantic import BaseModel, Field

from inkforge.models.router import GenerationCancelled, ModelRouter
from inkforge.models.types import CancelToken, Message

pytestmark = pytest.mark.ollama


@pytest.fixture(scope="module")
def ollama_up():
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=3)
    except httpx.HTTPError:
        pytest.skip("Ollama not reachable")


class Keeper(BaseModel):
    name: str = Field(min_length=2)
    age: int = Field(ge=18, le=99)
    fears: list[str] = Field(min_length=1, max_length=3)


def test_structured_on_real_model(ollama_up, tmp_path):
    async def go():
        r = ModelRouter.from_config("fast")
        try:
            res = await r.generate_structured(
                [Message("user", "Invent a lighthouse keeper: name, age, and 1-3 fears.")],
                Keeper,
                "planner",
                max_tokens=200,
            )
            assert r.count_tokens(res.value.name, "planner") >= 1
            return res
        finally:
            await r.aclose()

    res = asyncio.run(go())
    assert isinstance(res.value, Keeper) and len(res.attempts) <= 3


def test_cancel_real_stream_then_next_call_is_not_queued(ollama_up):
    async def go():
        r = ModelRouter.from_config("fast")
        try:
            tok = CancelToken()

            async def cancel_soon():
                await asyncio.sleep(2.0)
                tok.cancel()

            t = asyncio.ensure_future(cancel_soon())
            g = await r.generate(
                [Message("user", "Write 500 words about the sea.")], "prose", max_tokens=800, cancel=tok
            )
            await t
            nxt = await r.generate([Message("user", "Say OK.")], "planner", max_tokens=5)
            return g, nxt
        finally:
            await r.aclose()

    g, nxt = asyncio.run(go())
    assert g.finish_reason == "cancelled"
    assert nxt.first_token_s is not None and nxt.first_token_s < 15


def test_cancelled_structured_raises(ollama_up):
    async def go():
        r = ModelRouter.from_config("fast")
        tok = CancelToken()
        tok.cancel()
        try:
            await r.generate_structured([Message("user", "x")], Keeper, "planner", cancel=tok)
        finally:
            await r.aclose()

    with pytest.raises(GenerationCancelled):
        asyncio.run(go())
