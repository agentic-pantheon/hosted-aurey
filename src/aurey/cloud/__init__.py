"""Hosted Platform metadata persistence (SQLAlchemy) and provisioning helpers."""

from __future__ import annotations

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
from aurey.cloud.platform_client import (
    HostedPlatformApiError,
    OneClawPlatformClient,
    PlatformBootstrapResult,
    PlatformUpsertResult,
)
from aurey.cloud.provision import (
    HostedProvisioningError,
    ensure_telegram_user_provisioned,
    synthetic_email_for_telegram_user,
)
from aurey.cloud.session import make_engine, make_session_factory
from aurey.cloud.signing_context import (
    HostedSigningContext,
    current_hosted_signing_context,
    hosted_signing_context_scope,
)

__all__ = [
    "Base",
    "HostedPlatformApiError",
    "HostedSigningContext",
    "HostedPlatformUserORM",
    "HostedProvisioningError",
    "OneClawPlatformClient",
    "PlatformBootstrapResult",
    "PlatformUpsertResult",
    "refresh_hosted_user_claim_state",
    "ensure_telegram_user_provisioned",
    "make_engine",
    "make_session_factory",
    "current_hosted_signing_context",
    "hosted_signing_context_scope",
    "synthetic_email_for_telegram_user",
]
