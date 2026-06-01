"""TTL cache wrapper for :class:`~aurey.custody.secret_store.SecretStore` path reads."""

from __future__ import annotations

from aurey.custody.errors import SecretNotFoundError, SecretStoreUnavailableError
from aurey.custody.secret_store import SecretStore, SecretValue
from aurey.util.ttl_lru_cache import TtlLruCache


class CachingSecretStore:
    """Caches successful ``get_secret`` results by vault path (in-process only)."""

    def __init__(
        self,
        inner: SecretStore,
        *,
        ttl_s: float = 300.0,
        maxsize: int = 64,
    ) -> None:
        self._inner = inner
        self._cache: TtlLruCache[str, SecretValue] = TtlLruCache(maxsize=maxsize, ttl_s=ttl_s)

    def get_secret(self, path: str) -> SecretValue:
        key = path.strip()
        if not key:
            raise ValueError("Secret path must not be empty.")
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        try:
            value = self._inner.get_secret(path)
        except (SecretNotFoundError, SecretStoreUnavailableError):
            raise
        self._cache.set(key, value)
        return value


__all__ = ["CachingSecretStore"]
