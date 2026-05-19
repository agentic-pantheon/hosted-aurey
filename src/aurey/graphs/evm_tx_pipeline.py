"""Web3-backed transaction pipeline (Mercury-inspired flow; no Mercury dependency)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from web3 import Web3
from web3.exceptions import TimeExhausted

from aurey.custody import OneClawEvmTransactionSigner
from aurey.custody.secret_store import SecretStore
from aurey.graphs.api_key_resolution import effective_alchemy_api_key
from aurey.graphs.chains import alchemy_rpc_url_for_chain, chain_name_for_id
from aurey.graphs.evm_codec import (
    erc20_allowance_calldata,
    erc20_balance_of_calldata,
    normalize_contract_calldata,
)
from aurey.graphs.ports import TxPipelinePort
from aurey.graphs.results import (
    PreparedTxEnvelope,
    SimulationFailed,
    TxExecuteResult,
    TxReceiptSummary,
)
from aurey.graphs.swap_diag import SWAP_LOG, addr_short
from aurey.settings import AureySettings

_PRIVATE_KEY_HEX = re.compile(r"^(?:0x)?[a-fA-F0-9]{64}$")


def _lifi_swap_simulation_hint_suffix(exc: BaseException) -> str:
    low = str(exc).lower()
    if "revert" in low or "execution reverted" in low:
        return (
            " Hint: For ERC-20 sells, ensure allowance: call swap_prepare and use "
            "`allowance` → tx_prepare_erc20_approval → tx_execute, then run the swap tx "
            "(refresh quote if the first swap simulation fails after approval). "
            "See `error.details` when present for on-chain allowance/balance vs the quoted "
            "sell amount."
        )
    if "hex" in low or "hex string" in low:
        return (
            " Hint: Calldata/value shape may be invalid. Re-run `swap_prepare`, then "
            "`tx_prepare_lifi_swap` with fresh `prepared`; do not pass truncated JSON or "
            "a lone `idempotency_key` to `tx_execute`."
        )
    return ""


def _transfer_from_revert_heuristic(exc: BaseException) -> bool:
    low = str(exc).lower()
    return (
        "transfer_from" in low
        or "transferfrom" in low
        or "transfer helper" in low
        or "safeerc20" in low
        or "ds-math-sub-underflow" in low
    )


def _lifi_transfer_from_simulation_details(
    envelope: PreparedTxEnvelope,
    *,
    w3: Web3,
    from_cs: str,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "kind": "lifi_transfer_from_simulation",
        "swap_tx_to": envelope.to,
    }
    sell = envelope.lifi_sell_token
    spender = envelope.lifi_approval_spender
    if not sell or not spender:
        details["note"] = (
            "Envelope lacks LiFi sell-token metadata (prepare via swap_prepare / "
            "earn_prepare_deposit so allowance_context is attached)."
        )
        return details
    try:
        token_cs = Web3.to_checksum_address(sell)
        owner_cs = Web3.to_checksum_address(from_cs)
        spender_cs = Web3.to_checksum_address(spender)
        ald = erc20_allowance_calldata(owner_cs, spender_cs)
        bald = erc20_balance_of_calldata(owner_cs)
        allow_out = w3.eth.call({"to": token_cs, "data": ald})
        bal_out = w3.eth.call({"to": token_cs, "data": bald})
        allow_raw = int.from_bytes(allow_out, "big")
        bal_raw = int.from_bytes(bal_out, "big")
        quote_amt = envelope.lifi_sell_amount_raw or ""
        details.update(
            {
                "sell_token": sell,
                "lifi_approval_spender": spender,
                "wallet": envelope.from_address,
                "balance_raw": str(bal_raw),
                "allowance_raw": str(allow_raw),
                "quote_sell_amount_raw": envelope.lifi_sell_amount_raw,
            }
        )
        if quote_amt.isdigit():
            rq = int(quote_amt)
            details["allowance_covers_quote_amount"] = allow_raw >= rq
            details["balance_covers_quote_amount"] = bal_raw >= rq
    except Exception as diag_exc:
        details["onchain_read_error"] = " ".join(str(diag_exc).split())[:240]
    return details


def _simulation_failed(
    envelope: PreparedTxEnvelope,
    exc: BaseException,
    *,
    step: str,
) -> None:
    msg = f"simulation_failed: {step} ({exc})"
    if envelope.kind in ("erc20_transfer", "erc20_approval"):
        low = str(exc).lower()
        if "exceeds balance" in low:
            msg += (
                " Hint: amount must be in the token's smallest units. USDC uses 6 decimals "
                "(0.01 USDC = 10_000 raw, not 10**16). Using ether-style 1e18 scaling on USDC "
                "mints a huge transfer and reverts with 'exceeds balance'."
            )
    elif envelope.kind == "lifi_swap":
        msg += _lifi_swap_simulation_hint_suffix(exc)
    raise SimulationFailed(msg) from exc


@dataclass(frozen=True)
class _PreparedUnsignedContext:
    """Context after unsigned tx simulation; ready for signing and broadcast."""

    w3: Web3
    chain_name: str
    tx_body: dict[str, Any]
    mark: Callable[..., None]


def _decode_signed_raw_tx_hex(signed_tx: str) -> bytes:
    stripped = signed_tx.strip()
    if not stripped:
        raise RuntimeError("policy_rejected: signer returned empty signed_tx.")
    if stripped.startswith("0x"):
        stripped = stripped[2:]
    try:
        return bytes.fromhex(stripped)
    except ValueError as exc:
        msg = f"policy_rejected: signer returned invalid signed_tx hex ({exc})."
        raise RuntimeError(msg) from exc


class Web3TxPipeline(TxPipelinePort):
    """Alchemy JSON-RPC via HTTP: estimate gas, fees, eth.call, local sign, send_rawTransaction."""

    def __init__(
        self,
        *,
        settings: AureySettings,
        secret_store: SecretStore,
        web3_factory: Callable[[str], Web3] | None = None,
        receipt_timeout_s: float = 120.0,
    ) -> None:
        self._settings = settings
        self._secret_store = secret_store
        self._receipt_timeout_s = receipt_timeout_s
        self._web3_factory = web3_factory or (
            lambda url: Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
        )

    def _prepare_unsigned_transaction(
        self, envelope: PreparedTxEnvelope
    ) -> _PreparedUnsignedContext:
        t_pipe = time.perf_counter()

        def mark(stage: str, **kw: Any) -> None:
            if envelope.kind != "lifi_swap":
                return
            tail = ("  " + "  ".join(f"{k}={v}" for k, v in sorted(kw.items()))) if kw else ""
            SWAP_LOG.info(
                "lifi_pipeline stage=%s elapsed_ms=%.1f chain_id=%s from=%s to=%s%s",
                stage,
                (time.perf_counter() - t_pipe) * 1000,
                envelope.chain_id,
                addr_short(envelope.from_address),
                addr_short(envelope.to),
                tail,
            )

        api_key, alchemy_err = effective_alchemy_api_key(self._settings, self._secret_store)
        if alchemy_err is not None:
            code = alchemy_err.get("code")
            if code == "secret_not_configured":
                raise RuntimeError(
                    "policy_rejected: alchemy_api_secret_path is required for "
                    "transaction broadcast."
                )
            if code == "secret_not_found":
                raise RuntimeError("policy_rejected: Alchemy API key secret not found.")
            raise RuntimeError(
                "policy_rejected: secret store unavailable while loading Alchemy API key."
            )

        api_key = (api_key or "").strip()
        if not api_key:
            raise RuntimeError("policy_rejected: Alchemy API key is empty.")

        mark("alchemy_key_ready")

        chain_name = chain_name_for_id(envelope.chain_id)
        if chain_name is None:
            raise RuntimeError("policy_rejected: unsupported chain_id for transaction execution.")

        rpc_url = alchemy_rpc_url_for_chain(chain_name, api_key)
        if not rpc_url:
            raise RuntimeError("policy_rejected: could not derive Alchemy RPC URL.")

        w3 = self._web3_factory(rpc_url)

        if int(w3.eth.chain_id) != envelope.chain_id:
            raise RuntimeError("simulation_failed: RPC chain id does not match envelope.")

        mark("web3_connected")

        from_cs = Web3.to_checksum_address(envelope.from_address)
        to_cs = Web3.to_checksum_address(envelope.to)
        value_wei = int(envelope.value_hex, 0)

        try:
            data = normalize_contract_calldata(envelope.data)
        except ValueError as exc:
            raise RuntimeError(f"policy_rejected: invalid calldata ({exc}).") from exc

        nonce = envelope.nonce
        if nonce is not None:
            nonce = int(nonce)
        if nonce is None:
            nonce = int(w3.eth.get_transaction_count(from_cs, "pending"))

        base: dict[str, Any] = {
            "chainId": envelope.chain_id,
            "from": from_cs,
            "to": to_cs,
            "value": value_wei,
            "nonce": nonce,
            "data": data,
        }

        if envelope.gas_limit_hex is not None:
            gas_limit = int(envelope.gas_limit_hex, 0)
        else:
            try:
                gas_limit = int(w3.eth.estimate_gas(base))
            except Exception as exc:
                _simulation_failed(envelope, exc, step="gas estimation failed")

        mark("gas_ready", gas_limit=gas_limit)

        fee_fields = _tx_fee_fields(w3)
        tx_body: dict[str, Any] = {**base, "gas": gas_limit, **fee_fields}

        max_fee_unit = int(tx_body.get("maxFeePerGas") or tx_body.get("gasPrice") or 0)
        balance = int(w3.eth.get_balance(from_cs))
        need = value_wei + gas_limit * max_fee_unit
        if balance < need:
            raise RuntimeError(
                "simulation_failed: insufficient native balance for value and maximum gas spend."
            )

        try:
            w3.eth.call(tx_body)
        except Exception as exc:
            msg = f"simulation_failed: eth_call simulation failed ({exc})"
            details = None
            if envelope.kind == "lifi_swap":
                msg += _lifi_swap_simulation_hint_suffix(exc)
                if _transfer_from_revert_heuristic(exc):
                    details = _lifi_transfer_from_simulation_details(
                        envelope, w3=w3, from_cs=from_cs
                    )
            raise SimulationFailed(msg, details=details) from exc

        mark("simulation_ok")

        return _PreparedUnsignedContext(
            w3=w3,
            chain_name=chain_name,
            tx_body=tx_body,
            mark=mark,
        )

    def _broadcast_signed_raw_and_wait(
        self,
        w3: Web3,
        raw_bytes: bytes,
        *,
        mark: Callable[..., None],
    ) -> tuple[str, TxReceiptSummary]:
        try:
            tx_hash = w3.eth.send_raw_transaction(raw_bytes)
        except Exception as exc:
            raise RuntimeError(f"broadcast_failed: {exc}") from exc

        tx_hash_hex = Web3.to_hex(tx_hash)
        mark("broadcast_submitted", tx_hash=tx_hash_hex)

        receipt = _wait_receipt(w3, tx_hash, self._receipt_timeout_s)
        mark(
            "receipt_done",
            status=receipt.status,
            block=receipt.block_number,
            gas_used=receipt.gas_used,
        )

        return tx_hash_hex, receipt

    def run_prepared(
        self,
        envelope: PreparedTxEnvelope,
        *,
        signing_key_material_hex: str,
    ) -> TxExecuteResult:
        key_hex = _normalize_signing_key_hex(signing_key_material_hex)
        try:
            acct_signer = Account.from_key(key_hex)
        except Exception as exc:
            raise RuntimeError("policy_rejected: invalid signing key material.") from exc

        if acct_signer.address.lower() != envelope.from_address.lower():
            raise RuntimeError("policy_rejected: signing key does not match from_address.")

        if envelope.kind == "lifi_swap":
            SWAP_LOG.info(
                "lifi_pipeline run_prepared begin chain_id=%s from=%s to=%s",
                envelope.chain_id,
                addr_short(envelope.from_address),
                addr_short(envelope.to),
            )

        prepared = self._prepare_unsigned_transaction(envelope)

        try:
            signed = Account.sign_transaction(prepared.tx_body, key_hex)
        except Exception as exc:
            raise RuntimeError(f"policy_rejected: transaction signing failed ({exc}).") from exc

        prepared.mark("signed")

        raw = signed.raw_transaction
        raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)

        tx_hash_hex, receipt = self._broadcast_signed_raw_and_wait(
            prepared.w3,
            raw_bytes,
            mark=prepared.mark,
        )

        return TxExecuteResult(
            tx_hash=tx_hash_hex,
            receipt=receipt,
            stages={
                "simulate": "ok",
                "policy": "ok",
                "sign": "ok",
                "broadcast": "ok",
            },
        )

    def run_prepared_with_oneclaw_signer(
        self,
        envelope: PreparedTxEnvelope,
        signer: OneClawEvmTransactionSigner,
        *,
        agent_id: str,
        authorization_bearer: str | None = None,
    ) -> TxExecuteResult:
        if envelope.kind == "lifi_swap":
            SWAP_LOG.info(
                "lifi_pipeline run_prepared_with_oneclaw_signer begin chain_id=%s from=%s to=%s",
                envelope.chain_id,
                addr_short(envelope.from_address),
                addr_short(envelope.to),
            )

        prepared = self._prepare_unsigned_transaction(envelope)

        try:
            signing_key_path = envelope.signing_key_secret_path
            sign_out = signer.sign_evm_transaction(
                agent_id=agent_id,
                chain=prepared.chain_name,
                transaction=prepared.tx_body,
                signing_key_path=signing_key_path,
                authorization_bearer=authorization_bearer,
            )
        except Exception as exc:
            raise RuntimeError(f"policy_rejected: transaction signing failed ({exc}).") from exc

        if sign_out.from_address is not None and sign_out.from_address.strip():
            if sign_out.from_address.strip().lower() != envelope.from_address.lower():
                raise RuntimeError(
                    "policy_rejected: signer from_address does not match envelope from_address."
                )

        raw_bytes = _decode_signed_raw_tx_hex(sign_out.signed_tx)
        prepared.mark("signed")

        tx_hash_hex, receipt = self._broadcast_signed_raw_and_wait(
            prepared.w3,
            raw_bytes,
            mark=prepared.mark,
        )

        return TxExecuteResult(
            tx_hash=tx_hash_hex,
            receipt=receipt,
            stages={
                "simulate": "ok",
                "policy": "ok",
                "sign": "ok",
                "broadcast": "ok",
            },
        )


def _tx_fee_fields(w3: Web3) -> dict[str, Any]:
    try:
        priority = int(w3.eth.max_priority_fee)
        latest = w3.eth.get_block("latest")
        base_fee = int(latest["baseFeePerGas"])
        max_fee = (base_fee * 2) + priority
        return {
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
        }
    except Exception:
        return {"gasPrice": int(w3.eth.gas_price)}


def _wait_receipt(w3: Web3, tx_hash: Any, timeout_s: float) -> TxReceiptSummary:
    try:
        rec = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_s)
    except TimeExhausted:
        return TxReceiptSummary(status=0, block_number=0, gas_used=0)

    st = int(rec.get("status", 0))
    bn = int(rec["blockNumber"])
    gu = int(rec["gasUsed"])
    return TxReceiptSummary(status=st, block_number=bn, gas_used=gu)


def _normalize_signing_key_hex(signing_key_material_hex: str) -> str:
    raw = signing_key_material_hex.strip()
    if not _PRIVATE_KEY_HEX.match(raw):
        raise RuntimeError("policy_rejected: signing key must be a 32-byte hex string.")
    return raw if raw.startswith("0x") else f"0x{raw}"


__all__ = ["Web3TxPipeline"]
