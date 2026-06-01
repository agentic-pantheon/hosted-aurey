"""Postgres persistence for :class:`~aurey.cloud.models.TokenRegistryORM`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from aurey.cloud.models import TokenRegistryORM
from aurey.graphs.chains import chain_id_for
from aurey.graphs.evm_codec import to_checksum_evm_address

ALLOWLIST_TIERS = frozenset({"curated", "indexed"})


@dataclass(frozen=True)
class TokenRow:
    chain_slug: str
    chain_id: int | None
    symbol: str
    name: str
    address: str
    decimals: int | None
    coingecko_id: str | None
    market_cap_rank: int | None
    source: str
    trust_tier: str
    verified_onchain: bool
    cg_recognized: bool


def _orm_to_row(row: TokenRegistryORM) -> TokenRow:
    return TokenRow(
        chain_slug=row.chain_slug,
        chain_id=row.chain_id,
        symbol=row.symbol,
        name=row.name,
        address=row.address,
        decimals=row.decimals,
        coingecko_id=row.coingecko_id,
        market_cap_rank=row.market_cap_rank,
        source=row.source,
        trust_tier=row.trust_tier,
        verified_onchain=row.verified_onchain,
        cg_recognized=row.cg_recognized,
    )


class TokenRegistryRepository:
    """Session-scoped registry reads and guarded upserts."""

    def __init__(self, session_factory: Callable[..., Session]) -> None:
        self._session_factory = session_factory

    def lookup_symbol(self, chain_slug: str, symbol: str) -> TokenRow | None:
        slug = chain_slug.strip().lower()
        needle = symbol.strip().upper()
        with self._session_factory() as session:
            stmt = (
                select(TokenRegistryORM)
                .where(
                    TokenRegistryORM.chain_slug == slug,
                    func.upper(TokenRegistryORM.symbol) == needle,
                    TokenRegistryORM.trust_tier.in_(tuple(ALLOWLIST_TIERS)),
                )
                .order_by(
                    case((TokenRegistryORM.trust_tier == "curated", 0), else_=1),
                    TokenRegistryORM.market_cap_rank.asc().nulls_last(),
                )
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return None if row is None else _orm_to_row(row)

    def lookup_address(self, chain_slug: str, address: str) -> TokenRow | None:
        slug = chain_slug.strip().lower()
        try:
            addr = to_checksum_evm_address(address)
        except ValueError:
            return None
        with self._session_factory() as session:
            row = session.scalars(
                select(TokenRegistryORM).where(
                    TokenRegistryORM.chain_slug == slug,
                    TokenRegistryORM.address == addr,
                )
            ).first()
            return None if row is None else _orm_to_row(row)

    def upsert_curated_or_indexed(
        self,
        *,
        chain_slug: str,
        symbol: str,
        name: str,
        address: str,
        decimals: int | None,
        coingecko_id: str | None,
        market_cap_rank: int | None,
        source: str,
        trust_tier: str,
        verified_onchain: bool = True,
        cg_recognized: bool = True,
    ) -> None:
        if trust_tier not in ALLOWLIST_TIERS:
            raise ValueError("upsert_curated_or_indexed requires curated or indexed tier.")
        self._upsert(
            chain_slug=chain_slug,
            symbol=symbol,
            name=name,
            address=address,
            decimals=decimals,
            coingecko_id=coingecko_id,
            market_cap_rank=market_cap_rank,
            source=source,
            trust_tier=trust_tier,
            verified_onchain=verified_onchain,
            cg_recognized=cg_recognized,
            allow_overwrite_discovered=True,
            allow_downgrade=False,
        )

    def upsert_discovered(
        self,
        *,
        chain_slug: str,
        symbol: str,
        name: str,
        address: str,
        decimals: int,
        coingecko_id: str | None,
        cg_recognized: bool,
    ) -> None:
        existing = self.lookup_address(chain_slug, address)
        if existing is not None and existing.trust_tier in ALLOWLIST_TIERS:
            return
        self._upsert(
            chain_slug=chain_slug,
            symbol=symbol,
            name=name,
            address=address,
            decimals=decimals,
            coingecko_id=coingecko_id,
            market_cap_rank=None,
            source="on_demand",
            trust_tier="discovered",
            verified_onchain=True,
            cg_recognized=cg_recognized,
            allow_overwrite_discovered=True,
            allow_downgrade=False,
        )

    def _upsert(
        self,
        *,
        chain_slug: str,
        symbol: str,
        name: str,
        address: str,
        decimals: int | None,
        coingecko_id: str | None,
        market_cap_rank: int | None,
        source: str,
        trust_tier: str,
        verified_onchain: bool,
        cg_recognized: bool,
        allow_overwrite_discovered: bool,
        allow_downgrade: bool,
    ) -> None:
        slug = chain_slug.strip().lower()
        addr = to_checksum_evm_address(address)
        sym = symbol.strip().upper()[:32]
        nm = (name or sym).strip()[:255]
        cid = chain_id_for(slug)
        now = datetime.now(UTC)

        with self._session_factory() as session:
            existing = session.scalars(
                select(TokenRegistryORM).where(
                    TokenRegistryORM.chain_slug == slug,
                    TokenRegistryORM.address == addr,
                )
            ).first()
            if existing is not None:
                if existing.trust_tier in ALLOWLIST_TIERS and trust_tier == "discovered":
                    return
                if existing.trust_tier == "curated" and trust_tier != "curated" and not allow_downgrade:
                    return
                if (
                    existing.trust_tier == "indexed"
                    and trust_tier == "discovered"
                    and not allow_overwrite_discovered
                ):
                    return
                existing.symbol = sym
                existing.name = nm
                existing.decimals = decimals
                existing.coingecko_id = coingecko_id
                existing.market_cap_rank = market_cap_rank
                existing.verified_onchain = verified_onchain
                existing.cg_recognized = cg_recognized
                existing.updated_at = now
                existing.chain_id = cid
                if existing.trust_tier != "curated":
                    existing.trust_tier = trust_tier
                    existing.source = source
                session.commit()
                return

            session.add(
                TokenRegistryORM(
                    id=uuid.uuid4(),
                    ecosystem="evm",
                    chain_slug=slug,
                    chain_id=cid,
                    symbol=sym,
                    name=nm,
                    address=addr,
                    decimals=decimals,
                    coingecko_id=coingecko_id,
                    market_cap_rank=market_cap_rank,
                    source=source,
                    trust_tier=trust_tier,
                    verified_onchain=verified_onchain,
                    cg_recognized=cg_recognized,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
