"""Application settings (paths-only secret references, 1Claw connection config).

Note: Configuration lives in this package intentionally; do not add a sibling
``aurey/settings.py`` module, which would conflict with this package name.

Cloud-first layout distinguishes **platform** (``plt_``*, hosted Aurey operator /
1Claw app registration) from **operator runtime** (``ocv_*``, per-tenant vault
agent credentials used by the SecretStore HTTP client).
"""

from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EvmSigningMode = Literal["vault_key", "oneclaw_intents"]

_TELEGRAM_ALLOWLIST_SPLIT_RE = re.compile(r"[\s,]+")


def parse_telegram_allowed_chat_ids(raw: str | None) -> frozenset[int] | None:
    """Parse ``AUREY_TELEGRAM_ALLOWED_CHAT_IDS`` value into a frozen set.

    Returns ``None`` when ``raw`` is unset, empty, or whitespace-only (no restriction).
    Raises ``ValueError`` when any token is not a valid integer.
    """

    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    ids: list[int] = []
    for token in _TELEGRAM_ALLOWLIST_SPLIT_RE.split(stripped):
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError as exc:
            raise ValueError(
                f"Invalid Telegram chat id token {token!r} in AUREY_TELEGRAM_ALLOWED_CHAT_IDS."
            ) from exc
    return frozenset(ids) if ids else None


_DEFAULT_ONECLAW_BASE = "https://api.1claw.xyz"


def _read_trimmed_nonempty_env(var_name: str) -> str:
    name = var_name.strip()
    if not name:
        raise ValueError("Environment variable name must not be empty.")
    raw = os.environ.get(name)
    if raw is None:
        raise KeyError(name)
    value = raw.strip()
    if not value:
        raise ValueError(f"Environment variable {name!r} is set but empty.")
    return value


