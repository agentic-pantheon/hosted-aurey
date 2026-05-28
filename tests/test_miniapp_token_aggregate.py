"""Cross-chain token aggregation and curated flags."""

from __future__ import annotations

from decimal import Decimal

from aurey.miniapp.curated import is_curated_portfolio_token
from aurey.miniapp.schemas import PortfolioToken
from aurey.miniapp.token_aggregate import aggregate_tokens_by_symbol


def test_aggregate_eth_across_chains():
    rows = [
        PortfolioToken(
            chain="base",
            symbol="ETH",
            name="Native balance",
            balance_decimal="0.001",
            usd_value="2",
            curated=True,
        ),
        PortfolioToken(
            chain="ethereum",
            symbol="ETH",
            name="Native balance",
            balance_decimal="0.002",
            usd_value="3",
            curated=True,
        ),
    ]
    agg = aggregate_tokens_by_symbol(rows)
    assert len(agg) == 1
    assert agg[0].symbol == "ETH"
    assert set(agg[0].chains) == {"base", "ethereum"}
    assert Decimal(agg[0].balance_decimal) == Decimal("0.003")
    assert Decimal(agg[0].usd_value or "0") == Decimal("5")


def test_curated_native_on_base():
    assert is_curated_portfolio_token("base", symbol="ETH", token_address=None) is True


def test_curated_weth_by_address_on_base():
    assert (
        is_curated_portfolio_token(
            "base",
            symbol="WETH",
            token_address="0x4200000000000000000000000000000000000006",
        )
        is True
    )


def test_not_curated_spam_ticker():
    assert (
        is_curated_portfolio_token(
            "base",
            symbol="UGOR",
            token_address="0x1111111111111111111111111111111111111111",
        )
        is False
    )
