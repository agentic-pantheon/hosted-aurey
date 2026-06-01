"""Seed ``token_registry`` from bundled JSON (export + curated catalog)."""

from __future__ import annotations

import logging

from aurey.cloud.session import make_engine, make_session_factory
from aurey.known_addresses.book import iter_catalog_tokens, load_known_addresses
from aurey.settings import AureySettings
from aurey.token_registry.repository import TokenRegistryRepository
from aurey.token_registry.seed_bundle import iter_seed_tokens, load_token_registry_seed

_log = logging.getLogger(__name__)


def run_seed() -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))

    seed_doc = load_token_registry_seed()
    seed_count = 0
    for entry in iter_seed_tokens(seed_doc):
        repo.upsert_curated_or_indexed(
            chain_slug=entry.chain_slug,
            symbol=entry.symbol,
            name=entry.name,
            address=entry.address,
            decimals=entry.decimals,
            coingecko_id=entry.coingecko_id,
            market_cap_rank=entry.market_cap_rank,
            source=entry.source,
            trust_tier=entry.trust_tier,
            verified_onchain=True,
            cg_recognized=bool(entry.coingecko_id) or entry.trust_tier == "curated",
        )
        seed_count += 1
    _log.info("Seeded %s rows from token_registry_seed.json.", seed_count)

    catalog = load_known_addresses()
    curated_count = 0
    for chain_slug, symbol, name, address in iter_catalog_tokens(catalog):
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
        curated_count += 1
    _log.info(
        "Applied %s curated rows from known_addresses.json (wins on address collision).",
        curated_count,
    )
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_seed())


if __name__ == "__main__":
    main()