class AureySettings(BaseSettings):
    """Loaded from environment variables with prefix ``AUREY_``.

    Operator API material uses ``ocv_*``: the agent API key is read once from the
    env variable **named** by ``ocv_agent_api_key_secret_source`` (typically
    ``AUREY_OCV_AGENT_API_KEY`` — the variable holds the secret value).

    Platform/console fields (``plt_*``) are for hosted provisioning flows (e.g.
    app registration, templates); runtime bootstrap does not require them unless
    you wire those code paths.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUREY_",
        extra="ignore",
        populate_by_name=True,
    )

    # --- Platform (hosted product / 1Claw console) ---------------------------------
    plt_oneclaw_base_url: str = Field(
        default=_DEFAULT_ONECLAW_BASE,
        description="1Claw API base URL for platform-scoped HTTP calls (no trailing slash).",
    )
    plt_app_id: str = Field(
        default="",
        description="1Claw platform / app identifier (registration in console).",
    )
    plt_template_id: str = Field(
        default="",
        description="Aurey / 1Claw template identifier for onboarding or provisioning.",
    )
    plt_app_api_key_secret_source: str | None = Field(
        default=None,
        description=(
            "Name of an environment variable holding the platform app API key. "
            "Unset means no platform key is resolved at runtime."
        ),
    )
    oidc_issuer: str = Field(
        default="",
        description=(
            "HTTPS issuer URL registered on the 1Claw platform app (no trailing slash). "
            "Used as JWT ``iss`` and to publish ``/.well-known/jwks.json``."
        ),
    )
    subject_token_audience: str = Field(
        default="",
        description=(
            "JWT ``aud`` for minted ``subject_token`` values. When empty, ``plt_app_id`` is used."
        ),
    )
    subject_token_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Lifetime (seconds) for minted subject tokens sent to ``users/upsert``.",
    )
    claim_poll_interval_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=3600.0,
        description=(
            "Background interval (seconds) for polling platform users "
            "awaiting wallet claim."
        ),
    )
    oidc_rsa_private_key_pem_secret_source: str | None = Field(
        default=None,
        description=(
            "Name of an environment variable holding an RSA private key PEM used to sign "
            "``subject_token`` JWTs (RS256). Unset disables OIDC minting / JWKS wiring."
        ),
    )

    # --- Operator runtime (vault agent / SecretStore client) -----------------------
    ocv_oneclaw_base_url: str = Field(
        default=_DEFAULT_ONECLAW_BASE,
        description="1Claw API base URL for operator agent / vault traffic.",
    )
    ocv_vault_id: str = Field(
        default="",
        description="Operator vault identifier (``vlt_…``) for secret reads.",
    )
    ocv_agent_id: str | None = Field(
        default=None,
        description="Operator agent id for hosted token exchange (``agt_…``).",
    )
    ocv_agent_api_key_secret_source: str = Field(
        default="AUREY_OCV_AGENT_API_KEY",
        description="Name of the environment variable that holds the operator agent API key.",
    )
    ocv_agent_token_expiry_skew_seconds: float = Field(
        default=60.0,
        ge=0.0,
        description=(
            "Seconds before 1Claw JWT ``expires_in`` deadline to drop the cached token and call "
            "``POST /v1/auth/agent-token`` again (avoids expiry mid-request; 0 disables skew only "
            "when expiry is known)."
        ),
    )
    oneclaw_delegated_token_scope: str = Field(
        default="intents:sign",
        description=(
            "Proposed scope string for ``POST /v1/auth/delegated-token`` when exchanging a hosted "
            "user grant into a short-lived JWT for Intents / unified signing. As of upstream "
            "Platform API docs, that route is described but **not yet wired** on 1Claw — treat "
            "this value as provisional until the endpoint is live and 1Claw publishes the final "
            "scope grammar."
        ),
    )
    cloud_hosted_intents_signing_enabled: bool = Field(
        default=False,
        description=(
            "Allow Telegram (and similar) invokes for ``ready`` platform users to use "
            ":func:`aurey.principal_augment.augment_runtime_for_principal` when "
            "``evm_signing_mode`` is ``oneclaw_intents``. Leave **False** by default — hosted "
            "delegated signing needs a working delegated-token endpoint, operator ``ocv_`` HTTP, "
            "and grant JWT material readable from ``grant_ref_path`` in the operator vault."
        ),
    )
    hosted_user_grant_secret_path_template: str | None = Field(
        default=None,
        description=(
            "Template for the operator-vault secret path holding each hosted user's grant JWT "
            "(subject token for delegated exchange). Substitutions: ``{vault_id}``, "
            "``{connection_id}``, ``{agent_id}`` from the platform user row. When unset, Aurey "
            "falls back to a synthetic locator string (not a real vault path). Operators should "
            "set this to a path they populate after claim."
        ),
    )

    alchemy_api_secret_path: str | None = Field(
        default=None,
        description="1Claw vault path for the Alchemy API key used for reads and RPC URLs.",
    )
    lifi_api_secret_path: str | None = Field(
        default=None,
        description=(
            "Optional 1Claw vault path for LiFi API key. If unset, swap quotes use "
            "unauthenticated LiFi (lower rate limits)."
        ),
    )
    lifi_integrator: str = Field(
        default="aurey",
        description=(
            "Sent as LiFi ``integrator`` query param on ``GET /v1/quote`` (tracking / routing). "
            "Set empty to omit."
        ),
    )
    evm_signing_mode: EvmSigningMode = Field(
        default="vault_key",
        description=(
            "How EVM transactions are signed: ``vault_key`` (vault-backed key material) or "
            "``oneclaw_intents`` (unified 1Claw signing; reserved for future use)."
        ),
    )
    wallet_signing_key_secret_path: str | None = Field(
        default=None,
        description=(
            "1Claw vault path for signing material. Required when ``evm_signing_mode`` is "
            "``vault_key``; used as a 1Claw ``signing_key_path`` override for "
            "``oneclaw_intents``."
        ),
    )
    telegram_bot_token_secret_path: str | None = Field(
        default=None,
        description="1Claw vault path for the Telegram bot token.",
    )
    telegram_allowed_chat_ids: str | None = Field(
        default=None,
        description=(
            "Comma- or whitespace-separated Telegram chat ids permitted to use the bot. "
            "Unset or empty means no restriction."
        ),
    )
    deep_agent_default_model: str = Field(
        default="openai:gpt-4o-mini",
        description="Default Deep Agents model spec when the HTTP API omits ``model``.",
    )
    deep_agent_wallet_address: str | None = Field(
        default=None,
        description=(
            "Optional EVM address appended to the deep agent system prompt on every graph compile "
            "(operator-defined long-term context). Checkpointed thread history remains separate "
            "(see README / MemorySaver)."
        ),
    )
    database_url: str | None = Field(
        default=None,
        description=(
            "Optional PostgreSQL URL for LangGraph checkpoint persistence. "
            "Reads ``DATABASE_URL`` (e.g. Railway) or ``AUREY_DATABASE_URL``."
        ),
        validation_alias=AliasChoices("AUREY_DATABASE_URL", "DATABASE_URL"),
    )

    @field_validator("telegram_allowed_chat_ids")
    @classmethod
    def _telegram_allowed_chat_ids_syntax(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            return None
        parse_telegram_allowed_chat_ids(stripped)
        return stripped

    @field_validator("plt_app_api_key_secret_source")
    @classmethod
    def _plt_app_api_key_secret_source_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @field_validator("oidc_rsa_private_key_pem_secret_source")
    @classmethod
    def _oidc_rsa_private_key_pem_secret_source_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @field_validator("ocv_agent_api_key_secret_source")
    @classmethod
    def _ocv_agent_api_key_secret_source_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ocv_agent_api_key_secret_source must not be empty.")
        return stripped

    @field_validator("hosted_user_grant_secret_path_template")
    @classmethod
    def _hosted_user_grant_secret_path_template_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @property
    def telegram_allowed_chat_id_allowlist(self) -> frozenset[int] | None:
        """Frozen set of allowed chat ids, or ``None`` when the bot accepts any chat."""

        return parse_telegram_allowed_chat_ids(self.telegram_allowed_chat_ids)

    @property
    def evm_signing_requires_wallet_signing_key_secret_path(self) -> bool:
        """True when vault-backed signing key material must be configured."""

        return self.evm_signing_mode == "vault_key"

    def resolve_ocv_agent_api_key(self) -> str:
        """Return the operator agent API key from ``ocv_agent_api_key_secret_source``."""

        return _read_trimmed_nonempty_env(self.ocv_agent_api_key_secret_source)

    def resolve_plt_app_api_key_optional(self) -> str | None:
        """Resolve platform app API key when ``plt_app_api_key_secret_source`` is set."""

        name = self.plt_app_api_key_secret_source
        if name is None:
            return None
        return _read_trimmed_nonempty_env(name)

    def resolve_oidc_rsa_private_key_pem_optional(self) -> str | None:
        """Load RSA PEM from ``oidc_rsa_private_key_pem_secret_source`` when configured."""

        name = self.oidc_rsa_private_key_pem_secret_source
        if name is None:
            return None
        return _read_trimmed_nonempty_env(name)

    def format_hosted_user_grant_secret_path(
        self,
        *,
        vault_id: str | None,
        connection_id: str,
        agent_id: str | None,
    ) -> str:
        """Expand :attr:`hosted_user_grant_secret_path_template` or return empty string."""

        tpl = (self.hosted_user_grant_secret_path_template or "").strip()
        if not tpl:
            return ""
        vid = (vault_id or "").strip() or "unknown_vault"
        cid = (connection_id or "").strip()
        aid = (agent_id or "").strip()
        return (
            tpl.replace("{vault_id}", vid)
            .replace("{connection_id}", cid)
            .replace("{agent_id}", aid)
        )

    def cloud_onboarding_configured(self) -> bool:
        """True when Telegram ``/start`` onboarding + JWKS plumbing should activate."""

        if not (self.database_url or "").strip():
            return False
        if self.resolve_plt_app_api_key_optional() is None:
            return False
        if not (self.plt_app_id or "").strip():
            return False
        if not (self.plt_template_id or "").strip():
            return False
        if not (self.oidc_issuer or "").strip():
            return False
        if self.resolve_oidc_rsa_private_key_pem_optional() is None:
            return False
        audience = (self.subject_token_audience or "").strip() or (self.plt_app_id or "").strip()
        return bool(audience)
