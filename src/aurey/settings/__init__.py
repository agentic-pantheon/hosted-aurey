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

    @field_validator("ocv_agent_api_key_secret_source")
    @classmethod
    def _ocv_agent_api_key_secret_source_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ocv_agent_api_key_secret_source must not be empty.")
        return stripped

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
