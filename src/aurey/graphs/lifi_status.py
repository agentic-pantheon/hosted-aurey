"""LangGraph: LiFi cross-chain transfer status (GET /v1/status)."""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict
from urllib.parse import urlencode

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from aurey.custody.errors import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    secret_unavailable_graph_details,
)
from aurey.graphs.chains import chain_id_for
from aurey.graphs.ports import HttpJsonRequestError
from aurey.graphs.results import GraphErrorBody, LiFiStatusResult
from aurey.runtime import AureyRuntime

_log = logging.getLogger(__name__)

# LiFi blocks urllib default User-Agent; distinct UA string vs swap_prepare quote client.
_LIFI_STATUS_USER_AGENT = "Aurey/1.0 (LiFi status API; +https://docs.li.fi/)"

__all__ = ["LiFiStatusInput", "build_lifi_status_graph"]


class LiFiStatusInput(BaseModel):
    """``GET /v1/status`` on ``runtime.lifi_base_url``; optional ``x-lifi-api-key`` from settings."""

    tx_hash: str = Field(
        min_length=1,
        description="Sending-chain tx hash, destination tx hash, or LiFi step id.",
    )
    from_chain: str | None = Field(default=None, description="Source chain slug (Aurey registry).")
    to_chain: str | None = Field(default=None, description="Destination chain slug (Aurey registry).")
    from_chain_id: int | None = Field(default=None, ge=1, description="Numeric sending chain id.")
    to_chain_id: int | None = Field(default=None, ge=1, description="Numeric destination chain id.")
    bridge: str | None = Field(default=None, description="Bridging tool id (LiFi tools enum).")


class LiFiStatusGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="LiFi status input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _resolve_lifi_key(runtime: AureyRuntime) -> tuple[str | None, dict[str, Any] | None]:
    """Optional key when path unset; required resolution when ``lifi_api_secret_path`` is set."""

    path = runtime.settings.lifi_api_secret_path
    if path is None or not str(path).strip():
        return None, None
    path = str(path).strip()
    try:
        return runtime.secret_store.get_secret(path).reveal(), None
    except SecretNotFoundError:
        err = GraphErrorBody(
            code="secret_not_found",
            message="LiFi API secret could not be resolved.",
            details={"secret_kind": "lifi_api"},
        ).model_dump()
        return None, err
    except SecretStoreUnavailableError as exc:
        err = GraphErrorBody(
            code="secret_unavailable",
            message="Secret store unavailable while resolving LiFi API key.",
            details=secret_unavailable_graph_details(secret_kind="lifi_api", exc=exc),
        ).model_dump()
        return None, err


