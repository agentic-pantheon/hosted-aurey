# One-time 1Claw setup (hosted / cloud)

Use this checklist when onboarding a new environment (local, staging, or Railway). Exact console labels may change; follow the current [1Claw docs](https://docs.1claw.xyz) if steps diverge.

## 1. Platform (hosted operator)

1. Sign in to the 1Claw console and **register or select the Aurey platform app** (record the app id as `AUREY_PLT_APP_ID` when exposing it to automation).
2. Create or choose an **agent template** for Aurey workloads and note `AUREY_PLT_TEMPLATE_ID`.
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
