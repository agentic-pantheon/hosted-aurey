"""Structured graph outputs and stable error codes (no secret values)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from aurey.graphs.evm_codec import normalize_contract_calldata

GraphErrorCode = Literal[
    "needs_approval",
    "ens_not_found",
    "unsupported_chain",
    "simulation_failed",
    "secret_not_configured",
    "secret_not_supported",
    "secret_unavailable",
    "secret_not_found",
    "invalid_input",
    "rpc_error",
    "http_error",
    "swap_prepare_failed",
    "policy_rejected",
    "broadcast_failed",
    "not_implemented",
]


class GraphErrorBody(BaseModel):
    """Machine-readable failure for deep-agent continuations."""

    model_config = ConfigDict(frozen=True)

    code: GraphErrorCode
    message: str
    details: dict[str, Any] | None = None


class NativeBalanceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    wallet_address: str
    balance_wei_hex: str
    balance_wei: int
    balance_eth: str


class KnownAddressResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    ticker: str
    symbol: str
    name: str
    resolved_address: str


class EnsResolveResult(BaseModel):
    """Forward ENS lookup on Ethereum L1 via registry + resolver ``addr(bytes32)``."""

    model_config = ConfigDict(frozen=True)

    chain: Literal["ethereum"] = "ethereum"
    chain_id: int = 1
    name: str
    resolved_address: str


class Erc20DecimalsResult(BaseModel):
    """``decimals()`` read via ``eth_call`` (on-chain source of truth)."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    token_address: str
    decimals: int = Field(ge=0, le=255)


