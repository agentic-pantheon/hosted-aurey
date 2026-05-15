"""Normalize LangChain message payloads to plain API/Telegram text."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage


def flatten_message_content(content: Any) -> str | None:
    """Normalize LangChain/OpenAI ``content`` to plain text.

    Providers may return a string or a list of blocks, e.g.
    ``[{"type": "text", "text": "..."}]``.
    """

    if content is None:
        return None
    if isinstance(content, str):
        s = content.strip()
        return s or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
                else:
                    inner = block.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        nested = flatten_message_content(inner)
                        if nested:
                            parts.append(nested)
        joined = "".join(parts).strip()
        return joined or None
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str):
            s = t.strip()
            return s or None
    return None


def summarize_agent_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Return JSON-safe message summaries without provider-specific metadata."""

    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, BaseMessage):
            content = getattr(m, "content", None)
            flat = flatten_message_content(content)
            body: Any = flat if flat is not None else "<non-text>"
            out.append(
                {
                    "role": getattr(m, "type", m.__class__.__name__),
                    "type": m.__class__.__name__,
                    "content": body,
                }
            )
    return out


def reply_preview_from_summary(rows: list[dict[str, Any]] | None) -> str | None:
    """Last non-empty flattened ``content`` from summarized message rows."""

    if not rows:
        return None
    for row in reversed(rows):
        text = flatten_message_content(row.get("content"))
        if text:
            return text
    return None


__all__ = [
    "flatten_message_content",
    "reply_preview_from_summary",
    "summarize_agent_messages",
]
