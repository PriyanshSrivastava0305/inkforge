"""InkForge Phase-0 smoke test against local Ollama. Stdlib only.

python scripts/smoke_ollama.py basics llama3.2:1b 3     # structured output, prose, truncation signal
python scripts/smoke_ollama.py stream llama3.2:1b       # streaming + time to first token
python scripts/smoke_ollama.py cancel llama3.2:1b       # abort mid-stream, server stays healthy
python scripts/smoke_ollama.py switch                   # 1B -> 8B -> 1B with explicit unload
"""

from __future__ import annotations

import ctypes
import http.client
import json
import sys
import time
import urllib.request

HOST, PORT = "127.0.0.1", 11434
URL = f"http://{HOST}:{PORT}"
M_SMALL, M_DEFAULT = "llama3.2:1b", "llama3.1:8b-instruct-q4_K_M"

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "logline": {"type": "string"},
        "genre": {"type": "string"},
        "tone": {"type": "string"},
        "pov": {"type": "string", "enum": ["first", "third-limited", "omniscient", "second"]},
        "tense": {"type": "string", "enum": ["past", "present"]},
        "themes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "main_characters": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "want": {"type": "string"},
                },
                "required": ["name", "role", "want"],
            },
        },
        "target_words": {"type": "integer"},
    },
    "required": ["logline", "genre", "tone", "pov", "tense", "themes", "main_characters", "target_words"],
}
IDEA = "A lighthouse keeper on a drowned coast finds letters in bottles that predict storms a day before they happen."


# ---------- helpers ----------


def post(path: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    d["_wall"] = time.time() - t0
    return d


def get(path: str) -> dict:
    with urllib.request.urlopen(URL + path, timeout=30) as r:
        return json.loads(r.read())


def chat(model, messages, opts, fmt=None):
    body = {"model": model, "messages": messages, "stream": False, "options": opts, "keep_alive": "10m"}
    if fmt is not None:
        body["format"] = fmt
    return post("/api/chat", body)


def stats(d):
    ev, ed = d.get("eval_count", 0), d.get("eval_duration", 1) / 1e9
    pe, pd = d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 1) / 1e9
    txt = d["message"]["content"]
    return {
        "done_reason": d.get("done_reason"),
        "out_tok": ev,
        "gen_tok_s": round(ev / ed, 2) if ed else None,
        "prompt_tok": pe,
        "prompt_tok_s": round(pe / pd, 1) if pd else None,
        "chars_per_tok": round(len(txt) / ev, 2) if ev else None,
        "load_s": round(d.get("load_duration", 0) / 1e9, 1),
        "wall_s": round(d["_wall"], 1),
    }


def free_ram_gb() -> float:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    s = MEMORYSTATUSEX()
    s.dwLength = ctypes.sizeof(s)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))  # type: ignore[attr-defined]
    return s.ullAvailPhys / 2**30


def loaded() -> list[str]:
    return [m["name"] for m in get("/api/ps").get("models", [])]


def unload(model: str, wait_s: float = 30) -> float:
    """keep_alive=0 unloads; poll /api/ps until it is gone. Returns seconds taken."""
    t0 = time.time()
    post("/api/generate", {"model": model, "keep_alive": 0})
    while model in loaded():
        if time.time() - t0 > wait_s:
            raise RuntimeError(f"{model} still loaded after {wait_s}s")
        time.sleep(0.25)
    return time.time() - t0


def validate_spec(obj):
    errs = [k for k in SPEC_SCHEMA["required"] if k not in obj]
    if obj.get("pov") not in SPEC_SCHEMA["properties"]["pov"]["enum"]:
        errs.append("pov enum")
    if obj.get("tense") not in ("past", "present"):
        errs.append("tense enum")
    if not isinstance(obj.get("target_words"), int):
        errs.append("target_words int")
    mc = obj.get("main_characters", [])
    if not (2 <= len(mc) <= 4) or any(not all(c.get(k) for k in ("name", "role", "want")) for c in mc):
        errs.append("main_characters shape")
    return errs


def stream_chat(model: str, prompt: str, num_predict: int, stop_after_tokens: int | None = None):
    """Stream /api/chat over a raw HTTP connection so we can drop it mid-generation.

    Returns (chunks_seen, ttft_s, final_chunk_or_None, wall_s).
    """
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "keep_alive": "10m",
            "messages": [{"role": "user", "content": prompt}],
            "options": {"num_ctx": 2048, "temperature": 0.8, "num_predict": num_predict},
        }
    )
    conn = http.client.HTTPConnection(HOST, PORT, timeout=900)
    t0 = time.time()
    conn.request("POST", "/api/chat", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read()[:300]!r}")
    chunks, ttft, final = 0, None, None
    for line in resp:
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("message", {}).get("content"):
            chunks += 1
            if ttft is None:
                ttft = time.time() - t0
        if msg.get("done"):
            final = msg
            break
        if stop_after_tokens is not None and chunks >= stop_after_tokens:
            conn.close()  # cooperative cancel: drop the stream
            break
    conn.close()
    return chunks, ttft, final, time.time() - t0


# ---------- checks ----------


