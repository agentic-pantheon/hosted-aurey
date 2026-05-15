"""LangSmith tracing compatibility for LangChain tools."""

from __future__ import annotations

from typing import Any

# https://github.com/langchain-ai/langchain/issues/36517#issuecomment-4365249824
_applied: bool = False
_original_basetool_model_dump: Any = None


def _trace_binding_summary(tool: Any) -> dict[str, Any]:
    """Small JSON-safe substitute when tracing wraps a tool in RunnableBinding."""

    return {
        "trace_tool_binding": True,
        "tool_class": type(tool).__name__,
        "name": getattr(tool, "name", ""),
    }


def apply_langsmith_tool_output_patch() -> None:
    """Make LangSmith trace serialization tolerant of LangChain tools.

    1. **BaseTool JSON model_dump** — LangSmith invokes ``model_dump(mode="json")`` on traced
       outputs; see workaround (comment by @solarcloud7):
       https://github.com/langchain-ai/langchain/issues/36517#issuecomment-4365249824
    2. **RunnableBinding** — graph wiring often uses ``tool.with_config(...)``, whose dump still
       embeds an unwrapped tool; coerce those traced **outputs** to a short dict so
       LangSmith's ``_container_end`` avoids ``model_dump`` on the binding itself.
    """

    global _applied, _original_basetool_model_dump
    if _applied:
        return

    try:
        import langsmith.run_helpers as run_helpers
        from langchain_core.tools import BaseTool
        from pydantic import BaseModel
    except ImportError:
        _applied = True
        return

    if _original_basetool_model_dump is None:
        _original_basetool_model_dump = BaseTool.model_dump
        _ns = frozenset({"args_schema", "func", "coroutine"})

        def _patched_basetool_model_dump(  # noqa: PLR0912 - mirrors upstream workaround
            self: Any,
            *,
            mode: str = "python",
            **kwargs: Any,
        ) -> Any:
            if mode != "json":
                return _original_basetool_model_dump(self, mode=mode, **kwargs)

            include = kwargs.get("include", None)
            caller_exclude_raw = kwargs.pop("exclude", None)
            caller_exclude = caller_exclude_raw or set()

            if isinstance(caller_exclude, dict):
                caller_exclude_keys = set(caller_exclude.keys())
                exclude_merged: Any = {**caller_exclude, **{f: True for f in _ns}}
            else:
                caller_exclude_keys = set(caller_exclude)
                exclude_merged = set(caller_exclude) | _ns

            result = _original_basetool_model_dump(
                self, mode=mode, exclude=exclude_merged, **kwargs
            )

            if include is not None:
                fields_to_add = _ns & (
                    set(include) if not isinstance(include, dict) else set(include.keys())
                )
            else:
                fields_to_add = set(_ns)
            fields_to_add -= caller_exclude_keys

            exclude_none = kwargs.get("exclude_none", False)

            if "args_schema" in fields_to_add:
                asc = getattr(self, "args_schema", None)
                if asc is not None:
                    if isinstance(asc, type) and issubclass(asc, BaseModel):
                        result["args_schema"] = asc.model_json_schema()
                    else:
                        result["args_schema"] = asc
                elif not exclude_none:
                    result["args_schema"] = None

            for fld in ("func", "coroutine"):
                if fld not in fields_to_add:
                    continue
                val = getattr(self, fld, None)
                if val is not None:
                    result[fld] = getattr(val, "__qualname__", str(val))
                elif not exclude_none:
                    result[fld] = None

            return result

        BaseTool.model_dump = _patched_basetool_model_dump  # type: ignore[method-assign]

    _orig = run_helpers._container_end

    def _container_end(
        container: Any,
        outputs: Any = None,
        error: Any = None,
    ) -> None:
        outputs = _coerce_runnable_binding_tool_outputs(outputs, basetool_type=BaseTool)
        return _orig(container, outputs=outputs, error=error)

    run_helpers._container_end = _container_end  # type: ignore[assignment]
    _applied = True


def _coerce_runnable_binding_tool_outputs(
    outputs: Any,
    *,
    basetool_type: type[Any],
) -> Any:
    """Replace traced ``RunnableBinding`` outputs that embed a LangChain tool."""

    if outputs is None or isinstance(outputs, dict):
        return outputs
    bound = getattr(outputs, "bound", None)
    if bound is not None and isinstance(bound, basetool_type):
        return _trace_binding_summary(bound)
    return outputs


__all__ = ["apply_langsmith_tool_output_patch"]
