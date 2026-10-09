"""HTTP adapters against httpx.MockTransport — wire formats only, no real server."""

import asyncio
import json

import httpx
import pytest

from inkforge.models.adapters.ollama import OllamaAdapter
from inkforge.models.adapters.openai_local import OpenAILocalAdapter
from inkforge.models.types import (
    ChatRequest,
    Message,
    ModelError,
    ModelLoadError,
    RuntimeUnavailable,
    SamplingOptions,
)

REQ = ChatRequest(
    model="llama3.2:1b",
    messages=(Message("system", "s"), Message("user", "u")),
    options=SamplingOptions(num_ctx=4096, max_tokens=64, temperature=0.4, seed=7, stop=("END",)),
    json_schema={"type": "object"},
    keep_alive="5m",
)


def run(coro):
    return asyncio.run(coro)


def ndjson(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def test_ollama_stream_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "length",
                    "prompt_eval_count": 12,
                    "eval_count": 2,
                    "prompt_eval_duration": 2e9,
                    "eval_duration": 1e9,
                    "load_duration": 5e8,
                },
            ),
        )

    a = OllamaAdapter("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    g = run(a.chat(REQ))
    assert (g.text, g.finish_reason, g.prompt_tokens, g.output_tokens) == ("Hello", "length", 12, 2)
    assert (g.prompt_eval_s, g.eval_s, g.load_s) == (2.0, 1.0, 0.5) and g.tokens_per_s == 2.0
    b = seen["body"]
    assert b["stream"] is True and b["format"] == {"type": "object"} and b["keep_alive"] == "5m"
    assert b["options"] == {
        "num_ctx": 4096,
        "num_predict": 64,
        "temperature": 0.4,
        "seed": 7,
        "stop": ["END"],
    }
    assert b["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


@pytest.mark.parametrize(
    "status,body,exc",
    [
        (404, {"error": "model 'x' not found"}, ModelError),
        (500, {"error": "llama runner process has terminated: out of memory"}, ModelLoadError),
        (500, {"error": "something else"}, ModelError),
    ],
)
def test_ollama_errors(status, body, exc):
    a = OllamaAdapter(
        "http://127.0.0.1:11434", transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    )
    with pytest.raises(exc):
        run(a.chat(REQ))


def test_ollama_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused")

    a = OllamaAdapter("http://127.0.0.1:1", transport=httpx.MockTransport(refuse))
    with pytest.raises(RuntimeUnavailable):
        run(a.chat(REQ))
    h = run(a.health())
    assert not h.reachable and h.error


def test_ollama_health_and_unload_polls_ps():
    ps_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "llama3.2:1b"}, {"name": "llama3.1:8b-instruct-q4_K_M"}]}
            )
        if request.url.path == "/api/ps":
            ps_calls["n"] += 1
            loaded = [{"name": "llama3.2:1b"}] if ps_calls["n"] < 3 else []
            return httpx.Response(200, json={"models": loaded})
        if request.url.path == "/api/generate":
            assert json.loads(request.content) == {"model": "llama3.2:1b", "keep_alive": 0}
            return httpx.Response(200, json={})
        return httpx.Response(404)

    a = OllamaAdapter("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    h = run(a.health())
    assert h.reachable and h.version == "0.34.0" and h.loaded_models == ["llama3.2:1b"]
    run(a.unload("llama3.2:1b"))
    assert ps_calls["n"] >= 3


def sse(*objs) -> bytes:
    return b"".join(
        b"data: " + (o if isinstance(o, bytes) else json.dumps(o).encode()) + b"\n\n" for o in objs
    )


def test_openai_local_stream_usage_and_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=sse(
                {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": " there"}, "finish_reason": "length"}]},
                {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
                b"[DONE]",
            ),
        )

    a = OpenAILocalAdapter("http://127.0.0.1:8080", llamacpp=True, transport=httpx.MockTransport(handler))
    g = run(a.chat(REQ))
    assert (g.text, g.finish_reason, g.prompt_tokens, g.output_tokens) == ("Hi there", "length", 9, 2)
    b = seen["body"]
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"][
        "schema"
    ] == {"type": "object"}
    assert b["max_tokens"] == 64 and b["stop"] == ["END"] and b["stream_options"] == {"include_usage": True}


def test_llamacpp_tokenize_and_plain_openai_has_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        return httpx.Response(200, json={"tokens": [1, 2, 3]})

    t = httpx.MockTransport(handler)
    assert run(
        OpenAILocalAdapter("http://127.0.0.1:8080", llamacpp=True, transport=t).tokenize("m", "abc")
    ) == [1, 2, 3]
    assert run(OpenAILocalAdapter("http://127.0.0.1:1234", transport=t).tokenize("m", "abc")) is None
