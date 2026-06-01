"""Token registry lifi_supported flag updates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from aurey.cloud.models import Base, TokenRegistryORM
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.token_registry.repository import TokenRegistryRepository


@pytest.fixture
def registry_repo() -> TokenRegistryRepository:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return TokenRegistryRepository(factory)


def _insert_row(
    session: Session,
    *,
    chain_slug: str,
    chain_id: int,
    address: str,
    trust_tier: str = "indexed",
) -> None:
    now = datetime.now(UTC)
    session.add(
        TokenRegistryORM(
            id=uuid.uuid4(),
            ecosystem="evm",
            chain_slug=chain_slug,
            chain_id=chain_id,
            symbol="TST",
            name="Test",
            address=address,
            decimals=18,
            coingecko_id=None,
            market_cap_rank=None,
            source="test",
            trust_tier=trust_tier,
            verified_onchain=True,
            cg_recognized=True,
            lifi_supported=False,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def test_apply_lifi_supported_flags_marks_true_and_false(registry_repo: TokenRegistryRepository):
    usdc = to_checksum_evm_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    other = to_checksum_evm_address("0x1111111111111111111111111111111111111111")
    factory = registry_repo._session_factory
    with factory() as session:
        _insert_row(session, chain_slug="ethereum", chain_id=1, address=usdc)
        _insert_row(session, chain_slug="ethereum", chain_id=1, address=other)

    supported = {(1, usdc)}
    true_n, false_n = registry_repo.apply_lifi_supported_flags(
        supported=supported,
        chain_slugs=frozenset({"ethereum"}),
    )
    assert true_n == 1
    assert false_n == 0

    with factory() as session:
        rows = {r.address: r.lifi_supported for r in session.scalars(select(TokenRegistryORM)).all()}
    assert rows[usdc] is True
    assert rows[other] is False

    true_n2, false_n2 = registry_repo.apply_lifi_supported_flags(
        supported=set(),
        chain_slugs=frozenset({"ethereum"}),
    )
    assert true_n2 == 0
    assert false_n2 == 1
    with factory() as session:
        usdc_row = session.scalars(
            select(TokenRegistryORM).where(TokenRegistryORM.address == usdc)
        ).first()
        assert usdc_row is not None
        assert usdc_row.lifi_supported is False
