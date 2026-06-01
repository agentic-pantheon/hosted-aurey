"""Name -> token allowlist lookup (exact normalized match)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from aurey.cloud.models import Base, TokenRegistryORM
from aurey.known_addresses.book import lookup_known_token_by_name
from aurey.known_addresses.names import normalize_token_lookup_name
from aurey.token_registry.repository import TokenRegistryRepository


def test_normalize_token_lookup_name():
    assert normalize_token_lookup_name("  USD   Coin  ") == "usd coin"


def test_lookup_known_token_by_name_ethereum_usdc():
    hit = lookup_known_token_by_name("ethereum", "USD Coin")
    assert hit is not None
    assert hit.symbol == "USDC"
    assert hit.name == "USD Coin"


def test_lookup_known_token_by_name_misspelling_fails():
    assert lookup_known_token_by_name("ethereum", "USD Cion") is None


@pytest.fixture
def registry_repo():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    yield TokenRegistryRepository(factory)
    engine.dispose()


def test_lookup_name_db_indexed_only(registry_repo):
    repo = registry_repo
    now = datetime.now(UTC)
    with repo._session_factory() as session:
        session.add(
            TokenRegistryORM(
                id=uuid.uuid4(),
                ecosystem="evm",
                chain_slug="base",
                chain_id=8453,
                symbol="PEPE",
                name="Pepe",
                address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                decimals=18,
                coingecko_id="pepe",
                market_cap_rank=50,
                source="market_cap",
                trust_tier="indexed",
                verified_onchain=True,
                cg_recognized=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TokenRegistryORM(
                id=uuid.uuid4(),
                ecosystem="evm",
                chain_slug="base",
                chain_id=8453,
                symbol="SCAM",
                name="Pepe",
                address="0x4200000000000000000000000000000000000006",
                decimals=18,
                coingecko_id="scam",
                market_cap_rank=999,
                source="market_cap",
                trust_tier="discovered",
                verified_onchain=True,
                cg_recognized=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    hit = repo.lookup_name("base", "Pepe")
    assert hit is not None
    assert hit.symbol == "PEPE"
    assert hit.trust_tier == "indexed"
