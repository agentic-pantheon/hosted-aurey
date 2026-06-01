"""Postgres persistence for :class:`~aurey.cloud.models.TokenRegistryORM`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from aurey.cloud.models import TokenRegistryORM
from aurey.graphs.chains import chain_id_for
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.known_addresses.names import normalize_token_lookup_name
from aurey.token_registry.lifi_catalog import LifiCatalogEntry

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
    source: str
    trust_tier: str
    verified_onchain: bool
    cg_recognized: bool
    lifi_supported: bool = False
    ecosystem: str = "evm"


def _orm_to_row(row: TokenRegistryORM) -> TokenRow:
    return TokenRow(
        chain_slug=row.chain_slug,
        chain_id=row.chain_id,
        symbol=row.symbol,
        name=row.name,
        address=row.address,
        decimals=row.decimals,
        coingecko_id=row.coingecko_id,
        source=row.source,
        trust_tier=row.trust_tier,
        verified_onchain=row.verified_onchain,
        cg_recognized=row.cg_recognized,
        lifi_supported=row.lifi_supported,
        ecosystem=row.ecosystem,
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
                    TokenRegistryORM.symbol.asc(),
                )
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return None if row is None else _orm_to_row(row)

    def lookup_name(self, chain_slug: str, token_name: str) -> TokenRow | None:
        slug = chain_slug.strip().lower()
        needle = normalize_token_lookup_name(token_name)
        if not needle:
            return None
        with self._session_factory() as session:
            rows = session.scalars(
                select(TokenRegistryORM)
                .where(
                    TokenRegistryORM.chain_slug == slug,
                    TokenRegistryORM.trust_tier.in_(tuple(ALLOWLIST_TIERS)),
                    func.lower(TokenRegistryORM.name) == needle,
                )
                .order_by(
                    case((TokenRegistryORM.trust_tier == "curated", 0), else_=1),
                    TokenRegistryORM.symbol.asc(),
                )
            ).all()
        if not rows:
            return None
        return _orm_to_row(rows[0])

    def list_allowlist_rows(self, *, chain_slug: str | None = None) -> list[TokenRow]:
        """All curated/indexed rows, optionally filtered to one chain slug."""

        slug_filter = chain_slug.strip().lower() if chain_slug else None
        with self._session_factory() as session:
            stmt = select(TokenRegistryORM).where(
                TokenRegistryORM.trust_tier.in_(tuple(ALLOWLIST_TIERS)),
            )
            if slug_filter is not None:
                stmt = stmt.where(TokenRegistryORM.chain_slug == slug_filter)
            stmt = stmt.order_by(
                TokenRegistryORM.chain_slug.asc(),
                TokenRegistryORM.symbol.asc(),
            )
            rows = session.scalars(stmt).all()
        return [_orm_to_row(r) for r in rows]

    def apply_lifi_supported_flags(
        self,
        *,
        supported: set[tuple[int, str]],
        chain_slugs: frozenset[str],
        supported_solana: set[tuple[str, str]] | None = None,
    ) -> tuple[int, int]:
        """Set ``lifi_supported`` on curated/indexed rows for given chains from LiFi catalog."""

        if not chain_slugs:
            return (0, 0)
        slugs = tuple(sorted(chain_slugs))
        solana_keys = supported_solana or set()
        marked_true = 0
        marked_false = 0
        now = datetime.now(UTC)
        with self._session_factory() as session:
            rows = session.scalars(
                select(TokenRegistryORM).where(
                    TokenRegistryORM.trust_tier.in_(tuple(ALLOWLIST_TIERS)),
                    TokenRegistryORM.chain_slug.in_(slugs),
                )
            ).all()
            for row in rows:
                if row.chain_slug == "solana":
                    want = (row.chain_slug, row.address) in solana_keys
                else:
                    cid = row.chain_id if row.chain_id is not None else chain_id_for(row.chain_slug)
                    if cid is None:
                        continue
                    want = (cid, row.address) in supported
                if row.lifi_supported == want:
                    continue
                row.lifi_supported = want
                row.updated_at = now
                if want:
                    marked_true += 1
                else:
                    marked_false += 1
            session.commit()
        return (marked_true, marked_false)

    def lookup_address(self, chain_slug: str, address: str) -> TokenRow | None:
        slug = chain_slug.strip().lower()
        addr = _normalize_lookup_address(slug, address)
        if addr is None:
            return None
        with self._session_factory() as session:
            row = session.scalars(
                select(TokenRegistryORM).where(
                    TokenRegistryORM.chain_slug == slug,
                    TokenRegistryORM.address == addr,
                )
            ).first()
            return None if row is None else _orm_to_row(row)

    def upsert_lifi_catalog_entry(self, entry: LifiCatalogEntry) -> None:
        """Insert/update an indexed row from LiFi ``/v1/tokens`` (``lifi_supported=true``)."""

        self._upsert(
            ecosystem=entry.ecosystem,
            chain_slug=entry.chain_slug,
            chain_id=entry.chain_id if entry.chain_id is not None else chain_id_for(entry.chain_slug),
            symbol=entry.symbol,
            name=entry.name,
            address=entry.address,
            decimals=entry.decimals,
            coingecko_id=None,
            source="lifi_catalog",
            trust_tier="indexed",
            verified_onchain=True,
            cg_recognized=False,
            lifi_supported=True,
            allow_overwrite_discovered=True,
            allow_downgrade=False,
            overwrite_curated=False,
        )

    def prune_lifi_catalog_rows(
        self,
        *,
        keep: set[tuple[str, str]],
        chain_slugs: frozenset[str],
    ) -> int:
        """Delete ``source=lifi_catalog`` indexed rows on given chains not in ``keep``."""

        if not chain_slugs:
            return 0
        slugs = tuple(sorted(chain_slugs))
        with self._session_factory() as session:
            rows = session.scalars(
                select(TokenRegistryORM).where(
                    TokenRegistryORM.source == "lifi_catalog",
                    TokenRegistryORM.trust_tier == "indexed",
                    TokenRegistryORM.chain_slug.in_(slugs),
                )
            ).all()
            to_delete = [
                r.id
                for r in rows
                if (r.chain_slug, r.address) not in keep
            ]
            if not to_delete:
                return 0
            session.execute(delete(TokenRegistryORM).where(TokenRegistryORM.id.in_(to_delete)))
            session.commit()
            return len(to_delete)

    def upsert_curated_or_indexed(
        self,
        *,
        chain_slug: str,
        symbol: str,
        name: str,
        address: str,
        decimals: int | None,
        coingecko_id: str | None,
        source: str,
        trust_tier: str,
        verified_onchain: bool = True,
        cg_recognized: bool = True,
        lifi_supported: bool = False,
    ) -> None:
        if trust_tier not in ALLOWLIST_TIERS:
            raise ValueError("upsert_curated_or_indexed requires curated or indexed tier.")
        eco = "solana" if chain_slug.strip().lower() == "solana" else "evm"
        addr = _normalize_lookup_address(chain_slug, address)
        if addr is None:
            raise ValueError("invalid token address for chain.")
        self._upsert(
            ecosystem=eco,
            chain_slug=chain_slug,
            chain_id=chain_id_for(chain_slug.strip().lower()),
            symbol=symbol,
            name=name,
            address=addr,
            decimals=decimals,
            coingecko_id=coingecko_id,
            source=source,
            trust_tier=trust_tier,
            verified_onchain=verified_onchain,
            cg_recognized=cg_recognized,
            lifi_supported=lifi_supported,
            allow_overwrite_discovered=True,
            allow_downgrade=False,
            overwrite_curated=trust_tier == "curated",
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
        eco = "solana" if chain_slug.strip().lower() == "solana" else "evm"
        addr = _normalize_lookup_address(chain_slug, address)
        if addr is None:
            raise ValueError("invalid token address for chain.")
        self._upsert(
            ecosystem=eco,
            chain_slug=chain_slug,
            chain_id=chain_id_for(chain_slug.strip().lower()),
            symbol=symbol,
            name=name,
            address=addr,
            decimals=decimals,
            coingecko_id=coingecko_id,
            source="on_demand",
            trust_tier="discovered",
            verified_onchain=True,
            cg_recognized=cg_recognized,
            lifi_supported=False,
            allow_overwrite_discovered=True,
            allow_downgrade=False,
            overwrite_curated=False,
        )

    def _upsert(
        self,
        *,
        ecosystem: str,
        chain_slug: str,
        chain_id: int | None,
        symbol: str,
        name: str,
        address: str,
        decimals: int | None,
        coingecko_id: str | None,
        source: str,
        trust_tier: str,
        verified_onchain: bool,
        cg_recognized: bool,
        lifi_supported: bool,
        allow_overwrite_discovered: bool,
        allow_downgrade: bool,
        overwrite_curated: bool,
    ) -> None:
        slug = chain_slug.strip().lower()
        addr = address if ecosystem == "solana" else to_checksum_evm_address(address)
        sym = symbol.strip().upper()[:32]
        nm = (name or sym).strip()[:255]
        cid = chain_id if ecosystem == "solana" else (chain_id or chain_id_for(slug))
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
                if existing.trust_tier == "curated" and not overwrite_curated and not allow_downgrade:
                    if trust_tier != "curated":
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
                existing.verified_onchain = verified_onchain
                existing.cg_recognized = cg_recognized
                existing.lifi_supported = lifi_supported or existing.lifi_supported
                existing.ecosystem = ecosystem
                existing.updated_at = now
                existing.chain_id = cid
                if existing.trust_tier != "curated" or overwrite_curated:
                    existing.trust_tier = trust_tier
                    existing.source = source
                session.commit()
                return

            session.add(
                TokenRegistryORM(
                    id=uuid.uuid4(),
                    ecosystem=ecosystem,
                    chain_slug=slug,
                    chain_id=cid,
                    symbol=sym,
                    name=nm,
                    address=addr,
                    decimals=decimals,
                    coingecko_id=coingecko_id,
                    source=source,
                    trust_tier=trust_tier,
                    verified_onchain=verified_onchain,
                    cg_recognized=cg_recognized,
                    lifi_supported=lifi_supported,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()


def _normalize_lookup_address(chain_slug: str, address: str) -> str | None:
    slug = chain_slug.strip().lower()
    if slug == "solana":
        from aurey.token_registry.lifi_catalog import normalize_solana_address

        try:
            return normalize_solana_address(address)
        except ValueError:
            return None
    try:
        return to_checksum_evm_address(address)
    except ValueError:
        return None
