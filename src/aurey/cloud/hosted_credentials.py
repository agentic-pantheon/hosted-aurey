"""Hosted per-user ``ocv_`` vault paths, Fernet encryption, and dual-write persistence."""

from __future__ import annotations

import logging
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from aurey.cloud.models import HostedPlatformUserORM
from aurey.custody.errors import SecretNotFoundError, SecretStoreUnavailableError
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


class HostedVaultHttpPort(Protocol):
    """Minimal vault HTTP surface used by :class:`~aurey.custody.secret_store.OneClawHttpClient`."""

    def put_secret_human_api(
        self,
        *,
        vault_id: str,
        path: str,
        value: str,
        bearer_token: str,
    ) -> None: ...

    def get_secret_operator_bootstrap_resolve(self, *, vault_id: str, path: str) -> str: ...


def agent_api_key_secret_path(prefix: str, user_agent_id: str) -> str:
    """Logical vault path for the hosted user's agent API key (``ocv_``)."""

    pfx = (prefix or "").strip().strip("/")
    ua = (user_agent_id or "").strip().strip("/")
    if not pfx or not ua:
        raise ValueError("prefix and user_agent_id must be non-empty.")
    return f"{pfx}/{ua}/agent_api_key"


def hosted_ocv_operator_vault_id(settings: AureySettings) -> str:
    """Vault used for hosted ``ocv_`` secrets (defaults to ``oneclaw_vault_id``)."""

    vid = (settings.hosted_agent_api_key_vault_id or "").strip()
    return vid if vid else (settings.oneclaw_vault_id or "").strip()


class HostedSecretsCipher:
    """Encrypt/decrypt ``ocv_`` at rest (Postgres backup column)."""

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    @classmethod
    def from_settings_optional(cls, settings: AureySettings) -> HostedSecretsCipher | None:
        raw = (settings.hosted_secrets_master_key or "").strip()
        if not raw:
            return None
        try:
            return cls(Fernet(raw.encode("ascii")))
        except ValueError:
            _log.warning(
                "hosted_secrets_master_key is set but invalid for Fernet; skipping cipher.",
            )
            return None

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            _log.warning(
                "hosted agent_api_key ciphertext decrypt failed (wrong key or corrupt blob).",
            )
            return None


def resolve_hosted_ocv_for_signing(
    settings: AureySettings,
    http_client: HostedVaultHttpPort,
    *,
    agent_id: str,
    ciphertext: str | None,
    legacy_plaintext: str | None,
) -> str | None:
    """Resolve ``ocv_`` for agent-token: vault read, decrypt backup, legacy plaintext."""

    ag = agent_id.strip()
    vault_id = hosted_ocv_operator_vault_id(settings)
    prefix = (settings.hosted_agent_api_key_path_prefix or "").strip() or "hosted/agents"

    if vault_id:
        path = agent_api_key_secret_path(prefix, ag)
        try:
            return http_client.get_secret_operator_bootstrap_resolve(
                vault_id=vault_id,
                path=path,
            ).strip()
        except SecretNotFoundError:
            pass
        except SecretStoreUnavailableError:
            _log.warning(
                "hosted ocv vault read failed vault_id=%s path=%s (will try DB ciphertext)",
                vault_id,
                path,
                exc_info=False,
            )

    cipher = HostedSecretsCipher.from_settings_optional(settings)
    ct = (ciphertext or "").strip()
    if cipher is not None and ct:
        pt = cipher.decrypt(ct)
        if pt:
            return pt.strip()

    leg = (legacy_plaintext or "").strip()
    return leg if leg else None


def persist_hosted_agent_ocv_credentials(
    *,
    settings: AureySettings,
    http_client: HostedVaultHttpPort,
    row: HostedPlatformUserORM,
    ocv: str,
    user_agent_id: str,
) -> None:
    """Dual-write ``ocv_`` to operator vault (Human API) and encrypted Postgres column."""

    vid = hosted_ocv_operator_vault_id(settings)
    prefix = (settings.hosted_agent_api_key_path_prefix or "").strip() or "hosted/agents"
    bearer = (settings.oneclaw_human_api_token or "").strip()
    key = user_agent_id.strip()
    secret = ocv.strip()

    vault_written = False
    if vid and bearer and key:
        path = agent_api_key_secret_path(prefix, key)
        try:
            http_client.put_secret_human_api(
                vault_id=vid,
                path=path,
                value=secret,
                bearer_token=bearer,
            )
            vault_written = True
        except SecretStoreUnavailableError:
            _log.warning(
                "hosted ocv vault PUT failed vault_id=%s path=%s",
                vid,
                path,
                exc_info=True,
            )

    cipher = HostedSecretsCipher.from_settings_optional(settings)
    if cipher is not None:
        row.agent_api_key_encrypted = cipher.encrypt(secret)
        row.agent_api_key = None
    elif vault_written:
        row.agent_api_key = None
        _log.info(
            "hosted ocv stored in vault only (no AUREY_HOSTED_SECRETS_MASTER_KEY); "
            "plaintext agent_api_key column cleared.",
        )
    else:
        row.agent_api_key = secret
        _log.warning(
            "hosted ocv persisted as plaintext DB agent_api_key "
            "(set AUREY_ONECLAW_HUMAN_API_TOKEN for vault PUT and/or "
            "AUREY_HOSTED_SECRETS_MASTER_KEY for ciphertext).",
        )


__all__ = [
    "HostedSecretsCipher",
    "HostedVaultHttpPort",
    "agent_api_key_secret_path",
    "hosted_ocv_operator_vault_id",
    "persist_hosted_agent_ocv_credentials",
    "resolve_hosted_ocv_for_signing",
]
