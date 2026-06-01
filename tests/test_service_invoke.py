"""invoke_deep_agent_turn resilience and wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage
from openai import APIConnectionError, APITimeoutError

from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.service.invoke import invoke_deep_agent_turn
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


def test_invoke_retries_on_transient_openai_connection_error() -> None:
    calls: list[int] = []

    def invoke_side_effect(_payload, config=None):
        _ = config
        calls.append(1)
        if len(calls) < 3:
            raise APIConnectionError(request=MagicMock())
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings()
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = invoke_side_effect
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:1")
    assert out.ok is True
    assert graph.invoke.call_count == 3


def test_invoke_retries_when_openai_error_wrapped() -> None:
    """LangChain often wraps provider errors; retries should still run."""

    inner = APIConnectionError(request=MagicMock())
    calls: list[int] = []

    def invoke_side_effect(_payload, config=None):
        _ = config
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("model step failed") from inner
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings()
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = invoke_side_effect
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:wrap")
    assert out.ok is True
    assert graph.invoke.call_count == 2


def test_invoke_does_not_retry_on_non_openai_errors() -> None:
    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings()
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("boom")
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:2")
    assert out.ok is False
    assert out.error is not None
    assert out.error.code == "agent_invoke_failed"
    assert graph.invoke.call_count == 1


def test_invoke_prefixes_human_message_when_hosted_wallet_bound() -> None:
    payloads: list[object] = []

    def capture_invoke(payload, config=None):
        _ = config
        payloads.append(payload)
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings()
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = capture_invoke
    svc.get_or_create_graph.return_value = graph

    raw = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    chk = to_checksum_evm_address(raw)
    out = invoke_deep_agent_turn(
        svc,
        message="hi",
        session_id="t:w",
        context={"hosted_wallet_address": raw},
    )
    assert out.ok is True
    assert len(payloads) == 1
    msgs = payloads[0]["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], HumanMessage)
    assert chk in msgs[0].content
    assert "hi" in msgs[0].content


def test_invoke_prefixes_solana_from_server_context() -> None:
    payloads: list[object] = []

    def capture_invoke(payload, config=None):
        _ = config
        payloads.append(payload)
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings(hosted_platform_enabled=True)
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = capture_invoke
    svc.get_or_create_graph.return_value = graph

    from aurey.service.invoke import HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY

    sol = "SolPubkeyExample111"
    out = invoke_deep_agent_turn(
        svc,
        message="hi",
        session_id="t:sol",
        context={
            "hosted_solana_wallet_address": sol,
            "telegram_user_id": "99",
            HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY: True,
        },
    )
    assert out.ok is True
    msgs = payloads[0]["messages"]
    assert sol in msgs[0].content
    assert "Solana wallet address" in msgs[0].content


def test_invoke_uses_server_hosted_wallet_before_ready() -> None:
    payloads: list[object] = []

    def capture_invoke(payload, config=None):
        _ = config
        payloads.append(payload)
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings(hosted_platform_enabled=True)
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = capture_invoke
    svc.get_or_create_graph.return_value = graph

    from aurey.service.invoke import HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY

    raw = "0xcccccccccccccccccccccccccccccccccccccccc"
    chk = to_checksum_evm_address(raw)
    out = invoke_deep_agent_turn(
        svc,
        message="what is my wallet",
        session_id="t:pre-ready",
        context={
            "hosted_wallet_address": raw,
            HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY: True,
        },
        hosted_signing_context=None,
    )
    assert out.ok is True
    msgs = payloads[0]["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], HumanMessage)
    assert chk in msgs[0].content


def test_invoke_ignores_client_hosted_wallet_when_hosted_platform_enabled() -> None:
    from aurey.cloud.signing_context import HostedSigningContext, hosted_signing_context_scope

    payloads: list[object] = []

    def capture_invoke(payload, config=None):
        _ = config
        payloads.append(payload)
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings(hosted_platform_enabled=True)
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = capture_invoke
    svc.get_or_create_graph.return_value = graph

    fake_client = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    real_db = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    chk_real = to_checksum_evm_address(real_db)
    ctx = HostedSigningContext(
        telegram_user_id=1,
        user_agent_id="ua-1",
        wallet_address=real_db,
    )
    with hosted_signing_context_scope(ctx):
        out = invoke_deep_agent_turn(
            svc,
            message="hi",
            session_id="t:hosted-wallet",
            context={"hosted_wallet_address": fake_client},
            hosted_signing_context=ctx,
        )
    assert out.ok is True
    assert len(payloads) == 1
    msgs = payloads[0]["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], HumanMessage)
    assert chk_real in msgs[0].content
    assert to_checksum_evm_address(fake_client) not in msgs[0].content


def test_invoke_exhausts_retries() -> None:
    svc = MagicMock(spec=AureyServiceState)
    svc.settings = AureySettings()
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = APITimeoutError(request=MagicMock())
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:3")
    assert out.ok is False
    assert graph.invoke.call_count == 4
