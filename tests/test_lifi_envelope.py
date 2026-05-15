"""Liability tests for LiFi ``transactionRequest`` → :class:`PreparedTxEnvelope`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aurey.graphs.evm_codec import normalize_contract_calldata
from aurey.graphs.lifi_envelope import lifi_transaction_request_to_envelope
from aurey.graphs.results import PreparedTxEnvelope


def test_normalize_contract_calldata_lowercases_and_rejects_odd_length():
    assert normalize_contract_calldata("0xAbCdEf") == "0xabcdef"
    with pytest.raises(ValueError, match="odd length"):
        normalize_contract_calldata("0xabc")


def test_normalize_contract_calldata_strips_embedded_whitespace():
    inner = "abcd" * 16  # 64 hex chars (32 bytes)
    raw = "0x095ea7b3\n" + inner[:32] + " " + inner[32:]
    assert normalize_contract_calldata(raw) == "0x095ea7b3" + inner.lower()


def test_lifi_mapper_oneclaw_intents_omits_signing_key_path_when_unset():
    env = lifi_transaction_request_to_envelope(
        chain_id=8453,
        from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        transaction_request={
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "data": "0x",
            "value": 0,
            "chainId": 8453,
        },
        signing_mode="oneclaw_intents",
        signing_key_secret_path=None,
    )
    assert env.signing_mode == "oneclaw_intents"
    assert env.signing_key_secret_path is None


def test_lifi_mapper_oneclaw_intents_passes_through_signing_key_path_override():
    env = lifi_transaction_request_to_envelope(
        chain_id=8453,
        from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        transaction_request={
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "data": "0x",
            "value": 0,
            "chainId": 8453,
        },
        signing_mode="oneclaw_intents",
        signing_key_secret_path="aurey/wallets/primary/signing_key",
    )
    assert env.signing_mode == "oneclaw_intents"
    assert env.signing_key_secret_path == "aurey/wallets/primary/signing_key"


def test_lifi_mapper_accepts_numeric_chain_id_and_value():
    env = lifi_transaction_request_to_envelope(
        chain_id=8453,
        from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        transaction_request={
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "data": "0x",
            "value": 0,
            "chainId": 8453,
        },
        signing_key_secret_path="vault/k",
    )
    assert env.signing_mode == "vault_key"
    assert env.kind == "lifi_swap"
    assert env.chain_id == 8453
    assert env.value_hex == "0x0"


def test_lifi_mapper_preserves_even_length_function_selector():
    env = lifi_transaction_request_to_envelope(
        chain_id=8453,
        from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        transaction_request={
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "data": "0x5fd9ae2ee8bd",
            "value": 0,
            "chainId": 8453,
        },
        signing_key_secret_path="vault/k",
    )
    assert env.data.startswith("0x5fd9ae2e")


def test_lifi_mapper_rejects_odd_length_calldata():
    with pytest.raises(ValueError, match="transaction_request.data: .*odd length"):
        lifi_transaction_request_to_envelope(
            chain_id=8453,
            from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            transaction_request={
                "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "data": "0xabc",
                "value": 0,
                "chainId": 8453,
            },
            signing_key_secret_path="vault/k",
        )


def test_lifi_mapper_rejects_chain_id_mismatch():
    with pytest.raises(ValueError, match="chainId"):
        lifi_transaction_request_to_envelope(
            chain_id=8453,
            from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            transaction_request={
                "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "data": "0x",
                "value": 0,
                "chainId": 1,
            },
            signing_key_secret_path="vault/k",
        )


def test_lifi_mapper_rejects_from_mismatch():
    with pytest.raises(ValueError, match="from_address"):
        lifi_transaction_request_to_envelope(
            chain_id=8453,
            from_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            transaction_request={
                "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "data": "0x",
                "value": 0,
                "from": "0xcccccccccccccccccccccccccccccccccccccccc",
            },
            signing_key_secret_path="vault/k",
        )


_ADDR_A = "0x" + "aa" * 20
_ADDR_B = "0x" + "bb" * 20


def test_prepared_tx_envelope_legacy_dict_without_signing_mode():
    env = PreparedTxEnvelope.model_validate(
        {
            "kind": "native_transfer",
            "chain_id": 1,
            "from_address": _ADDR_A,
            "to": _ADDR_B,
            "data": "0x",
            "value_hex": "0x0",
            "signing_key_secret_path": "vault/k",
        }
    )
    assert env.signing_mode == "vault_key"
    assert env.signing_key_secret_path == "vault/k"


def test_prepared_tx_envelope_vault_key_requires_nonempty_secret_path():
    with pytest.raises(ValidationError):
        PreparedTxEnvelope(
            kind="native_transfer",
            chain_id=1,
            from_address=_ADDR_A,
            to=_ADDR_B,
            data="0x",
            value_hex="0x0",
            signing_mode="vault_key",
            signing_key_secret_path=None,
        )
    with pytest.raises(ValidationError):
        PreparedTxEnvelope(
            kind="native_transfer",
            chain_id=1,
            from_address=_ADDR_A,
            to=_ADDR_B,
            data="0x",
            value_hex="0x0",
            signing_mode="vault_key",
            signing_key_secret_path="   ",
        )


def test_prepared_tx_envelope_oneclaw_intents_secret_path_optional():
    env = PreparedTxEnvelope(
        kind="native_transfer",
        chain_id=1,
        from_address=_ADDR_A,
        to=_ADDR_B,
        data="0x",
        value_hex="0x0",
        signing_mode="oneclaw_intents",
    )
    assert env.signing_key_secret_path is None

    env2 = PreparedTxEnvelope(
        kind="native_transfer",
        chain_id=1,
        from_address=_ADDR_A,
        to=_ADDR_B,
        data="0x",
        value_hex="0x0",
        signing_mode="oneclaw_intents",
        signing_key_secret_path=None,
    )
    assert env2.signing_key_secret_path is None
