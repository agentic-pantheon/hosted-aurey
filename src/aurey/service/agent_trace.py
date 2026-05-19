"""Optional LangChain / LangGraph callbacks for step-by-step agent visibility."""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

_log = logging.getLogger("aurey.agent.trace")


def _clip(text: str, max_chars: int = 4000) -> str:
    collapsed = " ".join(str(text).strip().split())
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[:max_chars]} ... [truncated, {len(collapsed)} chars total]"


def format_exception_chain(exc: BaseException, *, max_chars: int = 900) -> str:
    """Format exception plus causes/context (LLM errors are often wrapped)."""

    parts: list[str] = []
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and len(parts) < 8 and id(e) not in seen:
        seen.add(id(e))
        parts.append(f"{type(e).__module__}.{type(e).__name__}: {e}")
        e = e.__cause__ or e.__context__
    return _clip(" | caused_by: ".join(parts), max_chars)


def _kv_line(**parts: str | int | UUID | None) -> str:
    rendered: list[str] = []
    for k, v in parts.items():
        if v is None:
            continue
        rendered.append(f"{k}={v}")
    return "  ".join(rendered)


def agent_trace_detail() -> str | None:
    """Return trace level from ``AUREY_AGENT_TRACE``.

    - ``None``: off
    - ``\"minimal\"``: tool/LLM boundaries and errors only (no large tool payloads)
    - ``\"info\"``: default; tool I/O clipped to 2000 chars
    - ``\"debug\"``: all graph nodes, per-token stream, verbose LLM metadata
    """

    raw = os.environ.get("AUREY_AGENT_TRACE", "").strip().lower()
    if not raw or raw in ("0", "false", "no", "off"):
        return None
    if raw in ("debug", "verbose", "2"):
        return "debug"
    if raw in ("minimal", "lite", "warn"):
        return "minimal"
    return "info"


def build_agent_trace_handler(*, session_id: str) -> BaseCallbackHandler | None:
    """Create a handler when tracing is enabled; callers attach via ``config[\"callbacks\"]``."""

    detail = agent_trace_detail()
    if detail is None:
        return None
    return AureyAgentTraceHandler(session_id=session_id, detail=detail)


_TOOL_IO_CLIP_INFO = 2000
_TOOL_IO_CLIP_MINIMAL = 280


