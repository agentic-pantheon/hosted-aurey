"""Resolve hosted vs legacy agent id + bearer for 1Claw signing calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurey.cloud.signing_context import (
    current_hosted_signing_context,
    hosted_signing_missing_context_tool_error,
)
from aurey.runtime import AureyRuntime


@dataclass(frozen=True)
class OneClawSigningPrincipal:
    """Who signs: per-user agent id (hosted) or operator ``oneclaw_agent_id``."""

    agent_id: str
    authorization_bearer: str | None
    """Usually ``None``: ``OneClawHttpClient`` uses ``POST /v1/auth/agent-token`` (bootstrap ``api_key`` + ``agent_id``)."""

    @classmethod
    def resolve(
        cls, runtime: AureyRuntime
    ) -> tuple[OneClawSigningPrincipal | None, dict[str, Any] | None]:
        """Return ``(principal, None)`` or ``(None, error_dict)`` for tool error payloads."""

        settings = runtime.settings
        signer = runtime.oneclaw_evm_signer
        if signer is None:
            return None, {
                "code": "secret_not_configured",
                "message": "OneClaw signer is not configured on this runtime.",
            }

        hctx = current_hosted_signing_context.get()
        if settings.hosted_platform_enabled:
            if hctx is None:
                return None, hosted_signing_missing_context_tool_error()
            aid = (hctx.user_agent_id or "").strip()
            if not aid:
                return None, {
                    "code": "secret_not_configured",
                    "message": (
                        "Hosted signing requires a provisioned user_agent_id for this Telegram user."
                    ),
                }
            return cls(agent_id=aid, authorization_bearer=None), None

        legacy_agent = settings.oneclaw_agent_id
        if legacy_agent is None or not str(legacy_agent).strip():
            return None, {
                "code": "secret_not_configured",
                "message": "oneclaw_agent_id must be configured for oneclaw_intents signing tools.",
            }
        return cls(agent_id=str(legacy_agent).strip(), authorization_bearer=None), None


__all__ = ["OneClawSigningPrincipal"]
