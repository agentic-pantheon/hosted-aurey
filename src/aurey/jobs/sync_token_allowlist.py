"""Sync top-by-market-cap tokens from CoinGecko into indexed allowlist rows."""

from __future__ import annotations

import logging
import time

from aurey.cloud.session import make_engine, make_session_factory
from aurey.graphs.api_key_resolution import effective_coingecko_api_key
from aurey.service.adapters import UrllibHttpJsonClient
from aurey.settings import AureySettings
from aurey.token_registry.coingecko import CoinGeckoClient
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)

# Demo tier ~30 calls/min — pause between per-coin detail fetches.
_DETAIL_SLEEP_S = 2.1


def run_sync() -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    class _EmptyStore:
        def get_secret(self, path: str):  # noqa: ANN001
            raise RuntimeError("vault not used in sync job")

    api_key, err = effective_coingecko_api_key(settings, _EmptyStore())
    if err is not None or not api_key:
        _log.error("CoinGecko API key not configured (AUREY_COINGECKO_API_KEY).")
        return 1

    http = UrllibHttpJsonClient()
    client = CoinGeckoClient(http=http, api_key=api_key)
    per_page = settings.coingecko_allowlist_size
    floor = settings.coingecko_market_cap_floor_usd

    markets = client.fetch_top_markets(per_page=per_page, page=1)
    eligible = [
        m
        for m in markets
        if isinstance(m.get("market_cap"), (int, float))
        and float(m["market_cap"]) >= floor
        and isinstance(m.get("id"), str)
    ]
    _log.info("Eligible coins after cap floor: %s", len(eligible))

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    rows_written = 0
    for m in eligible:
        coin_id = str(m["id"])
        rank = m.get("market_cap_rank")
        rank_i = int(rank) if isinstance(rank, (int, float)) else None
        detail = client.fetch_coin_platforms(coin_id)
        time.sleep(_DETAIL_SLEEP_S)
        if detail is None:
            continue
        for slug, addr, decimals, symbol, name in CoinGeckoClient.platforms_from_coin_detail(
            detail
        ):
            repo.upsert_curated_or_indexed(
                chain_slug=slug,
                symbol=symbol,
                name=name,
                address=addr,
                decimals=decimals,
                coingecko_id=coin_id,
                market_cap_rank=rank_i,
                source="market_cap",
                trust_tier="indexed",
                verified_onchain=True,
                cg_recognized=True,
            )
            rows_written += 1
    _log.info("Indexed allowlist upserts: %s", rows_written)
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_sync())


if __name__ == "__main__":
    main()
