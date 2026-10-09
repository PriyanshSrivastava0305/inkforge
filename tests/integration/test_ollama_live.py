"""Live checks against local Ollama, on the fast model only (llama3.2:1b).

Run with `uv run pytest -m ollama`. Skipped automatically if Ollama isn't reachable.
"""

import json

import httpx
import pytest

from inkforge.config import load_model_config

pytestmark = pytest.mark.ollama

CFG = load_model_config("fast")
BASE = CFG.runtime.base_url
MODEL = CFG.profiles["planner"].model


@pytest.fixture(scope="module")
def client():
    c = httpx.Client(base_url=BASE, timeout=600)
    try:
        tags = c.get("/api/tags").json()
    except httpx.HTTPError:
        pytest.skip("Ollama not reachable on loopback")
    if MODEL not in {m["name"] for m in tags.get("models", [])}:
        pytest.skip(f"{MODEL} not installed")
    yield c
    c.close()


def _chat(client, **kw):
    body = {"model": MODEL, "stream": False, "keep_alive": "5m", **kw}
    r = client.post("/api/chat", json=body)
    r.raise_for_status()
    return r.json()


def test_schema_constrained_output(client):
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
    }
    d = _chat(
        client,
        format=schema,
        options={"num_ctx": 2048, "temperature": 0, "num_predict": 60, "seed": 1},
        messages=[{"role": "user", "content": "Invent a lighthouse keeper. Give name and age as JSON."}],
    )
    obj = json.loads(d["message"]["content"])
    assert isinstance(obj["name"], str) and obj["name"]
    assert isinstance(obj["age"], int)


def test_truncation_reports_length(client):
    d = _chat(
        client,
        options={"num_ctx": 2048, "num_predict": 10},
        messages=[{"role": "user", "content": "Write 200 words about the sea."}],
    )
    assert d["done_reason"] == "length"


def test_natural_stop_reports_stop(client):
    d = _chat(
        client,
        options={"num_ctx": 2048, "num_predict": 50, "temperature": 0},
        messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
    )
    assert d["done_reason"] == "stop"
