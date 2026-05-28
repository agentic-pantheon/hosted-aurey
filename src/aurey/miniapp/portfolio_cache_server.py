"""Short-lived server cache for Zerion portfolio snapshots."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from aurey.miniapp.schemas import PortfolioSnapshot


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    snapshot: PortfolioSnapshot


class PortfolioSnapshotCache:
    """Process-local TTL cache keyed by ``(telegram_user_id, wallet, chains)``."""

    def __init__(self, *, max_entries: int = 512) -> None:
        self._max_entries = max(16, max_entries)
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def cache_key(
        *,
        telegram_user_id: int,
        wallet_address: str,
        chains: tuple[str, ...],
    ) -> str:
        w = wallet_address.strip().lower()
        c = ",".join(sorted(chains))
        return f"{telegram_user_id}:{w}:{c}"

    def get(self, key: str, *, now: float | None = None) -> PortfolioSnapshot | None:
        t = float(now) if now is not None else time.monotonic()
        with self._lock:
            self._purge_expired_locked(t)
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= t:
                if entry is not None:
                    self._entries.pop(key, None)
                return None
            return entry.snapshot

    def set(
        self,
        key: str,
        snapshot: PortfolioSnapshot,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            return
        t = float(now) if now is not None else time.monotonic()
        entry = _CacheEntry(expires_at=t + ttl_seconds, snapshot=snapshot)
        with self._lock:
            self._purge_expired_locked(t)
            self._entries[key] = entry
            while len(self._entries) > self._max_entries:
                oldest_key = min(self._entries, key=lambda k: self._entries[k].expires_at)
                self._entries.pop(oldest_key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _purge_expired_locked(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            self._entries.pop(k, None)


# Shared by the HTTP app process (tests may replace or clear).
portfolio_snapshot_cache = PortfolioSnapshotCache()

__all__ = ["PortfolioSnapshotCache", "portfolio_snapshot_cache"]
