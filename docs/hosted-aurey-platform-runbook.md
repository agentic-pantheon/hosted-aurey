# Aurey — 1Claw Platform runbook

Operators use the [1Claw Platform API](https://docs.1claw.xyz) to register apps, templates, and agent policies before pointing hosted Aurey at those resources via `AUREY_*` settings (see `src/aurey/settings/` and `.env.example`).

## 1. Register a Platform app

1. In the 1Claw console or via Platform API, create an **application** for hosted Aurey.
2. Store the **Platform API key** (prefix `plt_`) only in the environment — for example `AUREY_PLATFORM_API_KEY` (see [`AureySettings.platform_api_key`](../src/aurey/settings/__init__.py)).
3. Optionally set `AUREY_PLATFORM_APP_ID` so logs and future API paths can refer to a stable app id.

Never commit `plt_` keys; use secrets managers or deployment env config only.

When a hosted user’s claim link expires, Aurey calls **`POST /v1/platform/connections/{connection_id}/reissue-claim`** on `/start` (after claim-state poll) for bootstrapped connections still in `awaiting_claim`. Optional env: **`AUREY_PLATFORM_CLAIM_RETURN_TO`** for the JSON `return_to` field.

## 1b. Telegram verified email + SMTP (default)

By default (**`AUREY_HOSTED_REQUIRE_VERIFIED_EMAIL=true`**, see [`.env.example`](../.env.example)) new Telegram users stay in **`awaiting_email`** until they verify a real inbox: Aurey emails a 6-digit code, then **provision** uses that address for **`POST /v1/platform/users/upsert`**. Claim links are mailed from **`AUREY_HOSTED_EMAIL_FROM`** (default **`fabri@agentic-pantheon.com`**) when SMTP is configured (`AUREY_HOSTED_SMTP_*`). Throttle between claim emails: **`AUREY_HOSTED_CLAIM_EMAIL_THROTTLE_SECONDS`**. Onboarding states: `awaiting_email` → `awaiting_email_verification` → `email_verified` → `awaiting_claim` → `ready`. **Telegram agent chat** is allowed once the inbox is verified (`email_verified_at` set); **1Claw claim** (password / credentials on the claim page) can be completed later while the user is in `awaiting_claim`.

Verification and claim messages use branded HTML (purple/gold layout, inline Aurey medal header via `cid:`, footer links to **https://aurey.agentic-pantheon.com** and **@aurey_ai** on X). Asset: `src/aurey/cloud/email_assets/aurey-header.jpg`.

Set **`AUREY_HOSTED_REQUIRE_VERIFIED_EMAIL=false`** only to keep the legacy synthetic `tg_<id>@domain` upsert without inbox verification (tests and old scripts use this).

## 1c. Send to Aurey users by Telegram @handle

Hosted users with a persisted **`wallet_address`** can receive ERC-20/native transfers when the agent resolves their **`telegram_username`** via **`resolve_hosted_recipient_by_handle`** (case-insensitive). If the handle is unknown, the tool may return **`invite_deeplink`** when **`AUREY_TELEGRAM_BOT_USERNAME`** is set (see **`AUREY_HOSTED_SEND_INVITE_TTL_DAYS`**). Recipients who open `t.me/<bot>?start=inv_<token>` must use the **same Telegram @username** the sender targeted; otherwise the invite is not consumed. On success, Aurey records **`hosted_handle_claims`**: that @handle maps to their **`telegram_user_id`** for future sends (even if they change username later). Resolve shows `telegram_user_id` and `recipient_binding_note` when the claim registry is used.

After a successful peer **`tx_execute`**, Aurey may DM the recipient’s **`telegram_user_id`**—only if they have already started a chat with the bot (Telegram does not allow cold DMs).

## 2. Define a provisioning template (sketch)

Author a **template** JSON (exact schema per Platform docs) that describes:

- **Vault** — where runtime secrets (operator / user-scoped) live; align your **deployment** **`AUREY_ONECLAW_VAULT_ID`** with whichever vault the bootstrap key can reach.
- **Agents** — at least one agent with **`intents_api_enabled`** (or the current Platform equivalent) so delegated signing / intents flows work.
- **Signing surfaces (Intents API)** — enable **`message_signing_enabled`** when hosted users need **EIP-191** ``personal_sign`` (off-chain auth). For **EIP-712** structured data, configure **`eip712_domain_allowlist`** and/or **`eip712_default_policy`** per [1Claw Intents](https://docs.1claw.xyz/docs/guides/intents-api): Permit / Permit2-style flows require explicit domain allowlisting. **Sign-only transactions** use **`POST /v1/agents/{agent_id}/transactions/sign`** (BYORPC): same policy/guardrails as Intents submit, **decimal ETH** ``value`` string in the body, returns ``signed_tx`` with **no broadcast**—not a replacement for the normal ``tx_prepare`` → ``tx_execute`` path unless the user explicitly needs a raw signed tx for an external RPC.
- **Policies** — stub deny/allow rules appropriate for a hosted tier; tighten before production.

Record the template id returned by the API or console and set:

`AUREY_PLATFORM_TEMPLATE_ID=<id-from-bootstrap>`

Agents used for Telegram / hosted Aurey turns should generally have **`shroud_enabled: true`** on the provisioning template ([Shroud docs](https://docs.1claw.xyz/docs/guides/shroud)): the Deep Agent’s LLM calls go through `https://shroud.1claw.xyz/v1/chat/completions` with `X-Shroud-Agent-Key` / `X-Shroud-Provider`, so policy, redaction, and optional Vault-backed OpenAI keys apply. Prefer storing the upstream OpenAI key in the **end-user vault** at `providers/openai/api-key`; omit `OPENAI_API_KEY` unless you deliberately send a plaintext override via header. Operators can alternatively set **`AUREY_OPENAI_API_SECRET_PATH`** when the bootstrap key must resolve a **`vault://{vault}/{path}`** reference for **`X-Shroud-Api-Key`**. Standalone deployments also need **`AUREY_ONECLAW_AGENT_ID`** and the operator/agent credential (see `.env.example`). When you edit **`shroud_config`** or Shroud dashboard policy, rotate or re-exchange agent credentials so JWTs carrying `shroud_config` stay fresh (see 1Claw Shroud JWT refresh docs).

Use **`AUREY_LLM_PROXY=direct`** with **`OPENAI_API_KEY`** only for local bypass of Shroud.

## 3. Operator 1Claw API key (`AUREY_ONECLAW_BOOTSTRAP_API_KEY`)

Aurey still boots a **`OneClawHttpClient`** for vault access (fallback when env keys are not set) and for signing helpers. Configure:

- **`AUREY_ONECLAW_VAULT_ID`** — dashboard vault used for path-based reads.
- **`AUREY_ONECLAW_BOOTSTRAP_API_KEY`** — your operator / deployment API key (**not** the Platform `plt_` key).

**Delegated intents:** `POST /v1/auth/delegated-token` uses an **actor token**. Unless you set a dedicated **`AUREY_OPERATOR_AGENT_API_KEY`** (advanced), Aurey sends the **bootstrap key as the actor** so you operate with **one credential** by default (`resolve_delegated_actor_api_key` in [`AureySettings`](../src/aurey/settings/__init__.py)).

## 4. Intents delegation scope

Set `AUREY_ONECLAW_DELEGATED_TOKEN_SCOPE` to the scope string your Platform app expects for hosted delegation (default placeholder in settings: `1claw:intents:delegated`). Adjust to match **docs.1claw.xyz** and your security review.

## 5. Further reading

- Platform API and console: [https://docs.1claw.xyz](https://docs.1claw.xyz)
- Aurey env reference: repository root `.env.example`
- Example Platform `curl` commands (placeholders only): [`1claw-curls.example.txt`](1claw-curls.example.txt). Copy to gitignored `1claw-curls.txt` at the repo root for a local scratch pad with real keys.

## 6. Operator API keys via environment

Hosted deployments usually set plaintext operator keys (preferred over vault paths when both are configured):

- `AUREY_ALCHEMY_API_KEY`
- `AUREY_LIFI_API_KEY` (optional)
- `AUREY_TELEGRAM_BOT_TOKEN`

See [.env.example](../.env.example) and [`api_key_resolution`](../src/aurey/graphs/api_key_resolution.py).

## 7. Hosted intents auth (bootstrap + per-user agent)

For each Telegram user, once provisioning has a **`user_agent_id`**, Aurey obtains a JWT via **`POST /v1/auth/agent-token`**. The `OneClawHttpClient` caches those JWTs behind a **reentrant mutex** (safe under concurrent Telegram workers). The `api_key` in that request is resolved in order:

1. **Operator vault** — `POST .../secrets:resolve` with **`AUREY_ONECLAW_BOOTSTRAP_API_KEY`** as Bearer reads **`{AUREY_HOSTED_AGENT_API_KEY_PATH_PREFIX}/{user_agent_id}/agent_api_key`** (default prefix `hosted/agents`) under **`AUREY_ONECLAW_VAULT_ID`** or **`AUREY_HOSTED_AGENT_API_KEY_VAULT_ID`** when set.
2. **Encrypted Postgres backup** — Fernet ciphertext in **`hosted_platform_users.agent_api_key_encrypted`** when **`AUREY_HOSTED_SECRETS_MASTER_KEY`** is configured (generate key via `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
3. **Legacy plaintext column** — **`hosted_platform_users.agent_api_key`** until rows are migrated off plaintext.
4. **Fallback** — **`AUREY_ONECLAW_BOOTSTRAP_API_KEY`** when none of the above yield an `ocv_`.

On bootstrap, when **`summary.agent_api_key`** is present, Aurey **dual-writes**: **`PUT /v1/vaults/{vault_id}/secrets/{path}`** using the [**Human API**](https://docs.1claw.xyz/docs/human-api/secrets/create) Bearer **`AUREY_ONECLAW_HUMAN_API_TOKEN`** (when set), plus encrypts into **`agent_api_key_encrypted`** when the Fernet master key is set. If neither vault PUT nor encryption is configured, the key stays in **`agent_api_key`** plaintext as before.

**`plt_`** keys are only for Platform routes (upsert, bootstrap), not for `agent-token`.

Set **`AUREY_OPERATOR_AGENT_API_KEY`** only if your 1Claw setup uses a distinct operator credential from the bootstrap API key.

### Plaintext `agent_api_key` column after migration

Once **`agent_api_key_encrypted`** is populated and/or vault PUT succeeded for a row, operators should **`UPDATE hosted_platform_users SET agent_api_key = NULL`** where plaintext is no longer needed, so backups do not retain duplicate material.

## 8. Per-user EVM address + admin wallet sync

Bootstrap responses may include **`summary.signing_keys`** (Ethereum **`address`**). Aurey persists a checksummed **`wallet_address`** on `hosted_platform_users` when present and backfills via **`GET /v1/agents/{user_agent_id}/signing-keys`** during onboarding polling when the field is still empty.

To force a refresh without waiting for Telegram traffic, POST **`/v1/hosted/sync-wallet`** with JSON `{"telegram_user_id": <id>}` and header **`Authorization: Bearer <AUREY_HOSTED_HTTP_ADMIN_TOKEN>`**. Operators generate this token deployment-wide (not per user); leaving it unset disables the endpoint (503). On success the handler updates **`wallet_address`** from 1Claw when the signing-keys payload includes an Ethereum key.

## 9. Telegram portfolio Mini App (second Railway service)

Phase 1 exposes read-only portfolio JSON (via **Zerion** wallet portfolio, fungible positions, and balance chart APIs) and a static Web App when **`AUREY_TELEGRAM_MINIAPP_ENABLED=true`**, **`AUREY_TELEGRAM_MINIAPP_PUBLIC_URL`** points at the HTTP service’s **`/miniapp/`** URL, and `miniapp/dist` is built during deploy.

1. **Duplicate the environment** from the Telegram poller (1Claw bootstrap, vault-id, DB, `AUREY_HOSTED_PLATFORM_ENABLED`, Telegram bot token, **`AUREY_ZERION_API_KEY`**, etc.) onto a **second Railway service** rooted at the same repo.
2. Configure that service’s **Railway config file** to **`railway.http.toml`** (Dashboard → service → Settings, or `RAILWAY_CONFIG_FILE` as applicable). The HTTP service uses **`Dockerfile.http`** (Node builds `miniapp/dist`, then Python/uv runs `run_http.py`) so the build does not depend on Railpack installing `npm`.
3. Assign a **public HTTPS domain** to the HTTP service. Set **`AUREY_TELEGRAM_MINIAPP_PUBLIC_URL=https://<http-host>/miniapp/`** on **both** services: the Telegram worker needs this URL for `setChatMenuButton` and `/portfolio`.
4. In **BotFather**, register the Mini App **domain** to match the HTTP service hostname.
5. Keep the **polling** service on `run_telegram.py` (`railway.toml`); only one process may call `getUpdates` with a given bot token.

When the Mini App is disabled, omit the flag or leave it `false`; the bot skips Web App wiring if the public URL is unset.

**Security defaults (HTTP service):** `initData` max age **4h**; per-user / per-IP rate limits on `POST /v1/miniapp/portfolio`; **120s** server-side Zerion snapshot cache; portfolio reads do **not** trigger signing-keys backfill (use `/v1/hosted/sync-wallet` or Telegram onboarding instead). Tune via `AUREY_TELEGRAM_MINIAPP_*` env vars in `.env.example`.

**Build note:** If you stay on Railpack instead of `Dockerfile.http`, set service variable **`RAILPACK_PACKAGES=node@22`** so `npm` exists during the custom build command; otherwise you will see `npm: not found`.
