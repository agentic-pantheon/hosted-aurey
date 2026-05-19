"""LangChain tools wrapping compiled Aurey LangGraph subgraph invocations."""

from __future__ import annotations

import time
from typing import Any, Literal, Self

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurey.graphs import (
    EarnGraphInput,
    LiFiStatusInput,
    SwapPrepareInput,
    TxExecuteInput,
    TxPrepareErc20Approval,
    TxPrepareErc20Transfer,
    TxPrepareLiFiInput,
    TxPrepareNative,
    build_alchemy_graph,
    build_earn_graph,
    build_lifi_status_graph,
    build_read_graph,
    build_swap_prepare_graph,
    build_tx_execute_graph,
    build_tx_prepare_graph,
    build_tx_prepare_lifi_graph,
)
from aurey.graphs.chains import chain_name_for_id
from aurey.graphs.read import ReadGraphInput
from aurey.graphs.swap_diag import SWAP_LOG, log_swap_tool
from aurey.runtime import AureyRuntime
from aurey.custody.errors import OneClawSigningError, SecretStoreUnavailableError
from aurey.custody.intents_models import IntentsSignTransactionRequest
from aurey.custody.intents_principal import OneClawSigningPrincipal
from aurey.tools.user_input import RequestUserInputArgs, UserQuestion, note_user_input_request


def _graph_payload(state: dict[str, Any]) -> dict[str, Any]:
    err = state.get("error")
    res = state.get("result")
    if err is not None:
        return {"ok": False, "error": err}
    return {"ok": True, "result": res}


def _oneclaw_signing_tool_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, SecretStoreUnavailableError):
        return {"code": "secret_unavailable", "message": str(exc)[:800]}
    if isinstance(exc, OneClawSigningError):
        return {"code": "oneclaw_signing_error", "message": str(exc)[:800]}
    if isinstance(exc, ValueError):
        return {"code": "invalid_input", "message": str(exc)[:800]}
    return {"code": "internal_error", "message": f"Unexpected signing error ({type(exc).__name__})."}


