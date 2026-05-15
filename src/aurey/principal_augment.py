"""Build per-turn :class:`~aurey.runtime.AureyRuntime` overlays for hosted principals."""

from __future__ import annotations

from dataclasses import replace

from aurey.custody.delegated_signer import PrincipalBackedOneClawSigner
from aurey.principal import UserPrincipal
from aurey.runtime import AureyRuntime


def augment_runtime_for_principal(base: AureyRuntime, principal: UserPrincipal) -> AureyRuntime:
    """Return a runtime whose OneClaw signer uses delegated-token exchange for ``principal``."""

    http = base.oneclaw_operator_http
    if http is None:
        raise RuntimeError("Delegated signing requires `oneclaw_operator_http` on AureyRuntime.")
    scope = (base.settings.oneclaw_delegated_token_scope or "").strip()
    if not scope:
        raise RuntimeError(
            "`oneclaw_delegated_token_scope` / `AUREY_ONECLAW_DELEGATED_TOKEN_SCOPE` must be set."
        )
    signer = PrincipalBackedOneClawSigner(
        http=http,
        secret_store=base.secret_store,
        principal=principal,
        delegated_scope=scope,
    )
    return replace(base, principal=principal, oneclaw_evm_signer=signer)


__all__ = ["augment_runtime_for_principal"]
