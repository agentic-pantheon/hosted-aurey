"""Import bundled ``known_addresses.json`` into ``token_registry`` as curated rows."""

from __future__ import annotations

import logging
import sys

from aurey.cloud.session import make_engine, make_session_factory
from aurey.graphs.chains import chain_name_for_id
from aurey.known_addresses.book import load_known_addresses
from aurey.settings import AureySettings
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)


def run_seed() -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    doc = load_known_addresses()
    chains = doc.get("chains")
    if not isinstance(chains, dict):
        _log.error("known_addresses.json: missing chains.")
        return 1

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    count = 0
    for cid_str, block in chains.items():
        if not isinstance(block, dict):
            continue
        try:
            cid = int(cid_str)
        except ValueError:
            continue
        slug = chain_name_for_id(cid)
        if slug is None:
            continue
        toks = block.get("tokens")
        if not isinstance(toks, dict):
            continue
        for sym, row in toks.items():
            if not isinstance(row, dict):
                continue
            addr = row.get("address")
            if not isinstance(addr, str):
                continue
            name = row.get("name")
            repo.upsert_curated_or_indexed(
                chain_slug=slug,
                symbol=str(sym),
                name=str(name) if name is not None else str(sym),
                address=addr,
                decimals=None,
                coingecko_id=None,
                market_cap_rank=None,
                source="bundled",
                trust_tier="curated",
                verified_onchain=True,
                cg_recognized=True,
            )
            count += 1
    _log.info("Seeded %s curated token rows.", count)
    engine.dispose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_seed())


if __name__ == "__main__":
    main()
