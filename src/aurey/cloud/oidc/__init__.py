"""Mint OIDC-style subject tokens and publish JWKS for silent platform provisioning."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _compute_jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint (sha256, base64url)."""

    keys = sorted(jwk.keys())
    obj = {k: jwk[k] for k in keys}
    payload = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()
    return _b64url(hashlib.sha256(payload).digest())


@dataclass(frozen=True, slots=True)
class OidcSubjectTokenSigner:
    """Sign short-lived JWTs used as ``subject_token`` for ``users/upsert``."""

    issuer: str
    private_key_pem: str
    key_id: str
    default_audience: str

    @classmethod
    def from_pem(
        cls,
        private_key_pem: str,
        *,
        issuer: str,
        default_audience: str,
    ) -> OidcSubjectTokenSigner:
        iss = (issuer or "").strip().rstrip("/")
        if not iss:
            raise ValueError("oidc issuer must not be empty.")
        aud = (default_audience or "").strip()
        if not aud:
            raise ValueError("subject token audience must not be empty.")

        pem = (private_key_pem or "").strip()
        if not pem:
            raise ValueError("private key PEM must not be empty.")

        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("Only RSA private keys (RS256) are supported for subject tokens.")

        pub = key.public_key()
        pub_num = pub.public_numbers()
        n_bytes = pub_num.n.to_bytes((pub_num.n.bit_length() + 7) // 8, byteorder="big")
        e_int = pub_num.e
        e_bytes = e_int.to_bytes((e_int.bit_length() + 7) // 8, byteorder="big")

        jwk = {
            "kty": "RSA",
            "n": _b64url(n_bytes),
            "e": _b64url(e_bytes),
            "alg": "RS256",
            "use": "sig",
        }
        kid = _compute_jwk_thumbprint(jwk)
        return cls(issuer=iss, private_key_pem=pem, key_id=kid, default_audience=aud)

    def jwks_document(self) -> dict[str, Any]:
        key = serialization.load_pem_private_key(
            self.private_key_pem.encode(), password=None
        )
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("RSA key required")
        pub = key.public_key()
        pub_num = pub.public_numbers()
        n_bytes = pub_num.n.to_bytes((pub_num.n.bit_length() + 7) // 8, byteorder="big")
        e_int = pub_num.e
        e_bytes = e_int.to_bytes((e_int.bit_length() + 7) // 8, byteorder="big")

        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.key_id,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64url(n_bytes),
                    "e": _b64url(e_bytes),
                }
            ]
        }

    def openid_configuration(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "jwks_uri": f"{self.issuer}/.well-known/jwks.json",
        }

    def mint_subject_token(
        self,
        *,
        subject: str,
        audience: str | None = None,
        expires_in_seconds: int,
    ) -> str:
        sub = (subject or "").strip()
        if not sub:
            raise ValueError("subject must not be empty.")
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive.")

        aud = (audience or "").strip() or self.default_audience
        if not aud:
            raise ValueError("audience must not be empty.")

        now = int(time.time())
        return jwt.encode(
            {
                "sub": sub,
                "aud": aud,
                "iss": self.issuer,
                "iat": now,
                "exp": now + int(expires_in_seconds),
            },
            self.private_key_pem,
            algorithm="RS256",
            headers={"kid": self.key_id},
        )


__all__ = ["OidcSubjectTokenSigner"]
