"""Import Monad tokens from a local LiFi ``/v1/tokens`` JSON file into ``token_registry``."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aurey.cloud.session import make_engine, make_session_factory
from aurey.settings import AureySettings
from aurey.token_registry.lifi_import import (
    MONAD_CHAIN_ID,
    MONAD_CHAIN_SLUG,
    iter_monad_tokens_from_lifi_file,
    load_lifi_tokens_file,
)
from aurey.token_registry.lifi_tokens import build_lifi_address_set
from aurey.token_registry.repository import TokenRegistryRepository

_log = logging.getLogger(__name__)

_DEFAULT_FILE = Path("li_quest_tokens.json")


def run_import(*, file_path: Path) -> int:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        _log.error("DATABASE_URL (or AUREY_DATABASE_URL) is required.")
        return 1
    if not file_path.is_file():
        _log.error("LiFi tokens file not found: %s", file_path)
        return 1

    entries = list(iter_monad_tokens_from_lifi_file(file_path))
    if not entries:
        _log.error("No Monad (chain %s) tokens in %s", MONAD_CHAIN_ID, file_path)
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
            market_cap_rank=None,
            source="lifi_catalog",
            trust_tier="indexed",
            verified_onchain=True,
            cg_recognized=False,
        )
        upserted += 1

    payload = load_lifi_tokens_file(file_path)
    supported = build_lifi_address_set(payload)
    monad_supported = {(cid, addr) for cid, addr in supported if cid == MONAD_CHAIN_ID}
    marked_true, marked_false = repo.apply_lifi_supported_flags(
        supported=monad_supported,
        chain_slugs=frozenset({MONAD_CHAIN_SLUG}),
    )
    _log.info(
        "Imported %s Monad tokens from %s (indexed, source=lifi_catalog). "
        "lifi_supported: set_true=%s set_false=%s",
        upserted,
        file_path,
        marked_true,
        marked_false,
    )
    engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Monad (chain 143) tokens from a LiFi /v1/tokens JSON export.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=_DEFAULT_FILE,
        help=f"Path to LiFi tokens JSON (default: {_DEFAULT_FILE})",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_import(file_path=args.file))


if __name__ == "__main__":
    main()
