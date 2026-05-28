"""In-process sliding-window rate limits for Mini App routes."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Thread-safe limiter: at most ``max_events`` per ``window_seconds`` per key."""

    def __init__(self, *, max_events: int, window_seconds: float) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_events = max_events
        self._window = float(window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Return True when the event is allowed and record it."""

        k = key.strip()
        if not k:
            return False
        t = float(now) if now is not None else time.monotonic()
        cutoff = t - self._window
        with self._lock:
            q = self._events[k]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self._max_events:
                return False
            q.append(t)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


__all__ = ["SlidingWindowRateLimiter"]
