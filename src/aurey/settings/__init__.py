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
LlmProxyMode = Literal["shroud", "direct"]

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
            "Platform app UUID (from ``GET /v1/platform/apps``). When set, hosted onboarding "
            "poll uses ``GET /v1/platform/apps/{id}/users`` to detect claim completion when "
            "per-connection GET is unavailable."
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
    platform_claim_return_to: str = Field(
        default="",
        description=(
            "Optional ``return_to`` URL sent to Platform ``reissue-claim`` (OAuth-style callback). "
            "Leave empty when not used."
        ),
        validation_alias=AliasChoices("AUREY_PLATFORM_CLAIM_RETURN_TO"),
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
            "Name of the env var whose value is an optional **delegated-token actor** key "
            "(e.g. separate ``ocv_``). When unset or empty, ``resolve_delegated_actor_api_key`` "
            "falls back to ``resolve_oneclaw_bootstrap_api_key``."
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
    hosted_synthetic_email_domain: str = Field(
        default="hosted-aurey.local",
        description=(
            "Legacy synthetic domain for Platform upserts (``tg_123@<domain>``) when "
            "``hosted_require_verified_email`` is false. Verified onboarding uses the real inbox."
        ),
    )
    hosted_require_verified_email: bool = Field(
        default=True,
        description=(
            "When hosted platform provisioning is enabled, Telegram users must verify a real "
            "email before Platform ``users/upsert``. Set False to keep legacy synthetic-email flow."
        ),
    )
    hosted_email_from: str = Field(
        default="fabri@agentic-pantheon.com",
        description=(
            "RFC5322 From address for hosted verification and claim emails "
            "(e.g. ``fabri@agentic-pantheon.com``)."
        ),
    )
    hosted_smtp_host: str = Field(
        default="",
        description="SMTP hostname for hosted onboarding mail; empty skips sending (verification fails closed when required).",
    )
    hosted_smtp_port: int = Field(default=587, ge=1, le=65535)
    hosted_smtp_user: str = Field(
        default="", description="SMTP auth username when required by the relay."
    )
    hosted_smtp_password: str = Field(
        default="",
        description="SMTP auth password via ``AUREY_HOSTED_SMTP_PASSWORD``.",
    )
    hosted_smtp_use_tls: Literal["starttls", "ssl", "plain"] = Field(
        default="starttls",
        description=(
            "``starttls`` (port 587 typical), ``ssl`` (SMTP_SSL), or ``plain`` (not recommended)."
        ),
    )
    hosted_email_verification_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    hosted_email_code_pepper: str = Field(
        default="",
        description=(
            "Secret concatenated server-side before hashing hosted email OTPs; "
            "required when sending verification emails."
        ),
    )
    hosted_email_verification_max_attempts: int = Field(default=5, ge=1, le=20)
    hosted_claim_email_throttle_seconds: int = Field(
        default=120,
        ge=30,
        le=86400,
        description="Minimum seconds between outbound claim emails for the same user.",
    )
    hosted_http_admin_token: str | None = Field(
        default=None,
        description=(
            "Opaque Bearer token guarding ``POST /v1/hosted/sync-wallet``. "
            "Global per deployment (not per Telegram user); unset disables the endpoint (503)."
        ),
    )

    hosted_agent_api_key_vault_id: str = Field(
        default="",
        description=(
            "Vault UUID for dual-writing hosted users' ``ocv_`` secrets via Human API PUT; "
            "when empty, ``oneclaw_vault_id`` is used."
        ),
    )
    hosted_agent_api_key_path_prefix: str = Field(
        default="hosted/agents",
        description=(
            "Prefix segment before ``/{user_agent_id}/agent_api_key`` for vault paths "
            "(alphanumeric, hyphens, underscores, slashes only)."
        ),
    )
    hosted_secrets_master_key: str | None = Field(
        default=None,
        description=(
            "Fernet URL-safe base64 key for ``hosted_platform_users.agent_api_key_encrypted``. "
            "Generate with Fernet.generate_key().decode()."
        ),
    )
    oneclaw_human_api_token: str | None = Field(
        default=None,
        description=(
            "Human API Bearer JWT for vault PUT when persisting hosted ``ocv_`` at bootstrap. "
            "Unset skips vault PUT (Postgres ciphertext/plaintext only)."
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
    llm_proxy: LlmProxyMode = Field(
        default="shroud",
        description=(
            "How Deep Agent reaches the LLM: ``shroud`` (1Claw Shroud with ``X-Shroud-*`` "
            "headers) or ``direct`` (``OPENAI_API_KEY`` straight to OpenAI). "
            "Reads env ``AUREY_LLM_PROXY`` (underscore field → ``LLM_PROXY``)."
        ),
    )
    shroud_base_url: str = Field(
        default="https://shroud.1claw.xyz",
        description=(
            "1Claw Shroud base URL for the LLM proxy (``/v1`` appended for chat completions). "
            "Environment: ``AUREY_SHROUD_BASE_URL``."
        ),
    )
    openai_api_key: str | None = Field(
        default=None,
        description=(
            "Plaintext OpenAI API key via ``OPENAI_API_KEY``: required when ``llm_proxy`` is "
            "``direct``; optional ``X-Shroud-Api-Key`` override when using Shroud."
        ),
        validation_alias=AliasChoices("OPENAI_API_KEY"),
    )
    openai_api_secret_path: str | None = Field(
        default=None,
        description=(
            "Optional 1Claw vault path holding the OpenAI key; in Shroud mode sent as "
            "``vault://{vault_id}/{path}`` when ``OPENAI_API_KEY`` is unset."
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

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _strip_openai_api_key(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

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

    @field_validator("hosted_agent_api_key_vault_id", mode="before")
    @classmethod
    def _hosted_agent_api_key_vault_id_strip(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("hosted_agent_api_key_path_prefix")
    @classmethod
    def _hosted_agent_api_key_path_prefix_normalized(cls, v: str) -> str:
        s = (v or "").strip().strip("/")
        return s if s else "hosted/agents"

    @field_validator("hosted_email_from")
    @classmethod
    def _hosted_email_from_non_empty(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("hosted_email_from must not be empty.")
        return s

    @field_validator("hosted_smtp_host", "hosted_smtp_user")
    @classmethod
    def _hosted_smtp_host_user_strip(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("hosted_smtp_password", mode="before")
    @classmethod
    def _hosted_smtp_password_strip(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v)

    @field_validator("oneclaw_human_api_token", "hosted_secrets_master_key", mode="before")
    @classmethod
    def _strip_optional_hosted_crypto(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @property
    def telegram_allowed_chat_id_allowlist(self) -> frozenset[int] | None:
        """Frozen set of allowed chat ids, or ``None`` when the bot accepts any chat."""

        return parse_telegram_allowed_chat_ids(self.telegram_allowed_chat_ids)

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

    def hosted_email_smtp_configured(self) -> bool:
        """True when ``hosted_smtp_host`` is non-empty (outbound SMTP may proceed)."""

        return bool(self.hosted_smtp_host.strip())

    def hosted_email_hmac_pepper_present(self) -> bool:
        return bool(self.hosted_email_code_pepper.strip())

    def resolve_delegated_actor_api_key(self) -> str:
        """Actor token for ``POST /v1/auth/delegated-token`` when using hosted intents.

        If ``AUREY_OPERATOR_AGENT_API_KEY`` (via ``operator_agent_api_key_secret_source``)
        is set and non-empty, use it — otherwise reuse the bootstrap 1Claw API key so a
        single operator credential is enough for hosted deployments.
        """

        source = (self.operator_agent_api_key_secret_source or "").strip()
        if source:
            raw = os.environ.get(source)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return self.resolve_oneclaw_bootstrap_api_key()
