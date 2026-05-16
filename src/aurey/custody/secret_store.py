"""Path-based secret store: 1Claw HTTP client, in-memory fakes, and SecretValue wrapper."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from aurey.custody.errors import (
    EmptySecretValueError,
    OneClawSigningError,
    SecretNotFoundError,
    SecretStoreUnavailableError,
)

_log = logging.getLogger(__name__)


def delegated_subject_fingerprint(secret_subject_token: str) -> str:
    """Stable short id for cache keys and logs — never log the raw ``subject_token``."""

    return hashlib.sha256(secret_subject_token.encode("utf-8")).hexdigest()[:16]


def _http_error_snippet(exc: HTTPError, *, max_len: int = 800) -> str:
    """Best-effort read of error response body without failing the caller."""

    try:
        fp = getattr(exc, "fp", None)
        if fp is not None and hasattr(fp, "read"):
            raw = fp.read()
            if raw:
                return raw.decode("utf-8", errors="replace").strip()[:max_len]
    except Exception:
        pass
    return ""


def _web3_tx_to_oneclaw_unified_flat(tx_body: dict[str, Any]) -> dict[str, Any]:
    """Map a Web3/eth_account-style tx dict onto 1Claw unified ``/sign`` transaction fields.

    1Claw expects *flat* JSON keys (`tx_type`, `gas_limit`, `max_fee_per_gas`, …); it does not
    accept a nested ``transaction`` blob with camelCase EIP-1559 fields from Web3.py.
    See https://docs.1claw.xyz/docs/guides/intents-api (Unified sign endpoint).
    """

    if "to" not in tx_body:
        raise ValueError("transaction missing required key 'to'.")

    raw_val = tx_body.get("value", 0)
    value_wei = int(raw_val) if not isinstance(raw_val, str) else int(raw_val, 0)

    data = tx_body.get("data", "0x")
    if hasattr(data, "hex"):
        hx = data.hex()
        data = hx if hx.startswith("0x") else f"0x{hx}"
    data_s = data if isinstance(data, str) else str(data)

    nonce = int(tx_body["nonce"])
    gas_any = tx_body.get("gas", tx_body.get("gasLimit"))
    if gas_any is None:
        raise ValueError("transaction missing 'gas' / 'gasLimit'.")
    gas_limit = int(gas_any)

    out: dict[str, Any] = {
        "to": str(tx_body["to"]),
        "data": data_s if data_s else "0x",
        "nonce": nonce,
        "gas_limit": gas_limit,
        "value": str(value_wei),
    }

    mf = tx_body.get("maxFeePerGas")
    mp = tx_body.get("maxPriorityFeePerGas")
    gp = tx_body.get("gasPrice")

    if mf is not None and mp is not None:
        out["tx_type"] = 2
        out["max_fee_per_gas"] = str(int(mf))
        out["max_priority_fee_per_gas"] = str(int(mp))
    elif gp is not None:
        out["tx_type"] = 0
        out["gas_price"] = str(int(gp))
    else:
        raise ValueError(
            "transaction must include EIP-1559 fields maxFeePerGas and maxPriorityFeePerGas "
            "or legacy gasPrice."
        )

    return out


@dataclass(frozen=True, repr=False)
class SecretValue:
    """Typed secret value whose raw string is only available by explicit reveal."""

    path: str
    _value: str

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("Secret path must not be empty.")
        if not self._value.strip():
            raise EmptySecretValueError(self.path)

    def reveal(self) -> str:
        """Return the raw secret value for the narrow caller that needs it."""

        return self._value

    def __repr__(self) -> str:
        return f"SecretValue(path={self.path!r}, value=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


@runtime_checkable
class SecretStore(Protocol):
    """Path-based secret store interface."""

    def get_secret(self, path: str) -> SecretValue:
        """Resolve a secret path into a typed secret value."""


@runtime_checkable
class OneClawClient(Protocol):
    """Minimal 1Claw client shape used by the secret-store wrapper."""

    def get_secret(self, *, vault_id: str, path: str, agent_id: str | None = None) -> str:
        """Return the raw secret string for a path."""


@dataclass(frozen=True)
class OneClawSignTransactionResult:
    """Parsed response from 1Claw unified EVM signing (``POST /v1/agents/{agent_id}/sign``)."""

    signed_tx: str
    tx_hash: str | None = None
    from_address: str | None = None
    tx_type: str | None = None


@runtime_checkable
class OneClawEvmTransactionSigner(Protocol):
    """1Claw agent bearer flow for unified transaction signing."""

    def sign_evm_transaction(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict[str, Any],
        signing_key_path: str | None = None,
        authorization_bearer: str | None = None,
    ) -> OneClawSignTransactionResult:
        """Request a signed EVM transaction for the given chain and unsigned fields."""


class OneClawSecretStore:
    """SecretStore implementation backed by a 1Claw client."""

    def __init__(
        self,
        *,
        client: OneClawClient,
        vault_id: str,
        agent_id: str | None = None,
    ) -> None:
        if not vault_id.strip():
            raise ValueError("1Claw vault ID must not be empty.")

        self._client = client
        self._vault_id = vault_id
        self._agent_id = agent_id

    def get_secret(self, path: str) -> SecretValue:
        """Resolve a non-wallet secret path through 1Claw."""

        if not path.strip():
            raise ValueError("Secret path must not be empty.")

        try:
            aid = self._agent_id.strip() if self._agent_id and str(self._agent_id).strip() else None
            _log.info(
                "SecretStore issuing 1Claw read vault_id=%s secret_path=%s agent_id=%s",
                self._vault_id,
                path.strip(),
                aid or "(legacy resolve; no agent id)",
            )
            value = self._client.get_secret(
                vault_id=self._vault_id,
                path=path,
                agent_id=self._agent_id,
            )
        except SecretNotFoundError:
            raise
        except EmptySecretValueError:
            raise
        except SecretStoreUnavailableError:
            raise
        except Exception as exc:
            raise SecretStoreUnavailableError(path, store_name="1Claw") from exc

        if value is None:
            raise SecretNotFoundError(path)

        return SecretValue(path=path, _value=value)


class OneClawHttpClient:
    """Small stdlib HTTP adapter for 1Claw-compatible secret reads.

    Hosted ``api.1claw.xyz`` flow (when ``agent_id`` is passed on each read):

    1. ``POST /v1/auth/agent-token`` with ``agent_id`` and ``api_key`` (agent API key).
    2. ``GET /v1/vaults/{vault_id}/secrets/{path}`` with ``Authorization: Bearer <jwt>``.

    When ``agent_id`` is omitted, uses legacy ``POST .../secrets:resolve`` with the API key
    as the bearer token (for older or self-hosted deployments).

    Agent JWTs from ``POST /v1/auth/agent-token`` are cached and reused (per 1Claw guidance to
    refresh before expiry, not on every request). If the response includes ``expires_in`` seconds,
    the client refreshes after ``expires_in - agent_token_expiry_skew_seconds`` to avoid edge
    expiry; if ``expires_in`` is absent, the token is kept until the API returns 401.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        agent_token_expiry_skew_seconds: float = 60.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("1Claw base URL must not be empty.")
        if not api_key.strip():
            raise ValueError("1Claw API key must not be empty.")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._agent_token_expiry_skew_seconds = max(0.0, float(agent_token_expiry_skew_seconds))
        self._access_token: str | None = None
        self._access_token_agent: str | None = None
        self._access_token_expires_at: float | None = None
        self._delegated_access_tokens: dict[tuple[str, str], tuple[str, float | None]] = {}

    def get_secret(self, *, vault_id: str, path: str, agent_id: str | None = None) -> str:
        """Read a secret value from 1Claw without exposing it in errors."""

        agent = agent_id.strip() if agent_id and agent_id.strip() else None
        if agent is not None:
            _log.info(
                "1Claw hosted secret flow base_url=%s vault_id=%s secret_path=%s agent_id=%s",
                self._base_url,
                vault_id,
                path.strip(),
                agent,
            )
            return self._get_secret_hosted(vault_id=vault_id, path=path, agent_id=agent)
        _log.info(
            "1Claw legacy secrets:resolve base_url=%s vault_id=%s secret_path=%s",
            self._base_url,
            vault_id,
            path.strip(),
        )
        return self._get_secret_legacy_resolve(vault_id=vault_id, path=path)

    def _invalidate_access_token(self, agent_id: str) -> None:
        if self._access_token_agent == agent_id:
            self._access_token = None
            self._access_token_agent = None
            self._access_token_expires_at = None

    def _fetch_access_token(self, agent_id: str) -> str:
        url = f"{self._base_url}/v1/auth/agent-token"
        _log.info(
            "1Claw requesting agent access token POST %s agent_id=%s (credentials not logged)",
            url,
            agent_id,
        )
        body = json.dumps({"agent_id": agent_id, "api_key": self._api_key}).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            _log.warning(
                "1Claw POST agent-token failed HTTP %s url=%s agent_id=%s response_body_preview=%s",
                exc.code,
                url,
                agent_id,
                _http_error_snippet(exc, max_len=240),
            )
            raise SecretStoreUnavailableError(
                "/v1/auth/agent-token",
                store_name="1Claw",
                detail=(
                    f"Agent token exchange failed with HTTP {exc.code}. "
                    "This happens before any vault secret (e.g. Telegram path) is read. "
                    "Check `oneclaw_agent_id`, bootstrap API key, and 1Claw availability."
                ),
            ) from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            _log.warning(
                "1Claw POST agent-token failed (network/json) url=%s agent_id=%s err=%s",
                url,
                agent_id,
                type(exc).__name__,
            )
            raise SecretStoreUnavailableError(
                "/v1/auth/agent-token",
                store_name="1Claw",
                detail=(
                    "Agent token exchange failed (network error or invalid JSON). "
                    "This step runs before reading a vault secret path."
                ),
            ) from exc

        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise SecretStoreUnavailableError(
                "/v1/auth/agent-token",
                store_name="1Claw",
                detail="Agent token response contained no usable `access_token`.",
            )

        self._access_token = token.strip()
        self._access_token_agent = agent_id
        expires_raw = payload.get("expires_in")
        if isinstance(expires_raw, (int, float)) and float(expires_raw) > 0:
            ttl = float(expires_raw) - self._agent_token_expiry_skew_seconds
            self._access_token_expires_at = time.monotonic() + max(0.0, ttl)
        else:
            self._access_token_expires_at = None
        _log.info(
            "1Claw agent access token cached for agent_id=%s (value not logged; expires_in-based "
            "refresh=%s)",
            agent_id,
            "yes" if self._access_token_expires_at is not None else "no (refresh on 401 only)",
        )
        return self._access_token

    def _bearer_for_agent(self, agent_id: str) -> str:
        if self._access_token and self._access_token_agent == agent_id:
            if (
                self._access_token_expires_at is not None
                and time.monotonic() >= self._access_token_expires_at
            ):
                _log.info(
                    "1Claw cached agent token past expires_in window (skew=%ss); refreshing "
                    "agent_id=%s",
                    self._agent_token_expiry_skew_seconds,
                    agent_id,
                )
                self._invalidate_access_token(agent_id)
            else:
                _log.debug("1Claw reusing cached access token agent_id=%s", agent_id)
                return self._access_token
        return self._fetch_access_token(agent_id)

    def post_delegated_access_token(
        self,
        *,
        actor_token: str,
        subject_token: str,
        scope: str,
        agent_id: str,
    ) -> str:
        """Exchange subject grant + operator actor token for a short-lived bearer JWT.

        Calls ``POST /v1/auth/delegated-token``.
        """

        ag = agent_id.strip()
        if not ag:
            raise ValueError("agent_id must be non-empty for delegated token exchange.")
        sub_stripped = subject_token.strip()
        if not sub_stripped:
            raise ValueError("subject_token must be non-empty.")
        actor_stripped = actor_token.strip()
        if not actor_stripped:
            raise ValueError("actor_token must be non-empty.")
        scope_stripped = (scope or "").strip()
        if not scope_stripped:
            raise ValueError("scope must be non-empty.")

        fp = delegated_subject_fingerprint(sub_stripped)
        cache_key = (fp, ag)
        now_m = time.monotonic()
        cached = self._delegated_access_tokens.get(cache_key)
        if cached is not None:
            tok, exp_at = cached
            if exp_at is None or now_m < exp_at:
                _log.debug(
                    "1Claw reusing cached delegated JWT fp16=%s agent_id=%s",
                    fp,
                    ag,
                )
                return tok

        url = f"{self._base_url}/v1/auth/delegated-token"
        _log.info(
            "1Claw requesting delegated access token POST %s fp16=%s agent_id=%s",
            url,
            fp,
            ag,
        )
        body = json.dumps(
            {
                "subject_token": sub_stripped,
                "actor_token": actor_stripped,
                "scope": scope_stripped,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            _log.warning(
                "1Claw POST delegated-token failed HTTP %s url=%s fp16=%s agent_id=%s "
                "response_body_preview=%s",
                exc.code,
                url,
                fp,
                ag,
                _http_error_snippet(exc, max_len=240),
            )
            raise SecretStoreUnavailableError(
                "/v1/auth/delegated-token",
                store_name="1Claw",
                detail=(
                    f"Delegated token exchange failed with HTTP {exc.code}. "
                    "Check operator agent API key, subject token, and 1Claw availability."
                ),
            ) from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            _log.warning(
                "1Claw POST delegated-token failed (network/json) url=%s fp16=%s "
                "agent_id=%s err=%s",
                url,
                fp,
                ag,
                type(exc).__name__,
            )
            raise SecretStoreUnavailableError(
                "/v1/auth/delegated-token",
                store_name="1Claw",
                detail="Delegated token exchange failed (network error or invalid JSON).",
            ) from exc

        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise SecretStoreUnavailableError(
                "/v1/auth/delegated-token",
                store_name="1Claw",
                detail="Delegated token response contained no usable `access_token`.",
            )

        jwt = token.strip()
        expires_at: float | None = None
        expires_raw = payload.get("expires_in")
        if isinstance(expires_raw, (int, float)) and float(expires_raw) > 0:
            ttl = float(expires_raw) - self._agent_token_expiry_skew_seconds
            expires_at = time.monotonic() + max(0.0, ttl)
        self._delegated_access_tokens[cache_key] = (jwt, expires_at)
        _log.info(
            "1Claw delegated JWT cached fp16=%s agent_id=%s (value not logged; expires_in-based "
            "refresh=%s)",
            fp,
            ag,
            "yes" if expires_at is not None else "no (refresh on 401 only)",
        )
        return jwt

    def _secret_url_path_suffix(self, path: str) -> str:
        normalized = path.strip().lstrip("/")
        if not normalized:
            raise ValueError("Secret path must not be empty.")
        return quote(normalized, safe="/")

    def _get_secret_hosted(self, *, vault_id: str, path: str, agent_id: str) -> str:
        bearer = self._bearer_for_agent(agent_id)
        try:
            return self._http_get_secret_value(vault_id, path, bearer)
        except HTTPError as exc:
            if exc.code == 401:
                self._invalidate_access_token(agent_id)
                bearer = self._bearer_for_agent(agent_id)
                return self._http_get_secret_value(vault_id, path, bearer)
            if exc.code == 404:
                raise SecretNotFoundError(path) from exc
            if exc.code == 403:
                raise SecretStoreUnavailableError(
                    path,
                    store_name="1Claw",
                    detail=(
                        "HTTP 403 Forbidden - the agent token is valid but this principal "
                        "is not allowed to read that vault or secret path."
                    ),
                ) from exc
            raise SecretStoreUnavailableError(path, store_name="1Claw") from exc

    def _http_get_secret_value(self, vault_id: str, path: str, bearer: str) -> str:
        suffix = self._secret_url_path_suffix(path)
        url = f"{self._base_url}/v1/vaults/{vault_id}/secrets/{suffix}"
        _log.info(
            "1Claw GET secret (Authorization bearer redacted) url=%s vault_id=%s logical_path=%s",
            url,
            vault_id,
            path.strip(),
        )
        request = Request(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            _log.warning(
                "1Claw GET secret HTTP %s vault_id=%s logical_path=%s url=%s "
                "response_body_preview=%s",
                exc.code,
                vault_id,
                path.strip(),
                url,
                _http_error_snippet(exc, max_len=240),
            )
            raise
        except (OSError, URLError, json.JSONDecodeError) as exc:
            _log.warning(
                "1Claw GET secret failed vault_id=%s logical_path=%s url=%s err=%s",
                vault_id,
                path.strip(),
                url,
                type(exc).__name__,
            )
            raise SecretStoreUnavailableError(path, store_name="1Claw") from exc

        value = _extract_secret_value(response_payload)
        if value is None:
            raise SecretNotFoundError(path)
        return value

    def _get_secret_legacy_resolve(self, *, vault_id: str, path: str) -> str:
        url = f"{self._base_url}/v1/vaults/{vault_id}/secrets:resolve"
        _log.info(
            "1Claw POST secrets:resolve (authorization header redacted) url=%s "
            "vault_id=%s body_path=%s",
            url,
            vault_id,
            path.strip(),
        )
        payload = json.dumps({"path": path}).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            _log.warning(
                "1Claw POST secrets:resolve HTTP %s vault_id=%s path=%s url=%s "
                "response_body_preview=%s",
                exc.code,
                vault_id,
                path.strip(),
                url,
                _http_error_snippet(exc, max_len=240),
            )
            if exc.code == 404:
                raise SecretNotFoundError(path) from exc
            raise SecretStoreUnavailableError(path, store_name="1Claw") from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            _log.warning(
                "1Claw POST secrets:resolve failed vault_id=%s path=%s url=%s err=%s",
                vault_id,
                path.strip(),
                url,
                type(exc).__name__,
            )
            raise SecretStoreUnavailableError(path, store_name="1Claw") from exc

        value = _extract_secret_value(response_payload)
        if value is None:
            raise SecretNotFoundError(path)
        return value

    def sign_evm_transaction(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict[str, Any],
        signing_key_path: str | None = None,
        authorization_bearer: str | None = None,
    ) -> OneClawSignTransactionResult:
        """Sign an unsigned EVM transaction via 1Claw unified signing."""

        ag = agent_id.strip() if agent_id else ""
        ch = chain.strip() if chain else ""
        if not ag or not ch:
            raise ValueError("agent_id and chain must be non-empty.")

        sign_path = f"/v1/agents/{quote(ag, safe='')}/sign"
        override = (authorization_bearer or "").strip() or None

        if override is not None:
            try:
                return self._http_post_agent_sign(
                    agent_id=ag,
                    chain=ch,
                    transaction=transaction,
                    signing_key_path=signing_key_path,
                    bearer=override,
                )
            except HTTPError as exc:
                snip = _http_error_snippet(exc)
                body_note = f" Body: {snip}" if snip else ""
                raise SecretStoreUnavailableError(
                    sign_path,
                    store_name="1Claw",
                    detail=(
                        f"Unified signing failed with HTTP {exc.code} "
                        f"(delegated bearer; no bootstrap token retry).{body_note}"
                    ),
                ) from exc

        bearer = self._bearer_for_agent(ag)
        try:
            return self._http_post_agent_sign(
                agent_id=ag,
                chain=ch,
                transaction=transaction,
                signing_key_path=signing_key_path,
                bearer=bearer,
            )
        except HTTPError as exc:
            if exc.code == 401:
                self._invalidate_access_token(ag)
                bearer = self._bearer_for_agent(ag)
                try:
                    return self._http_post_agent_sign(
                        agent_id=ag,
                        chain=ch,
                        transaction=transaction,
                        signing_key_path=signing_key_path,
                        bearer=bearer,
                    )
                except HTTPError as exc2:
                    snip2 = _http_error_snippet(exc2)
                    raise SecretStoreUnavailableError(
                        sign_path,
                        store_name="1Claw",
                        detail=(
                            "Unified signing failed after refreshing the agent token "
                            f"(HTTP {exc2.code}).{f' Body: {snip2}' if snip2 else ''}"
                        ),
                    ) from exc2
            snip = _http_error_snippet(exc)
            body_note = f" Body: {snip}" if snip else ""
            raise SecretStoreUnavailableError(
                sign_path,
                store_name="1Claw",
                detail=(
                    f"Unified signing failed with HTTP {exc.code}.{body_note}"
                ),
            ) from exc

    def _http_post_agent_sign(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict[str, Any],
        signing_key_path: str | None,
        bearer: str,
    ) -> OneClawSignTransactionResult:
        sign_path_suffix = f"v1/agents/{quote(agent_id, safe='')}/sign"
        sign_path = f"/{sign_path_suffix}"
        url = f"{self._base_url}/{sign_path_suffix}"
        try:
            flat_tx = _web3_tx_to_oneclaw_unified_flat(transaction)
        except ValueError as exc:
            raise OneClawSigningError(
                "Cannot convert Web3-style transaction payload for 1Claw unified sign "
                f"({exc})."
            ) from exc

        body = json.dumps(
            {
                "intent_type": "transaction",
                "chain": chain,
                **flat_tx,
                **(
                    {"signing_key_path": signing_key_path.strip()}
                    if signing_key_path and signing_key_path.strip()
                    else {}
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError:
            raise
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise SecretStoreUnavailableError(
                sign_path,
                store_name="1Claw",
                detail="Unified signing failed (network error or invalid JSON).",
            ) from exc

        return _parse_sign_transaction_response(response_payload)


class FakeSecretStore:
    """In-memory SecretStore for tests."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def get_secret(self, path: str) -> SecretValue:
        try:
            value = self._secrets[path]
        except KeyError as exc:
            raise SecretNotFoundError(path) from exc

        return SecretValue(path=path, _value=value)


class FakeOneClawClient:
    """In-memory OneClawClient for wrapper tests."""

    def __init__(
        self,
        secrets: dict[str, str] | None = None,
        *,
        sign_response: OneClawSignTransactionResult | None = None,
        sign_exception: Exception | None = None,
        delegated_jwt: str = "delegated-jwt-test",
    ) -> None:
        self._secrets = dict(secrets or {})
        self.requests: list[dict[str, str | None]] = []
        self._sign_response = sign_response
        self._sign_exception = sign_exception
        self._delegated_jwt = delegated_jwt
        self.sign_requests: list[dict[str, Any]] = []
        self.delegated_calls: list[dict[str, str]] = []

    def get_secret(self, *, vault_id: str, path: str, agent_id: str | None = None) -> str:
        self.requests.append({"vault_id": vault_id, "path": path, "agent_id": agent_id})
        try:
            return self._secrets[path]
        except KeyError as exc:
            raise SecretNotFoundError(path) from exc

    def post_delegated_access_token(
        self,
        *,
        actor_token: str,
        subject_token: str,
        scope: str,
        agent_id: str,
    ) -> str:
        self.delegated_calls.append(
            {
                "actor_token": actor_token,
                "subject_token": subject_token,
                "scope": scope,
                "agent_id": agent_id,
            }
        )
        return self._delegated_jwt

    def sign_evm_transaction(
        self,
        *,
        agent_id: str,
        chain: str,
        transaction: dict[str, Any],
        signing_key_path: str | None = None,
        authorization_bearer: str | None = None,
    ) -> OneClawSignTransactionResult:
        req: dict[str, Any] = {
            "agent_id": agent_id,
            "chain": chain,
            "transaction": dict(transaction),
        }
        if signing_key_path is not None:
            req["signing_key_path"] = signing_key_path
        if authorization_bearer is not None:
            req["authorization_bearer"] = authorization_bearer
        self.sign_requests.append(req)
        if self._sign_exception is not None:
            raise self._sign_exception
        if self._sign_response is not None:
            return self._sign_response
        return OneClawSignTransactionResult(
            signed_tx="0xfake_signed_tx",
            tx_hash="0xfake_tx_hash",
            from_address="0xfake_from",
            tx_type="2",
        )


def _parse_sign_transaction_response(payload: Any) -> OneClawSignTransactionResult:
    if not isinstance(payload, dict):
        raise OneClawSigningError("1Claw signing response was not a JSON object.")

    signed_tx = payload.get("signed_tx")
    if not isinstance(signed_tx, str) or not signed_tx.strip():
        raise OneClawSigningError("1Claw signing response contained no usable `signed_tx`.")

    tx_hash = payload.get("tx_hash")
    tx_hash_out = tx_hash.strip() if isinstance(tx_hash, str) and tx_hash.strip() else None

    from_raw = payload.get("from")
    from_out = from_raw.strip() if isinstance(from_raw, str) and from_raw.strip() else None

    tx_type = payload.get("tx_type")
    tx_type_out = tx_type.strip() if isinstance(tx_type, str) and tx_type.strip() else None

    return OneClawSignTransactionResult(
        signed_tx=signed_tx.strip(),
        tx_hash=tx_hash_out,
        from_address=from_out,
        tx_type=tx_type_out,
    )


def _extract_secret_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("value")
    if isinstance(value, str):
        return value

    secret = payload.get("secret")
    if isinstance(secret, dict):
        nested_value = secret.get("value")
        if isinstance(nested_value, str):
            return nested_value

    return None
