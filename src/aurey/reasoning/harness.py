"""Optional Deep Agents harness registration (Mercury-style, wallet-tool focused)."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

AUREY_DEEP_HARNESS_BASE = (
    "You are Aurey - a custody-aware crypto operations agent.\n"
    "- Use the provided tools only; never invent balances, prices, routes, tx hashes, or token "
    "contract addresses—resolve tickers with ``resolve_known_address`` (bundled catalog).\n"
    "- Prefer the smallest tool that answers the user; combine reads before preparing "
    "transactions. ERC-20 amounts must use each token's on-chain decimals (e.g. USDC: 6; WETH: 18; "
    "WBTC: 8—call ``evm_get_erc20_decimals`` when unsure). If the user specifies a **USD notional** "
    "for the sell leg, call **``compute_token_amount_from_usd``**: use ``sell_kind=erc20`` (default) with "
    "the sell token ``token_address`` for ERC-20 sells; for **native ETH** sells use "
    "``sell_kind=native_eth`` (omit ``token_address``) and use ``amount_raw`` (wei) as "
    "``from_amount_wei``. Do not hand-compute or use balance unless they ask for max sell.\n"
    "- **One token balance:** resolve the token, then ``evm_get_erc20_balance``; use "
    "``evm_get_native_balance`` for gas ETH. **Full wallet:** ``alchemy_get_portfolio_tokens`` "
    "(include **``native_balance``**).\n"
    "- **request_user_input** is for blocking missing information only - keep questions minimal.\n"
    "Do not read or edit local files, spawn subagents, or run shell commands."
)


def resolve_harness_model_spec(model_hint: str | BaseChatModel | None) -> str:
    """Stable ``provider:model``-style key for harness registration (tests may use chat models)."""

    if isinstance(model_hint, str) and model_hint.strip():
        return model_hint.strip()
    if isinstance(model_hint, BaseChatModel):
        body = getattr(model_hint, "model_name", None) or getattr(model_hint, "model", None)
        if isinstance(body, str) and body.strip():
            return body.strip()
        return "openai:gpt-4o-mini"
    return "openai:gpt-4o-mini"


def ensure_aurey_wallet_harness(model_spec: str) -> None:
    """Register a lightweight harness profile when Deep Agents APIs are present."""

    try:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfileConfig,
            register_harness_profile,
        )
    except ImportError:
        return

    cfg = HarnessProfileConfig(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_middleware=frozenset({"TodoListMiddleware"}),
        base_system_prompt=AUREY_DEEP_HARNESS_BASE,
    )
    register_harness_profile(model_spec, cfg)


__all__ = [
    "AUREY_DEEP_HARNESS_BASE",
    "ensure_aurey_wallet_harness",
    "resolve_harness_model_spec",
]
