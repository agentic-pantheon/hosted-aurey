# Hosted Aurey — 1Claw Platform runbook

Operators use the [1Claw Platform API](https://docs.1claw.xyz) to register apps, templates, and agent policies before pointing hosted Aurey at those resources via `AUREY_*` settings (see `src/aurey/settings/` and `.env.example`).

## 1. Register a Platform app

1. In the 1Claw console or via Platform API, create an **application** for hosted Aurey.
2. Store the **Platform API key** (prefix `plt_`) only in the environment — for example `AUREY_PLATFORM_API_KEY` (see [`AureySettings.platform_api_key`](../src/aurey/settings/__init__.py)).
3. Optionally set `AUREY_PLATFORM_APP_ID` so logs and future API paths can refer to a stable app id.

Never commit `plt_` keys; use secrets managers or deployment env config only.

## 2. Define a provisioning template (sketch)

Author a **template** JSON (exact schema per Platform docs) that describes:

- **Vault** — where runtime secrets (operator / user-scoped) live; align your **deployment** **`AUREY_ONECLAW_VAULT_ID`** with whichever vault the bootstrap key can reach.
- **Agents** — at least one agent with **`intents_api_enabled`** (or the current Platform equivalent) so delegated signing / intents flows work.
- **Signing surfaces (Intents API)** — enable **`message_signing_enabled`** when hosted users need **EIP-191** ``personal_sign`` (off-chain auth). For **EIP-712** structured data, configure **`eip712_domain_allowlist`** and/or **`eip712_default_policy`** per [1Claw Intents](https://docs.1claw.xyz/docs/guides/intents-api): Permit / Permit2-style flows require explicit domain allowlisting. **Sign-only transactions** use **`POST /v1/agents/{agent_id}/transactions/sign`** (BYORPC): same policy/guardrails as Intents submit, **decimal ETH** ``value`` string in the body, returns ``signed_tx`` with **no broadcast**—not a replacement for the normal ``tx_prepare`` → ``tx_execute`` path unless the user explicitly needs a raw signed tx for an external RPC.
- **Policies** — stub deny/allow rules appropriate for a hosted tier; tighten before production.

Record the template id returned by the API or console and set:

`AUREY_PLATFORM_TEMPLATE_ID=<id-from-bootstrap>`

## 3. Operator 1Claw API key (`AUREY_ONECLAW_BOOTSTRAP_API_KEY`)

Hosted Aurey still boots a **`OneClawHttpClient`** for vault access (fallback when env keys are not set) and for signing helpers. Configure:

- **`AUREY_ONECLAW_VAULT_ID`** — dashboard vault used for path-based reads.
- **`AUREY_ONECLAW_BOOTSTRAP_API_KEY`** — your operator / deployment API key (**not** the Platform `plt_` key).

**Delegated intents:** `POST /v1/auth/delegated-token` uses an **actor token**. Unless you set a dedicated **`AUREY_OPERATOR_AGENT_API_KEY`** (advanced), Aurey sends the **bootstrap key as the actor** so you operate with **one credential** by default (`resolve_delegated_actor_api_key` in [`AureySettings`](../src/aurey/settings/__init__.py)).

## 4. Intents delegation scope

Set `AUREY_ONECLAW_DELEGATED_TOKEN_SCOPE` to the scope string your Platform app expects for hosted delegation (default placeholder in settings: `1claw:intents:delegated`). Adjust to match **docs.1claw.xyz** and your security review.

## 5. Further reading

- Platform API and console: [https://docs.1claw.xyz](https://docs.1claw.xyz)
- Aurey env reference: repository root `.env.example`

## 6. Operator API keys via environment

Hosted deployments usually set plaintext operator keys (preferred over vault paths when both are configured):

- `AUREY_ALCHEMY_API_KEY`
- `AUREY_LIFI_API_KEY` (optional)
- `AUREY_TELEGRAM_BOT_TOKEN`

See [.env.example](../.env.example) and [`api_key_resolution`](../src/aurey/graphs/api_key_resolution.py).

## 7. Hosted intents auth (bootstrap + per-user agent)

**Current behavior:** For each Telegram user, once provisioning has a **`user_agent_id`**, Aurey obtains a JWT via **`POST /v1/auth/agent-token`**. The `api_key` in that request is the per-user **`ocv_`** value from **`summary.agent_api_key`** in the Platform bootstrap JSON when Aurey has persisted it in **`hosted_platform_users.agent_api_key`**; until that field is populated, **`AUREY_ONECLAW_BOOTSTRAP_API_KEY`** is used as the fallback. The returned Bearer is then sent on intents (`/sign`, `transactions/sign`, etc.). **`plt_`** keys are only for Platform routes (upsert, bootstrap), not for `agent-token`. Existing users provisioned before this column existed may need another bootstrap (or a manual DB update with the one-time `ocv_` from 1Claw) before `agent_api_key` is filled.

**Legacy / optional:** The `hosted_platform_users.delegation_subject_token` column and Telegram **`/grant`** / **`/delegation_grant`** (when **`AUREY_HOSTED_ADMIN_TELEGRAM_USER_IDS`** is set) may still persist earlier **staging** subject tokens; they are **not** required for `oneclaw_intents` prepare/execute/tools when using the operator bootstrap key with template agents.

Set **`AUREY_OPERATOR_AGENT_API_KEY`** only if your 1Claw setup uses a distinct operator credential from the bootstrap API key.

## 8. Per-user EVM address + admin wallet sync

Bootstrap responses may include **`summary.signing_keys`** (Ethereum **`address`**). Aurey persists a checksummed **`wallet_address`** on `hosted_platform_users` when present and backfills via **`GET /v1/agents/{user_agent_id}/signing-keys`** during onboarding polling when the field is still empty.

To force a refresh without waiting for Telegram traffic, POST **`/v1/hosted/sync-wallet`** with JSON `{"telegram_user_id": <id>}` and header **`Authorization: Bearer <AUREY_HOSTED_HTTP_ADMIN_TOKEN>`**. Operators generate this token deployment-wide (not per user); leaving it unset disables the endpoint (503). On success the handler updates **`wallet_address`** from 1Claw when the signing-keys payload includes an Ethereum key.
