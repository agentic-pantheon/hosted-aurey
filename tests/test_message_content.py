"""Message content flattening for API/Telegram summaries."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from aurey.service.message_content import (
    flatten_message_content,
    reply_preview_from_summary,
    summarize_agent_messages,
)


def test_flatten_openai_style_text_blocks():
    assert (
        flatten_message_content([{"type": "text", "text": "Hello \n"}])
        == "Hello"
    )


def test_summarize_agent_messages_flattens_list_content():
    msgs = [AIMessage(content=[{"type": "text", "text": "hey there"}])]
    out = summarize_agent_messages(msgs)
    assert len(out) == 1
    assert out[0]["content"] == "hey there"


def test_flatten_plain_string():
    assert flatten_message_content("  ok  ") == "ok"


def test_reply_preview_from_summary_returns_last_non_empty_row():
    rows = [
        {"content": "<non-text>", "role": "tool"},
        {"content": "final reply", "role": "assistant"},
        {"content": "", "role": "assistant"},
    ]
    assert reply_preview_from_summary(rows) == "final reply"


def test_reply_preview_from_summary_empty_returns_none():
    assert reply_preview_from_summary([]) is None
