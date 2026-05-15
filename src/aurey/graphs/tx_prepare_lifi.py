"""LangGraph: LiFi ``swap_prepare`` output → ``PreparedTxEnvelope``."""

from __future__ import annotations

import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import AliasChoices, BaseModel, Field, ValidationError, model_validator

from aurey.graphs.chains import chain_id_for, chain_info
from aurey.graphs.evm_codec import normalize_evm_address
from aurey.graphs.lifi_envelope import lifi_transaction_request_to_envelope
from aurey.graphs.results import GraphErrorBody, LiFiAllowanceContext, LiFiPreparedTx
from aurey.graphs.swap_diag import SWAP_LOG, addr_short
from aurey.graphs.tx_prepare import _evm_prepare_signing_settings_error, _prepared_tx_signing_kwargs
from aurey.runtime import AureyRuntime


def _reason_clip(exc: BaseException, max_len: int = 160) -> str:
    return " ".join(str(exc).split())[:max_len]


class TxPrepareLiFiInput(BaseModel):
    """Build executable swap wiring from ``swap_prepare``; prefers ``prepared_id`` so calldata stays server-side (1Claw signing after prepare)."""

    chain: str = Field(min_length=1, description="Chain slug for the LiFi step.")
    from_address: str = Field(min_length=1, description="EOA that will send the swap tx (0x).")
    prepared: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Preferred: exact `prepared` from `swap_prepare` (`route_id` + `transaction_request`)."
        ),
    )
    prepared_id: str | None = Field(
        default=None,
        description=(
            "Server-side prepared id returned by swap_prepare; preferred because it avoids "
            "sending large LiFi calldata through the model."
        ),
    )
    route_id: str | None = Field(
        default=None,
        description="Alternative when `prepared` is awkward for the caller: LiFi step id string.",
    )
    transaction_request: dict[str, Any] | None = Field(
        default=None,
        description="LiFi transactionRequest dict; pair with `route_id` if no `prepared`.",
        validation_alias=AliasChoices("transaction_request", "transactionRequest"),
    )
    allowance_context: LiFiAllowanceContext | None = Field(
        default=None,
        description=(
            "Optional: ERC-20 allowance context from ``swap_prepare`` / ``earn_prepare_deposit`` "
            "(pass through verbatim)."
        ),
    )

    @model_validator(mode="after")
    def _ensure_prepared(self) -> TxPrepareLiFiInput:
        if self.prepared_id is not None and str(self.prepared_id).strip():
            return self
        if self.prepared is not None:
            return self
        if self.route_id is not None and self.transaction_request is not None:
            return self.model_copy(
                update={
                    "prepared": {
                        "route_id": self.route_id,
                        "transaction_request": dict(self.transaction_request),
                    }
                }
            )
        raise ValueError(
            "Provide `prepared_id` from swap_prepare, `prepared`, or both `route_id` and "
            "`transaction_request`."
        )


class TxPrepareLiFiGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="LiFi envelope prepare input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(
    runtime: AureyRuntime, state: TxPrepareLiFiGraphState
) -> TxPrepareLiFiGraphState:
    try:
        parsed = TxPrepareLiFiInput.model_validate(state.get("input") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}

    if chain_info(parsed.chain) is None:
        return {
            "error": GraphErrorBody(
                code="unsupported_chain",
                message=f"Unsupported chain '{parsed.chain}'.",
            ).model_dump()
        }

    err = _evm_prepare_signing_settings_error(runtime)
    if err:
        return {"error": err}

    try:
        normalize_evm_address(parsed.from_address)
    except ValueError as exc:
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="Invalid from_address.",
                details={"reason": str(exc)},
            ).model_dump()
        }

    try:
        if not parsed.prepared_id:
            LiFiPreparedTx.model_validate(parsed.prepared)
    except ValidationError as exc:
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="prepared dict is not a valid LiFi prepared payload.",
                details={"errors": exc.errors()},
            ).model_dump()
        }

    return {}


def _execute_node(runtime: AureyRuntime, state: TxPrepareLiFiGraphState) -> TxPrepareLiFiGraphState:
    if state.get("error"):
        return {}

    parsed = TxPrepareLiFiInput.model_validate(state["input"])
    chain = parsed.chain.strip().lower()
    cid = chain_id_for(chain)
    assert cid is not None
    signing = _prepared_tx_signing_kwargs(runtime)
    prepared = LiFiPreparedTx.model_validate(parsed.prepared)

    SWAP_LOG.info(
        "tx_prepare_lifi_graph start chain=%s chain_id=%s route_id=%s from=%s",
        chain,
        cid,
        prepared.route_id,
        addr_short(parsed.from_address),
    )
    t0 = time.perf_counter()
    try:
        env = lifi_transaction_request_to_envelope(
            chain_id=cid,
            from_address=parsed.from_address,
            transaction_request=dict(prepared.transaction_request),
            signing_mode=signing["signing_mode"],
            signing_key_secret_path=signing["signing_key_secret_path"],
            allowance_context=parsed.allowance_context,
        )
    except ValueError as exc:
        SWAP_LOG.info(
            "tx_prepare_lifi_graph fail code=envelope_value_error ms=%.1f reason=%s",
            (time.perf_counter() - t0) * 1000,
            _reason_clip(exc),
        )
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="Could not convert LiFi transaction request to an execute envelope.",
                details={"reason": str(exc)},
            ).model_dump()
        }
    ms = (time.perf_counter() - t0) * 1000
    SWAP_LOG.info(
        "tx_prepare_lifi_graph ok route_id=%s to=%s gas_limit_hex=%s ms=%.1f",
        prepared.route_id,
        addr_short(env.to),
        env.gas_limit_hex,
        ms,
    )
    return {"result": {"envelope": env.model_dump()}}


def _route_after_validate(state: TxPrepareLiFiGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_tx_prepare_lifi_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(TxPrepareLiFiGraphState)

    def validate(state: TxPrepareLiFiGraphState) -> TxPrepareLiFiGraphState:
        return _validate_node(runtime, state)

    def execute(state: TxPrepareLiFiGraphState) -> TxPrepareLiFiGraphState:
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


__all__ = [
    "TxPrepareLiFiInput",
    "build_tx_prepare_lifi_graph",
]
