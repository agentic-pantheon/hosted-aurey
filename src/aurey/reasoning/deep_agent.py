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
    "Privacy: use the user's public wallet address when configured; never ask for legal name or "
    "PII. Private keys, mnemonics, and API secrets never belong in chat—they are resolved "
    "server-side (env or 1Claw vault). Never echo vault paths or credential material.\n"
    "Hosted Telegram: honor per-turn EVM/Solana wallet binding from context or prefixed lines; "
    "call ``get_hosted_wallet_addresses`` when Solana/EVM addresses are missing—do not guess.\n"
    "- Aurey peer sends: resolve ``@handle`` with ``resolve_hosted_recipient_by_handle`` before "
    "prepare; confirm using the handle. On ``recipient_not_found`` or ``recipient_wallet_unavailable``, "
    "if the tool returns ``invite_deeplink``, you MUST paste that exact URL only (top-level or "
    "``error.invite_deeplink``)—never invent waitlist, marketing, or other ``t.me`` links. "
    "If there is no ``invite_deeplink``, say setup is blocked and do not fabricate a link. "
    "No escrow. When ``resolved_via_handle_claim`` is true, show ``telegram_user_id`` and "
    "``recipient_binding_note`` for sender verification. After ``tx_execute``, the recipient may get a "
    "Telegram DM if they use the bot.\n"
    "Core rules:\n"
    "- Structured tool arguments only; smallest tool that answers the user.\n"
    "- Explicit user confirmation before every ``tx_execute``; summarize chain, assets, risks.\n"
    "- Resolve tokens via allowlist tools (``resolve_known_address``, ``resolve_token_by_name``, "
    "``resolve_token_by_address``)—never invent ``0x`` addresses. Use ``list_supported_tokens`` "
    "for catalog questions.\n"
    "- Balances: ``evm_get_erc20_balance`` / ``evm_get_native_balance`` for one asset; "
    "``alchemy_get_portfolio_tokens`` for full wallet (include ``native_balance``).\n"
    "- USD sell sizing: ``compute_token_amount_from_usd`` → use ``amount_raw`` in "
    "``swap_prepare`` / ``earn_prepare_deposit``; do not fabricate amounts if pricing fails.\n"
    "- Swaps / Earn: follow tool docstrings for ``swap_prepare``, approvals, "
    "``tx_execute(prepared_id=...)``, ``earn_prepare_deposit``, and ``lifi_get_status`` on "
    "cross-chain routes. Prefer ``prepared_id`` over copying calldata or envelopes.\n"
    "- ENS on ethereum: ``evm_resolve_ens`` before using names as recipients.\n"
    "- ERC-20 ``amount_wei`` fields are raw token units (check ``evm_get_erc20_decimals``).\n"
    "- 1Claw off-chain signing tools only when ``evm_signing_mode`` is ``oneclaw_intents``; "
    "normal on-chain flow stays prepare → ``tx_execute``.\n"
    "- ``request_user_input`` only for missing non-secret fields.\n"
    "- Chat: avoid wide pipe tables; use compact blocks or short fixed-width tables."
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
