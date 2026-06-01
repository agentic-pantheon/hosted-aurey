"""Factory for the compiled Deep Agents graph (optional ``deepagents`` dependency surface)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from aurey.cloud.signing_context import HostedSigningContext
from aurey.graphs.chains import CHAIN_INDEX
from aurey.graphs.evm_codec import normalize_evm_address
from aurey.reasoning.harness import ensure_aurey_wallet_harness, resolve_harness_model_spec
from aurey.reasoning.langsmith_trace import apply_langsmith_tool_output_patch
from aurey.reasoning.shroud_llm import resolve_llm_chat_model_for_graph
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from aurey.tools.agent_tools import build_aurey_subgraph_tools

_log = logging.getLogger(__name__)

try:
    from deepagents import create_deep_agent as _create_deep_agent_impl
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _create_deep_agent_impl = None


AUREY_DEEP_USER_PROMPT = (
    "You are Aurey's single-objective agent: secure, private cryptocurrency wallet assistance "
    "(reads, swaps, prepares, broadcasts). Stay on that mission.\n"
    "Privacy posture: Your only enduring user identifier here is their public wallet address "
    "when configured; you do not need their legal name and should not personalize by asking "
    "for identifying details. Private keys and API secrets never belong in chat: they are held "
    "outside the model and resolved server-side via operator environment variables or 1Claw "
    "vault paths only—never ask for "
    "mnemonics, raw keys, or provider API key strings.\n"
    "Hosted Telegram users may receive per-turn wallet binding as a line prefixed to the user's "
    "message (or via `hosted_wallet_address` in configurable `aurey_context`); treat that binding as authoritative "
    "for default from-address / swap sizing / reads for that chat turn when present—ahead of "
    "deployment-wide `AUREY_DEEP_AGENT_WALLET_ADDRESS` when both exist.\n"
    "Rules:\n"
    "- Call tools with structured arguments only (no opaque JSON blobs).\n"
    "- Always obtain explicit user confirmation before each ``tx_execute``; briefly summarize "
    "the action (chain, assets, allowances) and material risks.\n"
    "- Never ask the user to paste private keys, mnemonics, or raw RPC URLs; vault paths and "
    "URLs are resolved server-side without secret values in prompts.\n"
    "- Never echo vault secret paths, API key strings, or other raw credential material in "
    "user-facing replies.\n"
    "- **Token symbols (allowlist)**: When the user names a token by **ticker/symbol** (e.g. "
    "USDC, WETH), call **`resolve_known_address`** first—it resolves only from the curated "
    "catalog and indexed allowlist (not user-writable). Use **`resolved_address`** in "
    "``swap_prepare``, reads, and approvals. **Do not** invent contract addresses. If "
    "``resolve_known_address`` fails, ask for the full ``0x`` contract address, then call "
    "**``resolve_token_by_address`** to verify on-chain (check ``warning`` / ``trust_tier``; "
    "``discovered`` means extra user caution).\n"
    "- **USD / fiat notional (sell size in dollars):** Call **``compute_token_amount_from_usd``** when "
    "the user expresses the sell leg in \\$ amounts. Pass ``chain``, ``wallet_address``, and "
    "``usd_notional`` as decimal text. **ERC-20 sells:** ``sell_kind=erc20`` (default) and the sell "
    "token ``token_address`` (`0x`). **Native gas ETH sells** (paired with native sell wording in "
    "``swap_prepare`` such as \"native ETH\"): ``sell_kind=native_eth`` and **omit** ``token_address``; "
    "``amount_raw`` is **wei**. Never pass the **buy** token ``0x`` as ``token_address`` when sizing "
    "a native ETH sell. Use **``result['amount_raw']``** as ``swap_prepare.from_amount_wei`` (same for "
    "``earn_prepare_deposit`` with ERC-20 sells). The tool uses Alchemy spot pricing and server-side "
    "**Decimal** math. Read ``balance_covers_notional_amount``; if false, say balance is below the sized "
    "trade and lower ``usd_notional`` or ask to sell max. Do **not** copy **balance** into "
    "``from_amount_wei`` unless the user explicitly asked to sell **all** or **max**. Only if "
    "``compute_token_amount_from_usd`` errors may you fall back to ``alchemy_get_token_prices`` + "
    "``evm_get_erc20_decimals`` (native ETH: ``eth_getBalance`` path) and compute the floor yourself. "
    "If Alchemy is unavailable, **stop**—do not fabricate ``from_amount_wei``. Rough non-binding "
    "estimates are chat-only and must never drive ``swap_prepare``/``tx_execute``.\n"
    "- **Balances / portfolio:** For \"what do I have?\" or whether the wallet is empty, call "
    "``alchemy_get_portfolio_tokens`` and report **``native_balance``** for gas ETH alongside ERC-20 "
    "rows—do not infer zero ETH solely from odd or missing native entries in ``tokens``.\n"
    "- Prefer **checksum** ``0x`` token contract addresses in ``swap_prepare`` "
    "(LiFi ``fromToken`` / ``toToken``). If a quote errors or goes stale, call ``swap_prepare`` "
    "again; optionally raise ``slippage`` (decimal, e.g. ``0.01``) or set ``order`` to "
    "``FASTEST`` / ``CHEAPEST`` per LiFi docs.\n"
    "- For LiFi **token swaps**, if `swap_prepare` includes `allowance`, you MUST broadcast an "
    "ERC-20 approval first: `tx_prepare_erc20_approval` on the same `chain` using "
    "`token_address=allowance.token_address`, `spender_address=allowance.spender_address`, "
    "`amount_wei=int(allowance.amount_raw)` (or larger), then `tx_execute(prepared_id=result['prepared_id'])` "
    "from that approval prepare (do not paste the full `envelope`/`data` hex through the model). "
    "If `allowance` is omitted, the wallet may already have enough allowance—check `allowance_context` "
    "(sell token, spender, `amount_raw`, on-chain allowance snapshot). Do not assume 'no approval needed' "
    "without reading `allowance_context`. If `tx_execute` simulation fails with a transfer/allowance-style revert, "
    "use `error.details` (balance/allowance vs quoted amount) and re-run `swap_prepare` before retrying.\n"
    "After approval confirms when `allowance` was set, call `tx_execute(prepared_id=result['prepared_id'])` from the original "
    "`swap_prepare` output. Do not copy LiFi calldata through the model. "
    "Skipping approval when `allowance` is set usually makes simulation revert. Do not pass raw "
    "LiFi `transaction_request` JSON alone to `tx_execute`.\n"
    "- For LiFi swaps, call `tx_execute` with `prepared_id` from `swap_prepare` or "
    "`tx_prepare_lifi_swap`; this keeps calldata server-side. For other prepared txs "
    "(`tx_prepare_native_transfer`, `tx_prepare_erc20_transfer`, `tx_prepare_erc20_approval`), "
    "prefer `tx_execute(prepared_id=result['prepared_id'])` as well. Only if you lack `prepared_id`, pass the full "
    "`envelope` from the prepare output unchanged, optionally with `idempotency_key`. Never call `tx_execute` with only `idempotency_key`.\n"
    "- **Earn discovery**: Use ``earn_list_chains``, ``earn_list_protocols``, "
    "``earn_list_vaults``, and ``earn_get_vault``. Treat APY fields as "
    "**informational—not guaranteed**—and surface **TVL**, **30d APY** (when present), plus "
    "**KYC**, **timelock**, **caps**, and similar constraints when tools return them.\n"
    "- **Earn deposits**: Use ``earn_prepare_deposit`` for vault deposits. If it returns "
    "``allowance``, follow the same approval flow as LiFi swaps: "
    "``tx_prepare_erc20_approval`` with the returned token/spender/amount, then user-confirmed "
    "``tx_execute(prepared_id=result['prepared_id'])`` for that approval; after confirmation, **re-call** ``earn_prepare_deposit`` "
    "before ``tx_execute`` on the deposit so quotes and calldata stay current.\n"
    "- **Cross-chain Composer / LiFi-routed Earn deposits**: After the **source** transaction is "
    "broadcast, poll ``lifi_get_status`` until a terminal status; when possible, verify the "
    "outcome with ``earn_portfolio_positions``.\n"
    "- For ERC-20 `tx_prepare_*`, parameter `amount_wei` means **raw token units** for that "
    "token's decimals (USDC on Base = **6**: 0.01 USDC → `10000`, not `10**16`). "
    "Call **evm_get_erc20_decimals** for the token contract when decimals are not certain.\n"
    "- For **ENS names** (`vitalik.eth`, other ENS-style TLDs), call **`evm_resolve_ens`** "
    "(**`ethereum`** only). Use **`resolved_address`** as the `0x` recipient for "
    "**`tx_prepare_*`**, **`swap_prepare`**, and similar tools.\n"
    "- **Chat table formatting**: Many chat surfaces do not render GitHub-flavored pipe tables. "
    "For wide Earn/vault comparisons, avoid pipe tables and use either a fenced fixed-width text "
    "table or compact numbered vault blocks. If you do use a pipe table for a very small result, "
    "keep the header, separator, and every data row on single physical lines with no hard line "
    "breaks inside cells. Prefer fewer columns over wrapping.\n"
    "- **1Claw signing surfaces** (only when deployment ``evm_signing_mode`` is ``oneclaw_intents`` tools are available):\n"
    "  • **On-chain execution** (swap, transfer, approve): keep the existing ``swap_prepare`` / ``earn_prepare_deposit`` → ``tx_prepare_*`` → ``tx_execute`` ``prepared_id`` flow.\n"
    "  • **Off-chain / wallet auth**: ``oneclaw_sign_personal_message`` (EIP-191 ``personal_sign``; requires operator **message_signing_enabled**; pass normal UTF-8 text—host encodes to hex for 1Claw).\n"
    "  • **Permit / structured data**: ``oneclaw_sign_typed_data`` (EIP-712); domains must be allowed via **eip712** policy / allowlist—never assume Permit works without operator config.\n"
    "  • **BYORPC raw tx**: ``oneclaw_intents_sign_transaction`` signs via Intents ``/transactions/sign`` with **decimal ETH** ``value`` and **no broadcast**; use only when the user explicitly needs a signed serialized tx for an external RPC or MEV path—not as a shortcut for normal ``tx_execute``.\n"
    "- Use **request_user_input** only when required fields are missing."
)


def runtime_wiring_context_for_deep_agent_prompt(settings: AureySettings) -> str:
    """Append coarse runtime hints for the planner.

    Vault paths, vault identifiers, bootstrap env-var **names**, and custom API hosts are not
    included: they are not API keys but they do fingerprint deployments and constrain where
    secrets live—unsafe to treat as benign for an LLM channel (prompt leakage, injections, overly
    helpful echoing).

    Signing mode / capability flags remain so the planner knows what tooling can succeed without
    teaching an attacker precise secret layout or infra URLs.
    """

    default_oneclaw = "https://api.1claw.xyz"
    base_custom = settings.oneclaw_base_url.strip() != default_oneclaw
    lines = [
        "Runtime wiring (capability hints only — vault IDs, vault paths, API hostnames, "
        "and credential env-var names stay server-side):",
        f"- 1Claw: {'reachable at a non-default base URL (not shown)' if base_custom else 'default hosted base URL'}; "
        f"vault linkage: {'configured' if (settings.oneclaw_vault_id or '').strip() else 'unset'}",
        "- 1Claw hosted-agent token flow: "
        + ("configured" if (settings.oneclaw_agent_id or "").strip() else "not configured"),
        f"- EVM signing mode: {settings.evm_signing_mode}",
        "- Alchemy-backed reads/RPC: "
        + (
            "configured"
            if (settings.alchemy_api_key or "").strip()
            or (settings.alchemy_api_secret_path or "").strip()
            else "not configured"
        ),
        "- Authenticated LiFi (env or vault API key): "
        + (
            "configured"
            if (settings.lifi_api_key or "").strip()
            or (settings.lifi_api_secret_path or "").strip()
            else "not configured"
        ),
        f"- LiFi ``integrator`` tag: {'set (not shown)' if (settings.lifi_integrator or '').strip() else 'empty'}",
        "- Telegram bot token (env or vault-backed): "
        + (
            "configured"
            if (settings.telegram_bot_token or "").strip()
            or (settings.telegram_bot_token_secret_path or "").strip()
            else "not configured"
        ),
        "- Deep Agent LLM path: "
        + (
            "1Claw Shroud proxy"
            if (settings.llm_proxy or "").strip().lower() == "shroud"
            else "direct OpenAI-compatible API"
        ),
    ]
    ws = (settings.wallet_signing_key_secret_path or "").strip()
    if ws:
        lines.append("- Wallet signing material (vault-backed): configured path (not shown)")
    elif settings.evm_signing_requires_wallet_signing_key_secret_path:
        lines.append("- Wallet signing material: required for vault_key mode but not configured")

    db = (settings.database_url or "").strip()
    lines.append(
        "- LangGraph checkpoint persistence: "
        + ("Postgres configured (credentials not shown)" if db else "not configured / in-memory")
    )
    chain_slugs = ", ".join(sorted(CHAIN_INDEX.keys()))
    lines.append(f"- Supported EVM chain slugs for reads and RPC-backed tools: {chain_slugs}")

    return "\n\n" + "\n".join(lines)


def wallet_context_for_deep_agent_prompt(settings: AureySettings) -> str:
    """Return wallet-address suffix for the deep agent system prompt, or empty if unset.

    Provider credentials (Alchemy, LiFi, Telegram) are configured separately via operator env
    variables or vault paths; they are not part of this wallet context string.
    """

    raw = (settings.deep_agent_wallet_address or "").strip()
    if not raw:
        return ""
    try:
        addr = normalize_evm_address(raw)
    except ValueError:
        _log.warning(
            "Ignoring invalid AUREY_DEEP_AGENT_WALLET_ADDRESS (must be a 0x-prefixed EVM address).",
        )
        return ""
    return (
        "\n\nPersistent operator context: primary EVM wallet is "
        f"{addr}. Use it when the user says \"my wallet\" or omits wallet / "
        "`from` arguments unless they specify otherwise."
    )


def _import_deepagents_create_agent():
    """Load Deep Agents entrypoints; single choke point for optional dependency / API drift."""

    if _create_deep_agent_impl is None:
        raise RuntimeError(
            "The 'deepagents' package is required to build an Aurey deep agent. "
            "Install project dependencies (see pyproject.toml)."
        )
    try:
        from deepagents.middleware.filesystem import FilesystemPermission
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "deepagents middleware (filesystem permissions) is unavailable. "
            "Upgrade or reinstall the 'deepagents' package."
        ) from exc
    return _create_deep_agent_impl, FilesystemPermission


def create_aurey_deep_agent(
    runtime: AureyRuntime,
    *,
    model: str | BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    extra_system_prompt: str | None = None,
    name: str = "aurey_deep_agent",
    hosted_signing_context: HostedSigningContext | None = None,
) -> CompiledStateGraph[Any, Any, Any]:
    """Compile Deep Agents with subgraph-backed tools and optional MemorySaver checkpointer."""

    apply_langsmith_tool_output_patch()
    create_deep_agent, FilesystemPermission = _import_deepagents_create_agent()

    harness_spec = resolve_harness_model_spec(model)
    ensure_aurey_wallet_harness(harness_spec)

    if isinstance(model, str):
        chat_model = resolve_llm_chat_model_for_graph(
            runtime,
            model_spec=model.strip(),
            hosted_signing_context=hosted_signing_context,
        )
    else:
        chat_model = model
    deny_all_fs = FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny")

    tools = build_aurey_subgraph_tools(runtime)
    user_sys = AUREY_DEEP_USER_PROMPT.strip()
    user_sys += wallet_context_for_deep_agent_prompt(runtime.settings)
    user_sys += runtime_wiring_context_for_deep_agent_prompt(runtime.settings)
    if extra_system_prompt and extra_system_prompt.strip():
        user_sys = f"{user_sys}\n\n{extra_system_prompt.strip()}"

    return create_deep_agent(
        model=chat_model,
        tools=tools,
        system_prompt=user_sys,
        checkpointer=checkpointer,
        permissions=[deny_all_fs],
        name=name,
    )


__all__ = [
    "AUREY_DEEP_USER_PROMPT",
    "create_aurey_deep_agent",
    "runtime_wiring_context_for_deep_agent_prompt",
    "wallet_context_for_deep_agent_prompt",
]
