"""LangGraph: Alchemy prices, portfolio, and transfer history (HTTP + key via SecretStore).

REST:
  - Prices — POST https://api.g.alchemy.com/prices/v1/{apiKey}/tokens/by-address
  - Portfolio ("Tokens By Wallet") — POST https://api.g.alchemy.com/data/v1/{apiKey}/assets/tokens/by-address

Transfers JSON-RPC:
  - POST https://{network}.g.alchemy.com/v2/{apiKey}  method ``alchemy_getAssetTransfers``
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from aurey.graphs.api_key_resolution import effective_alchemy_api_key
from aurey.graphs.cached_decimals import fetch_erc20_decimals_and_balance_raw
from aurey.graphs.chains import chain_id_for, chain_info
from aurey.graphs.checkpoint_serde import uint256_checkpoint_str
from aurey.graphs.evm_codec import (
    format_token_units,
    normalize_evm_address,
    parse_evm_uint,
)
from aurey.graphs.read import _alchemy_rpc_or_error
from aurey.graphs.results import (
    AlchemyPortfolioResult,
    AlchemyTokenPricesResult,
    AlchemyTransferHistoryResult,
    GraphErrorBody,
    NativeBalanceResult,
    UsdNotionalToTokenRawResult,
)
from aurey.runtime import AureyRuntime

# Wrapped native used as Alchemy **price feed** surrogate for sizing gas ETH notionals (`sell_kind=native_eth`).
_WRAPPED_ETH_BY_CHAIN_SLUG: dict[str, str] = {
    "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "avalanche": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
    "base": "0x4200000000000000000000000000000000000006",
    "bsc": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "gnosis": "0x6A023CCd1ff6F2045C3309768eAdE5d18d9f7f4b",
    "linea": "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf347",
    "polygon": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "scroll": "0x5300000000000000000000000000000000000004",
}


def _wrapped_native_contract(chain_slug: str) -> str | None:
    raw = chain_slug.strip().lower()
    w = _WRAPPED_ETH_BY_CHAIN_SLUG.get(raw)
    if not w:
        return None
    try:
        return normalize_evm_address(w)
    except ValueError:
        return None


def _alchemy_network(chain: str) -> str | None:
    info = chain_info(chain.strip().lower())
    return None if info is None else info.alchemy_network


class AlchemyGraphInput(BaseModel):
    operation: Literal[
        "token_prices",
        "portfolio_tokens",
        "transfer_history",
        "usd_notional_to_raw",
    ]
    chain: str = Field(min_length=1)
    wallet_address: str = Field(min_length=1)
    token_addresses: list[str] | None = None
    token_address: str | None = Field(
        default=None,
        description="ERC-20 contract (sell token) for usd_notional_to_raw.",
    )
    usd_notional: str | None = Field(
        default=None,
        description='USD notional as decimal text (e.g. "5" or "5.00") for usd_notional_to_raw.',
    )
    sell_kind: Literal["erc20", "native_eth"] | None = Field(
        default=None,
        description=(
            "For ``usd_notional_to_raw`` only — ``native_eth`` sizes gas ETH in wei from wrapped-native USD "
            "(ignores ``token_address``)."
        ),
    )


class AlchemyGraphState(TypedDict, total=False):
    input: dict[str, Any]
    parsed: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validate_ok(parsed: AlchemyGraphInput) -> AlchemyGraphState:
    return {"parsed": parsed.model_dump()}


def _load_parsed_input(state: AlchemyGraphState) -> AlchemyGraphInput:
    raw = state.get("parsed")
    if isinstance(raw, dict):
        return AlchemyGraphInput.model_validate(raw)
    return AlchemyGraphInput.model_validate(state["input"])


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Alchemy graph input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(state: AlchemyGraphState) -> AlchemyGraphState:
    try:
        parsed = AlchemyGraphInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    if chain_info(parsed.chain.strip().lower()) is None:
        return {
            "error": GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported chain '{parsed.chain}'.",
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

    if parsed.operation == "token_prices":
        addrs = parsed.token_addresses or []
        if not addrs:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="token_addresses is required for token_prices.",
                ).model_dump()
            }
    if parsed.operation == "usd_notional_to_raw":
        if not parsed.usd_notional or not str(parsed.usd_notional).strip():
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="usd_notional is required for usd_notional_to_raw.",
                ).model_dump()
            }
        sk = parsed.sell_kind or "erc20"
        if sk == "erc20":
            if not parsed.token_address or not str(parsed.token_address).strip():
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message="token_address is required for usd_notional_to_raw when sell_kind is erc20.",
                    ).model_dump()
                }
            try:
                normalize_evm_address(parsed.token_address)
            except ValueError as exc:
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message="Invalid token_address.",
                        details={"reason": str(exc)},
                    ).model_dump()
                }
        else:
            slug = parsed.chain.strip().lower()
            if _wrapped_native_contract(slug) is None:
                return {
                    "error": GraphErrorBody(
                        code="unsupported_chain",
                        message="native_eth USD sizing has no bundled wrapped-native catalog entry for this chain.",
                        details={"chain": slug},
                    ).model_dump()
                }
        try:
            usd_d = Decimal(str(parsed.usd_notional).strip())
        except InvalidOperation:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="usd_notional must be a positive decimal number.",
                ).model_dump()
            }
        if usd_d <= 0:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="usd_notional must be positive.",
                ).model_dump()
            }
    return _validate_ok(parsed)


def _resolve_alchemy_key(runtime: AureyRuntime) -> tuple[str | None, dict[str, Any] | None]:
    return effective_alchemy_api_key(runtime.settings, runtime.secret_store)


def _pick_usd_price(prices_raw: Any) -> str | None:
    """First USD price ``value``, else first available ``value``."""

    if not isinstance(prices_raw, list):
        return None
    fallback: str | None = None
    for row in prices_raw:
        if not isinstance(row, dict):
            continue
        val = row.get("value")
        if val is None:
            continue
        sval = str(val)
        cur = str(row.get("currency", "")).strip().upper()
        if cur == "USD":
            return sval
        if fallback is None:
            fallback = sval
    return fallback


def _parse_prices_payload(payload: dict[str, Any], token_addrs: list[str]) -> dict[str, str]:
    """Map normalized contract address → price string per Prices API ``data`` array."""

    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = rows.get("prices") or rows.get("results")
    if not isinstance(rows, list):
        return {}

    want = {normalize_evm_address(a) for a in token_addrs}
    out: dict[str, str] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            addr = normalize_evm_address(str(row.get("address") or ""))
        except ValueError:
            continue
        if want and addr not in want:
            continue
        err = row.get("error")
        if err:
            out[addr] = f"<error:{err}>"
            continue
        price = _pick_usd_price(row.get("prices"))
        if price is not None:
            out[addr] = price

    return out


def _decimal_plain_str(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _raw_amount_from_usd_notional(
    *,
    usd_notional: str,
    price_usd_str: str,
    decimals: int,
) -> tuple[int, Decimal]:
    if price_usd_str.strip().startswith("<error:"):
        raise ValueError("token price unavailable from Alchemy")
    usd = Decimal(str(usd_notional).strip())
    price = Decimal(str(price_usd_str).strip())
    if price <= 0:
        raise ValueError("non-positive token price")
    if usd <= 0:
        raise ValueError("non-positive usd notional")
    human = usd / price
    scale = Decimal(10) ** decimals
    raw_dec = (human * scale).to_integral_value(rounding=ROUND_DOWN)
    if raw_dec <= 0:
        raise ValueError(
            "usd_notional rounds to zero token base units at this price; increase USD amount."
        )
    if raw_dec > Decimal(2**256 - 1):
        raise ValueError("computed raw amount exceeds uint256")
    return int(raw_dec), human


def _coerce_decimals(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = parse_evm_uint(value) if isinstance(value, int | str) else int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 255 else None


def _token_decimals(token: dict[str, Any]) -> int | None:
    decimals = _coerce_decimals(token.get("decimals"))
    if decimals is not None:
        return decimals

    metadata = token.get("tokenMetadata")
    if isinstance(metadata, dict):
        return _coerce_decimals(metadata.get("decimals"))
    return None


def _normalize_portfolio_token(
    token: dict[str, Any], *, wallet_checksum: str
) -> dict[str, Any] | None:
    """Augment portfolio row with decoded balances; drop known bad rows."""

    faux = token.get("tokenAddress")
    wallet_ln = normalize_evm_address(wallet_checksum).lower()
    if faux is not None:
        faux_s = str(faux).strip()
        if faux_s:
            try:
                if normalize_evm_address(faux_s).lower() == wallet_ln:
                    raw_bal = token.get("tokenBalance")
                    if isinstance(raw_bal, int | str):
                        try:
                            if parse_evm_uint(raw_bal) == 0:
                                return None
                        except ValueError:
                            pass
            except ValueError:
                pass

    out = dict(token)
    raw_balance = token.get("tokenBalance")
    if not isinstance(raw_balance, int | str):
        return out

    try:
        balance_raw = parse_evm_uint(raw_balance)
    except ValueError:
        return out

    tp = token.get("tokenAddress")
    is_native_row = tp is None or str(tp).strip() == ""
    decimals = _token_decimals(token)
    if is_native_row and decimals is None:
        decimals = 18

    md = token.get("tokenMetadata")
    if isinstance(md, dict):
        md2 = dict(md)
        if is_native_row:
            md2.setdefault("symbol", md2.get("symbol") or "ETH")
            md2.setdefault("name", md2.get("name") or "Ether")
        out["tokenMetadata"] = md2

    # String: uint256 may exceed msgpack/orjson int64 range in LangGraph checkpoints.
    out["balance_raw"] = uint256_checkpoint_str(balance_raw)
    out["decimals"] = decimals
    out["balance_decimal"] = (
        format_token_units(balance_raw, decimals) if decimals is not None else None
    )
    return out


def _parse_portfolio_tokens(
    payload: dict[str, Any], *, wallet_checksum: str
) -> list[dict[str, Any]]:
    """Portfolio API nests tokens under ``data.tokens``."""

    data = payload.get("data")
    if isinstance(data, dict):
        tokens = data.get("tokens")
        if isinstance(tokens, list):
            rows: list[dict[str, Any]] = []
            for x in tokens:
                if not isinstance(x, dict):
                    continue
                normalized = _normalize_portfolio_token(
                    x, wallet_checksum=wallet_checksum
                )
                if normalized is not None:
                    rows.append(normalized)
            return rows
        return []
    return []


def _transfer_param_block(
    wallet: str,
    *,
    direction: Literal["from", "to"],
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "fromBlock": "0x0",
        "toBlock": "latest",
        "category": ["external", "erc20"],
        "withMetadata": True,
        "excludeZeroValue": True,
        "maxCount": "0x64",
        "order": "desc",
    }
    if direction == "from":
        block["fromAddress"] = wallet
    else:
        block["toAddress"] = wallet
    return block


def _post_asset_transfers(
    runtime: AureyRuntime,
    *,
    rpc_url: str,
    param_block: dict[str, Any],
) -> list[dict[str, Any]]:
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [param_block],
    }
    body = runtime.http.request_json(
        method="POST",
        url=rpc_url,
        headers={"Content-Type": "application/json"},
        json_body=req,
    )
    rpc_result = body.get("result") or {}
    if isinstance(rpc_result, str):
        return []
    transfers = rpc_result.get("transfers")
    if transfers is None:
        transfers = []
    if not isinstance(transfers, list):
        raise ValueError("unexpected transfers shape")
    return [dict(row) for row in transfers if isinstance(row, dict)]


def _merge_transfer_rows(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_uid: dict[str, dict[str, Any]] = {}
    for row in rows_a + rows_b:
        uid = str(row.get("uniqueId") or "")
        key = uid if uid else str(id(row))
        by_uid.setdefault(key, row)

    def sort_key(row: dict[str, Any]) -> int:
        raw = row.get("blockNum", "0x0")
        try:
            return int(str(raw), 16)
        except ValueError:
            return 0

    merged = sorted(by_uid.values(), key=sort_key, reverse=True)
    return merged


def _execute_node(runtime: AureyRuntime, state: AlchemyGraphState) -> AlchemyGraphState:
    if state.get("error"):
        return {}

    parsed = _load_parsed_input(state)
    api_key, err = _resolve_alchemy_key(runtime)
    if err is not None:
        return {"error": err}
    assert api_key is not None

    chain = parsed.chain.strip().lower()
    network = _alchemy_network(chain)
    if network is None:
        return {
            "error": GraphErrorBody(
                code="unsupported_chain",
                message="No Alchemy network mapping for this chain.",
                details={"chain": chain},
            ).model_dump()
        }

    wallet = normalize_evm_address(parsed.wallet_address)
    hdr_json = {"Content-Type": "application/json"}

    try:
        if parsed.operation == "token_prices":
            addrs_norm = [normalize_evm_address(a) for a in (parsed.token_addresses or [])]
            url = f"https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address"
            payload = runtime.http.request_json(
                method="POST",
                url=url,
                headers=hdr_json,
                json_body={
                    "addresses": [{"network": network, "address": a} for a in addrs_norm],
                },
            )
            if not isinstance(payload, dict):
                raise ValueError("unexpected prices envelope")
            prices = _parse_prices_payload(payload, addrs_norm)
            result = AlchemyTokenPricesResult(chain=chain, prices_by_address=prices)
            return {"result": result.model_dump()}

        if parsed.operation == "portfolio_tokens":
            url = f"https://api.g.alchemy.com/data/v1/{api_key}/assets/tokens/by-address"
            portfolio_json_body = {
                "addresses": [{"address": wallet, "networks": [network]}],
                "withMetadata": True,
                "withPrices": True,
                "includeNativeTokens": True,
                "includeErc20Tokens": True,
            }

            def _portfolio_payload() -> dict[str, Any] | list[Any]:
                return runtime.http.request_json(
                    method="POST",
                    url=url,
                    headers=hdr_json,
                    json_body=portfolio_json_body,
                )

            def _native_balance_payload() -> dict[str, Any] | None:
                rpc_local, err_native = _alchemy_rpc_or_error(runtime, chain)
                if err_native is not None or rpc_local is None:
                    return None
                try:
                    balance_hex = rpc_local.call("eth_getBalance", [wallet, "latest"])
                    if not isinstance(balance_hex, str):
                        return None
                    balance_wei = parse_evm_uint(balance_hex)
                    cid = chain_id_for(chain)
                    assert cid is not None
                    nb = NativeBalanceResult(
                        chain=chain,
                        chain_id=cid,
                        wallet_address=wallet,
                        balance_wei_hex=balance_hex,
                        balance_wei=balance_wei,
                        balance_eth=format_token_units(balance_wei, 18),
                    )
                    return nb.model_dump()
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                port_fut = pool.submit(_portfolio_payload)
                native_fut = pool.submit(_native_balance_payload)
                payload = port_fut.result()
                native_balance_payload = native_fut.result()

            tokens = _parse_portfolio_tokens(
                payload if isinstance(payload, dict) else {},
                wallet_checksum=wallet,
            )
            result = AlchemyPortfolioResult(
                chain=chain,
                wallet_address=wallet,
                tokens=tokens,
                native_balance=NativeBalanceResult.model_validate(native_balance_payload)
                if native_balance_payload
                else None,
            )
            return {"result": result.model_dump()}

        if parsed.operation == "usd_notional_to_raw":
            rpc_url = f"https://{network}.g.alchemy.com/v2/{api_key}"
            rpc_local = runtime.evm_rpc_factory(rpc_url)
            kind = parsed.sell_kind or "erc20"

            prices_url = f"https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address"
            hdr_json_px = hdr_json

            if kind == "native_eth":
                px_token_t = _wrapped_native_contract(chain)
                assert px_token_t is not None
                px_body = runtime.http.request_json(
                    method="POST",
                    url=prices_url,
                    headers=hdr_json_px,
                    json_body={"addresses": [{"network": network, "address": px_token_t}]},
                )
                if not isinstance(px_body, dict):
                    raise ValueError("unexpected prices envelope")
                prices_m = _parse_prices_payload(px_body, [px_token_t])
                price_str_n = prices_m.get(px_token_t)
                if price_str_n is None:
                    return {
                        "error": GraphErrorBody(
                            code="http_error",
                            message="No price returned for wrapped native (WETH surrogate).",
                            details={"reference_token_address": px_token_t},
                        ).model_dump()
                    }
                usd_sn = str(parsed.usd_notional).strip()
                try:
                    amount_raw_eth, human_eth = _raw_amount_from_usd_notional(
                        usd_notional=usd_sn,
                        price_usd_str=price_str_n,
                        decimals=18,
                    )
                except ValueError as exc:
                    return {
                        "error": GraphErrorBody(
                            code="invalid_input",
                            message=str(exc),
                        ).model_dump()
                    }
                eth_bal_raw: int | None = None
                try:
                    bal_hex_eth = rpc_local.call(
                        "eth_getBalance",
                        [wallet, "latest"],
                    )
                    if isinstance(bal_hex_eth, str):
                        eth_bal_raw = parse_evm_uint(bal_hex_eth)
                except Exception:
                    eth_bal_raw = None

                covers_eth: bool | None = (
                    eth_bal_raw >= amount_raw_eth
                    if eth_bal_raw is not None
                    else None
                )
                cid_eth = chain_id_for(chain)
                assert cid_eth is not None
                usd_norm_eth = _decimal_plain_str(Decimal(usd_sn))
                result_eth = UsdNotionalToTokenRawResult(
                    chain=chain,
                    chain_id=cid_eth,
                    wallet_address=wallet,
                    token_address=px_token_t,
                    usd_notional=usd_norm_eth,
                    price_usd=price_str_n,
                    decimals=18,
                    human_token_amount=_decimal_plain_str(human_eth),
                    amount_raw=str(amount_raw_eth),
                    wallet_balance_raw=str(eth_bal_raw) if eth_bal_raw is not None else None,
                    balance_covers_notional_amount=covers_eth,
                    sell_kind="native_eth",
                )
                return {"result": result_eth.model_dump()}

            token = normalize_evm_address(parsed.token_address or "")
            prices_url = f"https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address"
            hdr_json_px = hdr_json

            def _token_price_str() -> str:
                px_body_er = runtime.http.request_json(
                    method="POST",
                    url=prices_url,
                    headers=hdr_json_px,
                    json_body={"addresses": [{"network": network, "address": token}]},
                )
                if not isinstance(px_body_er, dict):
                    raise ValueError("unexpected prices envelope")
                prices_er = _parse_prices_payload(px_body_er, [token])
                price = prices_er.get(token)
                if price is None:
                    raise ValueError("no price")
                return price

            def _decimals_and_balance() -> tuple[int, int | None]:
                dec_val, bal_raw = fetch_erc20_decimals_and_balance_raw(
                    runtime,
                    chain_slug=chain,
                    token_address=token,
                    wallet_address=wallet,
                    rpc=rpc_local,
                )
                if dec_val is None:
                    raise ValueError("decimals unavailable")
                return dec_val, bal_raw

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    price_fut = pool.submit(_token_price_str)
                    chain_fut = pool.submit(_decimals_and_balance)
                    price_str_er = price_fut.result()
                    dec_val_t, bal_raw_er = chain_fut.result()
            except ValueError as exc:
                msg = str(exc)
                if "decimals" in msg:
                    return {
                        "error": GraphErrorBody(
                            code="rpc_error",
                            message="RPC eth_call for ERC-20 decimals() failed.",
                            details={"token_address": token},
                        ).model_dump()
                    }
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="No price returned for token.",
                        details={"token_address": token},
                    ).model_dump()
                }

            usd_se = str(parsed.usd_notional).strip()
            try:
                amount_raw_int, human = _raw_amount_from_usd_notional(
                    usd_notional=usd_se,
                    price_usd_str=price_str_er,
                    decimals=int(dec_val_t),
                )
            except ValueError as exc:
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message=str(exc),
                    ).model_dump()
                }

            bal_str_er = str(bal_raw_er) if bal_raw_er is not None else None
            covers_er: bool | None = (
                bal_raw_er >= amount_raw_int if bal_raw_er is not None else None
            )
            cid_er = chain_id_for(chain)
            assert cid_er is not None
            usd_norm_er = _decimal_plain_str(Decimal(usd_se))
            result_er = UsdNotionalToTokenRawResult(
                chain=chain,
                chain_id=cid_er,
                wallet_address=wallet,
                token_address=token,
                usd_notional=usd_norm_er,
                price_usd=price_str_er,
                decimals=int(dec_val_t),
                human_token_amount=_decimal_plain_str(human),
                amount_raw=str(amount_raw_int),
                wallet_balance_raw=bal_str_er,
                balance_covers_notional_amount=covers_er,
                sell_kind="erc20",
            )
            return {"result": result_er.model_dump()}

        rpc_url = f"https://{network}.g.alchemy.com/v2/{api_key}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            sent_fut = pool.submit(
                _post_asset_transfers,
                runtime,
                rpc_url=rpc_url,
                param_block=_transfer_param_block(wallet, direction="from"),
            )
            recv_fut = pool.submit(
                _post_asset_transfers,
                runtime,
                rpc_url=rpc_url,
                param_block=_transfer_param_block(wallet, direction="to"),
            )
            sent = sent_fut.result()
            received = recv_fut.result()
        xfer_models = _merge_transfer_rows(sent, received)
        result = AlchemyTransferHistoryResult(
            chain=chain, wallet_address=wallet, transfers=xfer_models
        )
        return {"result": result.model_dump()}
    except Exception:
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="Alchemy request failed.",
            ).model_dump()
        }


def _route_after_validate(state: AlchemyGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_alchemy_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(AlchemyGraphState)

    def validate(state: AlchemyGraphState) -> AlchemyGraphState:
        return _validate_node(state)

    def execute(state: AlchemyGraphState) -> AlchemyGraphState:
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
