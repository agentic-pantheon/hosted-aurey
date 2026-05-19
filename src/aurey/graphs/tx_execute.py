"""LangGraph: validate and execute prepared envelopes via :class:`~aurey.runtime.AureyRuntime`."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ValidationError

from aurey.cloud.signing_context import current_hosted_signing_context
from aurey.custody import OneClawEvmTransactionSigner
from aurey.custody.errors import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    secret_unavailable_graph_details,
)
from aurey.graphs.ports import TxPipelinePort
from aurey.graphs.results import (
    GraphErrorBody,
    PreparedTxEnvelope,
    TxExecuteResult,
    TxReceiptSummary,
)
from aurey.runtime import AureyRuntime


class TxExecuteInput(BaseModel):
    envelope: dict[str, Any]
    idempotency_key: str | None = None


class TxExecuteGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Transaction execute input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(state: TxExecuteGraphState) -> TxExecuteGraphState:
    try:
        TxExecuteInput.model_validate(state.get("input") or {})
        PreparedTxEnvelope.model_validate((state.get("input") or {}).get("envelope") or {})
    except ValidationError as exc:
        return {"error": _validation_error(exc)}
    return {}


def _pipeline_runtime_error_response(exc: RuntimeError) -> dict[str, Any]:
    message = str(exc)
    code: Literal["simulation_failed", "policy_rejected", "broadcast_failed"]
    if message.startswith("simulation_failed"):
        code = "simulation_failed"
    elif message.startswith("policy_rejected"):
        code = "policy_rejected"
    elif message.startswith("broadcast_failed"):
        code = "broadcast_failed"
    else:
        code = "simulation_failed"
    details = getattr(exc, "details", None)
    return GraphErrorBody(
        code=code,
        message=message,
        details=details if isinstance(details, dict) else None,
    ).model_dump()


def _execute_node(runtime: AureyRuntime, state: TxExecuteGraphState) -> TxExecuteGraphState:
    if state.get("error"):
        return {}

    root = TxExecuteInput.model_validate(state["input"])
    envelope = PreparedTxEnvelope.model_validate(root.envelope)
    settings_mode = runtime.settings.evm_signing_mode
    if envelope.signing_mode != settings_mode:
        return {
            "error": GraphErrorBody(
                code="policy_rejected",
                message=(
                    f"Envelope signing_mode {envelope.signing_mode!r} does not match "
                    f"operator evm_signing_mode {settings_mode!r}."
                ),
                details=None,
            ).model_dump()
        }

    if envelope.signing_mode == "vault_key":
        key_path = envelope.signing_key_secret_path
        try:
            signing_material = runtime.secret_store.get_secret(key_path).reveal()
        except SecretNotFoundError:
            return {
                "error": GraphErrorBody(
                    code="secret_not_found",
                    message="Signing secret could not be resolved.",
                    details={"secret_kind": "signing_key"},
                ).model_dump()
            }
        except SecretStoreUnavailableError as exc:
            return {
                "error": GraphErrorBody(
                    code="secret_unavailable",
                    message="Secret store unavailable while resolving signing material.",
                    details=secret_unavailable_graph_details(secret_kind="signing_key", exc=exc),
                ).model_dump()
            }

        try:
            outcome = runtime.tx_pipeline.run_prepared(
                envelope,
                signing_key_material_hex=signing_material,
            )
        except RuntimeError as exc:
            return {"error": _pipeline_runtime_error_response(exc)}

        return {"result": outcome.model_dump()}

    signer = runtime.oneclaw_evm_signer
    if signer is None:
        return {
            "error": GraphErrorBody(
                code="secret_not_configured",
                message="OneClaw EVM transaction signer is not configured on this runtime.",
                details=None,
            ).model_dump()
        }

    settings = runtime.settings
    hctx = current_hosted_signing_context.get()
    delegated_bearer: str | None = None
    agent_id: str | None = None

    if settings.hosted_platform_enabled and hctx is not None:
        aid = (hctx.user_agent_id or "").strip()
        if not aid:
            return {
                "error": GraphErrorBody(
                    code="secret_not_configured",
                    message=(
                        "Hosted signing requires a provisioned user_agent_id for this Telegram user."
                    ),
                    details=None,
                ).model_dump()
            }
        agent_id = aid
        delegated_bearer = None
    else:
        legacy_agent = settings.oneclaw_agent_id
        if legacy_agent is None or not str(legacy_agent).strip():
            return {
                "error": GraphErrorBody(
                    code="secret_not_configured",
                    message="oneclaw_agent_id must be configured for oneclaw_intents execution.",
                    details=None,
                ).model_dump()
            }
        agent_id = str(legacy_agent).strip()

    assert agent_id is not None

    try:
        outcome = runtime.tx_pipeline.run_prepared_with_oneclaw_signer(
            envelope,
            signer,
            agent_id=agent_id,
            authorization_bearer=delegated_bearer,
        )
    except RuntimeError as exc:
        return {"error": _pipeline_runtime_error_response(exc)}

    return {"result": outcome.model_dump()}


def _route_after_validate(state: TxExecuteGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_tx_execute_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(TxExecuteGraphState)

    def validate(state: TxExecuteGraphState) -> TxExecuteGraphState:
        return _validate_node(state)

    def execute(state: TxExecuteGraphState) -> TxExecuteGraphState:
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


class DeterministicTxPipeline(TxPipelinePort):
    """Test-friendly pipeline with explicit stage hooks; never echoes signing material."""

    def __init__(
        self,
        *,
        fail_stage: Literal["simulate", "policy", "broadcast"] | None = None,
    ) -> None:
        self._fail_stage = fail_stage

    def _deterministic_success(self, envelope: PreparedTxEnvelope) -> TxExecuteResult:
        payload = "|".join(
            [
                str(envelope.chain_id),
                envelope.kind,
                envelope.from_address,
                envelope.to,
                envelope.data,
                envelope.value_hex,
            ]
        )
        tx_hash = "0x" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        receipt = TxReceiptSummary(status=1, block_number=12_345, gas_used=21_000)
        stages = {
            "simulate": "ok",
            "policy": "ok",
            "sign": "ok",
            "broadcast": "ok",
        }
        return TxExecuteResult(tx_hash=tx_hash, receipt=receipt, stages=stages)

    def _maybe_raise_fail_stage(self) -> None:
        if self._fail_stage == "simulate":
            raise RuntimeError("simulation_failed: deterministic test failure")
        if self._fail_stage == "policy":
            raise RuntimeError("policy_rejected: deterministic test failure")
        if self._fail_stage == "broadcast":
            raise RuntimeError("broadcast_failed: deterministic test failure")

    def run_prepared(
        self,
        envelope: PreparedTxEnvelope,
        *,
        signing_key_material_hex: str,
    ) -> TxExecuteResult:
        _ = signing_key_material_hex  # would feed a real signer in production
        self._maybe_raise_fail_stage()
        return self._deterministic_success(envelope)

    def run_prepared_with_oneclaw_signer(
        self,
        envelope: PreparedTxEnvelope,
        signer: OneClawEvmTransactionSigner,
        *,
        agent_id: str,
        authorization_bearer: str | None = None,
    ) -> TxExecuteResult:
        """Exercise the signer for fakes; deterministic outcomes ignore real signatures."""

        from aurey.graphs.chains import chain_name_for_id

        ch = chain_name_for_id(envelope.chain_id) or "base"
        vraw = envelope.value_hex or "0x0"
        try:
            v = int(vraw, 0)
        except ValueError:
            v = 0
        signer.sign_evm_transaction(
            agent_id=agent_id,
            chain=ch,
            transaction={
                "to": envelope.to,
                "data": envelope.data or "0x",
                "value": v,
                "nonce": 0,
                "gas": 21_000,
                "maxFeePerGas": 30_000_000_000,
                "maxPriorityFeePerGas": 2_000_000_000,
            },
            signing_key_path=envelope.signing_key_secret_path,
            authorization_bearer=authorization_bearer,
        )
        self._maybe_raise_fail_stage()
        return self._deterministic_success(envelope)