def _parse_chain_id_field(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if isinstance(raw, str):
            s = raw.strip()
            return int(s, 16) if s.startswith(("0x", "0X")) else int(s, 10)
    except (TypeError, ValueError):
        return None
    return None


def _try_coerce_lifi_prepared_to_execute_envelope(
    envelope: dict[str, Any],
    prepare_lifi_g: Any,
) -> dict[str, Any] | None:
    """Build execute envelope when ``prepared`` (route + tx) was passed instead."""

    if envelope.get("kind"):
        return None
    rid = envelope.get("route_id") or envelope.get("routeId")
    tx_req = envelope.get("transaction_request") or envelope.get("transactionRequest")
    if not rid or not isinstance(tx_req, dict):
        return None

    cid = _parse_chain_id_field(tx_req.get("chainId"))
    if cid is None:
        return None
    chain = chain_name_for_id(cid)
    if chain is None:
        return None
    from_raw = tx_req.get("from")
    if from_raw is None or str(from_raw).strip() == "":
        return None

    payload = TxPrepareLiFiInput(
        chain=chain,
        from_address=str(from_raw).strip(),
        prepared={"route_id": str(rid), "transaction_request": dict(tx_req)},
    )
    state = prepare_lifi_g.invoke(
        {"input": payload.model_dump(mode="json", exclude_none=True)}
    )
    err = state.get("error")
    res = state.get("result")
    if err is not None or not isinstance(res, dict):
        return None
    fixed = res.get("envelope")
    return fixed if isinstance(fixed, dict) else None


def _data_selector(data_text: str) -> str | None:
    if data_text.startswith("0x") and len(data_text) >= 10:
        return data_text[:10]
    return None


def _data_bytes(data_text: str) -> int | None:
    if not data_text.startswith("0x"):
        return None
    return max((len(data_text) - 2) // 2, 0)


def _tx_request_summary(
    tx_req: dict[str, Any],
    *,
    route_id: str,
    prepared_id: str,
) -> dict[str, Any]:
    data = tx_req.get("data")
    data_text = data.strip() if isinstance(data, str) else ""
    chain_id = _parse_chain_id_field(tx_req.get("chainId"))
    return {
        "route_id": route_id,
        "prepared_id": prepared_id,
        "chain_id": chain_id,
        "from": tx_req.get("from"),
        "to": tx_req.get("to"),
        "value": tx_req.get("value"),
        "gas_limit": tx_req.get("gasLimit") or tx_req.get("gas"),
        "data_selector": _data_selector(data_text),
        "data_bytes": _data_bytes(data_text),
    }


def _envelope_summary(
    envelope: dict[str, Any],
    *,
    prepared_id: str | None = None,
) -> dict[str, Any]:
    data = envelope.get("data")
    data_text = data.strip() if isinstance(data, str) else ""
    out: dict[str, Any] = {
        "kind": envelope.get("kind"),
        "chain_id": envelope.get("chain_id"),
        "from_address": envelope.get("from_address"),
        "to": envelope.get("to"),
        "value_hex": envelope.get("value_hex"),
        "gas_limit_hex": envelope.get("gas_limit_hex"),
        "data_selector": _data_selector(data_text),
        "data_bytes": _data_bytes(data_text),
    }
    if prepared_id is not None:
        out["prepared_id"] = prepared_id
    return out


def _attach_execute_prepared_id(runtime: AureyRuntime, out: dict[str, Any]) -> dict[str, Any]:
    """Store full execute envelopes server-side so ``tx_execute`` can use ``prepared_id`` (no calldata round-trip)."""

    if not out.get("ok") or not isinstance(out.get("result"), dict):
        return out
    envelope = out["result"].get("envelope")
    if not isinstance(envelope, dict):
        return out
    stored_id = runtime.prepared_txs.put(
        kind="execute_envelope",
        payload=dict(envelope),
        summary=_envelope_summary(envelope),
    )
    out["result"] = {
        **out["result"],
        "prepared_id": stored_id,
        "envelope": _envelope_summary(envelope, prepared_id=stored_id),
    }
    return out


def _invalid_prepared_id(prepared_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "invalid_input",
            "message": "Prepared transaction id was not found or has expired.",
            "details": {"prepared_id": prepared_id},
        },
    }


def _swap_prepare_with_prepared_storage(
    runtime: AureyRuntime,
    swap_g: Any,
    prepare_lifi_g: Any,
    payload: SwapPrepareInput,
    *,
    log_tool_name: str,
) -> dict[str, Any]:
    """Run swap prepare and mirror ``swap_prepare`` server-side LiFi + execute envelope storage."""

    t0 = time.perf_counter()
    out = _graph_payload(swap_g.invoke({"input": payload.model_dump()}))
    rid = None
    if out.get("ok") and isinstance(out.get("result"), dict):
        prep = out["result"].get("prepared")
        if isinstance(prep, dict):
            rid = prep.get("route_id")
            tx_req = prep.get("transaction_request") or prep.get("transactionRequest")
            if isinstance(rid, str) and isinstance(tx_req, dict):
                lifi_prepared_id = runtime.prepared_txs.put(
                    kind="lifi_prepared",
                    payload=prep,
                    summary=_tx_request_summary(tx_req, route_id=rid, prepared_id=""),
                )
                prepare_input: dict[str, Any] = {
                    "chain": payload.from_chain,
                    "from_address": payload.from_address,
                    "prepared": prep,
                }
                ctx = out["result"].get("allowance_context")
                if ctx is not None:
                    prepare_input["allowance_context"] = ctx
                prepared_state = prepare_lifi_g.invoke(
                    {
                        "input": prepare_input,
                    }
                )
                err = prepared_state.get("error")
                res = prepared_state.get("result")
                if err is not None or not isinstance(res, dict):
                    out = {"ok": False, "error": err or {"code": "invalid_input"}}
                else:
                    envelope = res.get("envelope")
                    if isinstance(envelope, dict):
                        prepared_id = runtime.prepared_txs.put(
                            kind="execute_envelope",
                            payload=envelope,
                            summary=_envelope_summary(envelope),
                        )
                        compact = _tx_request_summary(
                            tx_req,
                            route_id=rid,
                            prepared_id=prepared_id,
                        )
                        compact["execute_prepared_id"] = prepared_id
                        compact["lifi_prepared_id"] = lifi_prepared_id
                        out["result"]["prepared"] = compact
                        out["result"]["prepared_id"] = prepared_id
    log_swap_tool(
        name=log_tool_name,
        wall_ms=(time.perf_counter() - t0) * 1000,
        ok=out.get("ok"),
        route_id=rid,
        from_chain=payload.from_chain,
        to_chain=payload.to_chain,
    )
    return out


def _earn_vault_summary_for_deposit(vault: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "address",
        "chain",
        "chain_id",
        "name",
        "slug",
        "network",
        "protocol",
        "is_composer_supported",
        "is_transactional",
        "is_redeemable",
    )
    out: dict[str, Any] = {k: vault[k] for k in keys if k in vault and vault[k] is not None}
    analytics = vault.get("analytics")
    if isinstance(analytics, dict):
        apy = analytics.get("apy")
        if isinstance(apy, dict) and apy.get("total") is not None:
            out["apy_total"] = apy.get("total")
        if analytics.get("tvl_usd") is not None:
            out["tvl_usd"] = analytics.get("tvl_usd")
    return out


def _earn_deposit_vault_eligible(vault: dict[str, Any]) -> tuple[bool, str | None]:
    """Reject Composer deposits when the Earn vault metadata forbids or implies no Composer path."""

    comp = vault.get("is_composer_supported")
    if comp is False:
        return False, "Vault is not Composer-supported (is_composer_supported is false)."
    transactional = vault.get("is_transactional")
    if comp is None and transactional is False:
        return (
            False,
            "is_composer_supported is absent and is_transactional is false; Composer deposit is unavailable.",
        )
    return True, None


class EarnListChainsArgs(BaseModel):
    """Discover LiFi Earn-supported chains (vault discovery); optional LiFi key via env or ``lifi_api_secret_path``."""

    model_config = ConfigDict(extra="forbid")


class EarnListProtocolsArgs(BaseModel):
    """List yield protocols exposed by LiFi Earn; use before filtering vaults."""

    model_config = ConfigDict(extra="forbid")


class EarnListVaultsArgs(BaseModel):
    """Paginated vault search on Earn (`https://earn.li.fi`); filter to Composer-capable vaults by default."""

    model_config = ConfigDict(extra="forbid")

    chain: str | None = Field(default=None, description="Chain slug (e.g. base, ethereum); optional global filter.")
    chain_id: int | None = Field(default=None, ge=1, description="EVM chain id; optional alternative to ``chain``.")
    asset: str | None = Field(
        default=None,
        description="Underlying asset filter: token address (0x) or symbol as accepted by Earn.",
    )
    protocol: str | None = Field(default=None, description="Protocol id from ``earn_list_protocols``.")
    min_tvl_usd: float | None = Field(default=None, ge=0.0, description="Minimum vault TVL in USD.")
    is_transactional: bool | None = Field(default=None, description="Restrict to vaults that allow on-chain deposits.")
    is_redeemable: bool | None = Field(default=None, description="Restrict to redeemable vaults.")
    is_composer_supported: bool = Field(
        default=True,
        description="When true (default), only vaults that support LiFi Composer routes are returned.",
    )
    sort_by: Literal["apy", "tvl"] | None = Field(default=None, description="Server-side sort key.")
    limit: int = Field(default=10, ge=1, le=100, description="Page size (max 100).")
    cursor: str | None = Field(default=None, min_length=1, description="Opaque pagination cursor from prior response.")


class EarnGetVaultArgs(BaseModel):
    """Fetch one Earn vault by chain + contract; use before ``earn_prepare_deposit`` to validate metadata."""

    model_config = ConfigDict(extra="forbid")

    chain: str | None = Field(default=None, min_length=1, description="Chain slug for the vault.")
    chain_id: int | None = Field(default=None, ge=1, description="Numeric chain id for the vault.")
    vault_address: str = Field(min_length=1, description="Vault (vault share) contract address (0x).")

    @model_validator(mode="after")
    def _chain_ref(self) -> Self:
        if (self.chain is None or not str(self.chain).strip()) and self.chain_id is None:
            raise ValueError("Provide chain or chain_id for earn_get_vault.")
        return self


class EarnPortfolioPositionsArgs(BaseModel):
    """LiFi Earn portfolio positions for a wallet across supported chains."""

    wallet_address: str = Field(min_length=1, description="Wallet to list Earn positions for (0x).")


class EarnPrepareDepositArgs(BaseModel):
    """Prepare a LiFi Composer quote that deposits into an Earn vault (``toToken`` = vault share token)."""

    model_config = ConfigDict(extra="forbid")

    vault_chain: str | None = Field(default=None, min_length=1, description="Chain slug where the vault lives.")
    vault_chain_id: int | None = Field(default=None, ge=1, description="Numeric chain id of the vault.")
    vault_address: str = Field(min_length=1, description="Vault contract on the vault chain (0x).")
    from_chain: str = Field(min_length=1, description="Source chain slug where ``from_asset`` is spent.")
    from_asset: str = Field(
        min_length=1,
        description="Sell token on ``from_chain`` (0x address or symbol accepted by LiFi ``fromToken``).",
    )
    from_amount_wei: str = Field(
        min_length=1,
        pattern=r"^[0-9]+$",
        description="Sell amount in smallest token units (decimal string of digits, same as ``swap_prepare``).",
    )
    from_address: str = Field(min_length=1, description="Sender; must sign the prepared tx on ``from_chain``.")
    to_address: str | None = Field(
        default=None,
        min_length=1,
        description="Recipient of vault shares on the vault chain; defaults to ``from_address``.",
    )
    slippage: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Max slippage as decimal fraction (e.g. 0.005 = 0.5%%).",
    )
    order: Literal["FASTEST", "CHEAPEST"] | None = Field(
        default=None,
        description="LiFi route preference for the Composer deposit leg.",
    )

    @model_validator(mode="after")
    def _vault_chain_ref(self) -> Self:
        if (self.vault_chain is None or not str(self.vault_chain).strip()) and self.vault_chain_id is None:
            raise ValueError("Provide vault_chain or vault_chain_id to resolve the vault.")
        return self


