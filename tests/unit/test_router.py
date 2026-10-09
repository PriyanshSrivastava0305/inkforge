import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from inkforge.config import load_model_config
from inkforge.models.adapters.fake import FakeAdapter, FakeResponse
from inkforge.models.recorder import RunRecorder
from inkforge.models.router import GenerationCancelled, ModelRouter, StructuredOutputError
from inkforge.models.types import CancelToken, GenerationTimeout, Message, ModelNotAllowed

M8, M1 = "llama3.1:8b-instruct-q4_K_M", "llama3.2:1b"
USER = [Message("user", "go")]


class Card(BaseModel):
    pov: str
    want: str


def router(script, profile_set=None, **cfg_updates) -> tuple[ModelRouter, FakeAdapter]:
    cfg = load_model_config(profile_set)
    for name, fields in cfg_updates.items():
        cfg.profiles[name] = cfg.profiles[name].model_copy(update=fields)
    fa = script if isinstance(script, FakeAdapter) else FakeAdapter(script)
    return ModelRouter(cfg, fa), fa


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- generate


def test_profile_options_reach_the_adapter():
    r, fa = router([FakeResponse("hello")])
    g = run(r.generate(USER, "prose"))
    req = fa.calls[0].request
    assert g.text == "hello" and g.finish_reason == "stop"
    assert req.model == M8 and req.options.num_ctx == 6144 and req.options.temperature == 0.85
    assert req.options.max_tokens == max(1500, int(6144 * 0.3)) and req.keep_alive == "15m"
    assert req.timeouts.first_token_s == 900


def test_fast_profile_set_uses_1b():
    r, fa = router([FakeResponse("x")], "fast")
    run(r.generate(USER, "planner"))
    assert fa.calls[0].request.model == M1


def test_disabled_or_unknown_profile():
    r, _ = router([])
    with pytest.raises(ModelNotAllowed):
        r.profile("embedding")
    with pytest.raises(KeyError):
        r.profile("poet")


def test_switching_models_unloads_the_other_first():
    fa = FakeAdapter([FakeResponse("a"), FakeResponse("b"), FakeResponse("c")], max_resident=1)
    r, _ = router(fa)
    fast, _ = router(fa, "fast")
    run(r.generate(USER, "planner"))
    run(fast.generate(USER, "planner"))  # would fail with "not enough memory" without the unload
    run(r.generate(USER, "planner"))
    assert fa.unloads == [M8, M1] and fa.loaded == [M8]


def test_load_error_frees_memory_and_retries_once():
    fa = FakeAdapter([FakeResponse("", load_error=True), FakeResponse("ok")])
    fa.loaded.append(M1)
    r, _ = router(fa)
    r.config.runtime.max_loaded_models = 2  # policy wouldn't unload; the retry path must
    assert run(r.generate(USER, "planner")).text == "ok"
    assert M1 in fa.unloads


# ---------------------------------------------------------------- structured


def test_structured_first_try():
    r, fa = router([FakeResponse('{"pov": "char-mira", "want": "home"}')])
    res = run(r.generate_structured(USER, Card, "planner"))
    assert res.value == Card(pov="char-mira", want="home") and res.first_try
    assert fa.calls[0].request.json_schema == Card.model_json_schema()


def test_repair_then_backoff_then_success():
    r, fa = router(
        [
            FakeResponse('{"pov": '),
            FakeResponse('{"pov": "char-bob", "want": "x"}'),
            FakeResponse('{"pov": "char-mira", "want": "x"}'),
        ]
    )
    res = run(r.generate_structured(USER, Card, "planner", allowed_ids={"character": ["char-mira"]}))
    assert len(res.attempts) == 3
    assert [a.temperature for a in res.attempts] == [0.4, 0.4, 0.2]
    assert "not valid JSON" in res.attempts[0].errors[0]
    assert 'char-bob" is not a known character id; use one of: char-mira' in res.attempts[1].errors[0]
    repair = fa.calls[1].request.messages
    assert [m.role for m in repair] == ["user", "assistant", "user"] and "not valid JSON" in repair[
        -1
    ].content
    assert fa.calls[2].request.messages[1].content == '{"pov": "char-bob", "want": "x"}'


def test_truncated_json_asks_for_concision_and_more_tokens():
    r, fa = router(
        [FakeResponse('{"pov": "char-mi', finish_reason="length"), FakeResponse('{"pov":"a","want":"b"}')]
    )
    run(r.generate_structured(USER, Card, "planner", max_tokens=400))
    assert fa.calls[1].request.options.max_tokens == 600
    assert (
        "cut off" in fa.calls[1].request.messages[-1].content
        and "concise" in fa.calls[1].request.messages[-1].content
    )


def test_validate_callback_feeds_repair():
    r, _ = router([FakeResponse('{"pov":"a","want":""}'), FakeResponse('{"pov":"a","want":"b"}')])
    res = run(
        r.generate_structured(USER, Card, "planner", validate=lambda c: [] if c.want else ["want is empty"])
    )
    assert res.attempts[0].errors == ["want is empty"] and res.value.want == "b"


def test_gives_up_after_max_attempts_keeping_every_attempt():
    r, _ = router([FakeResponse("nope")] * 3)
    with pytest.raises(StructuredOutputError) as ei:
        run(r.generate_structured(USER, Card, "planner"))
    assert len(ei.value.attempts) == 3


