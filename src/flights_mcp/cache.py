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

    String fields whose case should collapse (origin, destination, currency,
    cabin_class) are lowercased; everything else is passed through. `default=str`
    keeps the hash from crashing on values like Decimal or datetime, but callers
    should pass validated input — non-JSON-native types may produce surprising
    collisions (e.g., Decimal('1.0') vs Decimal('1.00') keyed differently).
    """
    normalized: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and k in {"origin", "destination", "currency", "cabin_class"}:
            normalized[k] = v.lower()
        else:
            normalized[k] = v
    payload = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class TTLCache:
    # Not thread-safe or async-safe. Phase 1 stdio transport serializes tool
    # calls so concurrent access on the same key cannot happen. Wrap _store
    # access in asyncio.Lock before Phase 2's HTTP transport — the read-then-
    # delete sequence in `get` would raise KeyError under contention.
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
