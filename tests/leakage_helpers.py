"""Shared fake secret markers and assertions for security regression tests."""

from __future__ import annotations

import json

# Values injected into FakeSecretStore or bootstrap env in tests; must never appear
# in graph outputs, tool payloads, or HTTP API bodies.
FAKE_RPC_URL_FRAGMENT = "INJECTED_RPC_URL_SECRET_FRAGMENT"
FAKE_ALCHEMY_API_KEY = "INJECTED_ALCHEMY_KEY_AAA"
FAKE_LIFI_API_KEY = "INJECTED_LIFI_KEY_BBB"
# 32-byte secp256k1-style hex (with 0x) used as fake signing material in tests.
FAKE_SIGNING_KEY_MATERIAL_HEX = "0x" + "ff" * 32
FAKE_BOOTSTRAP_API_KEY = "FAKE_ONECLAW_BOOTSTRAP_KEY_ZZZ"
FAKE_TELEGRAM_BOT_TOKEN = "123456:FAKE_TELEGRAM_TOKEN_SHOULD_NOT_LEAK"
FAKE_ERROR_BODY_SECRET = "SECRET_FRAGMENT_SHOULD_NOT_LEAK"

SENSITIVE_MARKERS: tuple[str, ...] = (
    FAKE_RPC_URL_FRAGMENT,
    FAKE_ALCHEMY_API_KEY,
    FAKE_LIFI_API_KEY,
    FAKE_SIGNING_KEY_MATERIAL_HEX,
    FAKE_BOOTSTRAP_API_KEY,
    FAKE_TELEGRAM_BOT_TOKEN,
    FAKE_ERROR_BODY_SECRET,
)


def assert_no_sensitive_leakage(payload: object) -> None:
    """Serialize *payload* and assert no test secret marker appears anywhere."""

    blob = json.dumps(payload, default=str, sort_keys=True)
    lowered = blob.lower()
    for fragment in SENSITIVE_MARKERS:
        assert fragment not in blob
        # RPC URLs and keys sometimes surface lowercased in encodings
        if fragment != FAKE_SIGNING_KEY_MATERIAL_HEX:
            assert fragment.lower() not in lowered