class AlchemyTokenPricesArgs(BaseModel):
    """Alchemy token spot prices; requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings."""

    chain: str = Field(
        min_length=1,
        description="Chain slug (e.g. ethereum, base).",
    )
    wallet_address: str = Field(
        min_length=1,
        description="Wallet address for API context.",
    )
    token_addresses: list[str] = Field(
        min_length=1,
        description="ERC-20 contract addresses to quote (0x form).",
    )


class AlchemyPortfolioArgs(BaseModel):
    """Alchemy portfolio (tokens-by-wallet); requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings."""

    chain: str = Field(
        min_length=1,
        description="Chain slug (e.g. ethereum, base).",
    )
    wallet_address: str = Field(
        min_length=1,
        description="Wallet to list token balances for.",
    )


class AlchemyTransferHistoryArgs(BaseModel):
    """Alchemy transfer history via ``alchemy_getAssetTransfers``; requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path``."""

    chain: str = Field(
        min_length=1,
        description="Chain slug (e.g. ethereum, base).",
    )
    wallet_address: str = Field(
        min_length=1,
        description="Wallet whose inbound/outbound transfers are listed.",
    )


class ComputeTokenAmountFromUsdArgs(BaseModel):
    """Map a USD sell notional to ERC-20 raw units (Alchemy price + on-chain ``decimals``)."""

    chain: str = Field(min_length=1, description="Chain slug where the token and wallet live.")
    wallet_address: str = Field(
        min_length=1,
        description="Wallet for price API context and optional balance check.",
    )
    token_address: str = Field(min_length=1, description="Sell token contract (0x).")
    usd_notional: str = Field(
        min_length=1,
        description='USD notional as decimal text (e.g. "5" or "12.34").',
    )


