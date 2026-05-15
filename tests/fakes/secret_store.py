from __future__ import annotations

from aurey.custody import FakeOneClawClient, OneClawSecretStore

TEST_BOOTSTRAP_KEY = "oneclaw-api-key-test-only"
TEST_RPC_URL = "https://rpc.example.invalid/secret-rpc-token"
TEST_VAULT_ID = "vault-test-only"


def fake_oneclaw_secret_store(
    secrets: dict[str, str] | None = None,
    *,
    agent_id: str | None = "agent-test-only",
) -> OneClawSecretStore:
    defaults = {
        "aurey/rpc/ethereum": TEST_RPC_URL,
        "aurey/rpc/base": "https://base.example.invalid/base-secret-token",
        "aurey/apis/alchemy": "alchemy-api-key-test-only",
        "aurey/apis/lifi": "lifi-api-key-test-only",
        "aurey/wallets/primary/signing_key": "0x" + "11" * 32,
    }
    if secrets is not None:
        defaults.update(secrets)
    return OneClawSecretStore(
        client=FakeOneClawClient(defaults),
        vault_id=TEST_VAULT_ID,
        agent_id=agent_id,
    )
