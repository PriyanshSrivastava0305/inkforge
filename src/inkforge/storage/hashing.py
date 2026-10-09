"""Content hashing for versions and declared inputs (docs/04 § Staleness)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    """Stable serialization: sorted keys, no whitespace, so equal data → equal hash."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))