class AureyAgentTraceHandler(BaseCallbackHandler):
    """Logs graph node transitions (from LangGraph metadata), tool boundaries, and LLM stream."""

    def __init__(self, *, session_id: str, detail: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._detail = detail

    def _tool_io_clip(self) -> int:
        if self._detail == "minimal":
            return _TOOL_IO_CLIP_MINIMAL
        return _TOOL_IO_CLIP_INFO

    def _meta(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        m = kwargs.get("metadata")
        return m if isinstance(m, dict) else {}

    def _should_log_chain(self, meta: dict[str, Any]) -> bool:
        node = meta.get("langgraph_node")
        if not isinstance(node, str):
            return False
        if self._detail == "debug":
            return True
        return node in {"model", "tools"}

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        if not self._should_log_chain(meta):
            return
        name = None
        if isinstance(serialized, dict):
            name = serialized.get("name") or serialized.get("id")
        node = meta.get("langgraph_node")
        in_keys = None
        n_messages = None
        if node == "model" and isinstance(inputs, dict) and inputs:
            in_keys = ",".join(sorted(inputs.keys()))[:120]
            msgs = inputs.get("messages")
            if isinstance(msgs, list):
                n_messages = len(msgs)
        line = _kv_line(
            session=self._session_id,
            event="chain_start",
            run_id=str(run_id)[:8],
            graph_node=meta.get("langgraph_node"),
            graph_step=meta.get("langgraph_step"),
            chain_name=name,
            input_keys=in_keys,
            n_messages=n_messages,
        )
        _log.info("agent_trace  %s", line)

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        tool_name = (serialized or {}).get("name") if isinstance(serialized, dict) else None
        parts: dict[str, str | int | UUID | None] = {
            "session": self._session_id,
            "event": "tool_start",
            "run_id": str(run_id)[:8],
            "tool": tool_name,
            "graph_node": meta.get("langgraph_node"),
            "graph_step": meta.get("langgraph_step"),
        }
        if self._detail != "minimal":
            parts["input"] = _clip(input_str or "", self._tool_io_clip())
        else:
            n = len(input_str or "")
            if n:
                parts["input_chars"] = n
        line = _kv_line(**parts)
        _log.info("agent_trace  %s", line)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        parts = {
            "session": self._session_id,
            "event": "tool_end",
            "run_id": str(run_id)[:8],
            "graph_node": meta.get("langgraph_node"),
            "graph_step": meta.get("langgraph_step"),
        }
        if self._detail != "minimal":
            parts["output"] = _clip(str(output), self._tool_io_clip())
        else:
            out_s = str(output)
            parts["output_chars"] = len(out_s)
            if isinstance(output, dict) and output.get("ok") is not None:
                parts["ok"] = str(output.get("ok"))
            err = output.get("error") if isinstance(output, dict) else None
            if isinstance(err, dict) and err.get("code"):
                parts["error_code"] = str(err.get("code"))
        line = _kv_line(**parts)
        _log.info("agent_trace  %s", line)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        line = _kv_line(
            session=self._session_id,
            event="tool_error",
            run_id=str(run_id)[:8],
            graph_node=meta.get("langgraph_node"),
            graph_step=meta.get("langgraph_step"),
            exc_type=type(error).__name__,
            detail=format_exception_chain(error),
        )
        _log.warning("agent_trace  %s", line)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        line = _kv_line(
            session=self._session_id,
            event="chain_error",
            run_id=str(run_id)[:8],
            graph_node=meta.get("langgraph_node"),
            graph_step=meta.get("langgraph_step"),
            exc_type=type(error).__name__,
            detail=format_exception_chain(error),
        )
        _log.warning("agent_trace  %s", line)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        meta = self._meta(kwargs)
        node = meta.get("langgraph_node")
        batches = len(messages) if isinstance(messages, list) else 0
        total_msgs = (
            sum(len(b) for b in messages)
            if isinstance(messages, list) and all(isinstance(b, list) for b in messages)
            else 0
        )
        model_name = None
        if isinstance(serialized, dict):
            model_name = serialized.get("name") or serialized.get("id")
        line = _kv_line(
            session=self._session_id,
            event="chat_model_start",
            run_id=str(run_id)[:8],
            graph_node=node,
            graph_step=meta.get("langgraph_step"),
            model=model_name,
            message_batches=batches,
            messages=total_msgs,
        )
        # LLM request: always log at INFO when this callback runs on the graph ``model`` node so
        # ``AUREY_AGENT_TRACE=info`` shows provider calls (not only generic ``chain_start``).
        if node == "model":
            _log.info("agent_trace  %s", line)
        elif self._detail == "debug":
            _log.debug("agent_trace  %s", line)

    def on_llm_new_token(
        self,
        token: str,
        *,
        chunk: Any = None,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        if self._detail != "debug":
            return
        extra = ""
        if chunk is not None and hasattr(chunk, "additional_kwargs"):
            ak = getattr(chunk, "additional_kwargs", None)
            if isinstance(ak, dict) and ak:
                keys = ",".join(sorted(ak))[:200]
                extra = f"chunk_additional_keys={keys}"
        line = _kv_line(
            session=self._session_id,
            event="llm_token",
            run_id=str(run_id)[:8],
            token=_clip(token, 120),
        )
        msg = f"agent_trace  {line}"
        if extra:
            msg = f"{msg}  {extra}"
        _log.debug(msg)

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        if self._detail != "debug":
            return
        gens = getattr(response, "generations", None)
        n_gens = len(gens) if isinstance(gens, list) else 0
        llm_out = getattr(response, "llm_output", None)
        out_keys = ""
        if isinstance(llm_out, dict) and llm_out:
            out_keys = ",".join(sorted(llm_out))[:300]
        line = _kv_line(
            session=self._session_id,
            event="llm_end",
            run_id=str(run_id)[:8],
            generations=n_gens,
            llm_output_keys=out_keys or None,
        )
        _log.debug("agent_trace  %s", line)


__all__ = [
    "AureyAgentTraceHandler",
    "agent_trace_detail",
    "build_agent_trace_handler",
    "format_exception_chain",
]
