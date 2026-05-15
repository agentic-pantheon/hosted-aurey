"""invoke_deep_agent_turn resilience and wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from openai import APIConnectionError, APITimeoutError

from aurey.service.invoke import invoke_deep_agent_turn
from aurey.service.state import AureyServiceState


def test_invoke_retries_on_transient_openai_connection_error() -> None:
    calls: list[int] = []

    def invoke_side_effect(_payload, config=None):
        _ = config
        calls.append(1)
        if len(calls) < 3:
            raise APIConnectionError(request=MagicMock())
        return {"messages": []}

    svc = MagicMock(spec=AureyServiceState)
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
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = invoke_side_effect
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:wrap")
    assert out.ok is True
    assert graph.invoke.call_count == 2


def test_invoke_does_not_retry_on_non_openai_errors() -> None:
    svc = MagicMock(spec=AureyServiceState)
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("boom")
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:2")
    assert out.ok is False
    assert out.error is not None
    assert out.error.code == "agent_invoke_failed"
    assert graph.invoke.call_count == 1


def test_invoke_exhausts_retries() -> None:
    svc = MagicMock(spec=AureyServiceState)
    svc.default_model = "openai:gpt-4o-mini"
    graph = MagicMock()
    graph.invoke.side_effect = APITimeoutError(request=MagicMock())
    svc.get_or_create_graph.return_value = graph

    out = invoke_deep_agent_turn(svc, message="hi", session_id="t:3")
    assert out.ok is False
    assert graph.invoke.call_count == 4
