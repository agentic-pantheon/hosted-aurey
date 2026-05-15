"""OIDC subject token + JWKS helpers (Phase B)."""

from __future__ import annotations

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aurey.cloud.oidc import OidcSubjectTokenSigner


def _rsa_pem_2048() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


def test_mint_subject_token_round_trip() -> None:
    pem = _rsa_pem_2048()
    signer = OidcSubjectTokenSigner.from_pem(
        pem,
        issuer="https://issuer.example",
        default_audience="app_123",
    )
    token = signer.mint_subject_token(
        subject="telegram:99",
        audience=None,
        expires_in_seconds=120,
    )
    priv = serialization.load_pem_private_key(pem.encode(), password=None)
    payload = jwt.decode(
        token,
        priv.public_key(),
        algorithms=["RS256"],
        audience="app_123",
        issuer="https://issuer.example",
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
    assert payload["sub"] == "telegram:99"
    assert payload["iss"] == "https://issuer.example"
    assert payload["aud"] == "app_123"


def test_jwks_contains_matching_kid() -> None:
    pem = _rsa_pem_2048()
    signer = OidcSubjectTokenSigner.from_pem(
        pem,
        issuer="https://issuer.example",
        default_audience="app_123",
    )
    token = signer.mint_subject_token(subject="telegram:1", expires_in_seconds=60)
    headers = jwt.get_unverified_header(token)
    doc = signer.jwks_document()
    kids = {k.get("kid") for k in doc.get("keys", [])}
    assert headers["kid"] in kids
