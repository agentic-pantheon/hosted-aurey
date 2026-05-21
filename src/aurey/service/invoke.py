"""Shared Deep Agent invocation path for HTTP and Telegram surfaces."""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel

from aurey.cloud.signing_context import (
    HostedSigningContext,
    hosted_signing_context_scope,
)
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.reasoning import thread_config
from aurey.reasoning.shroud_llm import hosted_shroud_llm_credentials_ready
from aurey.service.agent_trace import build_agent_trace_handler, format_exception_chain
from aurey.service.message_content import (
    flatten_message_content,
    reply_preview_from_summary,
    summarize_agent_messages,
)
from aurey.service.state import AureyServiceState

_log = logging.getLogger("aurey.turn")

# Transient LLM HTTP failures (server disconnect before response; httpx RemoteProtocolError).
_MODEL_TRANSIENT_ATTEMPTS = 4
_MODEL_TRANSIENT_BASE_DELAY_SEC = 1.5


def _log_clip(text: str, max_chars: int = 4000) -> str:
    """Single-line, length-bounded text for log lines."""

    collapsed = " ".join(text.strip().split())
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[:max_chars]} ... [truncated, {len(collapsed)} chars total]"


def _kv_line(**parts: str | int) -> str:
    """Compact key=value line (logger name already identifies the subsystem)."""

    return "  ".join(f"{k}={v}" for k, v in parts.items())


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Treat OpenAI SDK and common httpx/httpcore network failures as retryable (incl. wrapped)."""

    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (APIConnectionError, APITimeoutError)):
            return True
        mod = type(e).__module__
        name = type(e).__name__
        if mod == "httpx" and name in (
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "RemoteProtocolError",
            "WriteTimeout",
            "PoolTimeout",
        ):
            return True
        if mod == "httpcore" and "Timeout" in name:
            return True
        e = e.__cause__ or e.__context__
    return False


HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY = "hosted_wallet_from_server"


def _resolve_hosted_wallet_address_hint(
    context: dict[str, Any],
    *,
    hosted_signing_context: HostedSigningContext | None,
    hosted_platform_enabled: bool,
) -> str | None:
    """Return checksummed EVM wallet from invoke context or signing context.

    When ``hosted_platform_enabled``, ignore client-supplied ``hosted_wallet_address`` on
    HTTP ``/v1/invoke``. Telegram sets ``hosted_wallet_from_server`` when the address comes
    from Postgres (provisioning / signing-keys backfill).
    """

    raw: str | None = None
    if hosted_signing_context is not None:
        w = (hosted_signing_context.wallet_address or "").strip()
        if w:
            raw = w
    if not raw and context.get(HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY) is True:
        v = context.get("hosted_wallet_address")
        if isinstance(v, str) and v.strip():
            raw = v.strip()
    if not raw and not hosted_platform_enabled:
        v = context.get("hosted_wallet_address")
        if isinstance(v, str) and v.strip():
            raw = v.strip()
    if not raw:
        return None
    try:
        return to_checksum_evm_address(raw)
    except ValueError:
        _log.warning("Ignoring invalid hosted_wallet_address hint for invoke.")
        return None


def _hosted_wallet_system_turn_line(addr: str) -> str:
    return (
        "Hosted-user binding for this chat turn: the default EVM wallet address "
        f"(from/swap/read context) is {addr}. Treat this as authoritative when the user "
        'says "my wallet" or omits addresses unless they explicitly name a different '
        "`0x` or ENS name."
    )


def _invoke_graph_with_transient_retries(
    graph,
    *,
    message: str,
    config: dict[str, Any],
    hosted_wallet_address: str | None = None,
) -> Any:
    """Retry LLM HTTP/network blips during ``graph.invoke`` (often wrapped by LangChain)."""

    messages: list[Any] = []
    if hosted_wallet_address:
        messages.append(
            SystemMessage(content=_hosted_wallet_system_turn_line(hosted_wallet_address))
        )
    messages.append(HumanMessage(content=message))
    payload = {"messages": messages}
    last_exc: BaseException | None = None
    for attempt in range(_MODEL_TRANSIENT_ATTEMPTS):
        try:
            return graph.invoke(payload, config=config)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_llm_error(exc):
                raise
            if attempt + 1 >= _MODEL_TRANSIENT_ATTEMPTS:
                raise
            delay = _MODEL_TRANSIENT_BASE_DELAY_SEC * (2**attempt)
            _log.warning(
                "LLM transient network error (attempt %s/%s), retrying in %.1fs: %s",
                attempt + 1,
                _MODEL_TRANSIENT_ATTEMPTS,
                delay,
                format_exception_chain(exc, max_chars=600),
            )
            time.sleep(delay)
    raise AssertionError("unreachable") from last_exc


class AgentInvokeError(BaseModel):
    code: str
    message: str


class AgentInvokeResult(BaseModel):
    ok: bool
    session_id: str | None = None
    messages: list[dict[str, Any]] | None = None
    error: AgentInvokeError | None = None


def invoke_deep_agent_turn(
    svc: AureyServiceState | None,
    *,
    message: str,
    session_id: str,
    context: dict[str, Any] | None = None,
    model: str | None = None,
    extra_callbacks: list[Any] | None = None,
    hosted_signing_context: HostedSigningContext | None = None,
) -> AgentInvokeResult:
    """Invoke the shared deep-agent graph with sanitized error responses.

    ``hosted_signing_context`` is optional: bind per-user 1Claw delegation (Telegram-hosted).
    The HTTP ``POST /v1/invoke`` entrypoint typically omits it (MVP).
    """

    _log.info(
        "incoming  %s",
        _kv_line(session=session_id, text=_log_clip(message)),
    )

    if svc is None:
        err_msg = "The service is missing required configuration or bootstrap credentials."
        _log.info(
            "error  %s",
            _kv_line(session=session_id, code="service_misconfigured", detail=_log_clip(err_msg)),
        )
        return AgentInvokeResult(
            ok=False,
            session_id=session_id,
            error=AgentInvokeError(
                code="service_misconfigured",
                message=err_msg,
            ),
        )

    spec = (model or "").strip() or svc.default_model
    merged_context: dict[str, Any] = dict(context) if context else {}
    hosted_wallet_resolved = _resolve_hosted_wallet_address_hint(
        merged_context,
        hosted_signing_context=hosted_signing_context,
        hosted_platform_enabled=bool(svc.settings.hosted_platform_enabled),
    )
    if hosted_wallet_resolved:
        merged_context["hosted_wallet_address"] = hosted_wallet_resolved

    ctx_keys = ",".join(sorted(merged_context)) if merged_context else ""
    _log.debug(
        "invoke context  %s",
        _kv_line(model=spec, context_keys=ctx_keys or "(none)", session=session_id),
    )

    extra: dict[str, Any] = {}
    if merged_context:
        extra["aurey_context"] = merged_context
    config = thread_config(session_id, **extra)
    merged: list[Any] = []
    trace_handler = build_agent_trace_handler(session_id=session_id)
    if trace_handler is not None:
        merged.append(trace_handler)
    prior = config.get("callbacks")
    if prior is not None:
        if isinstance(prior, list):
            merged.extend(prior)
        else:
            merged.append(prior)
    if extra_callbacks:
        merged.extend(extra_callbacks)
    if merged:
        config = {**config, "callbacks": merged}

    settings = svc.settings
    hosted_for_graph = (
        hosted_signing_context if (settings.llm_proxy or "").strip().lower() == "shroud" else None
    )
    if hosted_for_graph is not None:
        if not hosted_shroud_llm_credentials_ready(svc.runtime, hosted_for_graph):
            err_msg = "Hosted LLM (Shroud) requires a provisioned agent API key for this user."
            _log.info(
                "error  %s",
                _kv_line(
                    session=session_id,
                    code="llm_credentials_unavailable",
                    detail=_log_clip(err_msg),
                ),
            )
            return AgentInvokeResult(
                ok=False,
                session_id=session_id,
                error=AgentInvokeError(
                    code="llm_credentials_unavailable",
                    message=err_msg,
                ),
            )

    try:
        graph = svc.get_or_create_graph(model, hosted_signing_context=hosted_for_graph)
    except RuntimeError as exc:
        code = "deep_agent_unavailable"
        if "deepagents" in str(exc).lower():
            code = "deep_agent_dependency"
        _log.debug("graph compile failed", exc_info=True)
        err_msg = "The deep agent runtime is not available or misconfigured."
        _log.info(
            "error  %s",
            _kv_line(session=session_id, code=code, detail=_log_clip(err_msg)),
        )
        return AgentInvokeResult(
            ok=False,
            session_id=session_id,
            error=AgentInvokeError(
                code=code,
                message=err_msg,
            ),
        )

    try:
        if hosted_signing_context is not None:
            with hosted_signing_context_scope(hosted_signing_context):
                result = _invoke_graph_with_transient_retries(
                    graph,
                    message=message,
                    config=config,
                    hosted_wallet_address=hosted_wallet_resolved,
                )
        else:
            result = _invoke_graph_with_transient_retries(
                graph,
                message=message,
                config=config,
                hosted_wallet_address=hosted_wallet_resolved,
            )
    except Exception as exc:
        _log.warning(
            "agent invoke failed after retries  session=%s  detail=%s",
            session_id,
            format_exception_chain(exc, max_chars=1200),
            exc_info=True,
        )
        err_msg = "The agent failed to complete this turn."
        _log.info(
            "error  %s",
            _kv_line(
                session=session_id,
                code="agent_invoke_failed",
                detail=_log_clip(err_msg),
            ),
        )
        return AgentInvokeResult(
            ok=False,
            session_id=session_id,
            error=AgentInvokeError(
                code="agent_invoke_failed",
                message=err_msg,
            ),
        )

    raw_messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(raw_messages, list):
        raw_messages = []

    summarized = summarize_agent_messages(raw_messages)
    preview = reply_preview_from_summary(summarized) or ""
    _log.info(
        "complete  %s",
        _kv_line(
            session=session_id,
            messages=len(summarized),
            preview=_log_clip(preview),
        ),
    )

    return AgentInvokeResult(
        ok=True,
        session_id=session_id,
        messages=summarized,
    )


__all__ = [
    "AgentInvokeError",
    "AgentInvokeResult",
    "HostedSigningContext",
    "flatten_message_content",
    "invoke_deep_agent_turn",
    "summarize_agent_messages",
]
