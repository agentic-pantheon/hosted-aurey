"""LangGraph: LiFi Earn Data API reads (vault discovery + portfolio)."""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict
from urllib.parse import quote, urlencode

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from aurey.graphs.api_key_resolution import effective_lifi_api_key
from aurey.graphs.chains import chain_id_for, chain_name_for_id
from aurey.graphs.evm_codec import normalize_evm_address, to_checksum_evm_address
from aurey.graphs.ports import HttpJsonRequestError
from aurey.graphs.results import (
    EarnChainResult,
    EarnPortfolioPositionResult,
    EarnPortfolioPositionsResult,
    EarnProtocolResult,
    EarnVaultDetailResult,
    EarnVaultListResult,
    EarnVaultSummary,
    GraphErrorBody,
)
from aurey.runtime import AureyRuntime

_log = logging.getLogger(__name__)

# Earn API rejects urllib default User-Agent; distinct from LiFi quote client in swap_prepare.
_EARN_BASE_URL = "https://earn.li.fi"
_EARN_HTTP_USER_AGENT = "Aurey/1.0 (LiFi Earn Data API; +https://earn.li.fi/docs)"

__all__ = ["EarnGraphInput", "build_earn_graph"]


class EarnGraphInput(BaseModel):
    """LiFi Earn (`https://earn.li.fi`).

    Optional ``x-lifi-api-key`` from env or ``lifi_api_secret_path``.
    """

    operation: Literal[
        "list_chains",
        "list_protocols",
        "list_vaults",
        "get_vault",
        "portfolio_positions",
    ]
    chain: str | None = Field(
        default=None,
        description="Chain slug (resolved via chain_id_for when set).",
    )
    chain_id: int | None = Field(
        default=None,
        ge=1,
        description="Numeric chain id (Earn API / EVM).",
    )
    vault_address: str | None = Field(
        default=None,
        description="Vault contract for get_vault.",
    )
    wallet_address: str | None = Field(
        default=None,
        description="User wallet for portfolio_positions.",
    )
    asset: str | None = Field(
        default=None,
        min_length=1,
        description="list_vaults filter (address or symbol).",
    )
    protocol: str | None = Field(
        default=None,
        min_length=1,
        description="list_vaults protocol id filter.",
    )
    min_tvl_usd: float | None = Field(default=None, ge=0.0)
    is_transactional: bool | None = None
    is_redeemable: bool | None = None
    is_composer_supported: bool | None = None
    sort_by: Literal["apy", "tvl"] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class EarnGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Earn graph input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _resolve_lifi_key(runtime: AureyRuntime) -> tuple[str | None, dict[str, Any] | None]:
    """Optional LiFi key from env or vault (same semantics as ``swap_prepare``)."""

    return effective_lifi_api_key(runtime.settings, runtime.secret_store)


