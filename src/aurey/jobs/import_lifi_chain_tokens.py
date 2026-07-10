"""Import a single chain's tokens from a local LiFi ``/v1/tokens`` JSON export.

Offline-friendly alternative to ``sync_lifi_token_catalog`` when you only need to
add/refresh one chain (e.g. a newly listed L2): fetch the chain slice once, then
upsert just those rows. Unlike the full sync it does **not** prune other chains.

Fetch the file (anywhere with internet)::

    curl -s "https://li.quest/v1/tokens?chains=4663" -o rh.json

Then import (needs DATABASE_URL)::

    uv run python -m aurey.jobs.import_lifi_chain_tokens --chain robinhood --file rh.json
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aurey.cloud.session import make_engine, make_session_factory
from aurey.graphs.chains import chain_info
from aurey.settings import AureySettings
from aurey.token_registry.lifi_import import (
    iter_chain_tokens_from_lifi_payload,
    load_lifi_tokens_file,
)
from aurey.token_registry.lifi_tokens import build_lifi_address_set
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)


def run_import(*, chain_slug: str, file_path: Path) -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1

    slug = chain_slug.strip().lower()
    info = chain_info(slug)
    if info is None:
        _log.error("Unknown chain slug %r (not in CHAIN_INDEX).", chain_slug)
        return 1
    if not file_path.is_file():
        _log.error("LiFi tokens file not found: %s", file_path)
        return 1

    payload = load_lifi_tokens_file(file_path)
    entries = list(
        iter_chain_tokens_from_lifi_payload(
            payload,
            chain_id=info.chain_id,
            chain_slug=slug,
        )
    )
    if not entries:
        _log.error("No %s (chain %s) tokens in %s", slug, info.chain_id, file_path)
        return 1

    engine = make_engine(settings)
    repo = TokenRegistryRepository(make_session_factory(engine))
    upserted = 0
    for entry in entries:
        repo.upsert_curated_or_indexed(
            chain_slug=entry.chain_slug,
            symbol=entry.symbol,
            name=entry.name,
            address=entry.address,
            decimals=entry.decimals,
            coingecko_id=None,
            source="lifi_catalog",
            trust_tier="indexed",
            verified_onchain=True,
            cg_recognized=False,
        )
        upserted += 1

    supported = build_lifi_address_set(payload)
    chain_supported = {(cid, addr) for cid, addr in supported if cid == info.chain_id}
    marked_true, marked_false = repo.apply_lifi_supported_flags(
        supported=chain_supported,
        chain_slugs=frozenset({slug}),
    )
    _log.info(
        "Imported %s %s tokens from %s (indexed, source=lifi_catalog). "
        "lifi_supported: set_true=%s set_false=%s",
        upserted,
        slug,
        file_path,
        marked_true,
        marked_false,
    )
    engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import one chain's tokens from a LiFi /v1/tokens JSON export.",
    )
    parser.add_argument(
        "--chain",
        required=True,
        help="Chain slug from CHAIN_INDEX (e.g. robinhood).",
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to LiFi tokens JSON (shape: {\"tokens\": {\"<chainId>\": [...]}}).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_import(chain_slug=args.chain, file_path=args.file))


if __name__ == "__main__":
    main()
