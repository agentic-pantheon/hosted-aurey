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

    import asyncio
    import contextlib
    import logging
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from starlette.responses import Response

    injected = state
    _claim_poll_log = logging.getLogger("aurey.cloud.claim_poll")

    async def _claim_poll_loop(svc: AureyServiceState) -> None:
        onboarding = svc.onboarding
        if onboarding is None:
            return
        interval = float(svc.settings.claim_poll_interval_seconds)
        while True:
            try:
                await asyncio.to_thread(onboarding.poll_awaiting_claims)
            except asyncio.CancelledError:
                raise
            except Exception:
                _claim_poll_log.exception("Claim poll tick failed")
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poll_task: asyncio.Task[None] | None = None
        if injected is not None:
            app.state.aurey = injected
        else:
            try:
                app.state.aurey = bootstrap_aurey_service_state(settings)
            except AureyServiceBootstrapError:
                app.state.aurey = None
        svc = getattr(app.state, "aurey", None)
        if svc is not None and svc.onboarding is not None:
            poll_task = asyncio.create_task(_claim_poll_loop(svc))
        try:
            yield
        finally:
            if poll_task is not None:
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
            st = getattr(app.state, "aurey", None)
            if st is not None:
                st.close_checkpointer()

    app = FastAPI(title="Aurey", lifespan=lifespan)

    @app.get("/.well-known/jwks.json")
    def well_known_jwks(request: Request) -> dict[str, object]:
        from fastapi import HTTPException

        svc = get_aurey_service_state(request)
        if svc is None or svc.oidc_signer is None:
            raise HTTPException(status_code=404, detail="Not found")
        return svc.oidc_signer.jwks_document()

    @app.get("/.well-known/openid-configuration")
    def well_known_openid_configuration(request: Request) -> dict[str, object]:
        from fastapi import HTTPException

        svc = get_aurey_service_state(request)
        if svc is None or svc.oidc_signer is None:
            raise HTTPException(status_code=404, detail="Not found")
        return svc.oidc_signer.openid_configuration()

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

    @app.post("/v1/cloud/onboarding/claim-events")
    async def claim_events_webhook_stub(request: Request) -> Response:
        """Optional webhook hook for claim completion (Phase C stub).

        Future work: verify a platform signature, enqueue idempotent claim verification, and
        reconcile with :meth:`~aurey.cloud.onboarding.OnboardingService.poll_awaiting_claims`.
        """

        _ = await request.body()
        return Response(status_code=204)

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