def _earn_headers(runtime: AureyRuntime, api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": _EARN_HTTP_USER_AGENT}
    if api_key and api_key.strip():
        headers["x-lifi-api-key"] = api_key.strip()
    return headers


def _earn_http_error_details(exc: HttpJsonRequestError) -> dict[str, Any]:
    pl = exc.payload if isinstance(exc.payload, dict) else {}
    code = pl.get("statusCode")
    if code is None:
        code = pl.get("code")
    message = pl.get("message")
    preview = (exc.body_text or "").strip()
    if not message and preview:
        message = preview[:800]
    details: dict[str, Any] = {
        "http_status": exc.status_code,
        "earn_status_code": code,
        "earn_message": str(message) if message is not None else None,
    }
    if preview and len(preview) > (len(str(message)) if message else 0):
        details["body_preview"] = preview[:400]
    return details


def _normalize_asset_filter(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    low = raw.lower()
    if low.startswith("0x") and len(low) == 42:
        try:
            return normalize_evm_address(raw)
        except ValueError:
            return raw
    return raw


def _resolved_chain_id_for_filters(
    chain: str | None,
    chain_id: int | None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Return effective chain id and optional GraphErrorBody dict."""

    if chain_id is not None and chain is not None and str(chain).strip():
        cid_slug = chain_id_for(str(chain).strip().lower())
        if cid_slug is None:
            return None, GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported chain '{chain}'.",
            ).model_dump()
        if cid_slug != chain_id:
            return None, GraphErrorBody(
                code="invalid_input",
                message="chain and chain_id disagree.",
                details={
                    "chain": str(chain).strip().lower(),
                    "chain_id": chain_id,
                    "resolved_chain_id": cid_slug,
                },
            ).model_dump()
        return chain_id, None

    if chain_id is not None:
        return chain_id, None

    if chain is not None and str(chain).strip():
        cid = chain_id_for(str(chain).strip().lower())
        if cid is None:
            return None, GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported chain '{chain}'.",
            ).model_dump()
        return cid, None

    return None, None


def _trim_token_row(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    row: dict[str, Any] = {}
    addr = raw.get("address")
    if addr is not None:
        row["address"] = addr
    sym = raw.get("symbol")
    if sym is not None:
        row["symbol"] = sym
    name = raw.get("name")
    if name is not None:
        row["name"] = name
    dec = raw.get("decimals")
    if dec is not None:
        row["decimals"] = dec
    weight = raw.get("weight")
    if weight is not None:
        row["weight"] = weight
    price = raw.get("priceUsd")
    if price is not None:
        row["price_usd"] = price
    return row


def _trim_protocol_core(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    if raw.get("id") is not None:
        out["id"] = raw["id"]
    if raw.get("name") is not None:
        out["name"] = raw["name"]
    if raw.get("logoUri") is not None:
        out["logo_uri"] = raw["logoUri"]
    if raw.get("url") is not None:
        out["url"] = raw["url"]
    return out


def _trim_analytics(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    apy = raw.get("apy")
    apy_out: dict[str, Any] = {}
    if isinstance(apy, dict):
        for k in ("base", "reward", "total"):
            if k in apy:
                apy_out[k] = apy[k]
    tvl = raw.get("tvl")
    tvl_usd = tvl.get("usd") if isinstance(tvl, dict) else None
    tvl_native = tvl.get("native") if isinstance(tvl, dict) else None
    out: dict[str, Any] = {
        "apy": apy_out,
        "apy_1d": raw.get("apy1d"),
        "apy_7d": raw.get("apy7d"),
        "apy_30d": raw.get("apy30d"),
        "tvl_usd": tvl_usd,
        "tvl_native": tvl_native,
        "updated_at": raw.get("updatedAt"),
    }
    return {k: v for k, v in out.items() if v not in (None, {})}


def _trim_vault_row(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cid = raw.get("chainId")
    row: dict[str, Any] = {
        "address": raw.get("address"),
        "network": raw.get("network"),
        "chain_id": cid,
        "slug": raw.get("slug"),
        "name": raw.get("name"),
        "protocol": _trim_protocol_core(raw.get("protocol")),
        "tags": raw.get("tags"),
        "analytics": _trim_analytics(raw.get("analytics")),
    }
    if isinstance(cid, int):
        slug = chain_name_for_id(cid)
        if slug:
            row["chain"] = slug
    row["is_transactional"] = raw.get("isTransactional")
    row["is_redeemable"] = raw.get("isRedeemable")
    row["is_composer_supported"] = raw.get("isComposerSupported")
    row["kyc"] = raw.get("kyc")
    row["time_lock"] = raw.get("timeLock")
    row["caps"] = raw.get("caps")
    row["verification_status"] = raw.get("verificationStatus")
    row["deposit_packs"] = raw.get("depositPacks")
    row["redeem_packs"] = raw.get("redeemPacks")
    row["synced_at"] = raw.get("syncedAt")
    ut = raw.get("underlyingTokens")
    if isinstance(ut, list):
        row["underlying_tokens"] = [_trim_token_row(x) for x in ut]
    lp = raw.get("lpTokens")
    if isinstance(lp, list):
        row["lp_tokens"] = [_trim_token_row(x) for x in lp]
    rw = raw.get("rewardTokens")
    if isinstance(rw, list):
        row["reward_tokens"] = [_trim_token_row(x) for x in rw]
    return {k: v for k, v in row.items() if v is not None}


def _trim_chain_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cid = raw.get("chainId")
    row: dict[str, Any] = {
        "name": raw.get("name"),
        "chain_id": cid,
        "network_caip": raw.get("networkCaip"),
    }
    if isinstance(cid, int):
        slug = chain_name_for_id(cid)
        if slug:
            row["chain"] = slug
    return {k: v for k, v in row.items() if v is not None}


def _trim_position(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    ast = raw.get("asset") if isinstance(raw.get("asset"), dict) else {}
    asset_out: dict[str, Any] = {}
    for k_src, k_dst in (
        ("address", "address"),
        ("name", "name"),
        ("symbol", "symbol"),
        ("decimals", "decimals"),
    ):
        if ast.get(k_src) is not None:
            asset_out[k_dst] = ast[k_src]
    return {
        "chain_id": raw.get("chainId"),
        "address": raw.get("address"),
        "protocol_name": raw.get("protocolName"),
        "asset": asset_out,
        "balance_usd": raw.get("balanceUsd"),
        "balance_native": raw.get("balanceNative"),
    }


def _dump_portfolio_position(row: EarnPortfolioPositionResult) -> dict[str, Any]:
    """Match `_trim_position`: top-level nulls preserved; ``asset`` omits null-only keys."""

    return {
        "chain_id": row.chain_id,
        "address": row.address,
        "protocol_name": row.protocol_name,
        "asset": row.asset.model_dump(exclude_none=True),
        "balance_usd": row.balance_usd,
        "balance_native": row.balance_native,
    }


def _validate_node(state: EarnGraphState) -> EarnGraphState:
    try:
        parsed = EarnGraphInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    op = parsed.operation

    if op in ("list_chains", "list_protocols"):
        return {}

    if op == "list_vaults":
        _, err = _resolved_chain_id_for_filters(parsed.chain, parsed.chain_id)
        if err is not None:
            return {"error": err}
        return {}

    if op == "get_vault":
        if not parsed.vault_address or not str(parsed.vault_address).strip():
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="vault_address is required for get_vault.",
                ).model_dump()
            }
        try:
            normalize_evm_address(parsed.vault_address)
        except ValueError as exc:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Invalid vault address.",
                    details={"reason": str(exc)},
                ).model_dump()
            }
        cid, err = _resolved_chain_id_for_filters(parsed.chain, parsed.chain_id)
        if err is not None:
            return {"error": err}
        if cid is None:
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="chain or chain_id is required for get_vault.",
                ).model_dump()
            }
        return {}

    if op == "portfolio_positions":
        if not parsed.wallet_address or not str(parsed.wallet_address).strip():
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="wallet_address is required for portfolio_positions.",
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
        return {}

    return {
        "error": GraphErrorBody(
            code="invalid_input",
            message="Unknown earn operation.",
        ).model_dump(),
    }


def _execute_node(runtime: AureyRuntime, state: EarnGraphState) -> EarnGraphState:
    if state.get("error"):
        return {}

    parsed = EarnGraphInput.model_validate(state["input"])
    api_key, err = _resolve_lifi_key(runtime)
    if err is not None:
        return {"error": err}

    base = _EARN_BASE_URL.rstrip("/")
    headers = _earn_headers(runtime, api_key)

    try:
        if parsed.operation == "list_chains":
            raw = runtime.http.request_json(
                method="GET",
                url=f"{base}/v1/chains",
                headers=headers,
                json_body=None,
            )
            if not isinstance(raw, list):
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="Unexpected Earn chains response shape.",
                        details={"got_type": type(raw).__name__},
                    ).model_dump()
                }
            chains = [
                EarnChainResult.model_validate(_trim_chain_item(x)).model_dump(exclude_none=True)
                for x in raw
                if isinstance(x, dict)
            ]
            return {"result": {"chains": chains}}

        if parsed.operation == "list_protocols":
            raw = runtime.http.request_json(
                method="GET",
                url=f"{base}/v1/protocols",
                headers=headers,
                json_body=None,
            )
            if not isinstance(raw, list):
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="Unexpected Earn protocols response shape.",
                        details={"got_type": type(raw).__name__},
                    ).model_dump()
                }
            protocols = [
                EarnProtocolResult.model_validate(_trim_protocol_core(x)).model_dump(exclude_none=True)
                for x in raw
                if isinstance(x, dict)
            ]
            return {"result": {"protocols": protocols}}

        if parsed.operation == "list_vaults":
            q: dict[str, str] = {}
            cid, cerr = _resolved_chain_id_for_filters(parsed.chain, parsed.chain_id)
            if cerr is not None:
                return {"error": cerr}
            if cid is not None:
                q["chainId"] = str(cid)
            asset = _normalize_asset_filter(parsed.asset)
            if asset:
                q["asset"] = asset
            if parsed.protocol and str(parsed.protocol).strip():
                q["protocol"] = str(parsed.protocol).strip()
            if parsed.min_tvl_usd is not None:
                q["minTvlUsd"] = str(parsed.min_tvl_usd)
            if parsed.is_transactional is not None:
                q["isTransactional"] = "true" if parsed.is_transactional else "false"
            if parsed.is_redeemable is not None:
                q["isRedeemable"] = "true" if parsed.is_redeemable else "false"
            if parsed.is_composer_supported is not None:
                q["isComposerSupported"] = "true" if parsed.is_composer_supported else "false"
            if parsed.sort_by is not None:
                q["sortBy"] = parsed.sort_by
            if parsed.limit is not None:
                q["limit"] = str(parsed.limit)
            if parsed.cursor and str(parsed.cursor).strip():
                q["cursor"] = str(parsed.cursor).strip()

            query = urlencode(q)
            url = f"{base}/v1/vaults?{query}" if query else f"{base}/v1/vaults"
            raw = runtime.http.request_json(method="GET", url=url, headers=headers, json_body=None)
            if not isinstance(raw, dict):
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="Unexpected Earn vault list response shape.",
                        details={"got_type": type(raw).__name__},
                    ).model_dump()
                }
            rows = raw.get("data")
            if not isinstance(rows, list):
                rows = []
            listed = EarnVaultListResult(
                vaults=[
                    EarnVaultSummary.model_validate(_trim_vault_row(x))
                    for x in rows
                    if isinstance(x, dict)
                ],
                total=raw.get("total"),
                normalized_at=raw.get("normalizedAt"),
                next_cursor=(raw.get("nextCursor") or None),
            )
            out: dict[str, Any] = {
                "vaults": [v.model_dump(exclude_none=True) for v in listed.vaults],
                "total": listed.total,
                "normalized_at": listed.normalized_at,
            }
            if listed.next_cursor:
                out["next_cursor"] = listed.next_cursor
            return {"result": out}

        if parsed.operation == "get_vault":
            cid, cerr = _resolved_chain_id_for_filters(parsed.chain, parsed.chain_id)
            if cerr is not None:
                return {"error": cerr}
            if cid is None:
                return {
                    "error": GraphErrorBody(
                        code="invalid_input",
                        message="chain or chain_id is required for get_vault.",
                    ).model_dump()
                }
            v_addr = normalize_evm_address(str(parsed.vault_address))
            path_addr = quote(to_checksum_evm_address(v_addr), safe="")
            raw = runtime.http.request_json(
                method="GET",
                url=f"{base}/v1/vaults/{cid}/{path_addr}",
                headers=headers,
                json_body=None,
            )
            if not isinstance(raw, dict):
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="Unexpected Earn vault response shape.",
                        details={"got_type": type(raw).__name__},
                    ).model_dump()
                }
            vault = EarnVaultSummary.model_validate(_trim_vault_row(raw))
            detail = EarnVaultDetailResult(vault=vault)
            return {"result": {"vault": detail.vault.model_dump(exclude_none=True)}}

        if parsed.operation == "portfolio_positions":
            w = normalize_evm_address(str(parsed.wallet_address))
            path_wallet = quote(w, safe="")
            raw = runtime.http.request_json(
                method="GET",
                url=f"{base}/v1/portfolio/{path_wallet}/positions",
                headers=headers,
                json_body=None,
            )
            if not isinstance(raw, dict):
                return {
                    "error": GraphErrorBody(
                        code="http_error",
                        message="Unexpected Earn portfolio response shape.",
                        details={"got_type": type(raw).__name__},
                    ).model_dump()
                }
            pos = raw.get("positions")
            if not isinstance(pos, list):
                pos = []
            positions = [
                EarnPortfolioPositionResult.model_validate(_trim_position(x))
                for x in pos
                if isinstance(x, dict)
            ]
            bundle = EarnPortfolioPositionsResult(positions=positions)
            dumped = [_dump_portfolio_position(p) for p in bundle.positions]
            return {"result": {"positions": dumped}}

        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="Unhandled earn operation.",
            ).model_dump()
        }

    except HttpJsonRequestError as exc:
        _log.debug("Earn HTTP error", exc_info=True)
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="LiFi Earn rejected the request (HTTP error).",
                details=_earn_http_error_details(exc),
            ).model_dump()
        }
    except Exception:
        _log.exception("Earn graph request failed")
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="Earn Data API request failed.",
            ).model_dump()
        }


def _route_after_validate(state: EarnGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_earn_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(EarnGraphState)

    def validate(state: EarnGraphState) -> EarnGraphState:
        return _validate_node(state)

    def execute(state: EarnGraphState) -> EarnGraphState:
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
