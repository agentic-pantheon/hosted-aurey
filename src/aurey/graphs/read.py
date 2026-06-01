"""LangGraph: EVM reads + known-address resolution (Mercury-parity subset)."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from aurey.graphs.api_key_resolution import effective_alchemy_api_key
from aurey.graphs.cached_decimals import (
    fetch_erc20_decimals_and_balance_raw,
    get_cached_erc20_decimals,
)
from aurey.graphs.chains import alchemy_rpc_url_for_chain, chain_id_for, chain_info
from aurey.graphs.ens_eth import (
    ENS_REGISTRY_MAINNET,
    decode_abi_address_word,
    ens_addr_calldata,
    ens_namehash,
    ens_resolver_calldata,
    is_zero_address,
    normalize_ens_query_name,
)
from aurey.graphs.evm_codec import (
    format_token_units,
    normalize_evm_address,
    parse_evm_uint,
)
from aurey.graphs.results import (
    EnsResolveResult,
    Erc20BalanceResult,
    Erc20DecimalsResult,
    GraphErrorBody,
    KnownAddressResult,
    NativeBalanceResult,
    SupportedSymbolGroup,
    SupportedTokenChainRef,
    SupportedTokenEntry,
    SupportedTokensGroupedResult,
    SupportedTokensOnChainResult,
)
from aurey.known_addresses.book import lookup_known_token, lookup_known_token_by_name
from aurey.runtime import AureyRuntime
from aurey.token_registry.catalog import list_grouped_by_symbol, list_on_chain


class ReadGraphInput(BaseModel):
    operation: Literal[
        "native_balance",
        "known_address",
        "token_by_name",
        "token_by_address",
        "list_supported_tokens",
        "erc20_balance",
        "erc20_decimals",
        "ens_resolve",
    ]
    chain: str = Field(min_length=1)
    wallet_address: str | None = None
    token_address: str | None = None
    known_ticker: str | None = None
    token_name: str | None = None
    list_supported_chain: str | None = Field(
        default=None,
        description="When set, list allowlisted tokens on this chain only.",
    )
    ens_name: str | None = None


def _alchemy_rpc_or_error(
    runtime: AureyRuntime,
    chain: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Open JSON-RPC for ``chain`` via Alchemy URL, or return ``GraphErrorBody`` dict."""

    alchemy_key, err_body = effective_alchemy_api_key(
        runtime.settings,
        runtime.secret_store,
        extra_secret_not_configured_details={"chain": chain},
    )
    if err_body is not None:
        return None, err_body
    assert alchemy_key is not None

    rpc_url = alchemy_rpc_url_for_chain(chain, alchemy_key)
    if rpc_url is None:
        return None, GraphErrorBody(
            code="unsupported_chain",
            message="No Alchemy RPC mapping for this chain.",
            details={"chain": chain},
        ).model_dump()

    rpc = runtime.evm_rpc_factory(rpc_url)
    return rpc, None


class ReadGraphState(TypedDict, total=False):
    input: dict[str, Any]
    parsed: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validate_ok(parsed: ReadGraphInput) -> ReadGraphState:
    return {"parsed": parsed.model_dump()}


