"""Import bundled ``known_addresses.json`` into ``token_registry`` as curated rows."""

from __future__ import annotations

import logging
import sys

from aurey.cloud.session import make_engine, make_session_factory
from aurey.known_addresses.book import iter_catalog_tokens, load_known_addresses
from aurey.settings import AureySettings
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)


def run_seed() -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    doc = load_known_addresses()
    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    count = 0
    for chain_slug, symbol, name, address in iter_catalog_tokens(doc):
        repo.upsert_curated_or_indexed(
            chain_slug=chain_slug,
            symbol=symbol,
            name=name,
            address=address,
            decimals=None,
            coingecko_id=None,
            market_cap_rank=None,
            source="bundled",
            trust_tier="curated",
            verified_onchain=True,
            cg_recognized=True,
        )
        count += 1
    _log.info("Seeded %s curated token rows from known_addresses.json.", count)
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_seed())


if __name__ == "__main__":
    main()
