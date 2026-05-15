"""Wires settings, SecretStore, and injectable client factories for graphs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from aurey.custody import OneClawEvmTransactionSigner
from aurey.custody.secret_store import OneClawHttpClient, SecretStore
from aurey.graphs.ports import EvmJsonRpcPort, HttpJsonPort, TxPipelinePort
from aurey.principal import UserPrincipal
from aurey.settings import AureySettings

PreparedPayloadKind = Literal["execute_envelope", "lifi_prepared"]


@dataclass(frozen=True)
class PreparedTransactionRecord:
    """Server-side transaction payload hidden from the LLM context."""

    prepared_id: str
    kind: PreparedPayloadKind
    payload: dict[str, Any]
    summary: dict[str, Any]
    created_monotonic: float


class PreparedTransactionStore:
    """Small in-memory store for large prepared tx payloads."""

    def __init__(self, *, max_entries: int = 128, ttl_s: float = 900.0) -> None:
        self._max_entries = max_entries
        self._ttl_s = ttl_s
        self._records: dict[str, PreparedTransactionRecord] = {}
        self._lock = Lock()

    def put(
        self,
        *,
        kind: PreparedPayloadKind,
        payload: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> str:
        now = monotonic()
        prepared_id = f"ptx_{uuid4().hex}"
        record = PreparedTransactionRecord(
            prepared_id=prepared_id,
            kind=kind,
            payload=dict(payload),
            summary=dict(summary or {}),
            created_monotonic=now,
        )
        with self._lock:
            self._purge_locked(now)
            self._records[prepared_id] = record
            while len(self._records) > self._max_entries:
                oldest = min(self._records.values(), key=lambda r: r.created_monotonic)
                self._records.pop(oldest.prepared_id, None)
        return prepared_id

    def get(self, prepared_id: str) -> PreparedTransactionRecord | None:
        pid = prepared_id.strip()
        if not pid:
            return None
        now = monotonic()
        with self._lock:
            self._purge_locked(now)
            return self._records.get(pid)

    def _purge_locked(self, now: float) -> None:
        expired = [
            pid
            for pid, record in self._records.items()
            if now - record.created_monotonic > self._ttl_s
        ]
        for pid in expired:
            self._records.pop(pid, None)


@dataclass(frozen=True)
class AureyRuntime:
    """Process-level dependencies; secret values are revealed only inside graph nodes."""

    settings: AureySettings
    secret_store: SecretStore
    evm_rpc_factory: Callable[[str], EvmJsonRpcPort]
    http: HttpJsonPort
    tx_pipeline: TxPipelinePort
    oneclaw_evm_signer: OneClawEvmTransactionSigner | None = None
    # Operator 1Claw HTTP session (same instance as signer when using OneClawHttpClient).
    oneclaw_operator_http: OneClawHttpClient | None = None
    principal: UserPrincipal | None = None
    lifi_base_url: str = "https://li.quest"
    prepared_txs: PreparedTransactionStore = field(default_factory=PreparedTransactionStore)
