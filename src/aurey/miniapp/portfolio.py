"""Aggregate Zerion wallet data for the Telegram portfolio Mini App."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

from aurey.graphs.ports import HttpJsonRequestError
from aurey.miniapp.curated import is_curated_portfolio_token
from aurey.miniapp.schemas import (
    PortfolioBalanceChart,
    PortfolioBalanceChartPoint,
    PortfolioDefiPosition,
    PortfolioFetchError,
    PortfolioSnapshot,
    PortfolioSummary,
    PortfolioSummaryByChain,
    PortfolioToken,
    utc_now_iso,
)
from aurey.miniapp.token_aggregate import aggregate_tokens_by_symbol
from aurey.miniapp.zerion_client import (
    fetch_wallet_balance_chart,
    fetch_wallet_fungible_positions,
    fetch_wallet_portfolio,
    normalize_chart_period,
    parse_balance_chart_points,
    parse_portfolio_summary,
    parse_position_row,
    zerion_chain_id_to_slug,
    zerion_http_error_message,
)
from aurey.runtime import AureyRuntime

_log = logging.getLogger(__name__)

_WALLET_POSITION_TYPES = frozenset({"wallet"})


def _resolve_zerion_api_key(runtime: AureyRuntime) -> str | None:
    key = (runtime.settings.zerion_api_key or "").strip()
    return key if key else None


def _curated_token_row(
    chain: str | None,
    *,
    symbol: str | None,
    token_address: str | None,
    zerion_verified: bool,
    is_trash: bool,
) -> bool:
    if is_trash:
        return False
    if zerion_verified:
        return True
    if not chain:
        return False
    return is_curated_portfolio_token(chain, symbol=symbol, token_address=token_address)


def aggregate_portfolio_snapshot(
    runtime: AureyRuntime,
    *,
    wallet_address: str,
    chains: tuple[str, ...],
    chart_period: str = "month",
) -> PortfolioSnapshot:
    """Merge Zerion portfolio, fungible positions, and balance chart."""

    period = normalize_chart_period(chart_period)
    errors: list[PortfolioFetchError] = []
    api_key = _resolve_zerion_api_key(runtime)
    if not api_key:
        errors.append(
            PortfolioFetchError(
                source="zerion",
                chain=None,
                code="missing_api_key",
                message="Set AUREY_ZERION_API_KEY for portfolio visualization.",
            )
        )
        return PortfolioSnapshot(
            wallet_address=wallet_address,
            updated_at=utc_now_iso(),
            chains_queried=chains,
            chains_available=(),
            summary=PortfolioSummary(total_usd=None, by_chain=[]),
            tokens=[],
            tokens_aggregated=[],
            defi=[],
            balance_chart=None,
            errors=errors,
        )

    http = runtime.http
    portfolio_payload: dict[str, Any] | None = None
    position_items: list[dict[str, Any]] | None = None
    chart_payload: dict[str, Any] | None = None

    def _portfolio_job() -> tuple[str, Any]:
        try:
            return (
                "portfolio",
                fetch_wallet_portfolio(
                    http,
                    api_key=api_key,
                    wallet_address=wallet_address,
                    chain_slugs=chains,
                ),
            )
        except Exception as exc:
            return ("portfolio", exc)

    def _positions_job() -> tuple[str, Any]:
        try:
            return (
                "positions",
                fetch_wallet_fungible_positions(
                    http,
                    api_key=api_key,
                    wallet_address=wallet_address,
                    chain_slugs=chains,
                ),
            )
        except Exception as exc:
            return ("positions", exc)

    def _chart_job() -> tuple[str, Any]:
        try:
            return (
                "chart",
                fetch_wallet_balance_chart(
                    http,
                    api_key=api_key,
                    wallet_address=wallet_address,
                    chart_period=period,
                    chain_slugs=chains,
                ),
            )
        except Exception as exc:
            return ("chart", exc)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_portfolio_job),
            pool.submit(_positions_job),
            pool.submit(_chart_job),
        ]
        for fut in as_completed(futures):
            label, result = fut.result()
            if isinstance(result, Exception):
                code = "http_error"
                message = str(result)
                if isinstance(result, HttpJsonRequestError):
                    code = f"http_{result.status_code}"
                    message = zerion_http_error_message(result)
                _log.debug("zerion %s failed: %s", label, message, exc_info=False)
                errors.append(
                    PortfolioFetchError(
                        source="zerion",
                        chain=None,
                        code=code,
                        message=message or None,
                    )
                )
                continue
            if label == "portfolio" and isinstance(result, dict):
                portfolio_payload = result
            elif label == "positions" and isinstance(result, list):
                position_items = result
            elif label == "chart" and isinstance(result, dict):
                chart_payload = result

    total_from_portfolio: Decimal | None = None
    usd_by_chain: dict[str, Decimal] = {}
    if portfolio_payload is not None:
        total_from_portfolio, by_z_chain = parse_portfolio_summary(portfolio_payload)
        for z_chain, usd in by_z_chain.items():
            slug = zerion_chain_id_to_slug(z_chain) or z_chain
            usd_by_chain[slug] = usd_by_chain.get(slug, Decimal(0)) + usd

    token_out: list[PortfolioToken] = []
    defi_out: list[PortfolioDefiPosition] = []
    chains_with_data: set[str] = set()
    token_usd_sum = Decimal(0)
    defi_usd_sum = Decimal(0)

    for raw in position_items or []:
        if not isinstance(raw, dict):
            continue
        parsed = parse_position_row(raw)
        if parsed is None:
            continue
        chain = parsed.get("chain")
        if isinstance(chain, str) and chain.strip() and chain.strip().lower() != "unknown":
            chains_with_data.add(chain.strip().lower())

        usd_dec = parsed.get("usd_value")
        if not isinstance(usd_dec, Decimal):
            usd_dec = None
        pos_type = str(parsed.get("position_type") or "wallet")

        if pos_type in _WALLET_POSITION_TYPES:
            sym = parsed.get("symbol")
            name = parsed.get("name")
            bal_s = parsed.get("balance_decimal")
            addr = parsed.get("token_address")
            curated = _curated_token_row(
                chain if isinstance(chain, str) else None,
                symbol=sym if isinstance(sym, str) else None,
                token_address=addr if isinstance(addr, str) else None,
                zerion_verified=bool(parsed.get("zerion_verified")),
                is_trash=bool(parsed.get("is_trash")),
            )
            if usd_dec is not None and usd_dec > 0:
                token_usd_sum += usd_dec
                if isinstance(chain, str):
                    usd_by_chain[chain] = usd_by_chain.get(chain, Decimal(0)) + usd_dec
            icon = parsed.get("icon_url")
            icon_s = icon.strip() if isinstance(icon, str) and icon.strip() else None
            token_out.append(
                PortfolioToken(
                    chain=chain or "unknown",
                    symbol=sym if isinstance(sym, str) else None,
                    name=name if isinstance(name, str) else None,
                    balance_decimal=bal_s if isinstance(bal_s, str) else None,
                    usd_value=str(usd_dec) if usd_dec is not None else None,
                    token_address=addr if isinstance(addr, str) else None,
                    curated=curated,
                    icon_url=icon_s,
                )
            )
        else:
            proto = parsed.get("protocol_name")
            sym = parsed.get("symbol")
            if usd_dec is not None and usd_dec > 0:
                defi_usd_sum += usd_dec
                if isinstance(chain, str):
                    usd_by_chain[chain] = usd_by_chain.get(chain, Decimal(0)) + usd_dec
            pool = parsed.get("pool_address")
            vault = str(pool).strip() if pool is not None and str(pool).strip() else None
            defi_out.append(
                PortfolioDefiPosition(
                    chain_id=None,
                    chain=chain if isinstance(chain, str) else None,
                    protocol_name=proto if isinstance(proto, str) else None,
                    symbol=sym if isinstance(sym, str) else None,
                    vault_address=vault,
                    balance_usd=str(usd_dec) if usd_dec is not None else None,
                    balance_native=(
                        parsed.get("balance_decimal")
                        if isinstance(parsed.get("balance_decimal"), str)
                        else None
                    ),
                )
            )

    total = total_from_portfolio
    if total is None or total <= 0:
        total = token_usd_sum + defi_usd_sum
    if total is not None and total <= 0:
        total = None

    summary_chains: list[PortfolioSummaryByChain] = []
    chain_keys = sorted(set(chains) | set(usd_by_chain.keys()) | chains_with_data)
    for c in chain_keys:
        v = usd_by_chain.get(c)
        summary_chains.append(
            PortfolioSummaryByChain(
                chain=c,
                usd=str(v) if v is not None and v > 0 else None,
            )
        )

    chart_points: list[PortfolioBalanceChartPoint] = []
    if chart_payload is not None:
        for ts, val in parse_balance_chart_points(chart_payload):
            chart_points.append(PortfolioBalanceChartPoint(ts=ts, usd=str(val)))

    balance_chart = (
        PortfolioBalanceChart(period=period, points=chart_points)
        if chart_points
        else None
    )

    aggregated = aggregate_tokens_by_symbol(token_out)

    return PortfolioSnapshot(
        wallet_address=wallet_address,
        updated_at=utc_now_iso(),
        chains_queried=chains,
        chains_available=tuple(sorted(chains_with_data)),
        summary=PortfolioSummary(
            total_usd=str(total) if total is not None and total > 0 else None,
            by_chain=summary_chains,
        ),
        tokens=token_out,
        tokens_aggregated=aggregated,
        defi=defi_out,
        balance_chart=balance_chart,
        errors=errors,
    )


__all__ = ["aggregate_portfolio_snapshot"]
