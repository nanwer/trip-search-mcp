"""In-memory TTL cache keyed on canonical input hash.

Phase 1 only — no persistence, no eviction beyond TTL. Quota protection for
2,000 calls/month, not a production cache.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


def canonical_key(params: dict[str, Any]) -> str:
    """Stable hash of input parameters.

    IATA fields (origin, destination) are lowercased so case variations collapse.
    All other keys are stringified as-is and sorted.
    """
    normalized: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and k in {"origin", "destination", "currency"}:
            normalized[k] = v.lower()
        else:
            normalized[k] = v
    payload = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class TTLCache:
    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)
