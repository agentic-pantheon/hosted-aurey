# Hosted Aurey — 1Claw Platform runbook

Operators use the [1Claw Platform API](https://docs.1claw.xyz) to register apps, templates, and agent policies before pointing hosted Aurey at those resources via `AUREY_*` settings (see `src/aurey/settings/` and `.env.example`).

## 1. Register a Platform app

1. In the 1Claw console or via Platform API, create an **application** for hosted Aurey.
2. Store the **Platform API key** (prefix `plt_`) only in the environment — for example `AUREY_PLATFORM_API_KEY` (see [`AureySettings.platform_api_key`](../src/aurey/settings/__init__.py)).
3. Optionally set `AUREY_PLATFORM_APP_ID` so logs and future API paths can refer to a stable app id.

Never commit `plt_` keys; use secrets managers or deployment env config only.

## 2. Define a provisioning template (sketch)

Author a **template** JSON (exact schema per Platform docs) that describes:

- **Vault** — where runtime secrets (operator / user-scoped) live; align `AUREY_OPERATOR_VAULT_ID` (and production vault ids) with this template.
- **Agents** — at least one agent with **`intents_api_enabled`** (or the current Platform equivalent) so delegated signing / intents flows work.
- **Policies** — stub deny/allow rules appropriate for a hosted tier; tighten before production.

Record the template id returned by the API or console and set:

`AUREY_PLATFORM_TEMPLATE_ID=<id-from-bootstrap>`

## 3. Operator agent and `ocv_` keys

For the control plane / operator runtime:

1. Create or note the **operator vault** and set `AUREY_OPERATOR_VAULT_ID` when ready (may stay empty until provisioned).
2. Set `AUREY_OPERATOR_AGENT_ID` if the Platform assigns a dedicated operator agent.
3. Put the **operator agent API key** (`ocv_` or bootstrap-style key per your setup) in the env var named by `AUREY_OPERATOR_AGENT_API_KEY_SECRET_SOURCE` (default: `AUREY_OPERATOR_AGENT_API_KEY`).

Application code resolves the key via [`AureySettings.resolve_operator_agent_api_key()`](../src/aurey/settings/__init__.py), same indirection pattern as the 1Claw bootstrap key.

## 4. Intents delegation scope

Set `AUREY_ONECLAW_DELEGATED_TOKEN_SCOPE` to the scope string your Platform app expects for hosted delegation (default placeholder in settings: `1claw:intents:delegated`). Adjust to match **docs.1claw.xyz** and your security review.

## 5. User provisioning flow (reference)

End-user onboarding is **Phase B** in most deployments: OIDC / token exchange, mapping users to vaults and agents created from the template above. Placeholder settings:

- `AUREY_HOSTED_OIDC_ISSUER_URL`
- `AUREY_HOSTED_OIDC_AUDIENCE`
- `AUREY_HOSTED_OIDC_SUBJECT_TOKEN_TTL_SECONDS`

Treat these as documentation-oriented defaults until the hosted auth path is implemented.

## 6. Further reading

- Platform API and console: [https://docs.1claw.xyz](https://docs.1claw.xyz)
- Aurey env reference: repository root `.env.example`

## 7. Operator API keys via environment

Hosted deployments usually set plaintext operator keys (preferred over vault paths when both are configured):

- `AUREY_ALCHEMY_API_KEY`
- `AUREY_LIFI_API_KEY` (optional)
- `AUREY_TELEGRAM_BOT_TOKEN`

See [.env.example](../.env.example) and [`api_key_resolution`](../src/aurey/graphs/api_key_resolution.py).

## 8. Delegation grant (staging)

Delegated signing depends on storing a Platform **user grant** subject token (`delegation_subject_token` on `hosted_platform_users`). Telegram `/grant` and `/delegation_grant` persist this when `AUREY_HOSTED_ADMIN_TELEGRAM_USER_IDS` lists your numeric user id.
Treat plaintext storage as **staging only**; replace with KMS or vault-managed references before production.
