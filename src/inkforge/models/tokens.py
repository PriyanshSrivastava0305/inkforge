"""Token counting chain (docs/06 § Budgeting): runtime /tokenize → cached tokenizer → calibrated estimate."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Method = Literal["runtime", "tokenizer", "estimate"]

# Measured 2026-10-09 on the Llama-3 tokenizer (docs/benchmarks/2026-10-09-smoke.md).
# The lower (JSON) ratio is used for everything so the estimate errs towards "too many tokens".
CHARS_PER_TOKEN = 3.7
ESTIMATE_MARGIN = 1.10


@dataclass(frozen=True)
class TokenCount:
    n: int
    method: Method


def estimate(text: str) -> int:
    # round() first: 370 / 3.7 * 1.1 is 110.00000000000001 in floating point
    return math.ceil(round(len(text) / CHARS_PER_TOKEN * ESTIMATE_MARGIN, 6)) if text else 0


class TokenizerCache:
    """Loads `<cache_root>/tokenizers/<family>.json` built by scripts/build_tokenizer.py (offline)."""

    def __init__(self, cache_root: Path):
        self.dir = Path(cache_root) / "tokenizers"
        self._loaded: dict[str, Any] = {}

    def get(self, family: str) -> Any | None:
        if family in self._loaded:
            return self._loaded[family]
        tok = None
        path = self.dir / f"{family}.json"
        if path.is_file():
            try:
                from tokenizers import Tokenizer  # optional extra: `uv sync --extra tokenizer`

                tok = Tokenizer.from_file(str(path))
            except ImportError:
                tok = None
        self._loaded[family] = tok
        return tok

    def meta(self, family: str) -> dict[str, Any] | None:
        p = self.dir / f"{family}.meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


class TokenCounter:
    def __init__(self, cache: TokenizerCache | None, families: dict[str, str]):
        self.cache, self.families = cache, families

    def count(self, text: str, model: str) -> TokenCount:
        """Synchronous count: cached tokenizer if available, else the calibrated estimate."""
        family = self.families.get(model)
        tok = self.cache.get(family) if (self.cache and family) else None
        if tok is not None:
            return TokenCount(len(tok.encode(text, add_special_tokens=False).ids), "tokenizer")
        return TokenCount(estimate(text), "estimate")

    def count_messages(self, messages: list[dict[str, str]] | list[Any], model: str) -> TokenCount:
        """Messages plus Llama-3 chat-template overhead (~4 tokens per message + BOS)."""
        counts = [self.count(m["content"] if isinstance(m, dict) else m.content, model) for m in messages]
        method: Method = "estimate" if any(c.method == "estimate" for c in counts) else "tokenizer"
        return TokenCount(sum(c.n for c in counts) + 4 * len(counts) + 1, method)
