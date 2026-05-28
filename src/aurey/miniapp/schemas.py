"""Stable JSON shapes returned by ``POST /v1/miniapp/portfolio``."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class PortfolioSummaryByChain(BaseModel):
    """USD subtotal for one chain (tokens + native where priced; best-effort)."""

    model_config = ConfigDict(frozen=True)

    chain: str
    usd: str | None = Field(
        default=None,
        description="Decimal string total USD for this chain, or null when indeterminate.",
    )


class PortfolioSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_usd: str | None = Field(
        default=None,
        description="Sum of priced token + DeFi USD rows; null when nothing was priced.",
    )
    by_chain: list[PortfolioSummaryByChain]


class PortfolioToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    symbol: str | None = None
    name: str | None = None
    balance_decimal: str | None = None
    usd_value: str | None = Field(
        default=None,
        description="Best-effort quoted USD for this row; null when unavailable.",
    )
    token_address: str | None = Field(
        default=None,
        description="ERC-20 contract, or nullish for native-symbol rows.",
    )
    curated: bool = Field(
        default=False,
        description="True when native on a catalog chain or ERC-20 matches ``known_addresses.json``.",
    )
    icon_url: str | None = Field(
        default=None,
        description="HTTPS PNG (or image) URL from Zerion ``fungible_info.icon`` when available.",
    )


class PortfolioTokenAggregated(BaseModel):
    """Same asset summed across chains (Tokens tab)."""

    model_config = ConfigDict(frozen=True)

    asset_key: str = Field(description="Grouping key, usually uppercase symbol.")
    symbol: str
    name: str | None = None
    chains: tuple[str, ...] = Field(description="Chain slugs where this asset has balance.")
    balance_decimal: str = Field(description="Sum of human-readable balances across chains.")
    usd_value: str | None = Field(
        default=None,
        description="Sum of priced USD across chains, or null if none priced.",
    )
    curated: bool = Field(
        default=False,
        description="True only if every per-chain leg is curated.",
    )
    icon_url: str | None = Field(
        default=None,
        description="Best icon URL among per-chain legs (Zerion or null).",
    )


class PortfolioDefiPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain_id: int | None = None
    chain: str | None = Field(
        default=None,
        description="Canonical slug when resolvable from numeric ``chain_id``.",
    )
    protocol_name: str | None = None
    symbol: str | None = None
    vault_address: str | None = None
    balance_usd: str | None = None
    balance_native: str | None = None


class PortfolioBalanceChartPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: int = Field(description="Unix timestamp (seconds) for this chart sample.")
    usd: str = Field(description="Portfolio USD value at ``ts`` (decimal string).")


class PortfolioBalanceChart(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: str = Field(description="Zerion chart period slug (e.g. ``week``, ``month``, ``max``).")
    points: list[PortfolioBalanceChartPoint] = Field(default_factory=list)


class PortfolioFetchError(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str = Field(description="``zerion`` | ``internal``")
    chain: str | None = None
    code: str | None = None
    message: str | None = None


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    wallet_address: str = Field(min_length=1)
    updated_at: str = Field(
        description="UTC ISO-8601 snapshot time (server wall clock).",
    )
    chains_queried: tuple[str, ...] = Field(
        description="Chain slugs passed to Zerion ``filter[chain_ids]`` for this call.",
    )
    chains_available: tuple[str, ...] = Field(
        description="Chains with any non-empty token row or DeFi balance field.",
    )
    summary: PortfolioSummary
    tokens: list[PortfolioToken]
    tokens_aggregated: list[PortfolioTokenAggregated] = Field(
        default_factory=list,
        description="Per-symbol totals across chains for the Tokens tab.",
    )
    defi: list[PortfolioDefiPosition]
    balance_chart: PortfolioBalanceChart | None = Field(
        default=None,
        description="Wallet balance over time from Zerion (when chart fetch succeeds).",
    )
    errors: list[PortfolioFetchError] = Field(default_factory=list)


def utc_now_iso() -> str:
    """UTC timestamp with Z suffix for JSON clients."""

    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "PortfolioBalanceChart",
    "PortfolioBalanceChartPoint",
    "PortfolioDefiPosition",
    "PortfolioFetchError",
    "PortfolioSnapshot",
    "PortfolioSummary",
    "PortfolioSummaryByChain",
    "PortfolioToken",
    "PortfolioTokenAggregated",
    "utc_now_iso",
]
