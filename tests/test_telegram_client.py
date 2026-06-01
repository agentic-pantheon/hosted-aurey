"""Telegram client handling without requiring the optional runtime dependency."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.reasoning import make_memory_checkpointer
from aurey.runtime import AureyRuntime
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings
from aurey.telegram import (
    TelegramConfigurationError,
    format_telegram_message,
    handle_telegram_text,
    resolve_telegram_bot_token,
    telegram_message_chunks,
)
from aurey.telegram.client import TelegramInvokeProgressCallback, _telegram_chat_is_allowed
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient
from tests.leakage_helpers import (
    FAKE_ERROR_BODY_SECRET,
    FAKE_TELEGRAM_BOT_TOKEN,
    assert_no_sensitive_leakage,
)


def test_telegram_chat_is_allowed_unrestricted() -> None:
    assert _telegram_chat_is_allowed(None, None) is True
    assert _telegram_chat_is_allowed(123, None) is True


def test_telegram_chat_is_allowed_restricted() -> None:
    allowed = frozenset({1, -100})
    assert _telegram_chat_is_allowed(None, allowed) is False
    assert _telegram_chat_is_allowed(1, allowed) is True
    assert _telegram_chat_is_allowed(-100, allowed) is True
    assert _telegram_chat_is_allowed(999, allowed) is False
    assert _telegram_chat_is_allowed(None, allowed, telegram_user_id=1) is True
    assert _telegram_chat_is_allowed(999, allowed, telegram_user_id=1) is True


def _simulate_telegram_progress_events(cb: TelegramInvokeProgressCallback) -> None:
    cb.on_chat_model_start(
        None,
        [[]],
        run_id=uuid4(),
        metadata={"langgraph_node": "model"},
    )
    cb.on_tool_start(None, "", run_id=uuid4(), metadata={"langgraph_node": "tools"})


def _callbacks_from_invoke_config(config: dict[str, Any] | None) -> list[Any]:
    if not config:
        return []
    raw = config.get("callbacks")
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


class _RecordingGraph:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.payload: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    def invoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None):
        self.payload = payload
        self.config = config
        if self.fail:
            raise RuntimeError(FAKE_ERROR_BODY_SECRET)
        for cb in _callbacks_from_invoke_config(config):
            if isinstance(cb, TelegramInvokeProgressCallback):
                _simulate_telegram_progress_events(cb)
        return {"messages": [AIMessage(content="telegram ok")]}


class _FakeServiceState:
    default_model = "stub-model"

    def __init__(self, graph: _RecordingGraph) -> None:
        self.graph = graph
        self.model: str | None = None
        self.settings = AureySettings()
        self.hosted_session_factory = None

    def get_or_create_graph(self, model: str | None, **_: object):
        self.model = model
        return self.graph


def _service_state_with_token(*, token_path: str | None, token: str | None) -> AureyServiceState:
    secrets = {}
    if token_path is not None and token is not None:
        secrets[token_path] = token
    settings = AureySettings(telegram_bot_token_secret_path=token_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
    )
    return AureyServiceState(
        settings=settings,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model="stub-model",
    )


def test_handle_telegram_text_reuses_shared_agent_invocation() -> None:
    graph = _RecordingGraph()
    state = _FakeServiceState(graph)

    reply = handle_telegram_text(
        state,  # type: ignore[arg-type]
        chat_id=123,
        user_id=456,
        text="hello aurey",
        model="stub-model",
    )

    assert reply == "telegram ok"
    assert state.model == "stub-model"
    assert graph.payload is not None
    assert len(graph.payload["messages"]) == 1
    assert graph.payload["messages"][0].content == "hello aurey"
    assert graph.config == {
        "configurable": {
            "thread_id": "telegram:123",
            "aurey_context": {"telegram_chat_id": "123", "telegram_user_id": "456"},
        }
    }


def test_handle_telegram_text_progress_sink_receives_invoke_events() -> None:
    graph = _RecordingGraph()
    state = _FakeServiceState(graph)
    captures: list[str] = []

    reply = handle_telegram_text(
        state,  # type: ignore[arg-type]
        chat_id=1,
        text="hi",
        progress_sink=captures.append,
    )

    assert reply == "telegram ok"
    assert captures == ["Thinking…", "Gathering details…"]
    cbs = _callbacks_from_invoke_config(graph.config)
    assert cbs and any(isinstance(c, TelegramInvokeProgressCallback) for c in cbs)


def test_handle_telegram_text_sanitizes_agent_errors() -> None:
    state = _FakeServiceState(_RecordingGraph(fail=True))
    reply = handle_telegram_text(
        state,  # type: ignore[arg-type]
        chat_id="chat-secret",
        text="boom",
    )

    assert "agent_invoke_failed" in reply
    assert FAKE_ERROR_BODY_SECRET not in reply
    assert_no_sensitive_leakage({"reply": reply})


def test_format_telegram_message_renders_common_markdown_as_html() -> None:
    raw = "**Title**\n\nUse `code` and <unsafe>.\n\n```python\nprint('<x>')\n```"

    formatted = format_telegram_message(raw)

    assert "<b>Title</b>" in formatted
    assert "<code>code</code>" in formatted
    assert "&lt;unsafe&gt;" in formatted
    assert "<pre>print('&lt;x&gt;')</pre>" in formatted


def test_format_telegram_message_links_tx_and_address_to_explorers() -> None:
    raw = (
        "**Swapped**\n\n"
        "- Approval tx (**USDC on Base**): "
        "0x6f2d7bd436f5817f7f6b728d008728487b1fbdcf6a78eeb0d39bc33e3905f82c\n"
        "- Swap + bridge tx (**WETH (Ethereum)**): "
        "0x00481f71cfe4f9ccd5ead1acbd7ed3def662c66ea2a3369a348fbedd23e33be5\n\n"
        "Recipient: 0x7a3e29106d238334b7134ddd824b7923bcf717d2"
    )
    formatted = format_telegram_message(raw)

    assert (
        '<a href="https://basescan.org/tx/0x6f2d7bd436f5817f7f6b728d008728487b1fbdcf6a78eeb0d39bc33e3905f82c"'
        ">0x6f2d7bd436f5817f7f6b728d008728487b1fbdcf6a78eeb0d39bc33e3905f82c</a>"
        in formatted
    )
    assert (
        '<a href="https://etherscan.io/tx/0x00481f71cfe4f9ccd5ead1acbd7ed3def662c66ea2a3369a348fbedd23e33be5"'
        ">0x00481f71cfe4f9ccd5ead1acbd7ed3def662c66ea2a3369a348fbedd23e33be5</a>"
        in formatted
    )
    assert (
        '<a href="https://etherscan.io/address/0x7a3e29106d238334b7134ddd824b7923bcf717d2"'
        ">0x7a3e29106d238334b7134ddd824b7923bcf717d2</a>"
        in formatted
    )


def test_format_telegram_message_skip_explorer_links_inside_inline_code() -> None:
    h = "0x6f2d7bd436f5817f7f6b728d008728487b1fbdcf6a78eeb0d39bc33e3905f82c"
    raw = f"USDC on Base and `approve()` then `{h}` and naked {h}"

    formatted = format_telegram_message(raw)

    assert "<code>approve()</code>" in formatted
    assert f"<code>{h}</code>" not in formatted
    assert formatted.count('<a href="https://basescan.org/tx/') == 2


def test_format_telegram_message_inherits_explorer_for_isolated_tx_line() -> None:
    raw = (
        "Done — sent USDC on Base from 0xc1923710468607b8b7db38a6afbb9b432744390c "
        "to fabri (0x7a3e29106d238334b7134ddd824b7923bcf717d2).\n"
        "\n"
        "Tx hash: 0x2833a66dcbe971532c305548337d7c87f914d7b96b1f06408d6f11821914d582"
    )
    formatted = format_telegram_message(raw)

    assert formatted.count('<a href="https://basescan.org/') == 3


def test_format_telegram_message_links_solana_address_to_explorer() -> None:
    pubkey = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    raw = f"Your **Solana** wallet address is {pubkey}."

    formatted = format_telegram_message(raw)

    assert (
        f'<a href="https://explorer.solana.com/address/{pubkey}">{pubkey}</a>' in formatted
    )


def test_format_telegram_message_solana_context_sticky_on_following_lines() -> None:
    pubkey = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    raw = f"Solana wallet\n{pubkey}"

    formatted = format_telegram_message(raw)

    assert (
        f'<a href="https://explorer.solana.com/address/{pubkey}">{pubkey}</a>' in formatted
    )


def test_format_telegram_message_does_not_link_solana_pubkey_without_context() -> None:
    pubkey = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    raw = f"Random token id {pubkey}"

    formatted = format_telegram_message(raw)

    assert "explorer.solana.com" not in formatted
    assert pubkey in formatted


def test_telegram_message_chunks_splits_long_text_before_formatting() -> None:
    raw = "a" * 5000

    chunks = telegram_message_chunks(raw)

    assert len(chunks) == 2
    assert "".join(chunks) == raw
    assert all(len(c) <= 3600 for c in chunks)


def test_resolve_telegram_bot_token_uses_secret_store() -> None:
    state = _service_state_with_token(
        token_path="aurey/telegram/bot_token",
        token=FAKE_TELEGRAM_BOT_TOKEN,
    )

    assert resolve_telegram_bot_token(state) == FAKE_TELEGRAM_BOT_TOKEN


def test_resolve_telegram_bot_token_prefers_env_over_vault() -> None:
    settings = AureySettings(
        telegram_bot_token="env-telegram-token",
        telegram_bot_token_secret_path="aurey/telegram/bot_token",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({"aurey/telegram/bot_token": "vault-token"}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
    )
    state = AureyServiceState(
        settings=settings,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model="stub-model",
    )
    assert resolve_telegram_bot_token(state) == "env-telegram-token"


def test_resolve_telegram_bot_token_missing_path_is_sanitized() -> None:
    state = _service_state_with_token(token_path=None, token=FAKE_TELEGRAM_BOT_TOKEN)

    with pytest.raises(TelegramConfigurationError) as exc:
        resolve_telegram_bot_token(state)

    blob = json.dumps({"error": str(exc.value)})
    assert "bot_token" not in blob
    assert_no_sensitive_leakage({"error": str(exc.value)})
