"""Replace allowlist catalog with LiFi ``GET /v1/tokens`` (EVM + Solana when API returns it)."""

from __future__ import annotations

import logging
import time

from aurey.cloud.session import make_engine, make_session_factory
from aurey.graphs.api_key_resolution import effective_lifi_api_key
from aurey.service.adapters import UrllibHttpJsonClient
from aurey.settings import AureySettings
from aurey.token_registry.lifi_catalog import (
    fetch_lifi_tokens_payload_for_import,
    iter_lifi_catalog_entries,
    lifi_import_chain_slugs,
)
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)


def run_sync(*, prune: bool = True) -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    class _EmptyStore:
        def get_secret(self, path: str):  # noqa: ANN001
            raise RuntimeError("vault not used in LiFi catalog sync job")

    api_key, _err = effective_lifi_api_key(settings, _EmptyStore())
    http = UrllibHttpJsonClient(timeout_s=120.0)
    t0 = time.perf_counter()
    _log.info("Fetching LiFi token catalog…")
    payload = fetch_lifi_tokens_payload_for_import(http, api_key=api_key)
    entries = list(iter_lifi_catalog_entries(payload))
    if not entries:
        _log.error("No importable tokens in LiFi response.")
        return 1

    by_chain: dict[str, int] = {}
    for e in entries:
        by_chain[e.chain_slug] = by_chain.get(e.chain_slug, 0) + 1
    _log.info("LiFi entries to import: %s (%s)", len(entries), dict(sorted(by_chain.items())))

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    keep = {(e.chain_slug, e.address) for e in entries}
    for entry in entries:
        repo.upsert_lifi_catalog_entry(entry)

    deleted = 0
    if prune:
        deleted = repo.prune_lifi_catalog_rows(
            keep=keep,
            chain_slugs=lifi_import_chain_slugs(),
        )
    elapsed = time.perf_counter() - t0
    _log.info(
        "LiFi catalog sync done: upserted=%s pruned=%s elapsed_s=%.1f",
        len(entries),
        deleted,
        elapsed,
    )
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_sync())


if __name__ == "__main__":
    main()
