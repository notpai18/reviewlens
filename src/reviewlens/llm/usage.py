"""Usage tracking and optional disk cache for LLM calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UsageTracker:
    """Per-request usage tracker (calls and tokens). Not shared across requests."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hits: int = 0
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def snapshot(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hits": self.cache_hits,
        }


def cache_key(*, model: str, system: str, prompt: str, schema_name: str, temperature: float) -> str:
    """Stable hash of everything that determines an LLM response."""
    payload = json.dumps(
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "schema": schema_name,
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DiskCache:
    """Tiny JSON-file cache used during evaluation development.

    Never enable for final reported numbers (spec 11.4).
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
