"""LangGraph: LiFi-style swap preparation (injectable HTTP; API key via SecretStore)."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, TypedDict
from urllib.parse import urlencode

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from aurey.graphs.api_key_resolution import effective_lifi_api_key
from aurey.graphs.chains import chain_id_for, chain_info
from aurey.graphs.evm_codec import (
    decode_abi_uint256_word,
    erc20_allowance_calldata,
    normalize_evm_address,
)
from aurey.graphs.ports import HttpJsonRequestError
from aurey.graphs.read import _alchemy_rpc_or_error
from aurey.graphs.results import (
    GraphErrorBody,
    LiFiAllowanceContext,
    LiFiAllowanceHint,
    LiFiPreparedTx,
    SwapPrepareResult,
)
from aurey.graphs.swap_diag import SWAP_LOG, addr_short
from aurey.runtime import AureyRuntime

_log = logging.getLogger(__name__)

# LiFi returns 403 for urllib's default ``Python-urllib/...`` user-agent (edge/WAF).
_LIFI_HTTP_USER_AGENT = "Aurey/1.0 (LiFi API client; +https://docs.li.fi/)"

# LiFi EVM native sell token (`fromToken`); see docs.li.fi cross-chain ETH examples.
_LIFI_NATIVE_EVM_TOKEN_ADDRESS = normalize_evm_address(
    "0x0000000000000000000000000000000000000000"
)


class SwapPrepareInput(BaseModel):
    """LiFi swap quote (`GET /v1/quote`).

    Optional LiFi key from env or ``lifi_api_secret_path``.
    """

    from_chain: str = Field(min_length=1, description="Source chain slug.")
    to_chain: str = Field(min_length=1, description="Destination chain slug.")
    from_asset: str = Field(
        min_length=1,
        description=(
            "Token contract (0x…) or symbol for LiFi ``fromToken``. "
            "Selling **native gas ETH**: use phrases like «native ETH» — Aurey maps this to LiFi native "
            "``0x000…000``. Selling **already-wrapped** WETH: pass WETH ticker or its contract "
            "``0x…`` — not rewritten to native. Do not use LiFi for WETH→WETH."
        ),
    )
    to_asset: str = Field(
        min_length=1,
        description=(
            "Token contract (0x…) or symbol as accepted by LiFi ``toToken``. "
            "Phrases like «native ETH» on the **to_chain** are rewritten to that chain's wrapped "
            "native (e.g. Base WETH) because LiFi requires an ERC-20 ``toToken``."
        ),
    )
    from_amount_wei: str = Field(min_length=1, pattern=r"^[0-9]+$")
    from_address: str = Field(min_length=1)
    to_address: str = Field(min_length=1)
    slippage: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Max slippage as decimal (e.g. 0.005 = 0.5%%). Omit for LiFi default.",
    )
    order: Literal["FASTEST", "CHEAPEST"] | None = Field(
        default=None,
        description="LiFi route preference; omit for LiFi default sorting.",
    )


class SwapGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Swap prepare input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(state: SwapGraphState) -> SwapGraphState:
    try:
        parsed = SwapPrepareInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    for label, chain in (("from_chain", parsed.from_chain), ("to_chain", parsed.to_chain)):
        if chain_info(chain) is None:
            return {
                "error": GraphErrorBody(
                    code="unsupported_chain",
                    message=f"Unsupported {label} '{chain}'.",
                ).model_dump()
            }

    try:
        normalize_evm_address(parsed.from_address)
        normalize_evm_address(parsed.to_address)
    except ValueError as exc:
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="Invalid swap address.",
                details={"reason": str(exc)},
            ).model_dump()
        }

    return {}


def _resolve_lifi_key(runtime: AureyRuntime) -> tuple[str | None, dict[str, Any] | None]:
    return effective_lifi_api_key(runtime.settings, runtime.secret_store)


def _normalize_lifi_token_param(value: str) -> str:
    """Normalize hex token addresses; pass symbols / other ids through unchanged."""

    raw = value.strip()
    low = raw.lower()
    if low.startswith("0x") and len(low) == 42:
        try:
            return normalize_evm_address(raw)
        except ValueError:
            return raw
    return raw


def _collapse_token_phrase(text: str) -> str:
    s = text.strip().lower()
    for c in "\u2018\u2019":  # unicode apostrophes
        s = s.replace(c, " ")
    s = s.replace("'", " ")
    return " ".join(s.split())


# Common "wrapped Ether" used as LiFi ERC-20 when the user asks for chain native ETH.
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


def _means_native_eth_intent(text: str) -> bool:
    """True for natural-language «native ETH» labels models often pass instead of WETH/0x."""

    raw = text.strip()
    low = raw.lower()
    if low.startswith("0x") and len(low) == 42:
        return False
    tokens = _collapse_token_phrase(raw).split()
    if not tokens:
        return False
    if "weth" in tokens:
        return False
    if "native" not in tokens:
        return False
    return ("eth" in tokens) or ("ether" in tokens) or ("ethereum" in tokens)


def _resolve_wrapped_eth_for_chain(for_chain: str) -> str:
    """Canonical wrapped native (WETH) address for LiFi ``toToken`` when user wants ETH out."""

    slug = for_chain.strip().lower()
    wrapped = _WRAPPED_ETH_BY_CHAIN_SLUG.get(slug)
    if wrapped is not None:
        return normalize_evm_address(wrapped)
    return "WETH"


def _resolve_lifi_token_param(
    value: str,
    *,
    for_chain: str,
    role: Literal["from", "to"],
) -> str:
    """Map user asset labels to LiFi ``fromToken`` / ``toToken`` strings."""

    raw = value.strip()
    if _means_native_eth_intent(raw):
        if role == "from":
            return _LIFI_NATIVE_EVM_TOKEN_ADDRESS
        return _resolve_wrapped_eth_for_chain(for_chain)
    return _normalize_lifi_token_param(raw)


def _lifi_quote_query_params(
    *,
    parsed: SwapPrepareInput,
    from_cid: int,
    to_cid: int,
    runtime: AureyRuntime,
) -> dict[str, str]:
    """Build flat query dict for ``GET /v1/quote`` (LiFi OpenAPI)."""

    params: dict[str, str] = {
        "fromChain": str(from_cid),
        "toChain": str(to_cid),
        "fromToken": _resolve_lifi_token_param(
            parsed.from_asset, for_chain=parsed.from_chain, role="from"
        ),
        "toToken": _resolve_lifi_token_param(
            parsed.to_asset, for_chain=parsed.to_chain, role="to"
        ),
        "fromAmount": str(parsed.from_amount_wei),
        "fromAddress": normalize_evm_address(parsed.from_address),
        "toAddress": normalize_evm_address(parsed.to_address),
    }
    if parsed.slippage is not None:
        params["slippage"] = str(parsed.slippage)
    if parsed.order is not None:
        params["order"] = parsed.order
    integrator = (runtime.settings.lifi_integrator or "").strip()
    if integrator:
        params["integrator"] = integrator
    return params


def _lifi_quote_http_error_details(exc: HttpJsonRequestError) -> dict[str, Any]:
    """Normalize LiFi / CDN error JSON and plain-text bodies for tool-facing ``details``."""

    li = exc.payload if isinstance(exc.payload, dict) else {}
    code: Any = li.get("code")
    if code is None:
        code = li.get("errorCode")

    message: Any = li.get("message")
    if message is None:
        err = li.get("error")
        if isinstance(err, str):
            message = err
        elif isinstance(err, dict):
            message = err.get("message")

    preview = (exc.body_text or "").strip()
    if not message and preview:
        message = preview[:800]

    details: dict[str, Any] = {
        "http_status": exc.status_code,
        "lifi_code": code,
        "lifi_message": str(message) if message is not None else None,
    }
    if preview and len(preview) > (len(str(message)) if message else 0):
        details["body_preview"] = preview[:400]
    return details


def _enrich_lifi_http_error_hints(
    details: dict[str, Any],
    *,
    sells_native_gas_eth: bool,
) -> dict[str, Any]:
    """Add agent-facing remediation hints for common LiFi failures."""

    code = details.get("lifi_code")
    code_i: int | None
    if isinstance(code, int):
        code_i = code
    elif isinstance(code, str) and code.strip().isdigit():
        code_i = int(code.strip())
    else:
        code_i = None

    if code_i == 1011:
        details["aurey_hint"] = (
            "fromToken equals toToken. For wrapping gas ETH, use ``from_asset`` phrasing «native ETH» "
            "(LiFi native 0x0…0) with ``to_asset`` WETH contract or ticker — not two identical WETH sides."
        )
    elif code_i == 1001:
        parts = [
            "LiFi found no executable route — amount may be below the protocol minimum, "
            "temporary liquidity/route issues, or ``fromAmount`` may not match native-wei sizing.",
        ]
        if sells_native_gas_eth:
            parts.append(
                "When selling native gas ETH, ``from_amount_wei`` must be **ETH wei (18 decimals)**. "
                "For USD notionals call ``compute_token_amount_from_usd`` with ``sell_kind=native_eth``."
            )
        details["aurey_hint"] = " ".join(parts)
    return details


def _same_resolved_lifi_tokens(a: str, b: str) -> bool:
    """Return True when resolved LiFi ``fromToken`` / ``toToken`` would collide (same-route error)."""

    aa = str(a).strip()
    bb = str(b).strip()
    native_aliases = {
        _LIFI_NATIVE_EVM_TOKEN_ADDRESS.lower(),
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    }
    al = aa.lower()
    bl = bb.lower()
    ca = _LIFI_NATIVE_EVM_TOKEN_ADDRESS.lower() if al in native_aliases else al
    cb = _LIFI_NATIVE_EVM_TOKEN_ADDRESS.lower() if bl in native_aliases else bl
    if len(ca) == 42 and ca.startswith("0x") and len(cb) == 42 and cb.startswith("0x"):
        return ca == cb
    return aa.strip().upper() == bb.strip().upper()


def _lifi_allowance_hint(payload: dict[str, Any]) -> LiFiAllowanceHint | None:
    """If LiFi returned an approval spender, surface ERC-20 approve params for the agent."""

    est = payload.get("estimate")
    if not isinstance(est, dict):
        return None
    spender = est.get("approvalAddress")
    if not spender:
        return None

    action = payload.get("action")
    if not isinstance(action, dict):
        return None
    from_tok = action.get("fromToken")
    if not isinstance(from_tok, dict):
        return None
    token_addr = from_tok.get("address")
    if not token_addr:
        return None
    raw = str(token_addr).strip().lower()
    if raw in (
        "0x0000000000000000000000000000000000000000",
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    ):
        return None

    from_amt = action.get("fromAmount")
    if from_amt is None or str(from_amt).strip() == "":
        return None
    amt_str = str(from_amt).strip()
    if not amt_str.isdigit():
        return None

    try:
        return LiFiAllowanceHint(
            token_address=normalize_evm_address(str(token_addr)),
            spender_address=normalize_evm_address(str(spender)),
            amount_raw=amt_str,
        )
    except ValueError:
        return None


def _onchain_allowance_for_hint(
    runtime: AureyRuntime,
    chain: str,
    owner: str,
    hint: LiFiAllowanceHint,
) -> int | None:
    """Return current allowance if Alchemy RPC works; ``None`` to skip filtering."""

    rpc, err_body = _alchemy_rpc_or_error(runtime, chain)
    if err_body is not None or rpc is None:
        return None
    token = hint.token_address
    spender = hint.spender_address
    try:
        data = erc20_allowance_calldata(owner, spender)
        raw = rpc.call(
            "eth_call",
            [{"to": token, "data": data}, "latest"],
        )
        if not isinstance(raw, str):
            return None
        return decode_abi_uint256_word(raw)
    except Exception:
        _log.debug("ERC-20 allowance eth_call failed; keeping LiFi allowance hint", exc_info=True)
        return None


def _allowance_context_and_actionable_hint(
    runtime: AureyRuntime,
    chain: str,
    owner: str,
    hint: LiFiAllowanceHint | None,
) -> tuple[LiFiAllowanceContext | None, LiFiAllowanceHint | None]:
    """Build always-on allowance context.

    Omit actionable ``allowance`` only when on-chain allowance is enough.
    """

    if hint is None:
        return None, None
    required = int(hint.amount_raw)
    current = _onchain_allowance_for_hint(runtime, chain, owner, hint)
    ctx = LiFiAllowanceContext(
        token_address=hint.token_address,
        spender_address=hint.spender_address,
        amount_raw=hint.amount_raw,
        current_allowance_raw=str(current) if current is not None else None,
        allowance_sufficient=(current >= required) if current is not None else None,
    )
    if current is not None and current >= required:
        return ctx, None
    return ctx, hint


def _execute_node(runtime: AureyRuntime, state: SwapGraphState) -> SwapGraphState:
    if state.get("error"):
        return {}

    parsed = SwapPrepareInput.model_validate(state["input"])
    api_key, err = _resolve_lifi_key(runtime)
    if err is not None:
        return {"error": err}

    lifi_key_header = api_key.strip() if api_key else ""

    from_cid = chain_id_for(parsed.from_chain)
    to_cid = chain_id_for(parsed.to_chain)
    if from_cid is None or to_cid is None:
        return {
            "error": GraphErrorBody(
                code="unsupported_chain",
                message="Could not resolve LiFi chain ids.",
            ).model_dump()
        }

    # GET /v1/quote per https://docs.li.fi/llms.txt and OpenAPI (integrator, slippage, order, …).
    q = _lifi_quote_query_params(
        parsed=parsed, from_cid=from_cid, to_cid=to_cid, runtime=runtime
    )
    if _same_resolved_lifi_tokens(q["fromToken"], q["toToken"]):
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message=(
                    "Cannot quote swap with identical fromToken and toToken. "
                    "For wrapping gas ETH use from_asset «native ETH» and to_asset WETH; "
                    "to sell WETH balance, set from_asset to WETH (ticker or contract), not native ETH."
                ),
                details={
                    "fromToken": q["fromToken"],
                    "toToken": q["toToken"],
                },
            ).model_dump()
        }
    url = f"{runtime.lifi_base_url.rstrip('/')}/v1/quote?{urlencode(q)}"
    headers: dict[str, str] = {"User-Agent": _LIFI_HTTP_USER_AGENT}
    if lifi_key_header:
        headers["x-lifi-api-key"] = lifi_key_header
    t_wall = time.perf_counter()
    SWAP_LOG.info(
        "swap_prepare_graph start from_chain=%s to_chain=%s from_asset=%s to_asset=%s "
        "amount_wei=%s from=%s to=%s",
        parsed.from_chain.strip().lower(),
        parsed.to_chain.strip().lower(),
        parsed.from_asset,
        parsed.to_asset,
        parsed.from_amount_wei,
        addr_short(parsed.from_address),
        addr_short(parsed.to_address),
    )
    lifi_http_ms = -1.0
    try:
        t_lifi = time.perf_counter()
        try:
            payload = runtime.http.request_json(
                method="GET",
                url=url,
                headers=headers,
                json_body=None,
            )
        finally:
            lifi_http_ms = (time.perf_counter() - t_lifi) * 1000
        tx_request = payload.get("transactionRequest") or payload.get("tx")
        if not isinstance(tx_request, dict) or not tx_request.get("to"):
            SWAP_LOG.info(
                "swap_prepare_graph fail code=no_tx_req lifi_http_ms=%.1f total_ms=%.1f",
                lifi_http_ms,
                (time.perf_counter() - t_wall) * 1000,
            )
            return {
                "error": GraphErrorBody(
                    code="swap_prepare_failed",
                    message="LiFi quote did not include an executable transaction request.",
                    details={
                        "lifi_step_id": payload.get("id"),
                        "keys": sorted(str(k) for k in payload.keys()),
                    },
                ).model_dump()
            }
        route_id = str(payload.get("routeId") or payload.get("id") or "lifi-route")
        prepared = LiFiPreparedTx(route_id=route_id, transaction_request=dict(tx_request))
        owner = normalize_evm_address(parsed.from_address)
        t_allow = time.perf_counter()
        try:
            al_ctx, hint = _allowance_context_and_actionable_hint(
                runtime,
                parsed.from_chain.strip().lower(),
                owner,
                _lifi_allowance_hint(payload),
            )
        finally:
            allowance_phase_ms = (time.perf_counter() - t_allow) * 1000
        result = SwapPrepareResult(
            prepared=prepared, allowance=hint, allowance_context=al_ctx
        )
        total_ms = (time.perf_counter() - t_wall) * 1000
        SWAP_LOG.info(
            "swap_prepare_graph ok route_id=%s lifi_http_ms=%.1f allowance_phase_ms=%.1f "
            "needs_erc20_approve=%s total_ms=%.1f",
            route_id,
            lifi_http_ms,
            allowance_phase_ms,
            hint is not None,
            total_ms,
        )
        return {"result": result.model_dump()}
    except HttpJsonRequestError as exc:
        _log.debug("LiFi quote HTTP error", exc_info=True)
        SWAP_LOG.info(
            "swap_prepare_graph fail code=http_error lifi_http_ms=%.1f total_ms=%.1f status=%s",
            lifi_http_ms,
            (time.perf_counter() - t_wall) * 1000,
            getattr(exc, "status_code", None),
        )
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="LiFi rejected the quote request (HTTP error).",
                details=_enrich_lifi_http_error_hints(
                    _lifi_quote_http_error_details(exc),
                    sells_native_gas_eth=_means_native_eth_intent(parsed.from_asset),
                ),
            ).model_dump()
        }
    except Exception:
        _log.exception("LiFi swap preparation failed")
        SWAP_LOG.info(
            "swap_prepare_graph fail code=exception lifi_http_ms=%.1f total_ms=%.1f",
            lifi_http_ms,
            (time.perf_counter() - t_wall) * 1000,
        )
        return {
            "error": GraphErrorBody(
                code="swap_prepare_failed",
                message="LiFi swap preparation failed.",
            ).model_dump()
        }


def _route_after_validate(state: SwapGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_swap_prepare_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(SwapGraphState)

    def validate(state: SwapGraphState) -> SwapGraphState:
        return _validate_node(state)

    def execute(state: SwapGraphState) -> SwapGraphState:
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
