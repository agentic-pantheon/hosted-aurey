"""FastAPI application: health check and a single Deep Agent invoke endpoint."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from aurey.miniapp.schemas import PortfolioSnapshot
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


class HostedSyncWalletBody(BaseModel):
    """Privileged sync: ``telegram_user_id`` targets one ``hosted_platform_users`` row."""

    telegram_user_id: int = Field(..., ge=1)


class HostedSyncWalletResponse(BaseModel):
    telegram_user_id: int
    user_agent_id: str | None = None
    wallet_address: str | None = None


class MiniAppPortfolioRequest(BaseModel):
    """Telegram WebApp ``initData`` payload (query string form, not JSON)."""

    init_data: str = Field(..., min_length=1, description="Telegram.WebApp.initData string.")


class MiniAppConfigResponse(BaseModel):
    chains: list[str]


def _hosted_http_bearer_matches_configured(admin_token: str, authorization_header: str | None) -> bool:
    ct = admin_token.strip()
    if not ct or authorization_header is None:
        return False
    auth = authorization_header.strip()
    if not auth.lower().startswith("bearer "):
        return False
    got = auth[7:].strip()
    return hmac.compare_digest(
        hashlib.sha256(ct.encode("utf-8")).digest(),
        hashlib.sha256(got.encode("utf-8")).digest(),
    )


def _miniapp_dist_dir() -> Path:
    """Repository ``miniapp/dist`` (sibling of ``src``)."""

    return Path(__file__).resolve().parents[3] / "miniapp" / "dist"


def _miniapp_rate_limiters(app: Any, st: AureySettings) -> tuple[Any, Any]:
    from aurey.miniapp.rate_limit import SlidingWindowRateLimiter

    key_user = (
        st.telegram_miniapp_portfolio_rate_limit_user_per_minute,
        st.telegram_miniapp_portfolio_rate_limit_ip_per_minute,
    )
    cached = getattr(app.state, "_miniapp_rl_key", None)
    if cached != key_user or not hasattr(app.state, "miniapp_rl_user"):
        app.state._miniapp_rl_key = key_user
        app.state.miniapp_rl_user = SlidingWindowRateLimiter(
            max_events=st.telegram_miniapp_portfolio_rate_limit_user_per_minute,
            window_seconds=60.0,
        )
        app.state.miniapp_rl_ip = SlidingWindowRateLimiter(
            max_events=st.telegram_miniapp_portfolio_rate_limit_ip_per_minute,
            window_seconds=60.0,
        )
    return app.state.miniapp_rl_user, app.state.miniapp_rl_ip


def create_fastapi_application(
    *,
    state: AureyServiceState | None = None,
    settings: AureySettings | None = None,
):
    """Build a FastAPI app; wiring runs in lifespan unless ``state`` is injected (tests).

    Installing ``aurey[api]`` is required to import :mod:`fastapi`.
    """

    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException
    from sqlalchemy import select

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

    @app.post("/v1/hosted/sync-wallet", response_model=HostedSyncWalletResponse)
    def hosted_sync_wallet(
        request: Request,
        body: HostedSyncWalletBody,
    ) -> HostedSyncWalletResponse:
        from aurey.cloud.models import HostedPlatformUserORM
        from aurey.cloud.platform_client import HostedPlatformApiError, OneClawPlatformClient
        from aurey.cloud.wallet_sync import sync_wallet_address_from_signing_keys

        authorization = request.headers.get("authorization")
        svc = get_aurey_service_state(request)
        if svc is None:
            raise HTTPException(status_code=503, detail="service_unavailable")
        settings = svc.settings
        if not settings.hosted_platform_enabled:
            raise HTTPException(status_code=503, detail="hosted_disabled")
        expected_token = (settings.hosted_http_admin_token or "").strip()
        if not expected_token:
            raise HTTPException(status_code=503, detail="hosted_wallet_sync_disabled")
        if not _hosted_http_bearer_matches_configured(expected_token, authorization):
            raise HTTPException(status_code=401, detail="unauthorized")
        factory = svc.hosted_session_factory
        if factory is None:
            raise HTTPException(status_code=503, detail="hosted_database_unconfigured")

        db = factory()
        try:
            row = db.scalar(
                select(HostedPlatformUserORM).where(
                    HostedPlatformUserORM.telegram_user_id == body.telegram_user_id,
                )
            )
            if row is None:
                raise HTTPException(status_code=404, detail="telegram_user_not_found")
            uid = (row.user_agent_id or "").strip()
            if not uid:
                raise HTTPException(status_code=400, detail="user_agent_missing")
            platform = OneClawPlatformClient.from_settings(settings)
            try:
                addr = sync_wallet_address_from_signing_keys(platform, user_agent_id=uid)
            except HostedPlatformApiError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="platform_signing_keys_failed",
                ) from exc
            if addr is not None:
                row.wallet_address = addr
                db.flush()
            db.commit()
            return HostedSyncWalletResponse(
                telegram_user_id=body.telegram_user_id,
                user_agent_id=row.user_agent_id,
                wallet_address=row.wallet_address,
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @app.get("/v1/miniapp/config", response_model=MiniAppConfigResponse)
    def miniapp_config(request: Request) -> MiniAppConfigResponse:
        svc = get_aurey_service_state(request)
        if svc is None:
            raise HTTPException(status_code=503, detail={"code": "service_unavailable"})
        if not svc.settings.telegram_miniapp_enabled:
            raise HTTPException(status_code=503, detail={"code": "miniapp_disabled"})
        return MiniAppConfigResponse(
            chains=list(svc.settings.telegram_miniapp_default_chain_slugs),
        )

    @app.post("/v1/miniapp/portfolio", response_model=PortfolioSnapshot)
    def miniapp_portfolio_post(
        request: Request,
        body: MiniAppPortfolioRequest,
    ) -> PortfolioSnapshot:
        from aurey.miniapp.http_helpers import client_ip_from_request
        from aurey.miniapp.portfolio import aggregate_portfolio_snapshot
        from aurey.miniapp.portfolio_cache_server import portfolio_snapshot_cache
        from aurey.miniapp.wallet import resolve_wallet_for_telegram_user
        from aurey.telegram.client import (
            TelegramConfigurationError,
            resolve_telegram_bot_token,
        )
        from aurey.telegram.webapp_auth import (
            TelegramWebAppAuthError,
            validate_telegram_webapp_init_data,
        )

        svc = get_aurey_service_state(request)
        if svc is None:
            raise HTTPException(status_code=503, detail={"code": "service_unavailable"})
        st = svc.settings
        if not st.telegram_miniapp_enabled:
            raise HTTPException(status_code=503, detail={"code": "miniapp_disabled"})
        if not st.hosted_platform_enabled:
            raise HTTPException(status_code=403, detail={"code": "hosted_disabled"})

        allow = st.telegram_allowed_chat_id_allowlist
        try:
            bot_token = resolve_telegram_bot_token(svc)
        except TelegramConfigurationError:
            raise HTTPException(
                status_code=503,
                detail={"code": "telegram_token_unavailable"},
            ) from None

        try:
            web_user = validate_telegram_webapp_init_data(
                init_data_raw=body.init_data,
                bot_token=bot_token,
                max_age_seconds=st.telegram_miniapp_initdata_max_age_seconds,
                max_future_skew_seconds=st.telegram_miniapp_initdata_max_future_skew_seconds,
            )
        except TelegramWebAppAuthError as exc:
            raise HTTPException(
                status_code=401,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

        if allow is not None and web_user.telegram_user_id not in allow:
            raise HTTPException(
                status_code=403,
                detail={"code": "telegram_user_not_allowlisted"},
            )

        rl_user, rl_ip = _miniapp_rate_limiters(request.app, st)
        client_ip = client_ip_from_request(request)
        if not rl_user.allow(f"uid:{web_user.telegram_user_id}"):
            raise HTTPException(
                status_code=429,
                detail={"code": "rate_limited_user", "message": "Too many portfolio requests."},
            )
        if not rl_ip.allow(f"ip:{client_ip}"):
            raise HTTPException(
                status_code=429,
                detail={"code": "rate_limited_ip", "message": "Too many portfolio requests."},
            )

        resolved = resolve_wallet_for_telegram_user(svc, telegram_user_id=web_user.telegram_user_id)
        if not resolved.has_row:
            raise HTTPException(status_code=403, detail={"code": "hosted_user_not_found"})
        if not resolved.wallet_address:
            raise HTTPException(status_code=403, detail={"code": "wallet_not_ready"})

        chains = st.telegram_miniapp_default_chain_slugs
        cache_ttl = st.telegram_miniapp_portfolio_cache_ttl_seconds
        if cache_ttl > 0 and resolved.wallet_address:
            cache_key = portfolio_snapshot_cache.cache_key(
                telegram_user_id=web_user.telegram_user_id,
                wallet_address=resolved.wallet_address,
                chains=chains,
            )
            cached = portfolio_snapshot_cache.get(cache_key)
            if cached is not None:
                return cached

        snapshot = aggregate_portfolio_snapshot(
            svc.runtime,
            wallet_address=resolved.wallet_address,
            chains=chains,
        )
        if cache_ttl > 0 and resolved.wallet_address:
            portfolio_snapshot_cache.set(
                cache_key,
                snapshot,
                ttl_seconds=float(cache_ttl),
            )
        return snapshot

    dist = _miniapp_dist_dir()
    if dist.is_dir():
        from starlette.staticfiles import StaticFiles

        app.mount(
            "/miniapp",
            StaticFiles(directory=str(dist), html=True),
            name="miniapp_static",
        )

    return app


def create_default_application():
    """Entry point for ``uvicorn aurey.service.app:create_default_application --factory``."""

    return create_fastapi_application()


# Uvicorn ASGI factory: ``uvicorn aurey.service.app:app --factory``
app = create_default_application


__all__ = [
    "HostedSyncWalletBody",
    "HostedSyncWalletResponse",
    "InvokeBody",
    "InvokeError",
    "InvokeResponse",
    "MiniAppConfigResponse",
    "MiniAppPortfolioRequest",
    "PortfolioSnapshot",
    "app",
    "create_default_application",
    "create_fastapi_application",
]
