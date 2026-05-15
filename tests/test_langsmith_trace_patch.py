"""LangSmith + LangChain tool tracing patch."""

from __future__ import annotations

import pytest

pytest.importorskip("langsmith")
pytest.importorskip("langchain_core.tools")


def test_apply_langsmith_patch_idempotent_and_basetool_json_model_dump(monkeypatch) -> None:
    """Monkey-patch from `langchain-ai/langchain#36517` + idempotent LangSmith container shim."""

    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    from langsmith import run_helpers

    from aurey.reasoning.langsmith_trace import apply_langsmith_tool_output_patch
    from langchain_core.tools import tool

    apply_langsmith_tool_output_patch()
    first_wrapper = run_helpers._container_end
    apply_langsmith_tool_output_patch()
    assert run_helpers._container_end is first_wrapper

    @tool
    def _demo_tool(x: int) -> int:
        """demo"""
        return x

    payload = _demo_tool.model_dump(mode="json")
    assert isinstance(payload.get("args_schema"), dict)


def test_coerce_runnable_binding_tool_outputs() -> None:
    """RunnableBinding still bypasses BaseTool.model_dump; ensure our output shim picks it up."""

    from langchain_core.tools import BaseTool, tool

    import aurey.reasoning.langsmith_trace as lt

    @tool
    def wrapped(x: int) -> int:
        """wrapped"""
        return x

    binding = wrapped.with_config(tags=["t"])
    out = lt._coerce_runnable_binding_tool_outputs(binding, basetool_type=BaseTool)
    assert out == {
        "trace_tool_binding": True,
        "tool_class": "StructuredTool",
        "name": "wrapped",
    }
