"""Refresh ``token_registry.lifi_supported`` from LiFi ``GET /v1/tokens`` (manual job)."""

from __future__ import annotations

import logging
import time

from aurey.cloud.session import make_engine, make_session_factory
from aurey.graphs.api_key_resolution import effective_lifi_api_key
from aurey.graphs.chains import CHAIN_INDEX
from aurey.service.adapters import UrllibHttpJsonClient
from aurey.settings import AureySettings
from aurey.token_registry.lifi_tokens import build_lifi_address_set, fetch_lifi_tokens_payload
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)

_LIFI_BASE_URL = "https://li.quest"


def run_sync() -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    class _EmptyStore:
        def get_secret(self, path: str):  # noqa: ANN001
            raise RuntimeError("vault not used in LiFi sync job")

    api_key, _err = effective_lifi_api_key(settings, _EmptyStore())
    base_url = _LIFI_BASE_URL
    http = UrllibHttpJsonClient(timeout_s=120.0)
    t0 = time.perf_counter()
    _log.info("Fetching LiFi tokens from %s/v1/tokens", base_url.rstrip("/"))
    payload = fetch_lifi_tokens_payload(http, base_url=base_url, api_key=api_key)
    supported = build_lifi_address_set(payload)
    chain_slugs = frozenset(CHAIN_INDEX.keys())
    _log.info(
        "LiFi address set size on Aurey chains: %s (chains: %s)",
        len(supported),
        len(chain_slugs),
    )

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    marked_true, marked_false = repo.apply_lifi_supported_flags(
        supported=supported,
        chain_slugs=chain_slugs,
    )
    elapsed = time.perf_counter() - t0
    _log.info(
        "lifi_supported updates: set_true=%s set_false=%s elapsed_s=%.1f",
        marked_true,
        marked_false,
        elapsed,
    )
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_sync())


if __name__ == "__main__":
    main()