def _load_parsed_input(state: ReadGraphState) -> ReadGraphInput:
    raw = state.get("parsed")
    if isinstance(raw, dict):
        return ReadGraphInput.model_validate(raw)
    return ReadGraphInput.model_validate(state["input"])


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Read graph input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(state: ReadGraphState) -> ReadGraphState:
    try:
        parsed = ReadGraphInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    chain = parsed.chain
    chain_key = chain.strip().lower()

    if parsed.operation == "ens_resolve":
        if not parsed.ens_name or not str(parsed.ens_name).strip():
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ens_name is required for ens_resolve.",
                ).model_dump()
            }
        if chain_key != "ethereum":
            return {
                "error": GraphErrorBody(
                    code="unsupported_chain",
                    message="ENS can only be resolved on ethereum mainnet.",
                    details={"chain": parsed.chain.strip()},
                ).model_dump()
            }
        return _validate_ok(parsed)

    if parsed.operation == "list_supported_tokens":
        target = (parsed.list_supported_chain or "").strip().lower()
        if target and chain_info(target) is None:
            return {
                "error": GraphErrorBody(
                    code="unsupported_chain",
                    message=f"Unsupported chain '{target}'.",
                ).model_dump()
            }
        return _validate_ok(parsed)

    if chain_info(chain) is None:
        return {
            "error": GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported chain '{chain}'.",
            ).model_dump()
        }

    if parsed.operation == "native_balance":
        if not parsed.wallet_address:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="wallet_address is required for native_balance.",
                ).model_dump()
            }
        try:
            normalize_evm_address(parsed.wallet_address)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid wallet address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
    if parsed.operation == "known_address":
        if not parsed.known_ticker:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="known_ticker is required for known_address.",
                ).model_dump()
            }
    if parsed.operation == "token_by_name":
        if not parsed.token_name or not str(parsed.token_name).strip():
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="token_name is required for token_by_name.",
                ).model_dump()
            }
    if parsed.operation == "token_by_address":
        if not parsed.token_address:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="token_address is required for token_by_address.",
                ).model_dump()
            }
        try:
            normalize_evm_address(parsed.token_address)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid token address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
    if parsed.operation == "erc20_balance":
        if not parsed.wallet_address or not parsed.token_address:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="wallet_address and token_address are required for erc20_balance.",
                ).model_dump()
            }
        try:
            normalize_evm_address(parsed.wallet_address)
            normalize_evm_address(parsed.token_address)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid wallet or token address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
    if parsed.operation == "erc20_decimals":
        if not parsed.token_address:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="token_address is required for erc20_decimals.",
                ).model_dump()
            }
        try:
            normalize_evm_address(parsed.token_address)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid token address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }

    return _validate_ok(parsed)