def test_recorder_files(tmp_path: Path):
    r, _ = router([FakeResponse("bad"), FakeResponse('{"pov":"a","want":"b"}')])
    rec = RunRecorder(tmp_path / "runs" / "job-x")
    rec.prompt(USER, prompt_ref="cards.generate@1", profile="planner", model=M8)
    run(r.generate_structured(USER, Card, "planner", recorder=rec))
    rec.finish("succeeded")
    raw = (tmp_path / "runs" / "job-x" / "raw_output.txt").read_text(encoding="utf-8")
    assert "attempt 1" in raw and "attempt 2" in raw and "bad" in raw
    import json

    meta = json.loads((tmp_path / "runs" / "job-x" / "meta.json").read_text(encoding="utf-8"))
    assert meta["prompt_ref"] == "cards.generate@1" and meta["status"] == "succeeded"
    assert len(meta["attempts"]) == 2 and "repair_prompt" in meta["attempts"][0]
    assert (tmp_path / "runs" / "job-x" / "prompt.txt").read_text(encoding="utf-8") == "### user\ngo\n\n"


# ---------------------------------------------------------------- cancellation & timeouts


async def _cancel_after(tok: CancelToken, s: float):
    await asyncio.sleep(s)
    tok.cancel()


def test_cancel_mid_stream_returns_partial():
    async def go():
        r, _ = router([FakeResponse(" ".join(f"w{i}" for i in range(500)), chunk_delay_s=0.005)])
        tok = CancelToken()
        task = asyncio.ensure_future(_cancel_after(tok, 0.1))
        g = await r.generate(USER, "prose", cancel=tok)
        await task
        return g

    g = run(go())
    assert g.finish_reason == "cancelled" and 0 < len(g.text.split()) < 500


def test_cancelled_structured_call_is_not_retried_and_leaves_partial(tmp_path: Path):
    async def go():
        r, fa = router([FakeResponse('{"pov": "' + "x" * 50 + '"}', chunk_delay_s=0.05)] * 3)
        tok = CancelToken()
        rec = RunRecorder(tmp_path / "run")
        task = asyncio.ensure_future(_cancel_after(tok, 0.02))
        with pytest.raises(GenerationCancelled):
            await r.generate_structured(USER, Card, "planner", cancel=tok, recorder=rec)
        await task
        return fa

    fa = run(go())
    assert len(fa.calls) == 1
    assert (tmp_path / "run" / "partial.txt").is_file()


def test_first_token_timeout():
    r, _ = router([FakeResponse("slow start", chunk_delay_s=0.5)], prose={"first_token_timeout_s": 0.1})
    with pytest.raises(GenerationTimeout, match="first token"):
        run(r.generate(USER, "prose"))


def test_idle_timeout_after_first_token():
    def script(req):
        return FakeResponse("a b c", chunk_delay_s=0.0)

    async def go():
        r, fa = router(script, prose={"idle_timeout_s": 0.05})
        orig = fa.chat

        async def stalling_chat(req, cancel=None):
            from inkforge.models.adapters.base import Chunk, consume_stream

            async def stream():
                yield Chunk(text="first ")
                await asyncio.sleep(1)
                yield Chunk(text="never")

            async def open_stream():
                return stream()

            async def close():
                return None

            return await consume_stream(req, open_stream, close, cancel)

        fa.chat = stalling_chat  # type: ignore[method-assign]
        try:
            await r.generate(USER, "prose")
        finally:
            fa.chat = orig  # type: ignore[method-assign]

    with pytest.raises(GenerationTimeout, match="next chunk"):
        run(go())


def test_count_tokens_falls_back_to_estimate_without_cache():
    r, _ = router([])
    assert r.count_tokens("x" * 370, "planner") == 110


# ---------------------------------------------------------------- repetition guard


def test_repetition_guard_stops_runaway_json_and_repairs():
    loop = '{"pov": "a", "want": "+ - > ' + "- > " * 400
    r, fa = router([FakeResponse(loop), FakeResponse('{"pov":"a","want":"b"}')])
    res = run(r.generate_structured(USER, Card, "planner"))
    first = res.attempts[0]
    assert first.generation.finish_reason == "repetition"
    assert len(first.generation.text) < 400  # stopped early, not after 400 repeats
    assert "stuck repeating '- > '" in first.errors[0]
    assert fa.calls[1].request.options.max_tokens == fa.calls[0].request.options.max_tokens  # no token bump


def test_repetition_guard_leaves_normal_prose_alone():
    prose = " ".join(f"The keeper climbed stair {i} and counted the gulls." for i in range(60))
    r, _ = router([FakeResponse(prose)])
    assert run(r.generate(USER, "prose")).finish_reason == "stop"


def test_repetition_guard_catches_long_cycles():
    """llama3.2:1b, 2026-10-09: cycled four names inside `participants` until max_tokens."""
    from inkforge.models.adapters.base import repeating_tail

    cycle = '"Ava Moreno", "Captain James Reed", "Dr. Liam Chen", "Captain James Reed", '
    assert repeating_tail('{"participants": [' + cycle * 5) == cycle
    assert repeating_tail('{"participants": [' + cycle * 3) is None  # 3 repeats: not yet conclusive
    r, _ = router(
        [FakeResponse('{"pov": "a", "want": [' + cycle * 200), FakeResponse('{"pov":"a","want":"b"}')]
    )
    res = run(r.generate_structured(USER, Card, "planner"))
    assert res.attempts[0].generation.finish_reason == "repetition"
    assert len(res.attempts[0].generation.text) < 1200
