"""Build the Llama-3 tokenizer offline from a local Ollama GGUF, then verify it against Ollama.

No network: vocab + merges are read from the GGUF that Ollama already stores.
Output: <cache>/tokenizers/llama3.json (Hugging Face `tokenizers` format) + llama3.meta.json.

    uv run python scripts/build_tokenizer.py                 # build from llama3.2:1b, verify on 1B
    uv run python scripts/build_tokenizer.py --verify-only   # re-run verification
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from gguf import GGUFReader
from tokenizers import AddedToken, Regex, Tokenizer, decoders, models, pre_tokenizers

OLLAMA = "http://127.0.0.1:11434"
MODELS_DIR = Path.home() / ".ollama" / "models"
CACHE = Path.home() / "InkForge" / "cache" / "tokenizers"

# llama.cpp "llama-bpe" pre-tokenizer pattern (Llama 3 / 3.1 / 3.2)
LLAMA3_PATTERN = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}"
    r"| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)
CONTROL = 3  # gguf token_type for control/special tokens

VERIFY_TEXTS = [
    "The lighthouse keeper found the first bottle at dawn.",
    "“Don’t open it,” Mira whispered — but Tom already had.",
    'JSON: {"pov": "third-limited", "tense": "past", "target_words": 24000}',
    "Chapter 12 — 1,284 words. Numbers: 3.14159, 2026-10-09, 100000.",
    "INT. LIGHTHOUSE - NIGHT\n\nWind howls.\n\nTOM\n(shouting)\nGet down!",
    "  multiple   spaces\tand\ttabs\n\n\nand newlines  ",
    "Émilie's café served naïve crêpes; 東京 and नमस्ते also appear.",
    "She'd known. They'll see. We're late. I'm here. You've won. It's over.",
]


def model_blob(model: str) -> Path:
    name, _, tag = model.partition(":")
    manifest = MODELS_DIR / "manifests" / "registry.ollama.ai" / "library" / name / (tag or "latest")
    layers = json.loads(manifest.read_text(encoding="utf-8"))["layers"]
    digest = next(lyr["digest"] for lyr in layers if lyr["mediaType"].endswith(".model"))
    return MODELS_DIR / "blobs" / digest.replace(":", "-")


def read_vocab(gguf_path: Path) -> tuple[list[str], list[int], list[str], str]:
    reader = GGUFReader(gguf_path)
    f = reader.fields
    pre = f["tokenizer.ggml.pre"].contents()
    if f["tokenizer.ggml.model"].contents() != "gpt2" or pre != "llama-bpe":
        raise SystemExit(f"unsupported tokenizer: model={f['tokenizer.ggml.model'].contents()} pre={pre}")
    return (
        f["tokenizer.ggml.tokens"].contents(),
        f["tokenizer.ggml.token_type"].contents(),
        f["tokenizer.ggml.merges"].contents(),
        pre,
    )


def build(tokens: list[str], types: list[int], merges: list[str]) -> Tokenizer:
    vocab = {tok: i for i, tok in enumerate(tokens)}
    merge_pairs = [tuple(m.split(" ", 1)) for m in merges]
    tok = Tokenizer(models.BPE(vocab=vocab, merges=merge_pairs, ignore_merges=True))  # type: ignore[arg-type]
    tok.pre_tokenizer = pre_tokenizers.Sequence(  # type: ignore[assignment]
        [
            pre_tokenizers.Split(Regex(LLAMA3_PATTERN), behavior="isolated", invert=False),
            pre_tokenizers.ByteLevel(add_prefix_space=False, trim_offsets=True, use_regex=False),
        ]
    )
    tok.decoder = decoders.ByteLevel()  # type: ignore[assignment]
    tok.add_special_tokens(
        [
            AddedToken(t, special=True, normalized=False)
            for t, ty in zip(tokens, types, strict=True)
            if ty == CONTROL
        ]
    )
    return tok


def ollama_count(model: str, text: str) -> int:
    """Prompt token count as Ollama sees it (raw mode, so no chat template)."""
    body = {
        "model": model,
        "prompt": text,
        "raw": True,
        "stream": False,
        "options": {"num_predict": 1, "num_ctx": 2048, "temperature": 0},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["prompt_eval_count"]


def verify(tok: Tokenizer, model: str) -> bool:
    ok = True
    for text in VERIFY_TEXTS:
        ours = len(tok.encode(text, add_special_tokens=False).ids)
        theirs = ollama_count(model, text)
        # Ollama prepends exactly one BOS in raw mode for Llama 3 GGUFs
        match = theirs == ours + 1
        ok &= match
        assert tok.decode(tok.encode(text, add_special_tokens=False).ids) == text, "round-trip failed"
        print(f"  {'OK ' if match else 'BAD'} ours={ours:3d} ollama={theirs:3d}  {text[:50]!r}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-model", default="llama3.2:1b")
    ap.add_argument(
        "--also-compare",
        default="llama3.1:8b-instruct-q4_K_M",
        help="check this model's GGUF has an identical vocab/merges",
    )
    ap.add_argument("--verify-model", default="llama3.2:1b")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    out, meta_path = CACHE / "llama3.json", CACHE / "llama3.meta.json"

    if not args.verify_only:
        t0 = time.time()
        tokens, types, merges, pre = read_vocab(model_blob(args.source_model))
        fingerprint = hashlib.sha256(("\n".join(tokens) + "\0" + "\n".join(merges)).encode()).hexdigest()
        print(
            f"read {len(tokens)} tokens / {len(merges)} merges from {args.source_model} ({time.time() - t0:.0f}s)"
        )
        same = None
        if args.also_compare:
            t2, _, m2, _ = read_vocab(model_blob(args.also_compare))
            same = t2 == tokens and m2 == merges
            print(f"{args.also_compare}: identical vocab/merges = {same}")
        build(tokens, types, merges).save(str(out))
        meta = {
            "family": "llama3",
            "pre": pre,
            "source_model": args.source_model,
            "vocab_size": len(tokens),
            "fingerprint": fingerprint,
            "shared_with": [args.also_compare] if same else [],
            "built": time.strftime("%Y-%m-%d"),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    tok = Tokenizer.from_file(str(out))
    print(f"verifying against Ollama ({args.verify_model}, raw mode):")
    ok = verify(tok, args.verify_model)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
