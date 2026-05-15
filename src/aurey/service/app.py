"""FastAPI application: health check and a single Deep Agent invoke endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from aurey.service.bootstrap import AureyServiceBootstrapError, bootstrap_aurey_service_state
from aurey.service.dependencies import get_aurey_service_state
from aurey.service.invoke import AgentInvokeError, AgentInvokeResult, invoke_deep_agent_turn
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


class InvokeBody(BaseModel):
    """Inbound chat turn."""

    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(..., min_length=1, description="User message text.")
    session_id: str = Field(..., min_length=1, description="Stable session / thread identifier.")
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional values merged into configurable state under ``aurey_context``.",
    )
    agent_model_spec: str | None = Field(
        default=None,
        alias="model",
        description="Optional Deep Agents provider:model override (JSON key ``model``).",
    )


InvokeError = AgentInvokeError
InvokeResponse = AgentInvokeResult


def create_fastapi_application(
    *,
    state: AureyServiceState | None = None,
    settings: AureySettings | None = None,
):
    """Build a FastAPI app; wiring runs in lifespan unless ``state`` is injected (tests).

    Installing ``aurey[api]`` is required to import :mod:`fastapi`.
    """

    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    injected = state

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if injected is not None:
            app.state.aurey = injected
        else:
            try:
                app.state.aurey = bootstrap_aurey_service_state(settings)
            except AureyServiceBootstrapError:
                app.state.aurey = None
        try:
            yield
        finally:
            st = getattr(app.state, "aurey", None)
            if st is not None:
                st.close_checkpointer()

    app = FastAPI(title="Aurey", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, bool]:
        return {"ok": get_aurey_service_state(request) is not None}

    @app.post("/v1/invoke", response_model=InvokeResponse)
    def invoke(request: Request, turn: InvokeBody) -> InvokeResponse:
        svc = get_aurey_service_state(request)
        return invoke_deep_agent_turn(
            svc,
            message=turn.message,
            session_id=turn.session_id,
            context=turn.context,
            model=turn.agent_model_spec,
        )

    return app


def create_default_application():
    """Entry point for ``uvicorn aurey.service.app:create_default_application --factory``."""

    return create_fastapi_application()


# Uvicorn ASGI factory: ``uvicorn aurey.service.app:app --factory``
app = create_default_application


__all__ = [
    "InvokeBody",
    "InvokeError",
    "InvokeResponse",
    "app",
    "create_default_application",
    "create_fastapi_application",
]
