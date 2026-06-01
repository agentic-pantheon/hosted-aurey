"""Symbol allowlist + address-keyed discovery (on-chain verify, optional CoinGecko enrich)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurey.graphs.api_key_resolution import effective_alchemy_api_key, effective_coingecko_api_key
from aurey.graphs.chains import alchemy_rpc_url_for_chain, chain_id_for, chain_info
from aurey.graphs.evm_codec import normalize_evm_address, to_checksum_evm_address
from aurey.known_addresses.book import (
    KnownToken,
    lookup_known_token,
    lookup_known_token_by_name,
)
from aurey.runtime import AureyRuntime
from aurey.token_registry.coingecko import CoinGeckoClient
from aurey.token_registry.onchain import read_erc20_decimals
from aurey.token_registry.catalog import list_grouped_by_symbol, list_on_chain
from aurey.token_registry.repository import TokenRegistryRepository, TokenRow


@dataclass(frozen=True)
class ResolvedToken:
    chain_slug: str
    chain_id: int
    symbol: str
    name: str
    address: str
    decimals: int | None
    source: str
    trust_tier: str
    verified_onchain: bool
    cg_recognized: bool
    lifi_supported: bool = False
    warning: str | None = None


def _warning_for_tier(trust_tier: str) -> str | None:
    if trust_tier == "curated":
        return None
    if trust_tier == "indexed":
        return (
            "Token is on the market-cap allowlist (not hand-curated). "
            "Confirm the contract address before trading."
        )
    return (
        "Token was resolved by contract address only (discovered tier). "
        "Verify the address and risks before trading."
    )


def _from_known(hit: KnownToken, chain_slug: str) -> ResolvedToken:
    cid = chain_id_for(chain_slug)
    assert cid is not None
    return ResolvedToken(
        chain_slug=chain_slug,
        chain_id=cid,
        symbol=hit.symbol,
        name=hit.name,
        address=hit.address,
        decimals=None,
        source="bundled",
        trust_tier="curated",
        verified_onchain=True,
        cg_recognized=True,
        lifi_supported=False,
        warning=None,
    )


def _from_row(row: TokenRow) -> ResolvedToken:
    cid = row.chain_id or chain_id_for(row.chain_slug)
    assert cid is not None
    return ResolvedToken(
        chain_slug=row.chain_slug,
        chain_id=cid,
        symbol=row.symbol,
        name=row.name,
        address=row.address,
        decimals=row.decimals,
        source=row.source,
        trust_tier=row.trust_tier,
        verified_onchain=row.verified_onchain,
        cg_recognized=row.cg_recognized,
        lifi_supported=row.lifi_supported,
        warning=_warning_for_tier(row.trust_tier),
    )


class TokenResolver:
    def __init__(
        self,
        *,
        runtime: AureyRuntime,
        repository: TokenRegistryRepository,
    ) -> None:
        self._runtime = runtime
        self._repo = repository

    def list_supported_on_chain(self, chain_slug: str) -> list[TokenRow]:
        return list_on_chain(repository=self._repo, chain_slug=chain_slug)

    def list_supported_grouped_by_symbol(self) -> dict[str, list[TokenRow]]:
        return list_grouped_by_symbol(repository=self._repo)

    def resolve_symbol(self, chain_slug: str, symbol: str) -> ResolvedToken | None:
        slug = chain_slug.strip().lower()
        hit = lookup_known_token(slug, symbol)
        if hit is not None:
            return _from_known(hit, slug)
        row = self._repo.lookup_symbol(slug, symbol)
        if row is None:
            return None
        return _from_row(row)

    def resolve_name(self, chain_slug: str, token_name: str) -> ResolvedToken | None:
        """Resolve by human-readable name (allowlist only; exact normalized match)."""

        slug = chain_slug.strip().lower()
        hit = lookup_known_token_by_name(slug, token_name)
        if hit is not None:
            return _from_known(hit, slug)
        row = self._repo.lookup_name(slug, token_name)
        if row is None:
            return None
        return _from_row(row)

    def resolve_address(self, chain_slug: str, address: str) -> tuple[ResolvedToken | None, dict[str, Any] | None]:
        """Verify on-chain, enrich, cache discovered; return graph error dict on failure."""

        slug = chain_slug.strip().lower()
        if chain_info(slug) is None:
            return None, {
                "code": "unsupported_chain",
                "message": f"Unsupported chain '{slug}'.",
            }
        try:
            addr = to_checksum_evm_address(address)
        except ValueError as exc:
            return None, {
                "code": "invalid_input",
                "message": "Invalid token contract address.",
                "details": {"reason": str(exc)},
            }

        cached = self._repo.lookup_address(slug, addr)
        if cached is not None:
            return _from_row(cached), None

        rpc, err = self._open_rpc(slug)
        if err is not None:
            return None, err
        decimals = read_erc20_decimals(rpc, addr)
        if decimals is None:
            return None, {
                "code": "invalid_input",
                "message": "Address does not appear to be a readable ERC-20 token on this chain.",
                "details": {"token_address": addr},
            }

        symbol = "UNKNOWN"
        name = addr
        coingecko_id: str | None = None
        cg_recognized = False

        cg_key, cg_err = effective_coingecko_api_key(
            self._runtime.settings,
            self._runtime.secret_store,
        )
        if cg_key and cg_err is None:
            client = CoinGeckoClient(http=self._runtime.http, api_key=cg_key)
            cg = client.fetch_contract_coin(chain_slug=slug, contract_address=addr)
            if cg is not None:
                cg_recognized = True
                coingecko_id = str(cg.get("id") or "") or None
                symbol = str(cg.get("symbol") or symbol).upper()[:32]
                name = str(cg.get("name") or name)[:255]

        self._repo.upsert_discovered(
            chain_slug=slug,
            symbol=symbol,
            name=name,
            address=addr,
            decimals=decimals,
            coingecko_id=coingecko_id,
            cg_recognized=cg_recognized,
        )
        row = self._repo.lookup_address(slug, addr)
        if row is None:
            cid = chain_id_for(slug)
            assert cid is not None
            return (
                ResolvedToken(
                    chain_slug=slug,
                    chain_id=cid,
                    symbol=symbol,
                    name=name,
                    address=addr,
                    decimals=decimals,
                    source="on_demand",
                    trust_tier="discovered",
                    verified_onchain=True,
                    cg_recognized=cg_recognized,
                    lifi_supported=False,
                    warning=_warning_for_tier("discovered"),
                ),
                None,
            )
        return _from_row(row), None

    def _open_rpc(self, chain_slug: str) -> tuple[Any | None, dict[str, Any] | None]:
        api_key, err_body = effective_alchemy_api_key(
            self._runtime.settings,
            self._runtime.secret_store,
        )
        if err_body is not None:
            return None, err_body
        assert api_key is not None
        url = alchemy_rpc_url_for_chain(chain_slug, api_key)
        if url is None:
            return None, {
                "code": "unsupported_chain",
                "message": "No Alchemy RPC mapping for this chain.",
            }
        rpc = self._runtime.evm_rpc_factory(url)
        return rpc, None
