# Aurey-as-a-service architecture design

I've cross-referenced the 1Claw [Platform API]([https://docs.1claw.xyz/docs/guides/platform-api](https://docs.1claw.xyz/docs/guides/platform-api)) and [Intents API]([https://docs.1claw.xyz/docs/guides/intents-api](https://docs.1claw.xyz/docs/guides/intents-api)) docs against the current Aurey codebase `src/aurey/...`) and your hard constraints. Below is the design map you asked for — with sections for what the docs **confirm**, what you should **validate with 1Claw before coding**, and a concrete proposal for the **DB schema, runtime flow, settings, and repo layout**.

---

## 1. What the docs validate (load-bearing quotes)

These are the lines you can build on without further confirmation.

**Platform-locked custody boundary** ([Platform API → Custody Guarantee]([https://docs.1claw.xyz/docs/guides/platform-api](https://docs.1claw.xyz/docs/guides/platform-api))):

> 1. Authorization: `plt_` keys cannot read secrets in locked vaults. 2. Token isolation: `plt_` keys cannot mint agent JWTs for user agents."

This is the source of your "we need a delegated trust path" requirement — confirmed.

**Delegated token shape** ([Platform API → Delegated access]([https://docs.1claw.xyz/docs/guides/platform-api](https://docs.1claw.xyz/docs/guides/platform-api))):

```text

POST /v1/auth/delegated-token

  subject_token: user_grant_jwt

  actor_token:   ocv_YOUR_KEY

  scope:         "secrets:read paths:api-keys/*"

→ Short-lived JWT (audit: actor=plt_ on behalf of usr_...)

```

Important nuance: the **actor_token is an `ocv`_ agent key, not your `plt`_ key.** So your backend needs **two** 1Claw identities (see §5).

**Grants are user-initiated and revocable**:

> "The user must explicitly grant this access — either during the claim flow or later in their 1Claw dashboard under Settings > Connected Apps."

**Signing keys are human-provisioned** ([Intents API → Multi-chain signing keys]([https://docs.1claw.xyz/docs/guides/intents-api](https://docs.1claw.xyz/docs/guides/intents-api))):

> "Only human users can provision and rotate keys — agents get 403."

→ Your `plt_` backend cannot call `POST /v1/agents/:id/signing-keys` for a user agent. The user has to either do it during claim or via dashboard.

**Per-agent JWT minting** ([Intents API → Quickstart]([https://docs.1claw.xyz/docs/guides/intents-api](https://docs.1claw.xyz/docs/guides/intents-api))):

> "Get an agent JWT: `POST /v1/auth/agent-token` with `agent_id` and `api_key`."

You already use this in `OneClawHttpClient._fetch_access_token` `src/aurey/custody/secret_store.py` lines 272-344). But for *user* agents this path is blocked — the user's `api_key` is something only they hold after claim. So at runtime you can't call `/v1/auth/agent-token` for the user agent; you must use the delegated-token endpoint instead.

**Template spec shape** ([Platform API → Quick Start step 2]([https://docs.1claw.xyz/docs/guides/platform-api](https://docs.1claw.xyz/docs/guides/platform-api))):

```json

{

  "name": "default-customer",

  "spec": {

    "vault": { "name": "main" },

    "agents": [{ "name": "primary", "shroud_enabled": true }],

    "policies": [{

      "principal_ref": "agents.primary",

      "vault_ref": "vault",

      "paths": ["api-keys/*"],

      "permissions": ["read", "write"]

    }]

  }

}

```

**Silent OIDC provisioning** ([Platform API → Choose your onboarding flow]([https://docs.1claw.xyz/docs/guides/platform-api](https://docs.1claw.xyz/docs/guides/platform-api))):

> "Set `auth_mode: 'silent'` on your app. If you have your own IdP, configure `oidc_jwks_url` and `oidc_issuer` to skip email-based provisioning entirely — just pass a signed JWT as `subject_token`."

→ This is your hook for "Telegram-only users." Aurey-cloud becomes its own minimal IdP that mints a JWT with `sub: telegram:{chat_id}` (or a UUID you assign), publishes a JWKS, and `users.upsert` accepts the subject_token. No email needed.

---

## 2. Open questions to confirm with 1Claw

Before you build, you'll want sign-off on these — the public docs don't pin them down:

1. **Template can declare signing keys?** Hosted Aurey aligns with **`spec.signing_keys`** (see `platform-api.md` and bootstrap `summary.signing_key_chains`). Still validate with 1Claw **which chains** your template id supports and whether any SKUs omit template-driven provisioning.
2. **Delegated-token scopes for Intents.** The doc example uses `scope: "secrets:read paths:api-keys/*"`. Ask: *What's the scope string for* `POST /v1/agents/{id}/transactions` *(and* `/sign`*)?* Candidates: `intents:submit agents:{user_agent_id}`, or just `agents:{user_agent_id}:transactions`. Without this the JWT may come back but 403 on the actual call.
3. **Grant lifetime + refresh.** Is `user_grant_jwt` a long-lived bearer the user signs once during claim and the operator stores? Or do you exchange it via `delegated-token` for short-lived JWTs each time (your code's caching pattern works fine for the latter)? Almost certainly the latter, but confirm: *what is the typical TTL and re-grant lifecycle?*
4. **Silent mode + claim_url.** With `auth_mode: silent`, does `bootstrap` still always return a `claim_url`, and is visiting it *required* for the user to hold a usable client share / grant any access? For a Telegram-first product where the user never opens a desktop browser, this is the UX cliff.
5. *`plt`_ audit visibility.** The docs say `plt`_ can "see metadata." Ask: *can* `plt_` *list a user's signing keys* `GET /v1/agents/{id}/signing-keys`*) without delegation?* This decides whether your DB-tracked `signing_keys_provisioned` flag can be verified from the operator side or must be reported by the user-side claim flow.
6. **`intents` in platform templates** — Use **`"intents": { "enabled": true }`** nested under each `agents[]` entry (not flat `intents_api_enabled` on the template). Confirmed in bootstrap engine / `platform-api.md`.

I'd recommend opening a single thread with 1Claw for any remaining product gaps (delegated-token availability, programmatic claim) before you rely on them in production UX.

---

## 3. End-to-end API sequence

### Phase A — one-time operator setup (manual, run once per environment)

```text

Authorization: Bearer <your-1claw-user-token>

1. POST /v1/platform/apps

   { name: "Aurey", slug: "aurey-cloud",

     billing_model: "platform_pays",

     auth_mode: "silent",

     oidc_issuer: "[https://aurey.cloud](https://aurey.cloud)",

     oidc_jwks_url: "[https://aurey.cloud/.well-known/jwks.json](https://aurey.cloud/.well-known/jwks.json)" }

   → returns app_id, api_key (plt_…)   ← store as AUREY_PLT_API_KEY

2. POST /v1/platform/apps/{app_id}/templates

   { name: "telegram-default",

     spec: {

       vault:   { name: "wallet", description: "Per-user secrets" },

       agents:  [{ name: "primary",

                   description: "Telegram user agent",

                   intents: { enabled: true },

                   shroud_enabled: true }],

       policies: [

         { principal_ref: "agents.primary",

           vault_ref:    "vault",

           paths:        ["api-keys/*", "keys/*"],

           permissions:  ["read", "write"] }

       ]

     } }

   → returns template_id

   Optionally include **`signing_keys: [{ "chain": "ethereum" }, …]`** in the template `spec` so chains
   are provisioned as part of bootstrap / claim where the Platform API supports it; otherwise users
   can add chains later via `POST /v1/agents/{agent_id}/signing-keys`.
   Hosted Aurey persists `provisioned_signing_key_chains` from normalized `summary.signing_key_chains`
   on `platform_users` when the bootstrap response includes them.
3. Provision the operator's *own* agent (separate from the platform app) so you have

   an ocv_ key for delegated-token actor_token.

   POST /v1/agents { name: "aurey-cloud-operator", intents_api_enabled: false }

   → returns agent_id + api_key (ocv_…)  ← store as AUREY_OPERATOR_AGENT_API_KEY

                                            + AUREY_OPERATOR_AGENT_ID

```

### Phase B — per-Telegram-user onboarding (runs from your `/start` handler)

```text

Authorization: Bearer plt_…

User sends /start in @aurey_bot, chat_id = 42

4. (your IdP) mint OIDC JWT

   { iss: "[https://aurey.cloud](https://aurey.cloud)",

     sub: "telegram:42",

     aud: app_id,

     exp: now+5min }

   signed with RS256 key whose pub is in /.well-known/jwks.json

5. POST /v1/platform/users/upsert

   { subject_token: "<your_OIDC_jwt>",

     display_name:  "Telegram user 42" }

   → { id: usr_…, connection_id: conn_… }

   ↳ persist in `platform_users` (see §6)

6. POST /v1/platform/connections/{conn_…}/bootstrap

   { template_id }

   → { claim_url: "[https://1claw.xyz/claim/abc…](https://1claw.xyz/claim/abc…)",

       claim_token: "ctk_…",

       vault_id: "vlt_…",            ← user-owned, platform_locked

       agent_id: "agt_…",            ← user-owned, platform_locked

       expires_at: "…" }

   ↳ persist; mark onboarding_state = "awaiting_claim"

7. Bot replies with an inline keyboard:

   "Tap to finish setup" → opens claim_url in Telegram in-app browser

```

### Phase C — claim (user-driven, on `1claw.xyz`)

This is the part you don't fully control:

```text

8. User opens claim_url. Because auth_mode=silent + OIDC, 1Claw verifies

   subject_token via your JWKS — no Google/email login.

9. 1Claw claim UI walks the user through:

   - acknowledging vault/agent ownership

   - MPC client share generation (if CMEK + MPC required by template)  ← VALIDATE Q4

   - signing key provisioning per template (or one-tap)                ← VALIDATE Q1

   - granting Aurey-cloud `intents:submit` scope                       ← VALIDATE Q2

10. 1Claw posts a webhook (or polls) back to your backend:

    POST [https://aurey.cloud/oneclaw/webhooks/claim](https://aurey.cloud/oneclaw/webhooks/claim)

    { connection_id: conn_…,

      status: "claimed",

      grants: [{ grant_id: "grn_…",

                 grant_token: "<long-lived user-issued JWT>",

                 scopes: ["intents:submit", "intents:sign"],

                 expires_at: … }],

      signing_keys: [{ chain: "ethereum", address: "0x…" }, …] }

    ↳ update onboarding_state = "ready", store user_grant_token encrypted

```

If 1Claw doesn't push a webhook (verify in their docs/console), poll `GET /v1/platform/apps/{app_id}/users` after the bot detects a claim deep-link return.

### Phase D — runtime turn (per Telegram message)

```text

11. Telegram → bot.on_message(chat_id=42, text="swap 100 USDC for ETH on Base")

12. backend looks up platform_users row by telegram_chat_id=42

    → connection_id, user_agent_id, user_vault_id, user_grant_token

13. backend ensures it has a fresh short-lived agent JWT:

    POST /v1/auth/delegated-token

      Authorization: Bearer ocv_OPERATOR_AGENT_API_KEY     # ← NOT plt_

      { subject_token: "<user_grant_token>",

        actor_token:   "ocv_OPERATOR_AGENT_API_KEY",

        scope:         "intents:submit agents:agt_USER" }  # ← VALIDATE Q2

    → { access_token: "<short-lived jwt>", expires_in: 600 }

    ↳ cache by (user_agent_id, scope) until expires_in - skew

14. Run the deep agent graph with a per-user AureyRuntime where:

    - secret_store knows only the operator-wide vault paths (Alchemy, LiFi)

    - oneclaw_evm_signer is configured for user_agent_id and uses the

      delegated JWT (not /v1/auth/agent-token)

    - signing_key_path is omitted → 1Claw default-resolves

      `agents/{user_agent_id}/chains/{chain}/private_key`

15. When agent calls tx_execute:

    POST /v1/agents/{user_agent_id}/sign  (or /transactions)

      Authorization: Bearer <delegated jwt from step 13>

      { intent_type: "transaction", chain: "base", …,

        signing_key_path: null }     ← let 1Claw resolve user's per-chain key

    → returns { signed_tx, tx_hash, … }

16. backend broadcasts via operator-shared Alchemy RPC (URL templated with

    AUREY_ALCHEMY_API_KEY read from operator vault — never the user vault)

```

---

## 4. Runtime delegation lifecycle (concrete changes to your code)

Your current model is **process-scoped** — `AureyServiceState` is built once in `bootstrap.py` and holds a single `OneClawHttpClient` with a single agent token cache:

```40:55:src/aurey/service/[bootstrap.py](http://bootstrap.py)

    client = OneClawHttpClient(

        base_url=s.oneclaw_base_url.strip(),

        api_key=api_key,

        agent_token_expiry_skew_seconds=s.oneclaw_agent_token_expiry_skew_seconds,

    )

    store = OneClawSecretStore(client=client, vault_id=vault_id, agent_id=s.oneclaw_agent_id)

    runtime = AureyRuntime(

        settings=s,

        secret_store=store,

        evm_rpc_factory=make_evm_rpc_factory(),

        http=UrllibHttpJsonClient(),

        tx_pipeline=Web3TxPipeline(settings=s, secret_store=store),

        oneclaw_evm_signer=client,

    )

```

For hosted-mode you need to **separate operator-wide singletons from per-user, per-request runtime**. The minimal shape:

```python

# new: src/aurey/cloud/[principal.py](http://principal.py)

@dataclass(frozen=True)

class UserPrincipal:

    user_id: str                    # internal uuid

    telegram_chat_id: int | None

    oneclaw_connection_id: str

    oneclaw_user_vault_id: str      # user-owned, locked

    oneclaw_user_agent_id: str      # user-owned, locked

    user_grant_token: str           # subject_token for delegated-token

@dataclass(frozen=True)

class OperatorWideSecrets:

    alchemy_api_key: SecretValue    # from operator vault

    lifi_api_key:    SecretValue

    telegram_token:  SecretValue

```

- `OperatorWideSecrets` is resolved **once at startup** through the existing `OneClawSecretStore(vault_id=OPERATOR_VAULT, agent_id=OPERATOR_AGENT_ID)`. These don't change per turn.
- `UserPrincipal` is loaded **per Telegram message** from Postgres.
- A new `DelegatedSigner` wraps `OneClawHttpClient` but caches by `(user_agent_id, scope)` and calls `/v1/auth/delegated-token` instead of `/v1/auth/agent-token`. Extend the existing JWT cache fields in `OneClawHttpClient` from a single `_access_token_`* triple to a dict keyed by `(agent_id, mode)`.
- `Web3TxPipeline` already gets a `SecretStore`; in cloud mode it gets the *operator* store (Alchemy etc.) but the EVM signer it uses is bound to the user agent.
- The graph keeps being compiled once per `model` spec at the process level — graphs are stateless re: per-user keys. Per-user state flows through `config["configurable"]`, which you already use for `aurey_context` and `thread_id`.

Cache lifetimes:

- Operator vault secrets — process-lifetime; refresh on 401 only (already implemented).
- Delegated short-lived JWT — by `expires_in - skew`, exactly like the current pattern `OneClawHttpClient._access_token_expires_at`).
- `user_grant_token` — DB row; refreshed only via re-claim or a 1Claw "rotate grant" endpoint if one exists (open question; not in public docs).

---

## 5. Settings split — operator-wide vs per-user

Your `AureySettings` (in `src/aurey/settings/__init__.py`) collapses everything into one bucket. For hosted mode you want a clean three-way split. I'd refactor like this:

| Env var (current → proposed) | Scope | Lives in |

|---|---|---|

| `AUREY_ONECLAW_VAULT_ID` → `AUREY_OPERATOR_VAULT_ID` | Operator | Settings |

| `AUREY_ONECLAW_BOOTSTRAP_API_KEY` → `AUREY_OPERATOR_AGENT_API_KEY` `ocv_…`) | Operator | Settings |

| `AUREY_ONECLAW_AGENT_ID` → `AUREY_OPERATOR_AGENT_ID` | Operator | Settings |

| **(new)** `AUREY_PLT_API_KEY` `plt_…`) | Platform | Settings |

| **(new)** `AUREY_PLT_APP_ID` | Platform | Settings |

| **(new)** `AUREY_PLT_TEMPLATE_ID` | Platform | Settings |

| **(new)** `AUREY_OIDC_SIGNING_KEY_SECRET_PATH` | Operator vault path | Settings (resolved via operator store) |

| `AUREY_ALCHEMY_API_SECRET_PATH` | Operator vault path | Settings ✓ stays |

| `AUREY_LIFI_API_SECRET_PATH` | Operator vault path | Settings ✓ stays |

| `AUREY_WALLET_SIGNING_KEY_SECRET_PATH` | **deprecated** in cloud mode | — |

| `AUREY_EVM_SIGNING_MODE` | hard-wire to `oneclaw_intents` in cloud mode | Settings |

| `AUREY_TELEGRAM_BOT_TOKEN_SECRET_PATH` | Operator vault path ✓ stays | Settings |

| `AUREY_DEEP_AGENT_WALLET_ADDRESS` | **per-user**, drop from settings | DB column `wallet_address` (set after claim from signing-keys list) |

| `connection_id`, `user_agent_id`, `user_vault_id`, `user_grant_token` | Per-user | DB |

Two specific points:

1. **Alchemy + LiFi are operator-shared.** Resolve them once at startup using the operator agent's `/v1/auth/agent-token` flow (the existing flow keeps working untouched — just point it at `AUREY_OPERATOR_`*). Don't try to read them through the user delegation path; the user's vault doesn't have those secrets and shouldn't.
2. **Wallet address in the prompt becomes per-user.** Today `runtime_wiring_context_for_deep_agent_prompt` and `wallet_context_for_deep_agent_prompt` bake the wallet address into a *shared* compiled graph `src/aurey/reasoning/deep_agent.py` 162-179). In cloud mode, either (a) move the address into the configurable context `aurey_context.wallet_address`) and have the prompt instruct the agent to read it from there, or (b) inject it at message build time before `graph.invoke`. Option (a) is cleaner and doesn't break graph caching.

---

## 6. DB schema for Telegram + connection + onboarding state

Postgres is already a dependency for LangGraph checkpoints `src/aurey/reasoning/checkpointer.py`), so add migrations to the same DB. Sketch:

```sql

-- Stable internal user id; never expose to the model.

CREATE TABLE platform_users (

    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    telegram_user_id         BIGINT      UNIQUE,

    telegram_chat_id         BIGINT,

    display_name             TEXT,

    -- 1Claw identifiers (all platform_locked from our perspective)

    oneclaw_user_id          TEXT        UNIQUE NOT NULL,    -- usr_…

    oneclaw_connection_id    TEXT        UNIQUE NOT NULL,    -- conn_…

    oneclaw_vault_id         TEXT        NOT NULL,           -- vlt_…

    oneclaw_agent_id         TEXT        NOT NULL,           -- agt_…

    wallet_address           TEXT,                            -- derived from signing key on claim

    -- Onboarding lifecycle

    onboarding_state         TEXT        NOT NULL

        CHECK (onboarding_state IN

               ('upserted','awaiting_claim','claimed','ready','disconnected','error')),

    claim_url                TEXT,

    claim_url_expires_at     TIMESTAMPTZ,

    claimed_at               TIMESTAMPTZ,

    -- Delegated access (encrypted at rest in app layer; prefer storing in operator vault

    -- under a path like  users/{user_id}/grant_token  rather than the DB)

    grant_token_secret_path  TEXT,                            -- vault path, not the secret itself

    grant_token_expires_at   TIMESTAMPTZ,

    grant_scopes             TEXT[] NOT NULL DEFAULT '{}',

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE INDEX ON platform_users (telegram_chat_id);

CREATE INDEX ON platform_users (oneclaw_connection_id);

CREATE TABLE platform_user_signing_keys (

    user_id        UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,

    chain          TEXT NOT NULL,                  -- ethereum, base, …

    public_key     TEXT NOT NULL,

    address        TEXT NOT NULL,

    key_version    INT  NOT NULL DEFAULT 1,

    provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, chain)

);

CREATE TABLE onboarding_events (

    id          BIGSERIAL PRIMARY KEY,

    user_id     UUID REFERENCES platform_users(id) ON DELETE CASCADE,

    kind        TEXT NOT NULL,                    -- upsert/bootstrap/claim_*/grant_*/key_*/error

    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE INDEX ON onboarding_events (user_id, created_at DESC);

-- Idempotency for webhooks and Telegram /start retries

CREATE TABLE bootstrap_attempts (

    idempotency_key  TEXT PRIMARY KEY,           -- e.g. "telegram:{chat_id}:start:{n}"

    user_id          UUID NULL REFERENCES platform_users(id),

    status           TEXT NOT NULL,              -- pending / ok / error

    response_json    JSONB,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()

);

```

Plus the **existing** LangGraph `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` tables that `PostgresSaver.setup()` creates (see your `open_postgres_checkpointer`).

Two design calls worth flagging:

- **Session/thread id.** Today `session_id = f"telegram:{chat_id}"` `src/aurey/telegram/client.py:305`). I'd keep `telegram:{chat_id}` as the Telegram-facing id but make the LangGraph `thread_id` be `user:{platform_users.id}` so a user's history survives a chat-id change (rare, but happens on group migrations) and so future surfaces (web, API) join the same thread cleanly. The mapping is in `platform_users`.
- **Don't store `user_grant_token` raw in the DB.** Either store it in your operator vault at a path like `users/{user_id}/oneclaw_grant_token` (and keep only the path in Postgres), or column-encrypt with a KMS-backed key. The DB row otherwise becomes the single weakest link.

---

## 7. Repo layout — same repo vs `aurey-cloud`

My recommendation: **start in the same repo, segregated by package** until cloud surface area justifies the split. Concretely:

```

hosted-aurey/

├── src/

│   └── aurey/

│       ├── settings/        # split into base/, operator/, cloud/

│       ├── custody/         # unchanged (vault primitives)

│       ├── graphs/          # unchanged (read/swap/tx subgraphs)

│       ├── reasoning/       # unchanged (deep agent factory)

│       ├── tools/           # unchanged

│       ├── service/         # existing HTTP /v1/invoke (single-tenant)

│       ├── telegram/        # bot — refactored to require Principal

│       └── cloud/           # ← NEW

│           ├── **init**.py

│           ├── [settings.py](http://settings.py)            # AUREY_PLT_*, AUREY_OIDC_*

│           ├── [principal.py](http://principal.py)           # UserPrincipal, OperatorWideSecrets

│           ├── platform_[client.py](http://client.py)     # plt_ key calls: users.upsert, bootstrap

│           ├── delegated_[signer.py](http://signer.py)    # subclass of OneClawHttpClient using

│           │                          # /v1/auth/delegated-token

│           ├── [oidc.py](http://oidc.py)                # mint subject_token, JWKS endpoint

│           ├── db/

│           │   ├── [models.py](http://models.py)          # SQLAlchemy / SQLModel rows

│           │   └── migrations/        # alembic

│           ├── [onboarding.py](http://onboarding.py)          # /start handler logic, claim webhook

│           └── [api.py](http://api.py)                 # FastAPI router: /oneclaw/webhooks/claim,

│                                      # /admin/users, /.well-known/jwks.json

├── run_[http.py](http://http.py)        # mounts [service.app](http://service.app) + cloud.api

├── run_[telegram.py](http://telegram.py)    # uses cloud.principal for per-message lookup

└── tests/

    ├── unit/cloud/...

    └── integration/cloud/...          # against 1Claw sandbox (if available)

```

Why monorepo for now:

- `aurey.graphs` + `aurey.reasoning` are 80% of the value and are shared. Splitting forces an unstable internal package boundary before you know what the cloud surface actually looks like.
- Tests already share fakes `FakeOneClawClient`, `FakeSecretStore` in `custody/secret_store.py`); the cloud module can reuse them directly.
- LangGraph + 1Claw versioning is enough churn — don't add an inter-repo version-pin tax.

When to split into `aurey-cloud` (separate repo):

- when the cloud package grows multi-tenant admin/billing UIs, or
- when you want to publish `aurey-core` to PyPI as a self-hosted toolkit while keeping `aurey-cloud` closed-source.

Until then, gate the cloud surface behind `pyproject.toml` optional extras:

```toml

[project.optional-dependencies]

cloud = [

  "fastapi", "uvicorn[standard]",

  "sqlalchemy>=2", "asyncpg", "alembic",

  "authlib",     # OIDC signing / JWKS

  "cryptography",

]

```

So `pip install -e '.[api]'` keeps working for self-hosters, and `pip install -e '.[cloud]'` is what your Railway deploy uses.

---

## 8. Critical-path checklist before you write code

1. Open a single thread with 1Claw covering the five **open questions** in §2 (especially Q1, Q2, Q4 — those gate UX and DB schema).
2. Confirm whether 1Claw can push a **claim webhook**; if not, design polling.
3. Decide whether `auth_mode: silent + OIDC + Telegram-only` actually skips the claim screen entirely, or whether the user must visit `claim_url` in a webview for MPC client-share custody. The hosted product UX hinges on this — if MPC client-share is required, your "Telegram-only" promise needs an asterisk (or you template *without* client-share MPC and rely on Shroud TEE for custody isolation).
4. Decide whether to keep `evm_signing_mode = "vault_key"` available at all in cloud mode. If not, **delete** `wallet_signing_key_secret_path` plumbing from the cloud code path — single signing path is much easier to reason about.
5. Plan a minimal cloud-mode evaluation harness: a second `OneClawHttpClient` variant in `tests/` that fakes `/v1/auth/delegated-token` and the user-agent `/sign` endpoint, so you can exercise the per-user runtime without a live 1Claw sandbox.

If you want, next pass I can sketch the `DelegatedSigner` class (concrete diff against `OneClawHttpClient`) or the SQLAlchemy models — just say which you want to start with.



