"""Regression check for the offline-built Llama-3 tokenizer (scripts/build_tokenizer.py).

Counts below were verified against Ollama's prompt_eval_count (minus BOS) on 2026-10-09.
"""

import json
from pathlib import Path

import pytest

tokenizers = pytest.importorskip("tokenizers")

CACHE = Path.home() / "InkForge" / "cache" / "tokenizers"
TOK = CACHE / "llama3.json"

pytestmark = pytest.mark.skipif(not TOK.is_file(), reason="run scripts/build_tokenizer.py first")

CASES = [
    ("The lighthouse keeper found the first bottle at dawn.", 11),
    ("“Don’t open it,” Mira whispered — but Tom already had.", 15),
    ("She'd known. They'll see. We're late. I'm here. You've won. It's over.", 24),
]


@pytest.fixture(scope="module")
def tok():
    return tokenizers.Tokenizer.from_file(str(TOK))


@pytest.mark.parametrize("text,count", CASES)
def test_counts_match_ollama(tok, text, count):
    assert len(tok.encode(text, add_special_tokens=False).ids) == count


def test_round_trip(tok):
    text = "INT. LIGHTHOUSE - NIGHT\n\nÉmilie's café — 東京.\n"
    assert tok.decode(tok.encode(text, add_special_tokens=False).ids) == text


def test_meta_shared_by_both_d16_models():
    meta = json.loads((CACHE / "llama3.meta.json").read_text(encoding="utf-8"))
    assert meta["vocab_size"] == 128256
    assert {meta["source_model"], *meta["shared_with"]} == {"llama3.2:1b", "llama3.1:8b-instruct-q4_K_M"}