def _execute_node(runtime: AureyRuntime, state: ReadGraphState) -> ReadGraphState:
    if state.get("error"):
        return {}

    parsed = _load_parsed_input(state)
    chain = parsed.chain.strip().lower()

    if parsed.operation == "ens_resolve":
        rpc, err_body = _alchemy_rpc_or_error(runtime, chain)
        if err_body is not None:
            return {"error": err_body}
        name = normalize_ens_query_name(parsed.ens_name or "")
        if not name:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ENS name is empty.",
                ).model_dump()
            }
        try:
            node = ens_namehash(name)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid ENS name.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
        try:
            resolver_raw = rpc.call(
                "eth_call",
                [
                    {"to": ENS_REGISTRY_MAINNET, "data": ens_resolver_calldata(node)},
                    "latest",
                ],
            )
            if not isinstance(resolver_raw, str):
                raise TypeError("unexpected eth_call result type")
            resolver_addr = decode_abi_address_word(resolver_raw)
        except Exception:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="ENS registry resolver(bytes32) eth_call failed.",
                ).model_dump()
            }

        if is_zero_address(resolver_addr):
            return {
                "error": GraphErrorBody(
                    code="ens_not_found",
                    message="No resolver is set for this ENS name.",
                    details={"name": name},
                ).model_dump()
            }

        try:
            addr_raw = rpc.call(
                "eth_call",
                [
                    {"to": resolver_addr, "data": ens_addr_calldata(node)},
                    "latest",
                ],
            )
            if not isinstance(addr_raw, str):
                raise TypeError("unexpected eth_call result type")
            resolved = decode_abi_address_word(addr_raw)
        except Exception:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="ENS resolver addr(bytes32) eth_call failed.",
                    details={"resolver": resolver_addr},
                ).model_dump()
            }

        if is_zero_address(resolved):
            return {
                "error": GraphErrorBody(
                    code="ens_not_found",
                    message="ENS name does not resolve to an Ethereum address.",
                    details={"name": name},
                ).model_dump()
            }

        out = EnsResolveResult(name=name, resolved_address=resolved)
        return {"result": out.model_dump()}

    if parsed.operation == "known_address":
        ticker = parsed.known_ticker or ""
        resolved = None
        if runtime.token_resolver is not None:
            resolved = runtime.token_resolver.resolve_symbol(chain, ticker)
        if resolved is None:
            hit = lookup_known_token(chain, ticker)
            if hit is None:
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message=(
                            "Unknown ticker for this chain (not in the allowlist). "
                            "Ask the user for the full contract address (0x…)."
                        ),
                        details={"ticker": ticker, "chain": chain},
                    ).model_dump()
                }
            cid = chain_id_for(chain)
            assert cid is not None
            result = KnownAddressResult(
                chain=chain,
                ticker=ticker.strip(),
                symbol=hit.symbol,
                name=hit.name,
                resolved_address=hit.address,
                source="bundled",
                trust_tier="curated",
                verified_onchain=True,
                cg_recognized=True,
            )
            return {"result": result.model_dump()}
        result = KnownAddressResult(
            chain=chain,
            ticker=ticker.strip(),
            symbol=resolved.symbol,
            name=resolved.name,
            resolved_address=resolved.address,
            source=resolved.source,
            trust_tier=resolved.trust_tier,
            verified_onchain=resolved.verified_onchain,
            cg_recognized=resolved.cg_recognized,
            lifi_supported=resolved.lifi_supported,
            warning=resolved.warning,
            decimals=resolved.decimals,
        )
        return {"result": result.model_dump()}

    if parsed.operation == "list_supported_tokens":
        repo = runtime.token_resolver._repo if runtime.token_resolver is not None else None
        target = (parsed.list_supported_chain or "").strip().lower()
        if target:
            rows = list_on_chain(repository=repo, chain_slug=target)
            entries = [
                SupportedTokenEntry(
                    symbol=r.symbol,
                    name=r.name,
                    address=r.address,
                    trust_tier=r.trust_tier,
                    source=r.source,
                    lifi_supported=r.lifi_supported,
                )
                for r in rows
            ]
            out = SupportedTokensOnChainResult(
                chain=target,
                chain_id=chain_id_for(target),
                token_count=len(entries),
                tokens=entries,
            )
            return {"result": out.model_dump()}
        grouped = list_grouped_by_symbol(repository=repo)
        symbols = [
            SupportedSymbolGroup(
                symbol=sym,
                chains=[
                    SupportedTokenChainRef(
                        chain=r.chain_slug,
                        name=r.name,
                        address=r.address,
                        trust_tier=r.trust_tier,
                        source=r.source,
                        lifi_supported=r.lifi_supported,
                    )
                    for r in chain_rows
                ],
            )
            for sym, chain_rows in grouped.items()
        ]
        deployment_count = sum(len(g.chains) for g in symbols)
        out = SupportedTokensGroupedResult(
            symbol_count=len(symbols),
            deployment_count=deployment_count,
            symbols=symbols,
        )
        return {"result": out.model_dump()}

    if parsed.operation == "token_by_name":
        display_name = (parsed.token_name or "").strip()
        resolved = None
        if runtime.token_resolver is not None:
            resolved = runtime.token_resolver.resolve_name(chain, display_name)
        if resolved is None:
            hit = lookup_known_token_by_name(chain, display_name)
            if hit is None:
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message=(
                            "Unknown token name for this chain (not in the allowlist). "
                            "Ask for the ticker symbol (e.g. USDC) or the full contract address (0x…)."
                        ),
                        details={"token_name": display_name, "chain": chain},
                    ).model_dump()
                }
                cid = chain_id_for(chain)
                assert cid is not None
                result = KnownAddressResult(
                    chain=chain,
                    ticker=hit.symbol,
                    symbol=hit.symbol,
                    name=hit.name,
                    resolved_address=hit.address,
                    source="bundled",
                    trust_tier="curated",
                    verified_onchain=True,
                    cg_recognized=True,
                )
                return {"result": result.model_dump()}
        result = KnownAddressResult(
            chain=chain,
            ticker=resolved.symbol,
            symbol=resolved.symbol,
            name=resolved.name,
            resolved_address=resolved.address,
            source=resolved.source,
            trust_tier=resolved.trust_tier,
            verified_onchain=resolved.verified_onchain,
            cg_recognized=resolved.cg_recognized,
            lifi_supported=resolved.lifi_supported,
            warning=resolved.warning,
            decimals=resolved.decimals,
        )
        return {"result": result.model_dump()}

    if parsed.operation == "token_by_address":
        token_addr = parsed.token_address or ""
        if runtime.token_resolver is None:
            return {
                "error": GraphErrorBody(
                    code="not_implemented",
                    message="Token registry requires DATABASE_URL.",
                ).model_dump()
            }
        resolved, err = runtime.token_resolver.resolve_address(chain, token_addr)
        if err is not None:
            return {"error": GraphErrorBody(**err).model_dump()}
        if resolved is None:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Could not resolve token by address.",
                ).model_dump()
            }
        result = KnownAddressResult(
            chain=chain,
            ticker=resolved.symbol,
            symbol=resolved.symbol,
            name=resolved.name,
            resolved_address=resolved.address,
            source=resolved.source,
            trust_tier=resolved.trust_tier,
            verified_onchain=resolved.verified_onchain,
            cg_recognized=resolved.cg_recognized,
            lifi_supported=resolved.lifi_supported,
            warning=resolved.warning,
            decimals=resolved.decimals,
        )
        return {"result": result.model_dump()}

    if parsed.operation == "erc20_balance":
        rpc, err_body = _alchemy_rpc_or_error(runtime, chain)
        if err_body is not None:
            return {"error": err_body}
        try:
            wallet = normalize_evm_address(parsed.wallet_address or "")
            token = normalize_evm_address(parsed.token_address or "")
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid wallet or token address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
        try:
            decimals, balance_raw = fetch_erc20_decimals_and_balance_raw(
                runtime,
                chain_slug=chain,
                token_address=token,
                wallet_address=wallet,
                rpc=rpc,
            )
            if decimals is None or balance_raw is None:
                raise ValueError("could not decode balance or decimals")
        except ValueError:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="Could not decode ERC-20 balance or decimals eth_call result.",
                    details={"token_address": token, "wallet_address": wallet},
                ).model_dump()
            }
        except Exception:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="RPC eth_call for ERC-20 balanceOf/decimals failed.",
                ).model_dump()
            }

        cid = chain_id_for(chain)
        assert cid is not None
        out = Erc20BalanceResult(
            chain=chain,
            chain_id=cid,
            wallet_address=wallet,
            token_address=token,
            decimals=int(decimals),
            balance_raw=str(balance_raw),
            balance_human=format_token_units(balance_raw, int(decimals)),
        )
        return {"result": out.model_dump()}

    if parsed.operation == "erc20_decimals":
        rpc, err_body = _alchemy_rpc_or_error(runtime, chain)
        if err_body is not None:
            return {"error": err_body}
        token = normalize_evm_address(parsed.token_address or "")
        dec_val = get_cached_erc20_decimals(
            runtime,
            chain_slug=chain,
            token_address=token,
            rpc=rpc,
        )
        if dec_val is None:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="RPC eth_call for ERC-20 decimals() failed.",
                    details={"token_address": token},
                ).model_dump()
            }
        value = dec_val
        if value > 255:
            return {
                "error": GraphErrorBody(
                    code="rpc_error",
                    message="Token decimals() value is outside the 0-255 range.",
                    details={"token_address": token},
                ).model_dump()
            }

        cid = chain_id_for(chain)
        assert cid is not None
        out = Erc20DecimalsResult(
            chain=chain,
            chain_id=cid,
            token_address=token,
            decimals=value,
        )
        return {"result": out.model_dump()}

    rpc, err_body = _alchemy_rpc_or_error(runtime, chain)
    if err_body is not None:
        return {"error": err_body}

    try:
        wallet = normalize_evm_address(parsed.wallet_address or "")
        balance_hex = rpc.call("eth_getBalance", [wallet, "latest"])
        if not isinstance(balance_hex, str):
            raise TypeError("unexpected balance type")
        balance_wei = parse_evm_uint(balance_hex)
    except Exception:
        return {
            "error": GraphErrorBody(
                code="rpc_error",
                message="RPC read failed.",
            ).model_dump()
        }

    cid = chain_id_for(chain)
    assert cid is not None
    result = NativeBalanceResult(
        chain=chain,
        chain_id=cid,
        wallet_address=wallet,
        balance_wei_hex=balance_hex,
        balance_wei=balance_wei,
        balance_eth=format_token_units(balance_wei, 18),
    )
    return {"result": result.model_dump()}


def _route_after_validate(state: ReadGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_read_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(ReadGraphState)

    def validate(state: ReadGraphState) -> ReadGraphState:
        return _validate_node(state)

    def execute(state: ReadGraphState) -> ReadGraphState:
        return _execute_node(runtime, state)

    g.add_node("validate", validate)
    g.add_node("execute", execute)
    g.set_entry_point("validate")
    g.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"execute": "execute", "done": END},
    )
    g.add_edge("execute", END)
    return g.compile()
