"""Zerion response normalization for the Mini App."""

from __future__ import annotations

from decimal import Decimal

from aurey.miniapp.zerion_client import (
    parse_balance_chart_points,
    parse_portfolio_summary,
    parse_position_row,
    zerion_chain_id_to_slug,
)


def test_zerion_chain_id_to_slug_aliases():
    assert zerion_chain_id_to_slug("binance-smart-chain") == "bsc"
    assert zerion_chain_id_to_slug("base") == "base"


def test_parse_portfolio_summary_total_and_by_chain():
    payload = {
        "data": {
            "attributes": {
                "total": {"positions": 1234.56},
                "positions_distribution_by_chain": {
                    "base": 1000,
                    "ethereum": 234.56,
                },
            }
        }
    }
    total, by_chain = parse_portfolio_summary(payload)
    assert total == Decimal("1234.56")
    assert by_chain["base"] == Decimal("1000")


def test_parse_balance_chart_points():
    payload = {
        "data": {
            "attributes": {
                "points": [[1674039600, 100.5], [1674126000, 105.25]],
            }
        }
    }
    pts = parse_balance_chart_points(payload)
    assert pts == [(1674039600, Decimal("100.5")), (1674126000, Decimal("105.25"))]


def test_parse_position_row_wallet_token():
    item = {
        "type": "positions",
        "attributes": {
            "position_type": "wallet",
            "value": 42.5,
            "quantity": {"numeric": "1.5", "decimals": 18},
            "fungible_info": {
                "symbol": "USDC",
                "name": "USD Coin",
                "flags": {"verified": True},
                "implementations": [
                    {"chain_id": "base", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
                ],
            },
            "flags": {"is_trash": False},
        },
        "relationships": {"chain": {"id": "base", "type": "chains"}},
    }
    row = parse_position_row(item)
    assert row is not None
    assert row["chain"] == "base"
    assert row["symbol"] == "USDC"
    assert row["usd_value"] == Decimal("42.5")
    assert row["zerion_verified"] is True


def test_parse_position_row_deposit():
    item = {
        "attributes": {
            "position_type": "deposit",
            "protocol": "Aave",
            "value": 10,
            "quantity": {"numeric": "10"},
            "fungible_info": {"symbol": "USDC", "name": "USD Coin"},
        },
        "relationships": {"chain": {"id": "ethereum"}},
    }
    row = parse_position_row(item)
    assert row is not None
    assert row["position_type"] == "deposit"
    assert row["protocol_name"] == "Aave"
