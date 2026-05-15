"""1Claw unified signing using delegated JWTs (hosted user agents)."""

from __future__ import annotations

from typing import Any

from aurey.custody.secret_store import (
    OneClawHttpClient,
    OneClawSignTransactionResult,
    SecretStore,
)
from aurey.principal import UserPrincipal


class PrincipalBackedOneClawSigner:
    """Resolves the user's grant from vault paths and signs via delegated-token."""

    def __init__(
        self,
        *,
        http: OneClawHttpClient,
        secret_store: SecretStore,
        principal: UserPrincipal,
        delegated_scope: str,
    ) -> None:
        self._http = http
        self._secret_store = secret_store
        self._principal = principal
        self._delegated_scope = delegated_scope.strip()

    def sign_evm_transaction(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict[str, Any],
        signing_key_path: str | None = None,
    ) -> OneClawSignTransactionResult:
        expected = self._principal.user_agent_id.strip()
        got = agent_id.strip()
        if got != expected:
            raise ValueError("sign_evm_transaction agent_id does not match hosted principal.")

        path = self._principal.grant_ref_path.strip()
        if not path:
            raise ValueError("hosted principal missing grant_ref_path.")

        subject_token = self._secret_store.get_secret(path).reveal()
        return self._http.sign_evm_transaction(
            agent_id=got,
            chain=chain,
            transaction=transaction,
            signing_key_path=signing_key_path,
            delegated_subject_token=subject_token,
            delegated_scope=self._delegated_scope,
        )


__all__ = ["PrincipalBackedOneClawSigner"]
