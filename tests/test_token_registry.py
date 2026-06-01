"""Token registry allowlist and poisoning resistance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from aurey.cloud.models import Base, TokenRegistryORM
from aurey.graphs.api_key_resolution import effective_coingecko_api_key
from aurey.settings import AureySettings
from aurey.token_registry.coingecko import CoinGeckoClient
from aurey.token_registry.repository import TokenRegistryRepository


class _EmptyStore:
    def get_secret(self, path: str):  # noqa: ANN001
        raise RuntimeError("unused")


def test_effective_coingecko_api_key_from_env(monkeypatch):
    monkeypatch.setenv("AUREY_COINGECKO_API_KEY", "demo-key")
    key, err = effective_coingecko_api_key(AureySettings(), _EmptyStore())
    assert err is None
    assert key == "demo-key"


def test_platforms_from_coin_detail():
    coin = {
        "symbol": "usdc",
        "name": "USD Coin",
        "detail_platforms": {
            "ethereum": {"contract_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "decimal_place": 6},
            "base": {"contract_address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "decimal_place": 6},
        },
    }
    rows = CoinGeckoClient.platforms_from_coin_detail(coin)
    slugs = {r[0] for r in rows}
    assert "ethereum" in slugs
    assert "base" in slugs


@pytest.fixture
def registry_repo():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    repo = TokenRegistryRepository(factory)
    yield repo, factory
    engine.dispose()


def _insert_row(
    session: Session,
    *,
    chain_slug: str,
    symbol: str,
    address: str,
    trust_tier: str,
    market_cap_rank: int | None = None,
) -> None:
    now = datetime.now(UTC)
    session.add(
        TokenRegistryORM(
            id=uuid.uuid4(),
            ecosystem="evm",
            chain_slug=chain_slug,
            chain_id=8453 if chain_slug == "base" else 1,
            symbol=symbol,
            name=symbol,
            address=address,
            decimals=6,
            coingecko_id="x",
            market_cap_rank=market_cap_rank,
            source="bundled" if trust_tier == "curated" else "market_cap",
            trust_tier=trust_tier,
            verified_onchain=True,
            cg_recognized=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def test_lookup_symbol_never_returns_discovered(registry_repo):
    repo, factory = registry_repo
    with factory() as session:
        _insert_row(
            session,
            chain_slug="base",
            symbol="SCAM",
            address="0x0000000000000000000000000000000000000001",
            trust_tier="discovered",
        )
        _insert_row(
            session,
            chain_slug="base",
            symbol="USDC",
            address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            trust_tier="indexed",
            market_cap_rank=10,
        )
    hit = repo.lookup_symbol("base", "SCAM")
    assert hit is None
    hit_usdc = repo.lookup_symbol("base", "USDC")
    assert hit_usdc is not None
    assert hit_usdc.trust_tier == "indexed"


def test_lookup_symbol_prefers_curated_rank(registry_repo):
    repo, factory = registry_repo
    with factory() as session:
        _insert_row(
            session,
            chain_slug="ethereum",
            symbol="USDC",
            address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            trust_tier="curated",
            market_cap_rank=999,
        )
        _insert_row(
            session,
            chain_slug="ethereum",
            symbol="USDC",
            address="0x0000000000000000000000000000000000000002",
            trust_tier="indexed",
            market_cap_rank=1,
        )
    hit = repo.lookup_symbol("ethereum", "USDC")
    assert hit is not None
    assert hit.trust_tier == "curated"
    assert hit.address.lower().endswith("eb48")


def test_upsert_discovered_skips_when_curated_exists(registry_repo):
    repo, factory = registry_repo
    curated_addr = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    repo.upsert_curated_or_indexed(
        chain_slug="base",
        symbol="USDC",
        name="USD Coin",
        address=curated_addr,
        decimals=6,
        coingecko_id="usd-coin",
        market_cap_rank=5,
        source="bundled",
        trust_tier="curated",
    )
    repo.upsert_discovered(
        chain_slug="base",
        symbol="FAKE",
        name="Fake",
        address=curated_addr,
        decimals=6,
        coingecko_id=None,
        cg_recognized=False,
    )
    from aurey.graphs.evm_codec import to_checksum_evm_address

    checksum = to_checksum_evm_address(curated_addr)
    with factory() as session:
        row = session.scalars(
            select(TokenRegistryORM).where(
                TokenRegistryORM.chain_slug == "base",
                TokenRegistryORM.address == checksum,
            )
        ).first()
    assert row is not None
    assert row.trust_tier == "curated"
    assert row.symbol == "USDC"
