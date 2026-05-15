# One-time 1Claw setup (hosted / cloud)

Use this checklist when onboarding a new environment (local, staging, or Railway). Exact console labels may change; follow the current [1Claw docs](https://docs.1claw.xyz) if steps diverge.

Platform API details in this repo (template `spec`, auth modes, limitations) are summarized in **[platform-api.md](../../platform-api.md)** — mirror that shape when calling `POST /v1/platform/apps/{app_id}/templates`.

## Requirements & limitations

- **Platform APIs need a Pro+ 1Claw plan** — see upstream billing docs.
- **Template `spec`** has three optional top-level sections: **`vault`**, **`agents[]`**, **`policies[]`**. Signing keys **cannot** be declared in templates; after each user bootstrap, provision keys with `POST /v1/agents/{agent_id}/signing-keys`.
- **`intents` in templates** use the nested flag `"intents": { "enabled": true }`. This differs from registering an agent directly, where `intents_api_enabled` appears flat — the bootstrap stack maps between them (see caution in `platform-api.md`).
- **`auth_mode: silent`** still returns a **`claim_url`** for claim / dashboard access — plan for bot-first UX accordingly.
- **Delegated token exchange** (`POST /v1/auth/delegated-token`) is described upstream but **not wired** for platform operators yet; do not rely on delegated JWT issuance until 1Claw ships it.
- **`plt_` keys** cannot read user signing keys (`GET /v1/agents/{id}/signing-keys`) across the user org boundary; use future delegated flows or user-held credentials.
- **Hosted grant JWT path:** set `AUREY_HOSTED_USER_GRANT_SECRET_PATH_TEMPLATE` to an operator-vault path pattern (placeholders `{vault_id}`, `{connection_id}`, `{agent_id}`) and **write each user’s grant bearer** to that path after they complete claim (until 1Claw automates this). If unset, Aurey stores only a synthetic locator that will not resolve in `SecretStore`.
- **Claim polling:** background poll calls `GET /v1/platform/connections/{connection_id}`. Canonical OpenAPI at `api.1claw.xyz` documents `POST …/connections/{connectionId}/bootstrap` for `plt_` keys but **not** that GET route — deployments may **404** until 1Claw publishes it; use `POST /v1/cloud/onboarding/claim-events` when you have a trustworthy claim signal.

## 1. Platform (hosted operator)

1. Sign in to the 1Claw console and **register or select the Aurey platform app** (record the app id as `AUREY_PLT_APP_ID` when exposing it to automation).
2. Create an **agent template** whose `spec` follows `platform-api.md` (enable Intents with `intents.enabled`, set policies for vault paths your agent needs) and note `AUREY_PLT_TEMPLATE_ID`.
3. Issue a **platform app API key** for provisioning APIs (store the value only in your secret manager; reference it via `AUREY_PLT_APP_API_KEY` and set `AUREY_PLT_APP_API_KEY_SECRET_SOURCE` if you use a non-default env var name).

## 2. Operator runtime (per deployment)

1. **Create a vault** for this deployment (`AUREY_OCV_VAULT_ID`).
2. **Create a hosted agent** bound to that vault; copy `AUREY_OCV_AGENT_ID` when using the JWT/agent-token exchange path.
3. Create an **operator agent API key** and put the raw value in `AUREY_OCV_AGENT_API_KEY` (or another env var, with `AUREY_OCV_AGENT_API_KEY_SECRET_SOURCE` pointing at its name).
4. Populate vault secret **paths** that match `AUREY_*_SECRET_PATH` entries (Alchemy, signing key, LiFi, Telegram, etc.—see [.env.example](../../.env.example)).

## 3. Wire Aurey

- Copy `.env.example` → `.env` (or set variables in Railway).
- Prefer `https://api.1claw.xyz`; override `AUREY_OCV_ONECLAW_BASE_URL` / `AUREY_PLT_ONECLAW_BASE_URL` only when directed by 1Claw.

Placeholder automation (no API calls):

```bash
./scripts/1claw-console-setup.placeholder.sh
```
