"""Application settings (paths-only secret references, 1Claw connection config).

Optional plaintext ``AUREY_*`` keys for Alchemy, LiFi, and Telegram may be set for hosted
deployments; when non-empty they take precedence over vault ``*_secret_path`` resolution.
Note: Configuration lives in this package intentionally; do not add a sibling
``aurey/settings.py`` module, which would conflict with this package name.
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


class AureySettings(BaseSettings):
    """Loaded from environment variables with prefix ``AUREY_``.

    Secret *paths* refer to vault paths resolved at runtime via a ``SecretStore``;
    the bootstrap API key is read once from whichever env variable name you set in
    ``oneclaw_api_key_secret_source`` (another env variable's **name**, not its value).
    """

    model_config = SettingsConfigDict(
        env_prefix="AUREY_",
        extra="ignore",
        populate_by_name=True,
    )

    oneclaw_base_url: str = Field(
        default="https://api.1claw.xyz",
        description="1Claw API base URL (no trailing slash required).",
    )
    oneclaw_vault_id: str = Field(
        default="",
        description="Vault identifier for secret reads.",
    )
    oneclaw_api_key_secret_source: str = Field(
        default="AUREY_ONECLAW_BOOTSTRAP_API_KEY",
        description="Name of the environment variable that holds the bootstrap 1Claw API key.",
    )
    oneclaw_agent_id: str | None = Field(
        default=None,
        description="Optional agent id for hosted token exchange flow.",
    )
    oneclaw_agent_token_expiry_skew_seconds: float = Field(
        default=60.0,
        ge=0.0,
        description=(
            "Seconds before 1Claw JWT ``expires_in`` deadline to drop the cached token and call "
            "``POST /v1/auth/agent-token`` again (avoids expiry mid-request; 0 disables skew only "
            "when expiry is known)."
        ),
    )
    oneclaw_delegated_token_scope: str = Field(
        default="1claw:intents:delegated",
        description=(
            "OAuth-style scope string for 1Claw intents delegation / hosted token flows; "
            "adjust to match your Platform app and security policy."
        ),
    )

    platform_app_id: str | None = Field(
        default=None,
        description=(
            "Optional Platform app identifier (for API paths, metrics, and logging). "
            "Unset when not wired to the 1Claw Platform API."
        ),
    )
    platform_api_key: str | None = Field(
        default=None,
        description=(
            "Hosted Platform API key value (``plt_…``). Supply via ``AUREY_PLATFORM_API_KEY`` "
            "(see ``validation_alias``)."
        ),
        validation_alias=AliasChoices("AUREY_PLATFORM_API_KEY"),
    )
    platform_template_id: str = Field(
        default="",
        description=(
            "Platform provisioning template id (from bootstrap / operator setup). "
            "Empty until a template is registered."
        ),
    )
    operator_vault_id: str = Field(
        default="",
        description=(
            "1Claw vault id the operator / agent runtime uses for secret reads "
            "(``ocv_`` context). Leave empty until provisioned."
        ),
    )
    operator_agent_id: str | None = Field(
        default=None,
        description="Optional operator agent id in 1Claw (hosted control plane).",
    )
    operator_agent_api_key_secret_source: str = Field(
        default="AUREY_OPERATOR_AGENT_API_KEY",
        description=(
            "Name of the environment variable whose **value** is the operator agent API key "
            "(``ocv_…`` or bootstrap-style key), same indirection pattern as "
            "``oneclaw_api_key_secret_source``. Never commit the key; set the named env only."
        ),
    )

    hosted_platform_enabled: bool = Field(
        default=False,
        description=(
            "When true, enable hosted control-plane integrations "
            "(Telegram provisioning, DB metadata). "
            "Requires a configured Postgres ``database_url``."
        ),
    )
    hosted_admin_telegram_user_ids: str | None = Field(
        default=None,
        description=(
            "Comma- or whitespace-separated Telegram **user** ids allowed to run "
            "``/grant`` / ``/delegation_grant`` (delegation subject token persistence). "
            "Unset or empty disables the grant command. "
            "**Staging only:** tokens are stored in plaintext in the DB."
        ),
    )
    hosted_synthetic_email_domain: str = Field(
        default="hosted-aurey.local",
        description=(
            "Synthetic email domain for Platform user upserts (e.g. ``tg_123@<domain>``). "
            "Whitespace is stripped; leading/trailing dots removed."
        ),
    )

    hosted_oidc_issuer_url: str | None = Field(
        default=None,
        description="Phase B: OIDC issuer URL for hosted user flows (optional).",
    )
    hosted_oidc_audience: str | None = Field(
        default=None,
        description="Phase B: OIDC audience for hosted tokens (optional).",
    )
    hosted_oidc_subject_token_ttl_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "Phase B: suggested TTL (seconds) in user-facing docs for subject / session-token "
            "freshness; not enforced by this settings model alone."
        ),
    )

    alchemy_api_secret_path: str | None = Field(
        default=None,
        description="1Claw vault path for the Alchemy API key used for reads and RPC URLs.",
    )
    alchemy_api_key: str | None = Field(
        default=None,
        description=(
            "Optional plaintext Alchemy API key (``AUREY_ALCHEMY_API_KEY``). "
            "When set, used instead of ``alchemy_api_secret_path``."
        ),
        validation_alias=AliasChoices("AUREY_ALCHEMY_API_KEY"),
    )
    lifi_api_secret_path: str | None = Field(
        default=None,
        description=(
            "Optional 1Claw vault path for LiFi API key. If unset, swap quotes use "
            "unauthenticated LiFi (lower rate limits)."
        ),
    )
    lifi_api_key: str | None = Field(
        default=None,
        description=(
            "Optional plaintext LiFi API key (``AUREY_LIFI_API_KEY``). When set, used instead of "
            "``lifi_api_secret_path``."
        ),
        validation_alias=AliasChoices("AUREY_LIFI_API_KEY"),
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
    telegram_bot_token: str | None = Field(
        default=None,
        description=(
            "Optional plaintext Telegram bot token (``AUREY_TELEGRAM_BOT_TOKEN``). When set, "
            "preferred over ``telegram_bot_token_secret_path``."
        ),
        validation_alias=AliasChoices("AUREY_TELEGRAM_BOT_TOKEN"),
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

    @field_validator("alchemy_api_key", "lifi_api_key", "telegram_bot_token", mode="before")
    @classmethod
    def _strip_optional_plaintext_api_credentials(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("hosted_synthetic_email_domain")
    @classmethod
    def _hosted_synthetic_email_domain_normalized(cls, v: str) -> str:
        s = (v or "").strip().strip(".")
        if not s:
            raise ValueError("hosted_synthetic_email_domain must not be empty.")
        return s

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

    @field_validator("hosted_admin_telegram_user_ids")
    @classmethod
    def _hosted_admin_telegram_user_ids_syntax(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            return None
        parse_telegram_allowed_chat_ids(stripped)
        return stripped

    @property
    def telegram_allowed_chat_id_allowlist(self) -> frozenset[int] | None:
        """Frozen set of allowed chat ids, or ``None`` when the bot accepts any chat."""

        return parse_telegram_allowed_chat_ids(self.telegram_allowed_chat_ids)

    @property
    def hosted_admin_telegram_user_id_allowlist(self) -> frozenset[int] | None:
        """Admins allowed to persist delegation tokens; ``None`` when unset (same as empty)."""

        return parse_telegram_allowed_chat_ids(self.hosted_admin_telegram_user_ids)

    @property
    def evm_signing_requires_wallet_signing_key_secret_path(self) -> bool:
        """True when vault-backed signing key material must be configured."""

        return self.evm_signing_mode == "vault_key"

    def resolve_oneclaw_bootstrap_api_key(self) -> str:
        """Return bootstrap API key from the env named by ``oneclaw_api_key_secret_source``."""

        name = self.oneclaw_api_key_secret_source.strip()
        if not name:
            raise ValueError("oneclaw_api_key_secret_source must not be empty.")
        raw = os.environ.get(name)
        if raw is None:
            raise KeyError(name)
        value = raw.strip()
        if not value:
            raise ValueError(f"Environment variable {name!r} is set but empty.")
        return value

    def resolve_operator_agent_api_key(self) -> str:
        """Return the operator agent API key from ``operator_agent_api_key_secret_source``."""

        name = self.operator_agent_api_key_secret_source.strip()
        if not name:
            raise ValueError("operator_agent_api_key_secret_source must not be empty.")
        raw = os.environ.get(name)
        if raw is None:
            raise KeyError(name)
        value = raw.strip()
        if not value:
            raise ValueError(f"Environment variable {name!r} is set but empty.")
        return value
