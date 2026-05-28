"""Mini App portfolio DTO normalization."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from aurey.miniapp.portfolio import aggregate_portfolio_snapshot
from aurey.miniapp.schemas import (
    PortfolioSummary,
    PortfolioSummaryByChain,
    PortfolioSnapshot,
    PortfolioToken,
    utc_now_iso,
)
from aurey.miniapp.token_aggregate import aggregate_tokens_by_symbol
from aurey.runtime import AureyRuntime


def test_portfolio_snapshot_json_roundtrip():
    snap = PortfolioSnapshot(
        wallet_address="0x1111111111111111111111111111111111111111",
        updated_at=utc_now_iso(),
        chains_queried=("ethereum", "base"),
        chains_available=("base",),
        summary=PortfolioSummary(
            total_usd="1",
            by_chain=[PortfolioSummaryByChain(chain="base", usd="1")],
        ),
        tokens=[],
        defi=[],
        errors=[],
    )
    PortfolioSnapshot.model_validate(snap.model_dump(mode="json"))


def test_aggregate_marks_mixed_curated_false():
    rows = [
        PortfolioToken(
            chain="base",
            symbol="USDC",
            balance_decimal="1",
            usd_value="1",
            curated=True,
        ),
        PortfolioToken(
            chain="base",
            symbol="USDC",
            balance_decimal="2",
            token_address="0xdead",
            curated=False,
        ),
    ]
    agg = aggregate_tokens_by_symbol(rows)
    assert len(agg) == 1
    assert agg[0].curated is False


def test_aggregate_missing_zerion_key():
    settings = MagicMock()
    settings.zerion_api_key = None
    runtime = MagicMock(spec=AureyRuntime)
    runtime.settings = settings

    snap = aggregate_portfolio_snapshot(
        runtime,
        wallet_address="0x1111111111111111111111111111111111111111",
        chains=("base",),
    )
    assert snap.errors
    assert snap.errors[0].code == "missing_api_key"
    assert snap.tokens == []


def test_aggregate_portfolio_from_zerion_mocks(monkeypatch):
    settings = MagicMock()
    settings.zerion_api_key = "zk_test"
    runtime = MagicMock(spec=AureyRuntime)
    runtime.settings = settings
    runtime.http = MagicMock()

    monkeypatch.setattr(
        "aurey.miniapp.portfolio.fetch_wallet_portfolio",
        lambda *a, **k: {
            "data": {
                "attributes": {
                    "total": {"positions": 50},
                    "positions_distribution_by_chain": {"base": 50},
                }
            }
        },
    )
    monkeypatch.setattr(
        "aurey.miniapp.portfolio.fetch_wallet_fungible_positions",
        lambda *a, **k: [
            {
                "attributes": {
                    "position_type": "wallet",
                    "value": 50,
                    "quantity": {"numeric": "50"},
                    "fungible_info": {
                        "symbol": "USDC",
                        "flags": {"verified": True},
                        "implementations": [{"chain_id": "base", "address": "0xabc", "decimals": 6}],
                    },
                    "flags": {"is_trash": False},
                },
                "relationships": {"chain": {"id": "base"}},
            }
        ],
    )
    monkeypatch.setattr(
        "aurey.miniapp.portfolio.fetch_wallet_balance_chart",
        lambda *a, **k: {
            "data": {"attributes": {"points": [[1, 40], [2, 50]]}},
        },
    )

    snap = aggregate_portfolio_snapshot(
        runtime,
        wallet_address="0x1111111111111111111111111111111111111111",
        chains=("base",),
    )
    assert snap.summary.total_usd == "50"
    base_row = next(r for r in snap.summary.by_chain if r.chain == "base")
    assert base_row.usd == "50"
    assert len(snap.tokens) == 1
    assert snap.tokens[0].symbol == "USDC"
    assert snap.balance_chart is not None
    assert len(snap.balance_chart.points) == 2
    assert snap.balance_chart.points[-1].usd == "50"