class Erc20ReadPlaceholder(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    operation: Literal["erc20_balance", "erc20_allowance", "erc20_metadata", "contract_read"]
    token_address: str | None = None
    status: Literal["placeholder"] = "placeholder"
    message: str = "On-chain ERC-20 read path not wired in this build."


class AlchemyTokenPricesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    prices_by_address: dict[str, str]


class UsdNotionalToTokenRawResult(BaseModel):
    """Sell-token raw amount from a USD notional using a live price and on-chain ``decimals()``."""

    model_config = ConfigDict(frozen=True)

    chain: str
    chain_id: int
    wallet_address: str
    token_address: str
    usd_notional: str
    price_usd: str
    decimals: int = Field(ge=0, le=255)
    human_token_amount: str
    amount_raw: str = Field(pattern=r"^[0-9]+$")
    wallet_balance_raw: str | None = Field(
        default=None,
        description="Current ``balanceOf(wallet)`` when the RPC read succeeded.",
    )
    balance_covers_notional_amount: bool | None = Field(
        default=None,
        description="True iff ``wallet_balance_raw >= amount_raw`` when both are known.",
    )


class AlchemyPortfolioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    wallet_address: str
    tokens: list[dict[str, Any]]


class AlchemyTransferHistoryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain: str
    wallet_address: str
    transfers: list[dict[str, Any]]


class LiFiPreparedTx(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    route_id: str = Field(validation_alias=AliasChoices("route_id", "routeId", "id"))
    transaction_request: dict[str, Any] = Field(
        validation_alias=AliasChoices("transaction_request", "transactionRequest"),
    )


class LiFiAllowanceHint(BaseModel):
    """ERC-20 approval LiFi expects before the swap tx can succeed on-chain."""

    model_config = ConfigDict(frozen=True)

    token_address: str
    spender_address: str
    amount_raw: str = Field(
        description="Minimum approval amount in raw token units (same as swap fromAmount).",
        pattern=r"^[0-9]+$",
    )


class LiFiAllowanceContext(BaseModel):
    """On-chain allowance snapshot for the LiFi route's sell token and approval spender.

    Always populated when LiFi returns an ERC-20 ``fromToken`` and ``approvalAddress``, even if
    ``allowance`` is omitted because the wallet already had enough allowance at prepare time.
    """

    model_config = ConfigDict(frozen=True)

    token_address: str
    spender_address: str
    amount_raw: str = Field(
        description=(
            "LiFi route sell amount in raw units (same as ``allowance.amount_raw`` when set)."
        ),
        pattern=r"^[0-9]+$",
    )
    current_allowance_raw: str | None = Field(
        default=None,
        description="``allowance(owner, spender)`` at prepare time when Alchemy read succeeded.",
        pattern=r"^[0-9]+$",
    )
    allowance_sufficient: bool | None = Field(
        default=None,
        description="True iff ``current_allowance_raw`` was read and is >= ``amount_raw``.",
    )


class SimulationFailed(RuntimeError):
    """Local gas/eth_call simulation failed; optional structured details for agents."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


class SwapPrepareResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["lifi"] = "lifi"
    prepared: LiFiPreparedTx
    allowance: LiFiAllowanceHint | None = None
    allowance_context: LiFiAllowanceContext | None = None


TxKind = Literal["native_transfer", "erc20_transfer", "erc20_approval", "lifi_swap"]

EnvelopeSigningMode = Literal["vault_key", "oneclaw_intents"]


class PreparedTxEnvelope(BaseModel):
    """Serializable transaction intent.

    Vault-key flows reference raw signing material via ``signing_key_secret_path``.
    ``oneclaw_intents`` may carry the same field as a 1Claw ``signing_key_path`` override,
    but Aurey never reads that key locally.
    """

    model_config = ConfigDict(frozen=True)

    kind: TxKind
    chain_id: int
    from_address: str
    to: str
    data: str
    value_hex: str
    gas_limit_hex: str | None = None
    nonce: int | None = None
    signing_mode: EnvelopeSigningMode = "vault_key"
    signing_key_secret_path: str | None = None
    lifi_sell_token: str | None = None
    lifi_approval_spender: str | None = None
    lifi_sell_amount_raw: str | None = None

    @model_validator(mode="after")
    def _enforce_signing_mode_secret_path_rules(self) -> Self:
        if self.signing_mode == "vault_key":
            path = self.signing_key_secret_path
            if path is None or not path.strip():
                raise ValueError(
                    "signing_key_secret_path must be non-empty when "
                    "signing_mode is 'vault_key'"
                )
        return self

    @field_validator("data")
    @classmethod
    def _normalize_calldata(cls, v: str) -> str:
        try:
            return normalize_contract_calldata(v)
        except ValueError as exc:
            raise ValueError(f"invalid calldata ({exc})") from exc


class TxReceiptSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: int
    block_number: int
    gas_used: int


class TxExecuteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tx_hash: str
    receipt: TxReceiptSummary
    stages: dict[str, Literal["ok"]]


class EarnChainResult(BaseModel):
    """Trimmed LiFi Earn `/v1/chains` row."""

    model_config = ConfigDict(frozen=True)

    name: Any | None = None
    chain_id: Any | None = None
    network_caip: Any | None = None
    chain: str | None = None


class EarnProtocolResult(BaseModel):
    """Trimmed LiFi Earn protocol core."""

    model_config = ConfigDict(frozen=True)

    id: Any | None = None
    name: Any | None = None
    logo_uri: Any | None = None
    url: Any | None = None


class EarnTokenSummary(BaseModel):
    """Trimmed token row from Earn vault payloads."""

    model_config = ConfigDict(frozen=True)

    address: Any | None = None
    symbol: Any | None = None
    name: Any | None = None
    decimals: Any | None = None
    weight: Any | None = None
    price_usd: Any | None = None


class EarnVaultSummary(BaseModel):
    """Trimmed LiFi Earn vault row (list + detail)."""

    model_config = ConfigDict(frozen=True)

    address: Any | None = None
    network: Any | None = None
    chain_id: Any | None = None
    slug: Any | None = None
    name: Any | None = None
    protocol: EarnProtocolResult = Field(default_factory=EarnProtocolResult)
    tags: Any | None = None
    analytics: dict[str, Any] | None = None
    chain: str | None = None
    is_transactional: Any | None = None
    is_redeemable: Any | None = None
    is_composer_supported: Any | None = None
    kyc: Any | None = None
    time_lock: Any | None = None
    caps: Any | None = None
    verification_status: Any | None = None
    deposit_packs: Any | None = None
    redeem_packs: Any | None = None
    synced_at: Any | None = None
    underlying_tokens: list[EarnTokenSummary] | None = None
    lp_tokens: list[EarnTokenSummary] | None = None
    reward_tokens: list[EarnTokenSummary] | None = None


class EarnVaultListResult(BaseModel):
    """LiFi Earn paginated vault list payload."""

    model_config = ConfigDict(frozen=True)

    vaults: list[EarnVaultSummary]
    total: Any | None = None
    normalized_at: Any | None = None
    next_cursor: Any | None = None


class EarnVaultDetailResult(BaseModel):
    """LiFi Earn single-vault payload."""

    model_config = ConfigDict(frozen=True)

    vault: EarnVaultSummary


class EarnPortfolioAssetResult(BaseModel):
    """Trimmed ``asset`` object inside Earn portfolio positions."""

    model_config = ConfigDict(frozen=True)

    address: Any | None = None
    name: Any | None = None
    symbol: Any | None = None
    decimals: Any | None = None


class EarnPortfolioPositionResult(BaseModel):
    """Trimmed LiFi Earn portfolio position row."""

    model_config = ConfigDict(frozen=True)

    chain_id: Any | None = None
    address: Any | None = None
    protocol_name: Any | None = None
    asset: EarnPortfolioAssetResult = Field(default_factory=EarnPortfolioAssetResult)
    balance_usd: Any | None = None
    balance_native: Any | None = None


class EarnPortfolioPositionsResult(BaseModel):
    """LiFi Earn portfolio positions payload."""

    model_config = ConfigDict(frozen=True)

    positions: list[EarnPortfolioPositionResult]


class LiFiStatusTokenResult(BaseModel):
    """Trimmed token fragment under LiFi status tx info."""

    model_config = ConfigDict(frozen=True)

    address: Any | None = None
    symbol: Any | None = None
    decimals: Any | None = None
    chain_id: Any | None = None
    name: Any | None = None
    coin_key: Any | None = None


class LiFiStatusTxInfoResult(BaseModel):
    """Trimmed ``sending`` / ``receiving`` fragment from LiFi `/v1/status`."""

    model_config = ConfigDict(frozen=True)

    tx_hash: Any | None = None
    tx_link: Any | None = None
    amount: Any | None = None
    chain_id: Any | None = None
    value: Any | None = None
    timestamp: Any | None = None
    token: LiFiStatusTokenResult | None = None
    gas_token: LiFiStatusTokenResult | None = None


class LiFiStatusMetadataResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    integrator: Any | None = None


class LiFiStatusResult(BaseModel):
    """Normalized LiFi `/v1/status` payload (trimmed)."""

    model_config = ConfigDict(frozen=True)

    status: Any | None = None
    substatus: Any | None = None
    transaction_id: Any | None = None
    tool: Any | None = None
    lifi_explorer_link: Any | None = None
    from_address: Any | None = None
    to_address: Any | None = None
    sending: LiFiStatusTxInfoResult | None = None
    receiving: LiFiStatusTxInfoResult | None = None
    metadata: LiFiStatusMetadataResult | None = None


class GraphRunResult(BaseModel):
    """Top-level graph response wrapper."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    result: dict[str, Any] | None = None
    error: GraphErrorBody | None = None