class TxExecuteToolArgs(BaseModel):
    """Execute a prepared tx: simulate, policy, sign via 1Claw, broadcast."""

    model_config = ConfigDict(extra="ignore")

    envelope: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required. The exact `envelope` object from a successful `tx_prepare_*` or "
            "`tx_prepare_lifi_swap` call: `prepare_output['result']['envelope']`. "
            "You cannot call this tool with only `idempotency_key`."
        ),
    )
    prepared_id: str | None = Field(
        default=None,
        description=(
            "Preferred for LiFi swaps: short server-side prepared transaction id returned by "
            "`swap_prepare`, `earn_prepare_deposit`, or `tx_prepare_lifi_swap`. Avoids sending large calldata through the "
            "model."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key for broadcast (never pass without `envelope`).",
    )

    @model_validator(mode="after")
    def _tx_reference_required(self) -> Self:
        if not self.envelope and not self.prepared_id:
            raise ValueError(
                "tx_execute requires `prepared_id` (preferred for LiFi swaps) or `envelope`: use "
                "`prepared_id` from `swap_prepare`/`tx_prepare_lifi_swap`, or the exact dict from "
                "a successful prepare tool at `result['envelope']`. `idempotency_key` alone is "
                "invalid."
            )
        return self


class OneClawSignPersonalMessageArgs(BaseModel):
    """EIP-191 personal message signing via 1Claw unified ``/sign`` (requires ``message_signing_enabled`` on the agent)."""

    chain: str = Field(min_length=1, description="Chain slug (e.g. base, ethereum).")
    message: str = Field(
        min_length=1,
        description="Human-readable text to sign (UTF-8); Aurey sends it as 0x-hex bytes to 1Claw. "
        "Use an explicit 0x + even hex string only if you need a pre-encoded byte string.",
    )
    signing_key_path: str | None = Field(
        default=None,
        description="Optional vault signing key path when multiple keys are configured.",
    )


class OneClawSignTypedDataArgs(BaseModel):
    """EIP-712 typed data signing via 1Claw unified ``/sign`` (domain allowlist / policy applies)."""

    model_config = ConfigDict(extra="forbid")

    chain: str = Field(min_length=1, description="Chain slug for the signing intent.")
    typed_data: dict[str, Any] = Field(
        description="Full EIP-712 object (``domain``, ``types``, ``primaryType``, ``message``).",
    )
    signing_key_path: str | None = Field(
        default=None,
        description="Optional vault signing key path when multiple keys are configured.",
    )

    @model_validator(mode="after")
    def _typed_nonempty(self) -> Self:
        if not self.typed_data:
            raise ValueError("typed_data must be a non-empty object.")
        return self


class OneClawIntentsSignTransactionToolArgs(IntentsSignTransactionRequest):
    """Tool input for BYORPC ``transactions/sign`` — same fields as Intents, unknown keys rejected."""

    model_config = ConfigDict(extra="forbid")


class TxPrepareNativeArgs(BaseModel):
    """Prepare native (ETH/Base ETH) transfer; signing uses wallet path from settings."""

    chain: str = Field(min_length=1, description="Chain slug (e.g. ethereum, base).")
    from_address: str = Field(min_length=1, description="Sender EVM address (0x).")
    to_address: str = Field(min_length=1, description="Recipient EVM address (0x).")
    value_wei: int = Field(ge=0, description="Native amount in wei.")


_ERC20_AMOUNT_FIELD = Field(
    ge=0,
    description=(
        "Token amount in the contract's smallest units (not necessarily 1e18). "
        "Lookup token decimals: USDC on Base/Ethereum uses **6** — e.g. 0.01 USDC = **10_000**, "
        "1 USDC = **1_000_000**. WETH uses 18. Do not scale USDC by 10**18."
    ),
)


class TxPrepareErc20TransferArgs(BaseModel):
    """Prepare ERC-20 transfer; RPC from Alchemy credentials (env or vault path) if configured."""

    chain: str = Field(min_length=1, description="Chain slug.")
    from_address: str = Field(min_length=1)
    token_address: str = Field(min_length=1, description="ERC-20 contract (0x).")
    to_address: str = Field(min_length=1)
    amount_wei: int = _ERC20_AMOUNT_FIELD


class TxPrepareErc20ApprovalArgs(BaseModel):
    """Prepare ERC-20 ``approve`` for DEX/spenders (e.g. before LiFi swaps)."""

    chain: str = Field(min_length=1, description="Chain slug.")
    from_address: str = Field(min_length=1)
    token_address: str = Field(min_length=1)
    spender_address: str = Field(min_length=1, description="Spender contract (e.g. router).")
    amount_wei: int = _ERC20_AMOUNT_FIELD


class EvmGetNativeBalanceArgs(BaseModel):
    """Native balance via JSON-RPC; requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` for RPC URL derivation."""

    chain: str = Field(min_length=1, description="Chain slug (e.g. ethereum, base).")
    wallet_address: str = Field(min_length=1, description="Address to read balance for.")


class ResolveKnownAddressArgs(BaseModel):
    """Map a known ticker to a contract address (bundled mapping; no Alchemy call)."""

    chain: str = Field(min_length=1, description="Chain slug.")
    known_ticker: str = Field(
        min_length=1,
        description="Ticker key, e.g. usdc, weth.",
    )


class EvmGetErc20BalanceArgs(BaseModel):
    """ERC-20 ``balanceOf`` via JSON-RPC; requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` for RPC."""

    chain: str = Field(min_length=1)
    wallet_address: str = Field(min_length=1)
    token_address: str = Field(min_length=1, description="ERC-20 contract (0x).")


class EvmGetErc20DecimalsArgs(BaseModel):
    """Read token ``decimals()``; uses RPC from Alchemy-backed URL (env key or vault path) when configured."""

    chain: str = Field(min_length=1)
    token_address: str = Field(min_length=1)


class EvmResolveEnsArgs(BaseModel):
    """Resolve ENS on Ethereum L1; uses RPC from settings-backed resolution path."""

    name: str = Field(
        min_length=1,
        description="ENS name such as nick.eth (trimmed and lower-cased by the tool).",
    )
    chain: str = Field(
        default="ethereum",
        min_length=1,
        description="Must be 'ethereum'. ENS forward resolution is only defined on L1 mainnet.",
    )


def build_aurey_subgraph_tools(runtime: AureyRuntime) -> list[BaseTool]:
    """Compile subgraphs once and expose strict LangChain tools (validated graph inputs only).

    Includes LiFi **Earn** discovery (chains, protocols, vaults, portfolio) and **Composer**
    vault deposits via ``earn_prepare_deposit``, which reuses the same ``prepared_id`` storage as
    ``swap_prepare``. Cross-chain deposits should poll ``lifi_get_status`` until the bridge
    completes.
    """

    read_g = build_read_graph(runtime)
    alchemy_g = build_alchemy_graph(runtime)
    earn_g = build_earn_graph(runtime)
    lifi_status_g = build_lifi_status_graph(runtime)
    swap_g = build_swap_prepare_graph(runtime)
    prepare_g = build_tx_prepare_graph(runtime)
    prepare_lifi_g = build_tx_prepare_lifi_graph(runtime)
    execute_g = build_tx_execute_graph(runtime)

    @tool(args_schema=EvmGetNativeBalanceArgs)
    def evm_get_native_balance(chain: str, wallet_address: str) -> dict[str, Any]:
        """Native token balance via JSON-RPC; RPC URL is derived from ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings (no raw URLs in chat)."""
        payload = EvmGetNativeBalanceArgs(chain=chain, wallet_address=wallet_address)
        graph_in = ReadGraphInput(
            operation="native_balance",
            chain=payload.chain,
            wallet_address=payload.wallet_address,
        )
        return _graph_payload(read_g.invoke({"input": graph_in.model_dump()}))

    @tool(args_schema=ResolveKnownAddressArgs)
    def resolve_known_address(chain: str, known_ticker: str) -> dict[str, Any]:
        """Map a bundled ticker (e.g. USDC) to contract metadata from ``known_addresses.json``.

        Call this **before** stating or using a ``0x`` for a named symbol on a chain; do not
        guess addresses. Offline lookup (does not call Alchemy)."""
        payload = ResolveKnownAddressArgs(chain=chain, known_ticker=known_ticker)
        graph_in = ReadGraphInput(
            operation="known_address",
            chain=payload.chain,
            known_ticker=payload.known_ticker,
        )
        return _graph_payload(read_g.invoke({"input": graph_in.model_dump()}))

    @tool(args_schema=EvmGetErc20BalanceArgs)
    def evm_get_erc20_balance(
        chain: str,
        wallet_address: str,
        token_address: str,
    ) -> dict[str, Any]:
        """ERC-20 ``balanceOf`` via RPC; requires Alchemy-derived RPC when using default wiring (see ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings)."""
        payload = EvmGetErc20BalanceArgs(
            chain=chain,
            wallet_address=wallet_address,
            token_address=token_address,
        )
        graph_in = ReadGraphInput(
            operation="erc20_balance",
            chain=payload.chain,
            wallet_address=payload.wallet_address,
            token_address=payload.token_address,
        )
        return _graph_payload(read_g.invoke({"input": graph_in.model_dump()}))

    @tool(args_schema=EvmGetErc20DecimalsArgs)
    def evm_get_erc20_decimals(chain: str, token_address: str) -> dict[str, Any]:
        """Read ERC-20 ``decimals()`` over JSON-RPC (Alchemy-derived URL when ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` is set)."""
        payload = EvmGetErc20DecimalsArgs(chain=chain, token_address=token_address)
        graph_in = ReadGraphInput(
            operation="erc20_decimals",
            chain=payload.chain,
            token_address=payload.token_address,
        )
        return _graph_payload(read_g.invoke({"input": graph_in.model_dump()}))

    @tool(args_schema=EvmResolveEnsArgs)
    def evm_resolve_ens(name: str, chain: str = "ethereum") -> dict[str, Any]:
        """Resolve ENS on Ethereum mainnet to a checksum ``0x`` address (RPC from settings-derived URL).

        Call **before** ``tx_prepare_*`` / ``swap_prepare`` when the user gives an ENS name as
        ``to_address`` or recipient; use ``result['resolved_address']``. Other chains reject.
        """
        payload = EvmResolveEnsArgs(name=name, chain=chain)
        graph_in = ReadGraphInput(
            operation="ens_resolve",
            chain=payload.chain,
            ens_name=payload.name,
        )
        return _graph_payload(read_g.invoke({"input": graph_in.model_dump()}))

    tools: list[BaseTool] = [
        evm_get_native_balance,
        evm_get_erc20_decimals,
        evm_resolve_ens,
        resolve_known_address,
        evm_get_erc20_balance,
    ]

    @tool(args_schema=AlchemyTokenPricesArgs)
    def alchemy_get_token_prices(
        chain: str,
        wallet_address: str,
        token_addresses: list[str],
    ) -> dict[str, Any]:
        """Alchemy Prices API quotes by token address; requires Alchemy credentials in Aurey settings (``AUREY_ALCHEMY_API_KEY`` or vault path—never paste the raw key in chat)."""
        payload = AlchemyTokenPricesArgs(
            chain=chain,
            wallet_address=wallet_address,
            token_addresses=list(token_addresses),
        )
        state = alchemy_g.invoke(
            {"input": {**payload.model_dump(), "operation": "token_prices"}},
        )
        return _graph_payload(state)

    tools.append(alchemy_get_token_prices)

    @tool(args_schema=ComputeTokenAmountFromUsdArgs)
    def compute_token_amount_from_usd(
        chain: str,
        wallet_address: str,
        token_address: str,
        usd_notional: str,
    ) -> dict[str, Any]:
        """Size a **sell** in raw token units from a **USD notional** (requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path``).

        Uses Alchemy spot price and on-chain ``decimals()`` with **Decimal** math server-side—prefer over
        hand-calculating ``from_amount_wei`` when the user asks for ``$n`` worth of a token. Returns
        ``amount_raw`` for ``swap_prepare`` / ``earn_prepare_deposit`` and ``balance_covers_notional_amount``
        when ``balanceOf`` succeeds. Do not use ``wallet_balance_raw`` as the swap size unless the user asked
        to sell max.
        """
        payload = ComputeTokenAmountFromUsdArgs(
            chain=chain,
            wallet_address=wallet_address,
            token_address=token_address,
            usd_notional=usd_notional,
        )
        state = alchemy_g.invoke(
            {
                "input": {
                    **payload.model_dump(),
                    "operation": "usd_notional_to_raw",
                }
            }
        )
        return _graph_payload(state)

    tools.append(compute_token_amount_from_usd)

    @tool(args_schema=AlchemyPortfolioArgs)
    def alchemy_get_portfolio_tokens(
        chain: str,
        wallet_address: str,
    ) -> dict[str, Any]:
        """Alchemy Data API portfolio (tokens-by-wallet); requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings."""
        state = alchemy_g.invoke(
            {
                "input": {
                    "operation": "portfolio_tokens",
                    "chain": chain,
                    "wallet_address": wallet_address,
                }
            }
        )
        return _graph_payload(state)

    tools.append(alchemy_get_portfolio_tokens)

    @tool(args_schema=AlchemyTransferHistoryArgs)
    def alchemy_get_transfer_history(
        chain: str,
        wallet_address: str,
    ) -> dict[str, Any]:
        """Recent ERC-20 and external transfers via Alchemy ``alchemy_getAssetTransfers``; requires ``AUREY_ALCHEMY_API_KEY`` or ``alchemy_api_secret_path`` in settings."""
        state = alchemy_g.invoke(
            {
                "input": {
                    "operation": "transfer_history",
                    "chain": chain,
                    "wallet_address": wallet_address,
                }
            }
        )
        return _graph_payload(state)

    tools.append(alchemy_get_transfer_history)

    @tool(args_schema=EarnListChainsArgs)
    def earn_list_chains() -> dict[str, Any]:
        """List chains supported by LiFi Earn — start here for vault discovery.

        Results are trimmed server-side. Use ``earn_list_vaults`` / ``earn_get_vault`` for Composer
        deposit metadata (``is_composer_supported``).
        """
        graph_in = EarnGraphInput(operation="list_chains")
        return _graph_payload(
            earn_g.invoke({"input": graph_in.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(earn_list_chains)

    @tool(args_schema=EarnListProtocolsArgs)
    def earn_list_protocols() -> dict[str, Any]:
        """List yield protocol ids/names from LiFi Earn; use ``protocol`` when filtering ``earn_list_vaults``."""
        graph_in = EarnGraphInput(operation="list_protocols")
        return _graph_payload(
            earn_g.invoke({"input": graph_in.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(earn_list_protocols)

    @tool(args_schema=EarnListVaultsArgs)
    def earn_list_vaults(
        chain: str | None = None,
        chain_id: int | None = None,
        asset: str | None = None,
        protocol: str | None = None,
        min_tvl_usd: float | None = None,
        is_transactional: bool | None = None,
        is_redeemable: bool | None = None,
        is_composer_supported: bool = True,
        sort_by: Literal["apy", "tvl"] | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search Earn vaults; default filter favors **Composer-supported** vaults (LiFi vault-as-``toToken`` deposits).

        Prefer vaults with ``is_composer_supported`` true for ``earn_prepare_deposit``. Pagination:
        pass ``next_cursor`` from a prior ``result`` when present.
        """
        args = EarnListVaultsArgs(
            chain=chain,
            chain_id=chain_id,
            asset=asset,
            protocol=protocol,
            min_tvl_usd=min_tvl_usd,
            is_transactional=is_transactional,
            is_redeemable=is_redeemable,
            is_composer_supported=is_composer_supported,
            sort_by=sort_by,
            limit=limit,
            cursor=cursor,
        )
        graph_in = EarnGraphInput(
            operation="list_vaults",
            chain=args.chain,
            chain_id=args.chain_id,
            asset=args.asset,
            protocol=args.protocol,
            min_tvl_usd=args.min_tvl_usd,
            is_transactional=args.is_transactional,
            is_redeemable=args.is_redeemable,
            is_composer_supported=args.is_composer_supported,
            sort_by=args.sort_by,
            limit=args.limit,
            cursor=args.cursor,
        )
        return _graph_payload(
            earn_g.invoke({"input": graph_in.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(earn_list_vaults)

    @tool(args_schema=EarnGetVaultArgs)
    def earn_get_vault(
        vault_address: str,
        chain: str | None = None,
        chain_id: int | None = None,
    ) -> dict[str, Any]:
        """Load one Earn vault record; inspect ``is_composer_supported`` before preparing a Composer deposit.

        ``earn_prepare_deposit`` re-validates this server-side and rejects unsupported vaults.
        """
        args = EarnGetVaultArgs(chain=chain, chain_id=chain_id, vault_address=vault_address)
        graph_in = EarnGraphInput(
            operation="get_vault",
            chain=args.chain,
            chain_id=args.chain_id,
            vault_address=args.vault_address,
        )
        return _graph_payload(
            earn_g.invoke({"input": graph_in.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(earn_get_vault)

    @tool(args_schema=EarnPortfolioPositionsArgs)
    def earn_portfolio_positions(wallet_address: str) -> dict[str, Any]:
        """List a wallet's Earn positions (vault balances) aggregated by LiFi Earn."""
        graph_in = EarnGraphInput(
            operation="portfolio_positions",
            wallet_address=wallet_address,
        )
        return _graph_payload(
            earn_g.invoke({"input": graph_in.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(earn_portfolio_positions)

    @tool(args_schema=SwapPrepareInput)
    def swap_prepare(
        from_chain: str,
        to_chain: str,
        from_asset: str,
        to_asset: str,
        from_amount_wei: str,
        from_address: str,
        to_address: str,
        slippage: float | None = None,
        order: Literal["FASTEST", "CHEAPEST"] | None = None,
    ) -> dict[str, Any]:
        """LiFi swap quote from ``GET /v1/quote``; optional LiFi key from ``AUREY_LIFI_API_KEY`` or ``lifi_api_secret_path`` in settings.

        Uses LiFi ``GET /v1/quote`` (see https://docs.li.fi/llms.txt). Optional ``slippage`` is
        a decimal fraction (e.g. ``0.005`` = 0.5%%). Optional ``order`` is ``FASTEST`` or
        ``CHEAPEST``. Prefer **checksum** ``0x`` token addresses when possible.

        For **native ETH** on an EVM chain, LiFi expects the wrapped native token (WETH). If
        ``from_asset`` / ``to_asset`` contain natural-language native-ETH phrases (e.g. ``native ETH``),
        Aurey maps them to wrapped native for the relevant **from** / **to** chain before calling LiFi
        (so the model should not leave quotes as ``toToken``).

        If the user asks for **\\$n worth** of the **sell** token (fiat notional only), call "
        "**``compute_token_amount_from_usd``** and use **``amount_raw``** as ``from_amount_wei``. Do "
        "not substitute wallet balance unless the user asked to sell **all** or **max**. If that "
        "tool fails, fall back to **``alchemy_get_token_prices``** + ``evm_get_erc20_decimals`` with "
        "the same floor rule; never invent prices or raw amounts.

        On success, call ``tx_execute(prepared_id=result['prepared_id'])``. The full LiFi
        transaction request is stored server-side so the model does not need to copy calldata.

        When ``result`` includes ``allowance``, the wallet must approve the spender for the
        sell token before the swap simulates. When Alchemy is configured and on-chain allowance
        is already sufficient, ``allowance`` is omitted but ``allowance_context`` still lists the
        LiFi sell token, approval spender, required ``amount_raw``, and the on-chain allowance
        snapshot (so agents can debug ``TRANSFER_FROM`` simulation failures without guessing).
        Use ``tx_prepare_erc20_approval`` then ``tx_execute`` that tx first when ``allowance`` is set.

        If ``to_address`` (or ``from_address``) is an ENS name like ``alice.eth``, run
        ``evm_resolve_ens`` on **ethereum** first and pass the returned hex address here.

        **Earn / Composer:** tokens swaps are the same API shape as vault deposits prepared via
        ``earn_prepare_deposit`` (vault share address as ``toToken``). When ``from_chain`` differs
        from the vault chain, treat the route as **bridging**: after execution, poll LiFi status with
        ``lifi_get_status`` using the source-chain broadcast hash until the destination leg is done.
        Quotes **expire**; if ``tx_execute`` fails validation or allowance changed, call
        ``swap_prepare`` (or ``earn_prepare_deposit``) again before re-approving or executing.
        """
        payload = SwapPrepareInput(
            from_chain=from_chain,
            to_chain=to_chain,
            from_asset=from_asset,
            to_asset=to_asset,
            from_amount_wei=from_amount_wei,
            from_address=from_address,
            to_address=to_address,
            slippage=slippage,
            order=order,
        )
        return _swap_prepare_with_prepared_storage(
            runtime,
            swap_g,
            prepare_lifi_g,
            payload,
            log_tool_name="swap_prepare",
        )

    tools.append(swap_prepare)

    @tool(args_schema=EarnPrepareDepositArgs)
    def earn_prepare_deposit(
        vault_address: str,
        from_chain: str,
        from_asset: str,
        from_amount_wei: str,
        from_address: str,
        vault_chain: str | None = None,
        vault_chain_id: int | None = None,
        to_address: str | None = None,
        slippage: float | None = None,
        order: Literal["FASTEST", "CHEAPEST"] | None = None,
    ) -> dict[str, Any]:
        """Prepare a **LiFi Composer deposit**: quotes a route whose ``toToken`` is the **vault share** token.

        Flow: (1) fetch/validate the vault via Earn (``is_composer_supported`` / ``is_transactional`` rules),
        (2) call LiFi ``/v1/quote`` with ``to_asset`` = vault address and ``to_chain`` = vault chain — same
        machinery as ``swap_prepare``. (3) Output includes ``prepared_id`` and compact ``prepared`` summary;
        execute with ``tx_execute(prepared_id=...)`` only — **no broadcast or approve** here.

        If ``result`` includes ``allowance``, approve the indicated spender with ``tx_prepare_erc20_approval``
        then ``tx_execute`` that tx before executing the deposit. Re-quote with this tool if the quote expires.

        **Cross-chain:** when ``from_chain`` differs from the vault chain, ``requires_status_polling`` is true:
        after the first on-chain tx, use ``lifi_get_status`` with the **source** tx hash and chain hints until
        LiFi reports completion (destination funds or substatus).

        For **USD notional** on the sell token, call **``compute_token_amount_from_usd``** and use
        ``amount_raw`` as ``from_amount_wei`` (same floor/price/decimals logic as ``swap_prepare``).

        ENS names on **ethereum** must be resolved with ``evm_resolve_ens`` before passing addresses.
        """
        payload = EarnPrepareDepositArgs(
            vault_chain=vault_chain,
            vault_chain_id=vault_chain_id,
            vault_address=vault_address,
            from_chain=from_chain,
            from_asset=from_asset,
            from_amount_wei=from_amount_wei,
            from_address=from_address,
            to_address=to_address,
            slippage=slippage,
            order=order,
        )
        vault_in = EarnGraphInput(
            operation="get_vault",
            chain=payload.vault_chain,
            chain_id=payload.vault_chain_id,
            vault_address=payload.vault_address,
        )
        vstate = earn_g.invoke({"input": vault_in.model_dump(mode="json", exclude_none=True)})
        if vstate.get("error"):
            return {"ok": False, "error": vstate["error"]}
        vres = vstate.get("result")
        if not isinstance(vres, dict):
            return {
                "ok": False,
                "error": {
                    "code": "http_error",
                    "message": "Unexpected earn get_vault result shape.",
                },
            }
        vault = vres.get("vault")
        if not isinstance(vault, dict):
            return {
                "ok": False,
                "error": {
                    "code": "http_error",
                    "message": "Unexpected earn get_vault payload.",
                },
            }
        ok_dep, dep_msg = _earn_deposit_vault_eligible(vault)
        if not ok_dep:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_input",
                    "message": dep_msg,
                    "details": {"vault_address": vault.get("address")},
                },
            }

        to_chain = vault.get("chain")
        if not to_chain and vault.get("chain_id") is not None:
            to_chain = chain_name_for_id(int(vault["chain_id"]))
        if not to_chain or not str(to_chain).strip():
            return {
                "ok": False,
                "error": {
                    "code": "invalid_input",
                    "message": "Could not resolve vault chain slug from Earn vault details.",
                    "details": {"chain_id": vault.get("chain_id")},
                },
            }

        to_eff = payload.to_address or payload.from_address
        v_addr = vault.get("address") or payload.vault_address
        swap_payload = SwapPrepareInput(
            from_chain=payload.from_chain,
            to_chain=str(to_chain).strip(),
            from_asset=payload.from_asset,
            to_asset=str(v_addr),
            from_amount_wei=payload.from_amount_wei,
            from_address=payload.from_address,
            to_address=to_eff,
            slippage=payload.slippage,
            order=payload.order,
        )
        out = _swap_prepare_with_prepared_storage(
            runtime,
            swap_g,
            prepare_lifi_g,
            swap_payload,
            log_tool_name="earn_prepare_deposit",
        )
        if out.get("ok") and isinstance(out.get("result"), dict):
            from_slug = payload.from_chain.strip().lower()
            dest_slug = str(to_chain).strip().lower()
            out["result"]["earn_deposit"] = {
                "vault": _earn_vault_summary_for_deposit(vault),
                "requires_status_polling": from_slug != dest_slug,
            }
        return out

    tools.append(earn_prepare_deposit)

    @tool(args_schema=LiFiStatusInput)
    def lifi_get_status(
        tx_hash: str,
        from_chain: str | None = None,
        to_chain: str | None = None,
        from_chain_id: int | None = None,
        to_chain_id: int | None = None,
        bridge: str | None = None,
    ) -> dict[str, Any]:
        """Poll **LiFi** ``GET /v1/status`` for bridging / Composer transfers.

        Optional authenticated calls use ``AUREY_LIFI_API_KEY`` or ``lifi_api_secret_path`` when configured.

        Use after ``swap_prepare`` or ``earn_prepare_deposit`` when **source and destination chains
        differ**: pass the tx hash from the *sending* chain (or LiFi step id), and optional
        ``from_chain`` / ``to_chain`` (or ids) plus ``bridge`` to disambiguate stuck routes.
        """
        st = LiFiStatusInput(
            tx_hash=tx_hash,
            from_chain=from_chain,
            to_chain=to_chain,
            from_chain_id=from_chain_id,
            to_chain_id=to_chain_id,
            bridge=bridge,
        )
        return _graph_payload(
            lifi_status_g.invoke({"input": st.model_dump(mode="json", exclude_none=True)})
        )

    tools.append(lifi_get_status)

    @tool(args_schema=TxPrepareLiFiInput)
    def tx_prepare_lifi_swap(
        chain: str,
        from_address: str,
        prepared: dict[str, Any] | None = None,
        prepared_id: str | None = None,
        route_id: str | None = None,
        transaction_request: dict[str, Any] | None = None,
        allowance_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Turn LiFi ``swap_prepare`` output into an executable envelope; RPC/signing stays server-side (1Claw, Alchemy-derived RPC).

        Prefer ``prepared_id`` from ``swap_prepare`` or ``earn_prepare_deposit``; it keeps large calldata out of the model
        context. Legacy callers may still pass ``prepared`` verbatim or ``route_id`` plus
        ``transaction_request``. Optional ``allowance_context`` is copied from ``swap_prepare`` when
        re-building an envelope so simulation errors can include sell-token allowance diagnostics.
        On success, call ``tx_execute(prepared_id=result['prepared_id'])``.
        Resolve ENS names on ethereum with ``evm_resolve_ens`` before supplying ``from_address``.
        """
        if prepared_id:
            record = runtime.prepared_txs.get(prepared_id)
            if record is None:
                return _invalid_prepared_id(prepared_id)
            if record.kind == "execute_envelope":
                summary = _envelope_summary(record.payload, prepared_id=prepared_id)
                return {"ok": True, "result": {"prepared_id": prepared_id, "envelope": summary}}
            if record.kind == "lifi_prepared":
                prepared = dict(record.payload)

        payload = TxPrepareLiFiInput(
            chain=chain,
            from_address=from_address,
            prepared=prepared,
            prepared_id=prepared_id,
            route_id=route_id,
            transaction_request=transaction_request,
            allowance_context=allowance_context,
        )
        t0 = time.perf_counter()
        out = _graph_payload(
            prepare_lifi_g.invoke({"input": payload.model_dump(mode="json", exclude_none=True)})
        )
        rid = route_id
        if rid is None and isinstance(prepared, dict):
            rid = prepared.get("route_id")
        if out.get("ok") and isinstance(out.get("result"), dict):
            envelope = out["result"].get("envelope")
            if isinstance(envelope, dict):
                stored_id = runtime.prepared_txs.put(
                    kind="execute_envelope",
                    payload=envelope,
                    summary=_envelope_summary(envelope),
                )
                out["result"] = {
                    "prepared_id": stored_id,
                    "envelope": _envelope_summary(envelope, prepared_id=stored_id),
                }
        log_swap_tool(
            name="tx_prepare_lifi_swap",
            wall_ms=(time.perf_counter() - t0) * 1000,
            ok=out.get("ok"),
            route_id=rid,
            chain=chain,
        )
        return out

    tools.append(tx_prepare_lifi_swap)

    @tool(args_schema=TxPrepareNativeArgs)
    def tx_prepare_native_transfer(
        chain: str,
        from_address: str,
        to_address: str,
        value_wei: int,
    ) -> dict[str, Any]:
        """Prepare a native transfer envelope; signing never exposes key material—1Claw signs using configured vault paths.

        On success (`ok` true), broadcast with ``tx_execute(prepared_id=result['prepared_id'])`` (preferred)
        or ``tx_execute(envelope=...)`` using the returned summary plus ``prepared_id``. If ``to_address`` is an ENS name,
        call ``evm_resolve_ens`` first on ethereum and use ``resolved_address`` as ``to_address``.
        """
        payload = TxPrepareNative(
            chain=chain,
            from_address=from_address,
            to_address=to_address,
            value_wei=value_wei,
        )
        return _attach_execute_prepared_id(runtime, _graph_payload(prepare_g.invoke({"input": payload.model_dump()})))

    tools.append(tx_prepare_native_transfer)

    @tool(args_schema=TxPrepareErc20TransferArgs)
    def tx_prepare_erc20_transfer(
        chain: str,
        from_address: str,
        token_address: str,
        to_address: str,
        amount_wei: int,
    ) -> dict[str, Any]:
        """Prepare ERC-20 transfer envelope; broadcasting signs via 1Claw—do not paste private keys.

        `amount_wei` is misleadingly named: use the token's **native decimals** (raw integer),
        not ETH wei. USDC = 6 decimals. On success (`ok` true), call
        ``tx_execute(prepared_id=result['prepared_id'])`` (preferred). Resolve ENS recipients with
        ``evm_resolve_ens`` (ethereum) before passing ``to_address``.
        """
        payload = TxPrepareErc20Transfer(
            chain=chain,
            from_address=from_address,
            token_address=token_address,
            to_address=to_address,
            amount_wei=amount_wei,
        )
        return _attach_execute_prepared_id(runtime, _graph_payload(prepare_g.invoke({"input": payload.model_dump()})))

    tools.append(tx_prepare_erc20_transfer)

    @tool(args_schema=TxPrepareErc20ApprovalArgs)
    def tx_prepare_erc20_approval(
        chain: str,
        from_address: str,
        token_address: str,
        spender_address: str,
        amount_wei: int,
    ) -> dict[str, Any]:
        """Prepare ERC-20 ``approve`` envelope; required before some LiFi swaps when ``allowance`` is returned.

        `amount_wei` must be raw token units per token decimals (USDC: 6). On success (`ok` true),
        broadcast with ``tx_execute(prepared_id=result['prepared_id'])`` (preferred) so calldata is not copied through the model.
        """
        payload = TxPrepareErc20Approval(
            chain=chain,
            from_address=from_address,
            token_address=token_address,
            spender_address=spender_address,
            amount_wei=amount_wei,
        )
        return _attach_execute_prepared_id(runtime, _graph_payload(prepare_g.invoke({"input": payload.model_dump()})))

    tools.append(tx_prepare_erc20_approval)

    @tool(args_schema=TxExecuteToolArgs)
    def tx_execute(
        envelope: dict[str, Any] | None = None,
        prepared_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Simulate, enforce policy, sign via 1Claw, and broadcast; never passes private keys through the model.

        Prefer ``prepared_id`` from any prepare step: ``swap_prepare``, ``earn_prepare_deposit``,
        ``tx_prepare_lifi_swap``, or **``tx_prepare_native_transfer`` / ``tx_prepare_erc20_*``**
        (avoids corrupting long ``data`` hex when the model copies an envelope). Legacy callers may
        pass the exact ``result['envelope']`` dict from a successful ``tx_prepare_*`` tool. If you mistakenly pass ``swap_prepare``'s legacy ``prepared`` object
        (``route_id`` + ``transaction_request`` only), this tool attempts to repair it.
        """
        if not prepared_id and isinstance(envelope, dict) and envelope.get("prepared_id"):
            prepared_id = str(envelope["prepared_id"])

        if prepared_id:
            record = runtime.prepared_txs.get(prepared_id)
            if record is None:
                return _invalid_prepared_id(prepared_id)
            if record.kind == "execute_envelope":
                envelope = dict(record.payload)
            elif record.kind == "lifi_prepared":
                fixed = _try_coerce_lifi_prepared_to_execute_envelope(
                    dict(record.payload),
                    prepare_lifi_g,
                )
                if fixed is None:
                    return _invalid_prepared_id(prepared_id)
                envelope = fixed

        if isinstance(envelope, dict):
            fixed = _try_coerce_lifi_prepared_to_execute_envelope(envelope, prepare_lifi_g)
            if fixed is not None:
                SWAP_LOG.info(
                    "tx_execute auto-prepared LiFi envelope from mistaken prepared blob "
                    "route_id=%s",
                    envelope.get("route_id") or envelope.get("routeId"),
                )
                envelope = fixed

        root = TxExecuteToolArgs(
            envelope=envelope,
            prepared_id=prepared_id,
            idempotency_key=idempotency_key,
        )
        execute_in = TxExecuteInput.model_validate(root.model_dump()).model_dump()
        t0 = time.perf_counter()
        out = _graph_payload(execute_g.invoke({"input": execute_in}))
        kind = envelope.get("kind") if isinstance(envelope, dict) else None
        th = None
        if out.get("ok") and isinstance(out.get("result"), dict):
            th = out["result"].get("tx_hash")
        log_swap_tool(
            name="tx_execute",
            wall_ms=(time.perf_counter() - t0) * 1000,
            ok=out.get("ok"),
            kind=kind,
            tx_hash=th,
        )
        return out

    tools.append(tx_execute)

    if (
        runtime.settings.evm_signing_mode == "oneclaw_intents"
        and runtime.oneclaw_evm_signer is not None
    ):
        signer = runtime.oneclaw_evm_signer

        @tool(args_schema=OneClawSignPersonalMessageArgs)
        def oneclaw_sign_personal_message(
            chain: str,
            message: str,
            signing_key_path: str | None = None,
        ) -> dict[str, Any]:
            """Sign a plaintext message off-chain via 1Claw ``intent_type: personal_sign`` (EIP-191). Does not broadcast.

            Requires the 1Claw agent to have **message signing** enabled by the operator. Use for wallet
            verification / auth challenges—not for swapping or transferring tokens on-chain."""
            principal, err = OneClawSigningPrincipal.resolve(runtime)
            if err is not None:
                return {"ok": False, "error": err}
            try:
                signed = signer.sign_personal_message(
                    agent_id=principal.agent_id,
                    chain=chain.strip(),
                    message=message,
                    signing_key_path=signing_key_path,
                    authorization_bearer=principal.authorization_bearer,
                )
            except (ValueError, OneClawSigningError, SecretStoreUnavailableError) as exc:
                return {"ok": False, "error": _oneclaw_signing_tool_error(exc)}
            return {
                "ok": True,
                "result": {
                    "signature": signed.signature,
                    "signer_address": signed.signer_address,
                    "chain": chain.strip(),
                },
            }

        @tool(args_schema=OneClawSignTypedDataArgs)
        def oneclaw_sign_typed_data(
            chain: str,
            typed_data: dict[str, Any],
            signing_key_path: str | None = None,
        ) -> dict[str, Any]:
            """Sign structured EIP-712 data via 1Claw unified ``intent_type: typed_data``.

            Permit / Permit2 and similar types require explicit operator allowlisting (**eip712** policy).
            This does **not** execute on-chain contracts by itself—a separate broadcast path may be needed."""
            principal, err = OneClawSigningPrincipal.resolve(runtime)
            if err is not None:
                return {"ok": False, "error": err}
            try:
                signed = signer.sign_typed_data(
                    agent_id=principal.agent_id,
                    chain=chain.strip(),
                    typed_data=typed_data,
                    signing_key_path=signing_key_path,
                    authorization_bearer=principal.authorization_bearer,
                )
            except (ValueError, OneClawSigningError, SecretStoreUnavailableError) as exc:
                return {"ok": False, "error": _oneclaw_signing_tool_error(exc)}
            return {
                "ok": True,
                "result": {
                    "signature": signed.signature,
                    "signer_address": signed.signer_address,
                    "chain": chain.strip(),
                },
            }

        @tool(args_schema=OneClawIntentsSignTransactionToolArgs)
        def oneclaw_intents_sign_transaction(
            chain: str,
            to: str,
            value: str = "0",
            data: str = "0x",
            signing_key_path: str | None = None,
            simulate_first: bool | None = None,
            nonce: int | None = None,
            gas_limit: int | None = None,
            gas_price: str | None = None,
            max_fee_per_gas: str | None = None,
            max_priority_fee_per_gas: str | None = None,
        ) -> dict[str, Any]:
            """Sign-only (BYORPC): build a signed serialized tx via ``POST …/transactions/sign`` without 1Claw broadcast.

            ``value`` is a **decimal ETH string** per Intents API (not wei). Prefer the normal prepare →
            ``tx_execute`` flow for standard swaps/transfers unless the user explicitly needs a raw signed tx
            for an external RPC or MEV path."""
            principal, err = OneClawSigningPrincipal.resolve(runtime)
            if err is not None:
                return {"ok": False, "error": err}
            try:
                req = IntentsSignTransactionRequest(
                    chain=chain.strip(),
                    to=to.strip(),
                    value=value,
                    data=data,
                    signing_key_path=signing_key_path,
                    simulate_first=simulate_first,
                    nonce=nonce,
                    gas_limit=gas_limit,
                    gas_price=gas_price,
                    max_fee_per_gas=max_fee_per_gas,
                    max_priority_fee_per_gas=max_priority_fee_per_gas,
                )
                out = signer.intents_sign_transaction(
                    agent_id=principal.agent_id,
                    request=req,
                    authorization_bearer=principal.authorization_bearer,
                )
            except (ValueError, OneClawSigningError, SecretStoreUnavailableError) as exc:
                return {"ok": False, "error": _oneclaw_signing_tool_error(exc)}
            res: dict[str, Any] = {
                "signed_tx": out.signed_tx,
                "tx_hash": out.tx_hash,
                "status": out.status,
                "from_address": out.from_address,
                "chain": out.chain_slug,
                "chain_id": out.chain_id,
            }
            if out.extras:
                res["extras"] = out.extras
            return {"ok": True, "result": res}

        tools.extend(
            [
                oneclaw_sign_personal_message,
                oneclaw_sign_typed_data,
                oneclaw_intents_sign_transaction,
            ]
        )

    @tool(args_schema=RequestUserInputArgs)
    def request_user_input(questions: list[UserQuestion]) -> dict[str, Any]:
        """Ask the host/UI for clarifying wallet-operation fields only; never solicit secrets or PII unrelated to txs."""
        count = note_user_input_request(questions)
        return {"ok": True, "result": {"status": "needs_user_input", "question_count": count}}

    tools.append(request_user_input)

    return tools


__all__ = [
    "AlchemyPortfolioArgs",
    "AlchemyTokenPricesArgs",
    "AlchemyTransferHistoryArgs",
    "ComputeTokenAmountFromUsdArgs",
    "EarnGetVaultArgs",
    "EarnListChainsArgs",
    "EarnListProtocolsArgs",
    "EarnListVaultsArgs",
    "EarnPortfolioPositionsArgs",
    "EarnPrepareDepositArgs",
    "EvmGetErc20BalanceArgs",
    "EvmGetNativeBalanceArgs",
    "EvmResolveEnsArgs",
    "ResolveKnownAddressArgs",
    "SwapPrepareInput",
    "TxPrepareErc20ApprovalArgs",
    "TxPrepareErc20TransferArgs",
    "TxPrepareNativeArgs",
    "TxPrepareErc20Approval",
    "TxPrepareErc20Transfer",
    "TxPrepareNative",
    "TxExecuteToolArgs",
    "build_aurey_subgraph_tools",
]
