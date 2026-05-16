"""Unit tests for :class:`~aurey.graphs.evm_tx_pipeline.Web3TxPipeline` (Web3 mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from eth_account import Account
from hexbytes import HexBytes

from aurey.custody import FakeSecretStore, OneClawSignTransactionResult
from aurey.graphs.evm_tx_pipeline import Web3TxPipeline
from aurey.graphs.results import PreparedTxEnvelope
from aurey.settings import AureySettings


def _mock_w3_for_success() -> MagicMock:
    mock_w3 = MagicMock()
    mock_w3.eth.chain_id = 8453
    mock_w3.eth.get_transaction_count.return_value = 0
    mock_w3.eth.estimate_gas.return_value = 21_000
    mock_w3.eth.max_priority_fee = 1_000_000_000
    mock_w3.eth.get_block.return_value = {"baseFeePerGas": 2_000_000_000}
    mock_w3.eth.get_balance.return_value = 10**20
    mock_w3.eth.call.return_value = b""
    mock_w3.eth.send_raw_transaction.return_value = HexBytes(b"\xab" * 32)
    mock_w3.eth.wait_for_transaction_receipt.return_value = {
        "status": 1,
        "blockNumber": 1_000,
        "gasUsed": 21_000,
    }
    return mock_w3


class _LocalOneClawSigner:
    """Signs tx_body locally like 1Claw would, for pipeline tests."""

    def __init__(self, signing_account: Account) -> None:
        self._acct = signing_account

    def sign_evm_transaction(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict,
        signing_key_path: str | None = None,
        authorization_bearer: str | None = None,
    ) -> OneClawSignTransactionResult:
        _ = signing_key_path, authorization_bearer
        key_hex = "0x" + self._acct.key.hex()
        signed = Account.sign_transaction(transaction, key_hex)
        raw = signed.raw_transaction
        raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
        return OneClawSignTransactionResult(
            signed_tx="0x" + raw_bytes.hex(),
            from_address=self._acct.address,
        )


def _oneclaw_envelope(
    *,
    from_address: str,
    chain_id: int = 8453,
    signing_key_secret_path: str | None = None,
) -> PreparedTxEnvelope:
    return PreparedTxEnvelope(
        kind="native_transfer",
        chain_id=chain_id,
        from_address=from_address,
        to="0x1111111111111111111111111111111111111111",
        data="0x",
        value_hex="0x0",
        gas_limit_hex=None,
        nonce=None,
        signing_mode="oneclaw_intents",
        signing_key_secret_path=signing_key_secret_path,
    )


def _envelope(*, signer: Account, chain_id: int = 8453) -> PreparedTxEnvelope:
    return PreparedTxEnvelope(
        kind="native_transfer",
        chain_id=chain_id,
        from_address=signer.address,
        to="0x1111111111111111111111111111111111111111",
        data="0x",
        value_hex="0x0",
        gas_limit_hex=None,
        nonce=None,
        signing_key_secret_path="vault/signing",
    )


def test_chain_name_for_id():
    from aurey.graphs.chains import chain_name_for_id

    assert chain_name_for_id(8453) == "base"
    assert chain_name_for_id(1) == "ethereum"
    assert chain_name_for_id(999_999) is None


def test_run_prepared_address_mismatch():
    signer = Account.create()
    other = Account.create()
    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _u: MagicMock(),
    )
    env = _envelope(signer=other)
    with pytest.raises(RuntimeError, match="policy_rejected"):
        pipeline.run_prepared(env, signing_key_material_hex=signer.key.hex())


def test_run_prepared_success_with_mock_w3():
    signer = Account.create()
    mock_w3 = _mock_w3_for_success()

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = _envelope(signer=signer)
    key_hex = "0x" + signer.key.hex()

    out = pipeline.run_prepared(env, signing_key_material_hex=key_hex)

    assert out.tx_hash.startswith("0x")
    assert len(out.tx_hash) == 66
    assert out.receipt.status == 1
    assert out.receipt.block_number == 1_000
    assert out.receipt.gas_used == 21_000
    mock_w3.eth.send_raw_transaction.assert_called_once()
    mock_w3.eth.wait_for_transaction_receipt.assert_called_once()


def test_run_prepared_success_with_alchemy_env_key_only():
    signer = Account.create()
    mock_w3 = _mock_w3_for_success()

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_key="test-alchemy-key"),
        secret_store=FakeSecretStore({}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = _envelope(signer=signer)
    key_hex = "0x" + signer.key.hex()

    out = pipeline.run_prepared(env, signing_key_material_hex=key_hex)

    assert out.tx_hash.startswith("0x")
    assert len(out.tx_hash) == 66
    assert out.receipt.status == 1


def test_run_prepared_with_oneclaw_signer_success():
    acct = Account.create()
    mock_w3 = _mock_w3_for_success()

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    class RecordingSigner(_LocalOneClawSigner):
        def __init__(self, account: Account) -> None:
            super().__init__(account)
            self.seen_chain: str | None = None
            self.seen_agent_id: str | None = None
            self.seen_signing_key_path: str | None = None

        def sign_evm_transaction(
            self,
            *,
            agent_id: str,
            chain: str,
            transaction: dict,
            signing_key_path: str | None = None,
            authorization_bearer: str | None = None,
        ) -> OneClawSignTransactionResult:
            self.seen_agent_id = agent_id
            self.seen_chain = chain
            self.seen_signing_key_path = signing_key_path
            return super().sign_evm_transaction(
                agent_id=agent_id,
                chain=chain,
                transaction=transaction,
                signing_key_path=signing_key_path,
                authorization_bearer=authorization_bearer,
            )

    signer = RecordingSigner(acct)
    env_with_key_path = _oneclaw_envelope(
        from_address=acct.address,
        signing_key_secret_path="wallets/hot-wallet",
    )
    out = pipeline.run_prepared_with_oneclaw_signer(
        env_with_key_path,
        signer,
        agent_id="agent-1",
    )

    assert signer.seen_chain == "base"
    assert signer.seen_agent_id == "agent-1"
    assert signer.seen_signing_key_path == "wallets/hot-wallet"

    assert out.tx_hash.startswith("0x")
    assert len(out.tx_hash) == 66
    assert out.receipt.status == 1
    assert out.stages == {
        "simulate": "ok",
        "policy": "ok",
        "sign": "ok",
        "broadcast": "ok",
    }
    mock_w3.eth.send_raw_transaction.assert_called_once()
    mock_w3.eth.wait_for_transaction_receipt.assert_called_once()


def test_run_prepared_with_oneclaw_signer_returns_wrong_from_address():
    acct = Account.create()
    other = Account.create()
    mock_w3 = _mock_w3_for_success()
    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = _oneclaw_envelope(from_address=acct.address)

    class WrongFromSigner:
        def sign_evm_transaction(
            self,
            *,
            agent_id: str,
            chain: str,
            transaction: dict,
            signing_key_path: str | None = None,
            authorization_bearer: str | None = None,
        ) -> OneClawSignTransactionResult:
            _ = agent_id
            _ = chain
            _ = transaction
            _ = signing_key_path
            _ = authorization_bearer
            return OneClawSignTransactionResult(
                signed_tx="0xabcd",
                from_address=other.address,
            )

    with pytest.raises(RuntimeError, match="policy_rejected.*from_address"):
        pipeline.run_prepared_with_oneclaw_signer(env, WrongFromSigner(), agent_id="a")


def test_run_prepared_with_oneclaw_signer_sign_exception():
    acct = Account.create()
    mock_w3 = _mock_w3_for_success()

    class BoomSigner:
        def sign_evm_transaction(
            self,
            *,
            agent_id: str,
            chain: str,
            transaction: dict,
            signing_key_path: str | None = None,
            authorization_bearer: str | None = None,
        ) -> None:
            _ = agent_id, chain, transaction, signing_key_path, authorization_bearer
            raise ValueError("1claw unreachable")

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = _oneclaw_envelope(from_address=acct.address)

    with pytest.raises(RuntimeError, match="policy_rejected.*signing failed"):
        pipeline.run_prepared_with_oneclaw_signer(env, BoomSigner(), agent_id="x")


def test_run_prepared_with_oneclaw_signer_broadcast_failed():
    acct = Account.create()
    mock_w3 = _mock_w3_for_success()
    mock_w3.eth.send_raw_transaction.side_effect = Exception("insufficient funds")

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _url: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = _oneclaw_envelope(from_address=acct.address)

    with pytest.raises(RuntimeError, match="broadcast_failed"):
        pipeline.run_prepared_with_oneclaw_signer(
            env, _LocalOneClawSigner(acct), agent_id="a"
        )


def test_simulation_failed_hint_for_erc20_balance_revert():
    from aurey.graphs.evm_tx_pipeline import _simulation_failed
    from aurey.graphs.results import SimulationFailed

    env = PreparedTxEnvelope(
        kind="erc20_transfer",
        chain_id=8453,
        from_address="0x" + "11" * 20,
        to="0x" + "22" * 20,
        data="0xa9059cbb",
        value_hex="0x0",
        gas_limit_hex=None,
        nonce=None,
        signing_key_secret_path="vault/signing",
    )
    with pytest.raises(SimulationFailed) as ei:
        _simulation_failed(
            env,
            Exception("execution reverted: ERC20: transfer amount exceeds balance"),
            step="gas estimation failed",
        )
    assert "6 decimals" in str(ei.value).lower() or "10_000" in str(ei.value)


def test_lifi_swap_eth_call_includes_transfer_from_diagnostics():
    from aurey.graphs.results import SimulationFailed

    signer = Account.create()
    mock_w3 = MagicMock()
    mock_w3.eth.chain_id = 8453
    mock_w3.eth.get_transaction_count.return_value = 0
    mock_w3.eth.estimate_gas.return_value = 500_000
    mock_w3.eth.max_priority_fee = 1_000_000_000
    mock_w3.eth.get_block.return_value = {"baseFeePerGas": 2_000_000_000}
    mock_w3.eth.get_balance.return_value = 10**20

    n = {"i": 0}

    def eth_call_side_effect(_tx: object) -> bytes:
        n["i"] += 1
        if n["i"] == 1:
            raise Exception("execution reverted: TransferHelper: TRANSFER_FROM_FAILED")
        if n["i"] == 2:
            return (500_000).to_bytes(32, "big")
        if n["i"] == 3:
            return (2_000_000).to_bytes(32, "big")
        raise AssertionError(f"unexpected eth.call #{n['i']}")

    mock_w3.eth.call.side_effect = eth_call_side_effect

    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path="alchemy/k"),
        secret_store=FakeSecretStore({"alchemy/k": "test-alchemy-key"}),
        web3_factory=lambda _u: mock_w3,
        receipt_timeout_s=5.0,
    )
    env = PreparedTxEnvelope(
        kind="lifi_swap",
        chain_id=8453,
        from_address=signer.address,
        to="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        data="0xcafe",
        value_hex="0x0",
        gas_limit_hex=None,
        nonce=None,
        signing_key_secret_path="vault/signing",
        lifi_sell_token="0x1111111111111111111111111111111111111111",
        lifi_approval_spender="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        lifi_sell_amount_raw="1000000",
    )
    with pytest.raises(SimulationFailed) as ei:
        pipeline.run_prepared(env, signing_key_material_hex="0x" + signer.key.hex())
    det = ei.value.details
    assert det is not None
    assert det["kind"] == "lifi_transfer_from_simulation"
    assert det["allowance_raw"] == "500000"
    assert det["balance_raw"] == "2000000"
    assert det["allowance_covers_quote_amount"] is False
    assert det["balance_covers_quote_amount"] is True

    signer = Account.create()
    pipeline = Web3TxPipeline(
        settings=AureySettings(alchemy_api_secret_path=None),
        secret_store=FakeSecretStore({}),
        web3_factory=lambda _u: MagicMock(),
    )
    with pytest.raises(RuntimeError, match="policy_rejected"):
        pipeline.run_prepared(_envelope(signer=signer), signing_key_material_hex=signer.key.hex())
