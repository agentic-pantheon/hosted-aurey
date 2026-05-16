"""LangGraph: prepare native/ERC-20 transactions as typed envelopes (paths-only secrets)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from aurey.cloud.signing_context import current_hosted_signing_context
from aurey.graphs.chains import chain_id_for, chain_info
from aurey.graphs.ens_eth import is_zero_address
from aurey.graphs.evm_codec import erc20_approve_data, erc20_transfer_data, normalize_evm_address
from aurey.graphs.results import GraphErrorBody, PreparedTxEnvelope
from aurey.runtime import AureyRuntime


def _evm_prepare_signing_settings_error(runtime: AureyRuntime) -> dict[str, Any] | None:
    settings = runtime.settings
    if settings.hosted_platform_enabled and settings.evm_signing_mode == "vault_key":
        return GraphErrorBody(
            code="secret_not_supported",
            message="Vault key signing is not supported when hosted_platform_enabled is true.",
        ).model_dump()
    if settings.evm_signing_requires_wallet_signing_key_secret_path:
        path = settings.wallet_signing_key_secret_path
        if path is None or not str(path).strip():
            return GraphErrorBody(
                code="secret_not_configured",
                message="Wallet signing key secret path is not configured.",
            ).model_dump()
    if settings.evm_signing_mode == "oneclaw_intents":
        ctx = current_hosted_signing_context.get()
        if settings.hosted_platform_enabled and ctx is not None:
            uid = (ctx.user_agent_id or "").strip()
            tok = (ctx.delegation_subject_token or "").strip()
            if not uid or not tok:
                return GraphErrorBody(
                    code="secret_not_configured",
                    message=(
                        "Hosted oneclaw_intents requires user_agent_id and delegation "
                        "subject token (provision the user and run /delegation_grant)."
                    ),
                ).model_dump()
        else:
            agent_id = settings.oneclaw_agent_id
            if agent_id is None or not str(agent_id).strip():
                return GraphErrorBody(
                    code="secret_not_configured",
                    message=(
                        "oneclaw_agent_id must be configured when evm_signing_mode is "
                        "oneclaw_intents."
                    ),
                ).model_dump()
    return None


def _prepared_tx_signing_kwargs(runtime: AureyRuntime) -> dict[str, Any]:
    mode = runtime.settings.evm_signing_mode
    path = runtime.settings.wallet_signing_key_secret_path
    if mode == "vault_key":
        return {"signing_mode": mode, "signing_key_secret_path": path.strip() if path else ""}
    return {"signing_mode": mode, "signing_key_secret_path": path.strip() if path else None}


class TxPrepareNative(BaseModel):
    kind: Literal["native_transfer"] = "native_transfer"
    chain: str = Field(min_length=1)
    from_address: str = Field(min_length=1)
    to_address: str = Field(min_length=1)
    value_wei: int = Field(ge=0)


class TxPrepareErc20Transfer(BaseModel):
    kind: Literal["erc20_transfer"] = "erc20_transfer"
    chain: str = Field(min_length=1)
    from_address: str = Field(min_length=1)
    token_address: str = Field(min_length=1)
    to_address: str = Field(min_length=1)
    amount_wei: int = Field(
        ge=0,
        description="Raw token amount (token-specific decimals; USDC uses 6).",
    )


class TxPrepareErc20Approval(BaseModel):
    kind: Literal["erc20_approval"] = "erc20_approval"
    chain: str = Field(min_length=1)
    from_address: str = Field(min_length=1)
    token_address: str = Field(min_length=1)
    spender_address: str = Field(min_length=1)
    amount_wei: int = Field(
        ge=0,
        description="Raw token amount (token-specific decimals; USDC uses 6).",
    )


TxPrepareInput = Annotated[
    TxPrepareNative | TxPrepareErc20Transfer | TxPrepareErc20Approval,
    Field(discriminator="kind"),
]

_tx_prepare_adapter = TypeAdapter(TxPrepareInput)


class TxPrepareGraphState(TypedDict, total=False):
    input: dict[str, Any]
    error: dict[str, Any]
    result: dict[str, Any]


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return GraphErrorBody(
        code="invalid_input",
        message="Transaction prepare input failed validation.",
        details={"errors": exc.errors()},
    ).model_dump()


def _validate_node(runtime: AureyRuntime, state: TxPrepareGraphState) -> TxPrepareGraphState:
    try:
        parsed = _tx_prepare_adapter.validate_python(state.get("input") or {})
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
        if isinstance(parsed, TxPrepareNative):
            normalize_evm_address(parsed.to_address)
        if isinstance(parsed, TxPrepareErc20Transfer):
            normalize_evm_address(parsed.token_address)
            normalize_evm_address(parsed.to_address)
        if isinstance(parsed, TxPrepareErc20Approval):
            normalize_evm_address(parsed.token_address)
            normalize_evm_address(parsed.spender_address)
    except ValueError as exc:
        return {
            "error": GraphErrorBody(
                code="invalid_input",
                message="Invalid address in transaction prepare input.",
                details={"reason": str(exc)},
            ).model_dump()
        }

    if isinstance(parsed, TxPrepareNative):
        if is_zero_address(parsed.to_address):
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="Native transfer recipient must not be the zero address.",
                ).model_dump()
            }
    if isinstance(parsed, TxPrepareErc20Transfer):
        if is_zero_address(parsed.to_address):
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ERC-20 transfer recipient must not be the zero address.",
                ).model_dump()
            }
        if is_zero_address(parsed.token_address):
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ERC-20 token_address must not be the zero address.",
                ).model_dump()
            }
    if isinstance(parsed, TxPrepareErc20Approval):
        if is_zero_address(parsed.token_address):
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ERC-20 token_address must not be the zero address.",
                ).model_dump()
            }
        if is_zero_address(parsed.spender_address):
            return {
                "error": GraphErrorBody(
                    code="invalid_input",
                    message="ERC-20 spender_address must not be the zero address.",
                ).model_dump()
            }

    return {}


def _execute_node(runtime: AureyRuntime, state: TxPrepareGraphState) -> TxPrepareGraphState:
    if state.get("error"):
        return {}

    parsed = _tx_prepare_adapter.validate_python(state["input"])
    chain = parsed.chain.strip().lower()
    cid = chain_id_for(chain)
    assert cid is not None
    signing = _prepared_tx_signing_kwargs(runtime)

    if isinstance(parsed, TxPrepareNative):
        env = PreparedTxEnvelope(
            kind="native_transfer",
            chain_id=cid,
            from_address=normalize_evm_address(parsed.from_address),
            to=normalize_evm_address(parsed.to_address),
            data="0x",
            value_hex=hex(parsed.value_wei),
            gas_limit_hex=None,
            nonce=None,
            **signing,
        )
        return {"result": {"envelope": env.model_dump()}}

    if isinstance(parsed, TxPrepareErc20Transfer):
        data = erc20_transfer_data(parsed.to_address, parsed.amount_wei)
        env = PreparedTxEnvelope(
            kind="erc20_transfer",
            chain_id=cid,
            from_address=normalize_evm_address(parsed.from_address),
            to=normalize_evm_address(parsed.token_address),
            data=data,
            value_hex="0x0",
            gas_limit_hex=None,
            nonce=None,
            **signing,
        )
        return {"result": {"envelope": env.model_dump()}}

    if isinstance(parsed, TxPrepareErc20Approval):
        data = erc20_approve_data(parsed.spender_address, parsed.amount_wei)
        env = PreparedTxEnvelope(
            kind="erc20_approval",
            chain_id=cid,
            from_address=normalize_evm_address(parsed.from_address),
            to=normalize_evm_address(parsed.token_address),
            data=data,
            value_hex="0x0",
            gas_limit_hex=None,
            nonce=None,
            **signing,
        )
        return {"result": {"envelope": env.model_dump()}}

    return {
        "error": GraphErrorBody(
            code="invalid_input",
            message="Unsupported prepare kind.",
        ).model_dump()
    }


def _route_after_validate(state: TxPrepareGraphState) -> Literal["execute", "done"]:
    return "done" if state.get("error") else "execute"


def build_tx_prepare_graph(runtime: AureyRuntime):
    g: StateGraph = StateGraph(TxPrepareGraphState)

    def validate(state: TxPrepareGraphState) -> TxPrepareGraphState:
        return _validate_node(runtime, state)

    def execute(state: TxPrepareGraphState) -> TxPrepareGraphState:
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
