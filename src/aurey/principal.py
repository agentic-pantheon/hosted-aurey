"""Per-invoke identity for hosted (delegated signing) flows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    """Stable hosted-user identity for signing and threading (no grant secrets).

    ``grant_ref_path`` is a vault-relative locator resolved via the operator
    :class:`~aurey.custody.secret_store.SecretStore`; raw grant tokens must
    never be stored on this object.
    """

    db_user_id: str
    user_agent_id: str
    grant_ref_path: str
    wallet_address: str | None = None


__all__ = ["UserPrincipal"]