def _resolved_lifi_chain_query_value(
    *,
    label: str,
    chain: str | None,
    chain_id: int | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return chain id string for LiFi query params, or error dict."""

    chain_s = (str(chain).strip().lower() if chain is not None else "")
    has_slug = bool(chain_s)
    has_id = chain_id is not None

    if has_slug and has_id:
        cid_slug = chain_id_for(chain_s)
        if cid_slug is None:
            return None, GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported {label} '{chain}'.",
            ).model_dump()
        if cid_slug != chain_id:
            return None, GraphErrorBody(
                code="invalid_input",
                message=f"{label} slug and chain id disagree.",
                details={
                    "field": label,
                    "chain": chain_s,
                    "chain_id": chain_id,
                    "resolved_chain_id": cid_slug,
                },
            ).model_dump()
        return str(chain_id), None

    if has_id:
        return str(chain_id), None

    if has_slug:
        cid = chain_id_for(chain_s)
        if cid is None:
            return None, GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported {label} '{chain}'.",
            ).model_dump()
        return str(cid), None

    return None, None


def _lifi_status_http_error_details(exc: HttpJsonRequestError) -> dict[str, Any]:
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


def _trim_token(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for src, dst in (
        ("address", "address"),
        ("symbol", "symbol"),
        ("decimals", "decimals"),
        ("chainId", "chain_id"),
        ("name", "name"),
        ("coinKey", "coin_key"),
    ):
        if raw.get(src) is not None:
            out[dst] = raw[src]
    return out or None


def _trim_tx_info(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    tok = _trim_token(raw.get("token"))
    gas_tok = _trim_token(raw.get("gasToken"))
    out: dict[str, Any] = {}
    for src, dst in (
        ("txHash", "tx_hash"),
        ("txLink", "tx_link"),
        ("amount", "amount"),
        ("chainId", "chain_id"),
        ("value", "value"),
        ("timestamp", "timestamp"),
    ):
        if raw.get(src) is not None:
            out[dst] = raw[src]
    if tok:
        out["token"] = tok
    if gas_tok:
        out["gas_token"] = gas_tok
    return out or None


def _trim_metadata(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    if raw.get("integrator") is not None:
        out["integrator"] = raw["integrator"]
    return out or None


def _normalize_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": payload.get("status"),
        "substatus": payload.get("substatus"),
        "transaction_id": payload.get("transactionId"),
        "tool": payload.get("tool"),
        "lifi_explorer_link": payload.get("lifiExplorerLink"),
        "from_address": payload.get("fromAddress"),
        "to_address": payload.get("toAddress"),
        "sending": _trim_tx_info(payload.get("sending")),
        "receiving": _trim_tx_info(payload.get("receiving")),
    }
    meta = _trim_metadata(payload.get("metadata"))
    if meta:
        result["metadata"] = meta
    return {k: v for k, v in result.items() if v is not None}


def _validate_node(state: LiFiStatusGraphState) -> LiFiStatusGraphState:
    try:
        parsed = LiFiStatusInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    if not str(parsed.tx_hash).strip():
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="tx_hash must be non-empty.",
            ).model_dump()
        }

    fc = parsed.from_chain
    if fc is not None and not str(fc).strip():
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="from_chain, when set, must be non-empty.",
            ).model_dump()
        }
    tc = parsed.to_chain
    if tc is not None and not str(tc).strip():
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="to_chain, when set, must be non-empty.",
            ).model_dump()
        }
    if parsed.bridge is not None and not str(parsed.bridge).strip():
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="bridge, when set, must be non-empty.",
            ).model_dump()
        }

    _, err = _resolved_lifi_chain_query_value(
        label="from_chain",
        chain=parsed.from_chain,
        chain_id=parsed.from_chain_id,
    )
    if err is not None:
        return {"error": err}
    _, err = _resolved_lifi_chain_query_value(
        label="to_chain",
        chain=parsed.to_chain,
        chain_id=parsed.to_chain_id,
    )
    if err is not None:
        return {"error": err}

    return {}


def _execute_node(runtime: AureyRuntime, state: LiFiStatusGraphState) -> LiFiStatusGraphState:
    if state.get("error"):
        return {}

    parsed = LiFiStatusInput.model_validate(state["input"])
    api_key, err = _resolve_lifi_key(runtime)
    if err is not None:
        return {"error": err}

    from_qv, ferr = _resolved_lifi_chain_query_value(
        label="from_chain",
        chain=parsed.from_chain,
        chain_id=parsed.from_chain_id,
    )
    if ferr is not None:
        return {"error": ferr}
    to_qv, terr = _resolved_lifi_chain_query_value(
        label="to_chain",
        chain=parsed.to_chain,
        chain_id=parsed.to_chain_id,
    )
    if terr is not None:
        return {"error": terr}

    q: dict[str, str] = {"txHash": str(parsed.tx_hash).strip()}
    if from_qv is not None:
        q["fromChain"] = from_qv
    if to_qv is not None:
        q["toChain"] = to_qv
    if parsed.bridge is not None and str(parsed.bridge).strip():
        q["bridge"] = str(parsed.bridge).strip()

    url = f"{runtime.lifi_base_url.rstrip('/')}/v1/status?{urlencode(q)}"
    headers: dict[str, str] = {"User-Agent": _LIFI_STATUS_USER_AGENT}
    if api_key and str(api_key).strip():
        headers["x-lifi-api-key"] = str(api_key).strip()

    try:
        payload = runtime.http.request_json(
            method="GET",
            url=url,
            headers=headers,
            json_body=None,
        )
        if not isinstance(payload, dict):
            return {
                "error": GraphErrorBody(
                    code="http_error",
                    message="Unexpected LiFi status response shape.",
                    details={"got_type": type(payload).__name__},
                ).model_dump()
            }
        normalized = _normalize_status_payload(payload)
        status = LiFiStatusResult.model_validate(normalized)
        return {"result": status.model_dump(exclude_none=True)}
    except HttpJsonRequestError as exc:
        _log.debug("LiFi status HTTP error", exc_info=True)
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="LiFi rejected the status request (HTTP error).",
                details=_lifi_status_http_error_details(exc),
            ).model_dump()
        }
    except Exception:
        _log.exception("LiFi status request failed")
        return {
            "error": GraphErrorBody(
                code="http_error",
                message="LiFi status request failed.",
            ).model_dump()
        }


def _route_after_validate(state: LiFiStatusGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_lifi_status_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(LiFiStatusGraphState)

    def validate(state: LiFiStatusGraphState) -> LiFiStatusGraphState:
        return _validate_node(state)

    def execute(state: LiFiStatusGraphState) -> LiFiStatusGraphState:
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
