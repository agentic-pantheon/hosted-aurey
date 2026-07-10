"""Write ``token_registry`` (or a LiFi JSON dump) to a local MCP export file."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aurey.cloud.session import make_engine, make_session_factory
from aurey.settings import AureySettings
from aurey.token_registry.export_document import build_export_document, write_export_document
from aurey.token_registry.lifi_catalog import iter_lifi_catalog_entries
from aurey.token_registry.lifi_import import load_lifi_tokens_file
from aurey.token_registry.repository import ALLOWLIST_TIERS, TokenRegistryRepository, TokenRow

_log = logging.getLogger(__name__)

_DEFAULT_LIFI_FILE = Path("li_quest_tokens.json")
_DEFAULT_OUTPUT = Path("token_registry_mcp_export.json")


def _rows_from_lifi_file(path: Path) -> list[TokenRow]:
    payload = load_lifi_tokens_file(path)
    rows: list[TokenRow] = []
    seen: set[tuple[str, str]] = set()
    for entry in iter_lifi_catalog_entries(payload):
        key = (entry.chain_slug, entry.address.lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            TokenRow(
                chain_slug=entry.chain_slug,
                chain_id=entry.chain_id,
                symbol=entry.symbol,
                name=entry.name,
                address=entry.address,
                decimals=entry.decimals,
                coingecko_id=None,
                source="lifi_catalog",
                trust_tier="indexed",
                verified_onchain=True,
                cg_recognized=False,
                lifi_supported=True,
                ecosystem=entry.ecosystem,
            )
        )
    rows.sort(key=lambda r: (r.chain_slug, r.symbol, r.address.lower()))
    return rows


def _rows_from_database(*, allowlist_only: bool) -> list[TokenRow]:
    settings = AureySettings()
    if not (settings.database_url or "").strip():
        raise RuntimeError("DATABASE_URL (or AUREY_DATABASE_URL) is required for --source db.")
    engine = make_engine(settings)
    try:
        repo = TokenRegistryRepository(make_session_factory(engine))
        tiers = ALLOWLIST_TIERS if allowlist_only else None
        return repo.list_registry_rows(trust_tiers=tiers)
    finally:
        engine.dispose()


def run_export(
    *,
    source: str,
    lifi_file: Path,
    output: Path,
    allowlist_only: bool,
    pretty: bool,
) -> int:
    if source == "lifi-file":
        if not lifi_file.is_file():
            _log.error("LiFi tokens file not found: %s", lifi_file)
            return 1
        rows = _rows_from_lifi_file(lifi_file)
        source_table = str(lifi_file)
        tier_filter: list[str] | None = ["indexed"]
    elif source == "db":
        try:
            rows = _rows_from_database(allowlist_only=allowlist_only)
        except RuntimeError as exc:
            _log.error("%s", exc)
            return 1
        source_table = "token_registry"
        tier_filter = sorted(ALLOWLIST_TIERS) if allowlist_only else None
    else:
        _log.error("Unknown source %r", source)
        return 1

    doc = build_export_document(
        rows,
        source_table=source_table,
        trust_tier_filter=tier_filter,
    )
    write_export_document(output, doc, pretty=pretty)
    _log.info("Wrote %s tokens to %s (%s bytes).", doc["row_count"], output, output.stat().st_size)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export token metadata to token_registry_mcp_export.json (DB or local LiFi file).",
    )
    parser.add_argument(
        "--source",
        choices=("db", "lifi-file"),
        default="lifi-file",
        help="db=Postgres token_registry; lifi-file=local LiFi /v1/tokens JSON (default: lifi-file)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=_DEFAULT_LIFI_FILE,
        help=f"LiFi tokens JSON when --source lifi-file (default: {_DEFAULT_LIFI_FILE})",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output path (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--all-tiers",
        action="store_true",
        help="With --source db, include discovered rows (default: curated+indexed only).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON (much larger on disk).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(
        run_export(
            source=args.source,
            lifi_file=args.file,
            output=args.output,
            allowlist_only=not args.all_tiers,
            pretty=args.pretty,
        )
    )


if __name__ == "__main__":
    main()