def check_basics(model: str, runs: int) -> bool:
    print(f"\n===== basics: {model} =====", flush=True)
    ok = True
    for i in range(runs):
        d = chat(
            model,
            [
                {
                    "role": "system",
                    "content": "You are a story planner. Reply only with JSON matching the schema.",
                },
                {"role": "user", "content": f"Create a book spec for a novella from this idea: {IDEA}"},
            ],
            {"num_ctx": 4096, "temperature": 0.4, "num_predict": 600},
            SPEC_SCHEMA,
        )
        try:
            obj = json.loads(d["message"]["content"])
            errs = validate_spec(obj)
        except json.JSONDecodeError as e:
            obj, errs = None, [f"json: {e}"]
        ok &= not errs
        print(f"[structured #{i + 1}] valid={not errs} errs={errs} {stats(d)}", flush=True)
        if obj and i == 0:
            print("   logline:", obj.get("logline"))
            print("   chars:", [(c.get("name"), c.get("role")) for c in obj.get("main_characters", [])])

    d = chat(
        model,
        [
            {
                "role": "system",
                "content": "You are a novelist. Write only the scene prose, no headings or commentary.",
            },
            {
                "role": "user",
                "content": f"Premise: {IDEA}\nWrite a ~200-word opening scene, third-person limited, past "
                "tense, in which the keeper finds the first bottle. End on the line of the letter.",
            },
        ],
        {"num_ctx": 4096, "temperature": 0.85, "top_p": 0.92, "repeat_penalty": 1.08, "num_predict": 450},
    )
    txt = d["message"]["content"]
    print(f"[prose] words={len(txt.split())} {stats(d)}", flush=True)
    print("   " + txt[:500].replace("\n", " ") + ("…" if len(txt) > 500 else ""))

    d = chat(
        model,
        [{"role": "user", "content": "Describe a storm hitting a lighthouse in 300 words."}],
        {"num_ctx": 2048, "temperature": 0.8, "num_predict": 40},
    )
    trunc_ok = d.get("done_reason") == "length"
    ok &= trunc_ok
    print(f"[truncation] done_reason={d.get('done_reason')!r} ok={trunc_ok}")
    return ok


def check_stream(model: str) -> bool:
    print(f"\n===== stream: {model} =====", flush=True)
    chunks, ttft, final, wall = stream_chat(model, "Write two sentences about a lighthouse.", 80)
    ok = final is not None and chunks > 0 and final.get("done_reason") in ("stop", "length")
    print(
        f"[stream] chunks={chunks} ttft={ttft:.2f}s wall={wall:.1f}s "
        f"done_reason={final and final.get('done_reason')!r} ok={ok}"
    )
    return ok


def check_cancel(model: str) -> bool:
    print(f"\n===== cancel: {model} =====", flush=True)
    chunks, _, final, wall = stream_chat(
        model, "Write a 600-word story about a storm.", 900, stop_after_tokens=15
    )
    print(
        f"[cancel] dropped stream after {chunks} chunks at {wall:.1f}s (final chunk seen: {final is not None})"
    )
    # If Ollama kept generating the abandoned request, this short one would queue behind ~900 tokens.
    t0 = time.time()
    _, ttft, final2, wall2 = stream_chat(model, "Say OK.", 5)
    healthy = final2 is not None and model in loaded()
    fast = (ttft or 99) < 15
    print(
        f"[after cancel] next request ttft={ttft:.2f}s wall={wall2:.1f}s (since cancel {time.time() - t0:.1f}s) "
        f"healthy={healthy} not_queued={fast}"
    )
    return healthy and fast and final is None


def check_switch() -> bool:
    print("\n===== switch: 1B -> 8B -> 1B (explicit unload) =====", flush=True)
    ok = True
    for current, nxt in ((None, M_SMALL), (M_SMALL, M_DEFAULT), (M_DEFAULT, M_SMALL)):
        for m in loaded():
            if m != nxt:
                print(f"  unload {m}: {unload(m):.1f}s  free RAM now {free_ram_gb():.1f} GB")
        t0 = time.time()
        try:
            d = post(
                "/api/generate",
                {
                    "model": nxt,
                    "prompt": "Say OK.",
                    "stream": False,
                    "options": {"num_predict": 3, "num_ctx": 2048},
                },
            )
            print(
                f"  load+gen {nxt}: {time.time() - t0:.1f}s (load {d.get('load_duration', 0) / 1e9:.1f}s) "
                f"loaded={loaded()} free RAM {free_ram_gb():.1f} GB"
            )
        except Exception as e:
            ok = False
            print(f"  FAILED to load {nxt} after {current}: {e}")
        ok &= loaded() == [nxt]
    return ok


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    print(f"free RAM at start: {free_ram_gb():.1f} GB, loaded: {loaded()}")
    if cmd == "basics":
        ok = check_basics(argv[1], int(argv[2]) if len(argv) > 2 else 2)
    elif cmd == "stream":
        ok = check_stream(argv[1])
    elif cmd == "cancel":
        ok = check_cancel(argv[1])
    elif cmd == "switch":
        ok = check_switch()
    else:
        print(__doc__)
        return 2
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
