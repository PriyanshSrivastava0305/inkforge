import asyncio
import json
import shutil
from pathlib import Path

import pytest

from inkforge.models.adapters.fake import FakeAdapter, FakeResponse, RecordingAdapter, request_key
from inkforge.models.allowed import find_id_violations
from inkforge.models.tokens import TokenCounter, TokenizerCache, estimate
from inkforge.models.types import ChatRequest, Message, ModelError, SamplingOptions


def req(text: str, temp: float = 0.4) -> ChatRequest:
    return ChatRequest("llama3.2:1b", (Message("user", text),), SamplingOptions(4096, 64, temp))


def test_record_then_replay(tmp_path: Path):
    inner = FakeAdapter([FakeResponse("recorded answer", prompt_tokens=5)])
    rec = RecordingAdapter(inner, tmp_path)
    asyncio.run(rec.chat(req("hello")))
    assert len(list(tmp_path.glob("*.json"))) == 1
    replay = FakeAdapter.replay(tmp_path)
    g = asyncio.run(replay.chat(req("hello")))
    assert g.text == "recorded answer" and g.prompt_tokens == 5
    with pytest.raises(ModelError, match="no recorded fixture"):
        asyncio.run(replay.chat(req("hello", temp=0.9)))  # different sampling → different key


def test_request_key_ignores_timeouts_but_not_content():
    import dataclasses

    from inkforge.models.types import Timeouts

    a = req("x")
    assert request_key(a) == request_key(dataclasses.replace(a, timeouts=Timeouts(1, 1, 1), keep_alive="0"))
    assert request_key(a) != request_key(req("y"))


def test_script_exhausted_and_unknown_model():
    fa = FakeAdapter([])
    with pytest.raises(ModelError, match="exhausted"):
        asyncio.run(fa.chat(req("x")))
    with pytest.raises(ModelError, match="not found"):
        asyncio.run(
            FakeAdapter([FakeResponse("x")]).chat(ChatRequest("qwen:14b", (), SamplingOptions(1, 1, 0)))
        )


# ---------------------------------------------------------------- tokens


def test_estimate_errs_high():
    assert estimate("") == 0
    assert estimate("a" * 37) == 11  # 37/3.7 = 10, +10%


REAL_CACHE = Path.home() / "InkForge" / "cache"


@pytest.mark.skipif(
    not (REAL_CACHE / "tokenizers" / "llama3.json").is_file(), reason="tokenizer cache not built"
)
def test_counter_uses_cached_tokenizer(tmp_path: Path):
    (tmp_path / "tokenizers").mkdir()
    for f in ("llama3.json", "llama3.meta.json"):
        shutil.copy(REAL_CACHE / "tokenizers" / f, tmp_path / "tokenizers" / f)
    c = TokenCounter(TokenizerCache(tmp_path), {"llama3.2:1b": "llama3"})
    got = c.count("The lighthouse keeper found the first bottle at dawn.", "llama3.2:1b")
    assert (got.n, got.method) == (11, "tokenizer")
    assert c.count("abc", "unknown-model").method == "estimate"
    msgs = c.count_messages(
        [{"role": "user", "content": "The lighthouse keeper found the first bottle at dawn."}], "llama3.2:1b"
    )
    assert msgs.n == 11 + 4 + 1


def test_counter_without_cache_estimates(tmp_path: Path):
    c = TokenCounter(TokenizerCache(tmp_path), {"llama3.2:1b": "llama3"})
    assert c.count("a" * 37, "llama3.2:1b").method == "estimate"


# ---------------------------------------------------------------- allowed ids


def test_find_id_violations_walks_nested_values():
    out = {
        "cards": [
            {
                "pov": "char-mira",
                "participants": ["char-mira", "char-bob"],
                "location": "loc-x",
                "note": "char-ish words",
            }
        ]
    }
    errs = find_id_violations(out, {"character": ["char-mira", "char-tom"]})
    assert errs == [
        '$.cards[0].participants[1]: "char-bob" is not a known character id; use one of: char-mira, char-tom'
    ]
    assert find_id_violations(out, {"location": []})[0].startswith('$.cards[0].location: "loc-x"')
    assert find_id_violations(json.loads('{"a": 1}'), {"character": []}) == []
