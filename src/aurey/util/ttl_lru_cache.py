"""Thread-safe TTL + LRU cache for process-scoped memoization."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass
class _CacheEntry[V]:
    value: V
    expires_at: float


class TtlLruCache[K, V]:
    """LRU eviction with per-entry TTL (monotonic clock)."""

    def __init__(self, *, maxsize: int = 2048, ttl_s: float = 86400.0) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1.")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive.")
        self._maxsize = maxsize
        self._ttl_s = ttl_s
        self._data: OrderedDict[K, _CacheEntry[V]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: K) -> V | None:
        now = monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return entry.value

    def set(self, key: K, value: V) -> None:
        now = monotonic()
        with self._lock:
            self._data[key] = _CacheEntry(value=value, expires_at=now + self._ttl_s)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


__all__ = ["TtlLruCache"]
