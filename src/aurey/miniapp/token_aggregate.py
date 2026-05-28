"""Roll per-chain token rows into one row per asset (symbol) for the Mini App."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aurey.miniapp.schemas import PortfolioToken, PortfolioTokenAggregated


def _dec(s: str | None) -> Decimal:
    if not s:
        return Decimal(0)
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _dec_plain(d: Decimal) -> str:
    return format(d.normalize(), "f") if d != 0 else "0"


def aggregate_tokens_by_symbol(rows: list[PortfolioToken]) -> list[PortfolioTokenAggregated]:
    """Sum balances and USD per uppercase symbol; collect chain slugs."""

    buckets: dict[str, dict] = {}

    for row in rows:
        sym = (row.symbol or "?").strip()
        key = sym.upper()
        if key not in buckets:
            buckets[key] = {
                "symbol": sym.upper() if sym != "?" else "?",
                "name": row.name,
                "chains": set(),
                "balance": Decimal(0),
                "usd": Decimal(0),
                "has_usd": False,
                "curated": True,
                "icon_url": None,
            }
        b = buckets[key]
        if row.name and (not b["name"] or b["name"] == "Native balance"):
            b["name"] = row.name
        if not b["icon_url"] and row.icon_url:
            b["icon_url"] = row.icon_url
        b["chains"].add(row.chain)
        b["balance"] += _dec(row.balance_decimal)
        u = _dec(row.usd_value) if row.usd_value else Decimal(0)
        if row.usd_value is not None and u > 0:
            b["usd"] += u
            b["has_usd"] = True
        if not row.curated:
            b["curated"] = False

    out: list[PortfolioTokenAggregated] = []
    for key in sorted(buckets.keys(), key=lambda k: (-buckets[k]["usd"], k)):
        b = buckets[key]
        usd_str: str | None = None
        if b["has_usd"] and b["usd"] > 0:
            usd_str = _dec_plain(b["usd"])
        elif b["has_usd"]:
            usd_str = "0"
        out.append(
            PortfolioTokenAggregated(
                asset_key=key,
                symbol=b["symbol"],
                name=b["name"],
                chains=tuple(sorted(b["chains"])),
                balance_decimal=_dec_plain(b["balance"]),
                usd_value=usd_str,
                curated=b["curated"],
                icon_url=b["icon_url"],
            )
        )
    return out


__all__ = ["aggregate_tokens_by_symbol"]
