"""Small keyed JSONL tables (facts, review queue, findings). Rewritten atomically on change."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from inkforge.storage.atomic import atomic_write_text


class JsonlTable[T: BaseModel]:
    def __init__(self, path: Path, model: type[T], key: str = "id"):
        self.path, self.model, self.key = path, model, key

    def all(self) -> list[T]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [self.model.model_validate_json(line) for line in f if line.strip()]

    def __iter__(self) -> Iterator[T]:
        return iter(self.all())

    def get(self, key: str) -> T | None:
        return next((r for r in self.all() if getattr(r, self.key) == key), None)

    def keys(self) -> list[str]:
        return [getattr(r, self.key) for r in self.all()]

    def upsert(self, record: T) -> bool:
        """Insert or replace by key. Returns True if it replaced an existing record."""
        rows = self.all()
        k = getattr(record, self.key)
        replaced = False
        for i, r in enumerate(rows):
            if getattr(r, self.key) == k:
                rows[i], replaced = record, True
                break
        else:
            rows.append(record)
        self._save(rows)
        return replaced

    def remove(self, key: str) -> bool:
        rows = self.all()
        kept = [r for r in rows if getattr(r, self.key) != key]
        if len(kept) != len(rows):
            self._save(kept)
            return True
        return False

    def _save(self, rows: list[T]) -> None:
        atomic_write_text(self.path, "".join(r.model_dump_json() + "\n" for r in rows))
