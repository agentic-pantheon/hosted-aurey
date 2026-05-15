"""Injectable boundaries for RPC, HTTP, and transaction execution."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from aurey.custody import OneClawEvmTransactionSigner
from aurey.graphs.results import PreparedTxEnvelope, TxExecuteResult


class HttpJsonRequestError(RuntimeError):
    """Non-2xx HTTP response with optional JSON body (e.g. LiFi ``message`` / ``code``)."""

    def __init__(
        self,
        *,
        status_code: int,
        body_text: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body_text = body_text
        self.payload = payload
        msg = f"HTTP {status_code}"
        if isinstance(payload, dict):
            if "message" in payload:
                msg = f"{msg}: {payload['message']}"
        elif body_text:
            msg = f"{msg}: {body_text[:500]}"
        super().__init__(msg)


@runtime_checkable
class EvmJsonRpcPort(Protocol):
    """Minimal JSON-RPC surface used by read/prepare flows."""

    def call(self, method: str, params: list[Any]) -> Any:
        """Perform a JSON-RPC call and return the decoded ``result`` payload."""


@runtime_checkable
class HttpJsonPort(Protocol):
    """HTTP client used by Alchemy/LiFi adapters (callers build URLs)."""

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Perform an HTTP request and return a parsed JSON body (object or array)."""


@runtime_checkable
class TxPipelinePort(Protocol):
    """Simulate, sign, and broadcast a prepared EVM transaction."""

    def run_prepared(
        self,
        envelope: PreparedTxEnvelope,
        *,
        signing_key_material_hex: str,
    ) -> TxExecuteResult:
        """Consume signing material in-process only; never embed it in the returned model."""

    def run_prepared_with_oneclaw_signer(
        self,
        envelope: PreparedTxEnvelope,
        signer: OneClawEvmTransactionSigner,
        *,
        agent_id: str,
    ) -> TxExecuteResult:
        """Sign via 1Claw and broadcast (``signing_mode`` oneclaw_intents)."""
