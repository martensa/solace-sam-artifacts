"""TTL cache for search results."""

from __future__ import annotations

import time
from typing import Any, Optional


class TTLCache:
    """In-memory cache with TTL for search results."""

    def __init__(self, ttl: int = 300, max_size: int = 100) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        # Evict expired entries if at capacity
        if len(self._store) >= self._max_size:
            self._evict_expired()
        # If still at capacity, evict oldest
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic(), value)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
