"""Map LiFi ``transactionRequest`` dicts into ``PreparedTxEnvelope``."""

from __future__ import annotations

from typing import Any

from aurey.graphs.evm_codec import (
    normalize_contract_calldata,
    normalize_evm_address,
    parse_evm_uint,
)
from aurey.graphs.results import EnvelopeSigningMode, LiFiAllowanceContext, PreparedTxEnvelope


def lifi_transaction_request_to_envelope(
    *,
    chain_id: int,
    from_address: str,
    transaction_request: dict[str, Any],
    signing_key_secret_path: str | None = None,
    signing_mode: EnvelopeSigningMode = "vault_key",
    allowance_context: LiFiAllowanceContext | None = None,
) -> PreparedTxEnvelope:
    """Normalize an ethers-style tx request from LiFi into our execute envelope.

    ``from`` / ``chainId`` fields on the LiFi payload, when present, must match expectations.

    For ``vault_key``, ``signing_key_secret_path`` is required downstream. For ``oneclaw_intents``,
    leave it unset to use 1Claw agent defaults, or set it to the same vault path as
    ``AUREY_WALLET_SIGNING_KEY_SECRET_PATH`` so 1Claw receives an explicit ``signing_key_path``.

    Optional ``allowance_context`` (from ``swap_prepare``) attaches sell token + spender metadata for
    richer simulation error diagnostics.
    """

    tr = transaction_request
    if not isinstance(tr, dict):
        raise ValueError("transaction_request must be an object.")

    to_raw = tr.get("to")
    if not to_raw:
        raise ValueError("transaction_request.to is required.")

    to_norm = normalize_evm_address(str(to_raw))
    from_norm = normalize_evm_address(from_address)

    if "from" in tr and tr["from"] is not None:
        li_from = normalize_evm_address(str(tr["from"]))
        if li_from != from_norm:
            raise ValueError("transaction_request.from does not match from_address.")

    if "chainId" in tr and tr["chainId"] is not None:
        raw_cid = tr["chainId"]
        if isinstance(raw_cid, str):
            cid = int(raw_cid, 0)
        else:
            cid = int(raw_cid)
        if cid != chain_id:
            raise ValueError("transaction_request.chainId does not match chain.")

    data_raw = tr.get("data")
    if data_raw is not None and not isinstance(data_raw, str):
        raise ValueError("transaction_request.data must be a hex string.")
    try:
        data = normalize_contract_calldata(data_raw if isinstance(data_raw, str) else None)
    except ValueError as exc:
        raise ValueError(f"transaction_request.data: {exc}") from exc

    value_raw = tr.get("value")
    if value_raw is None:
        value_int = 0
    else:
        value_int = parse_evm_uint(value_raw)

    value_hex = hex(value_int)

    gas_limit_hex: str | None = None
    gas_raw = tr.get("gasLimit")
    if gas_raw is None:
        gas_raw = tr.get("gas")
    if gas_raw is not None:
        gas_limit_hex = hex(parse_evm_uint(gas_raw))

    nonce: int | None = None
    if tr.get("nonce") is not None:
        nonce = int(parse_evm_uint(tr["nonce"]))

    if signing_mode == "vault_key":
        secret_path = signing_key_secret_path
    else:
        # Hosted-agent / 1Claw signing: omit path to use agent defaults, or pass a non-empty path as
        # 1Claw ``signing_key_path`` override (same env as ``wallet_signing_key_secret_path``).
        o = (signing_key_secret_path or "").strip()
        secret_path = o or None

    sell_token = approval_spender = sell_amt = None
    if allowance_context is not None:
        sell_token = allowance_context.token_address
        approval_spender = allowance_context.spender_address
        sell_amt = allowance_context.amount_raw

    return PreparedTxEnvelope(
        kind="lifi_swap",
        chain_id=chain_id,
        from_address=from_norm,
        to=to_norm,
        data=data,
        value_hex=value_hex,
        gas_limit_hex=gas_limit_hex,
        nonce=nonce,
        signing_mode=signing_mode,
        signing_key_secret_path=secret_path,
        lifi_sell_token=sell_token,
        lifi_approval_spender=approval_spender,
        lifi_sell_amount_raw=sell_amt,
    )
