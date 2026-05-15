# Aurey
> **Production-grade EVM agentic wallet. Powered by LangGraph and 1Claw.**
<p align="center">
  <img src="asset/aurey-wordmark.png" alt="Aurey" width="260" />
</p>


### **Production-grade EVM agentic wallet. LangGraph brains. Secrets that never sleep in `.env`.**

**A standalone autonomous wallet agent** — reason over chain state, compose DeFi flows, and broadcast transactions — with **vault-backed custody via [1Claw](https://docs.1claw.xyz)** so operators ship a **secure agent** without drowning in key sprawl.

[LangGraph](https://github.com/langchain-ai/langgraph)  
[Deep Agents](https://github.com/langchain-ai/deepagents)  
[1Claw](https://docs.1claw.xyz)  
[FastAPI](https://fastapi.tiangolo.com/)  
[GitHub stars](https://github.com/agentic-pantheon/aurey)

[Deploy Agent on Railway](#one-click-deploy-on-railway)

**[⭐ Star if you ship agents](https://github.com/agentic-pantheon/aurey)** · **[Report an issue](https://github.com/agentic-pantheon/aurey/issues/new)** · **[Agentic Pantheon org](https://github.com/agentic-pantheon)**

---

## ✨ Why Aurey?


| Problem                                             | Aurey                                                                                                                  |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Autonomous wallets leak keys into env vars and logs | Configuration holds **vault paths only** — **1Claw** resolves provider material through a **Secret Store** abstraction |
| “Agent demos” ≠ production graph logic              | Built on **LangGraph** + Deep Agents harness for repeatable, inspectable workflows                                     |
| Web3 × AI integrations sprawl forever               | Batteries-included EVM tooling (reads, prepares, executes) wired for **production HTTP** + optional Telegram           |


If you’re an **AI agent builder**, **Web3 developer**, or **protocol team** shipping user-facing autonomy, Aurey is the **secure agent → autonomous wallet** path that stays boring where it matters: **custody**.

---

## 🚀 Key features

- 🧠 **LangGraph-powered reasoning** — Compose graphs per capability; deterministic boundaries between “think,” “simulate,” and “send.”  
- 🔐 **1Claw-first security** — **Secure agent** pattern: bootstrap API key from a named env var; everything else resolves from the vault.
- ⛓️ **Native EVM agentic wallet** — Read chain state, prepare and execute transactions, interoperate with real protocols (routing / yield flows per your tooling).   
- 🗄️ **Postgres checkpoints** — Optional **PostgreSQL** LangChain checkpointer for resilient multi-turn sessions.  
- ✈️ **Telegram shell** — chat with Aurey from telegram.  
- 📊 **Operations-ready hooks** — LangSmith-friendly tracing knobs; structured agent trace for evaluations.

---

## ⚡ Quick start

### 1 · Install dependencies

```bash
git clone https://github.com/agentic-pantheon/aurey.git
cd aurey

uv sync --group dev           # core + dev toolchain
uv sync --group dev --extra api      # FastAPI HTTP service
uv sync --group dev --extra telegram # optional Telegram bot deps
```

### 2 · Configure environment (minimal)

```bash
cp .env.example .env
```

Set at least `**AUREY_OCV_VAULT_ID**`, `**AUREY_OCV_AGENT_API_KEY**` (the operator agent key’s **value** lives in env; vault entries remain path-only), optional **`AUREY_PLT_*`** identifiers for hosted provisioning (see the runbook), and your model provider (`**OPENAI_API_KEY**` when using defaults like `openai:gpt-4o-mini`). See `[.env.example](.env.example)` and `[docs/runbooks/1claw-cloud-setup.md](docs/runbooks/1claw-cloud-setup.md)`. **Do not inline production secrets.**

### 3 · Run the HTTP API locally

From an environment with `**aurey[api]`** installed:

```bash
uv run python run_http.py --host 127.0.0.1 --port 8000
```

Smoke `**GET /health**`, then call `**POST /v1/invoke**` with JSON: `message`, `session_id`, optional `context`, optional `model`. Responses follow `InvokeResponse` — errors surface **stable codes** without secret leakage.

### 4 · One click deploy on Railway

**Before Railway:** create a **1Claw account** here so Aurey has a vault and agent to talk to ([docs](https://docs.1claw.xyz)):

1. **Create an account** — Sign up or log in to the **[1Claw dashboard](https://1claw.xyz/)**.
2. **Create a vault** — Add a vault for your deployment. Inside it, create **secret entries at these paths** (the path strings are yours to choose; use the same strings in `.env` via the variables below):

  | Set in 1Claw at path… (example) | Aurey env var (path only)              | What to store                                                                              |
  | ------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------ |
  | `aurey/alchemy/api_key`         | `AUREY_ALCHEMY_API_SECRET_PATH`        | **Alchemy API key**  *(needed for portfolio and data api)*             |
  | `aurey/lifi/api_key`            | `AUREY_LIFI_API_SECRET_PATH`           | **LiFi API key**        *(needed for 1click deposits on major protocols)*                                                                    |
  | `aurey/wallet/signing_key`      | `AUREY_WALLET_SIGNING_KEY_SECRET_PATH` | **Your wallet private key** *(required when `AUREY_EVM_SIGNING_MODE=vault_key`, the default)* |
  | `aurey/telegram/bot_token`      | `AUREY_TELEGRAM_BOT_TOKEN_SECRET_PATH` | **Telegram bot token** *(Telegram bot token from telegram botfather)*                                |

   Paths like `aurey/...` are **examples** — any stable vault path works as long as `**AUREY_*_SECRET_PATH` matches what you configured in the 1Claw UI** and you never put the secret values in Aurey’s env (only the path strings + `**AUREY_OCV_*` operator ids + **`AUREY_OCV_AGENT_API_KEY`** — see [.env.example](.env.example)).
3. **Create an agent** — Register a **hosted agent** (or equivalent) tied to that vault so the operator agent API key can resolve secrets and (when configured) sign transactions via 1Claw’s flows.
4. **Wire secrets in the UI** — Paste each real secret value at the path you picked in step 2. Double-check that `**AUREY_ALCHEMY_API_SECRET_PATH`**, `**AUREY_WALLET_SIGNING_KEY_SECRET_PATH**` (if using `vault_key` signing), `**AUREY_LIFI_API_SECRET_PATH**`, and `**AUREY_TELEGRAM_BOT_TOKEN_SECRET_PATH**` in `.env` / Railway **exactly match** those vault paths (not the secrets themselves).
5. **Copy IDs for Railway** — Use **vault id** → `AUREY_OCV_VAULT_ID`, **agent id** (if using hosted token exchange) → `AUREY_OCV_AGENT_ID`, and the **operator agent API key** value → `AUREY_OCV_AGENT_API_KEY` (never commit it; set it only in Railway). Platform app/template ids (`AUREY_PLT_APP_ID`, `AUREY_PLT_TEMPLATE_ID`, optional `AUREY_PLT_APP_API_KEY*`) are for hosted provisioning—see the runbook.
6. **Optional** — Run `./scripts/1claw-console-setup.placeholder.sh` as a reminder hook; it only prints the checklist path until automation lands.
7. Then click here to [Deploy on Railway](https://railway.com/deploy/10EU4s?referralCode=WNfHEr&utm_medium=integration&utm_source=template&utm_campaign=generic). Add the needed variables in the template.



---

## 🎬 Demo

<p align="center">
  <a href="https://youtu.be/3AVGVJ9BWfQ" title="Watch the Aurey demo on YouTube">
    <img src="https://img.youtube.com/vi/3AVGVJ9BWfQ/maxresdefault.jpg" alt="Aurey demo — play on YouTube" width="560" />
  </a>
</p>

**[Watch on YouTube →](https://youtu.be/3AVGVJ9BWfQ)**

---

## 🏗 Architecture

### Request path (mental model)

```mermaid
flowchart LR
  Client[HTTP / Telegram] --> API[FastAPI invoke]
  API --> Agent[Deep agent + LangGraph]
  Agent --> Custody[1Claw SecretStore]
  Custody --> Keys[Vault paths resolve at runtime]
  Agent --> Graphs[Compiled subgraphs: read / prepare / execute]
  Graphs --> EVM[EVM RPC + tx pipeline]
```



### Repository layout (`src/aurey/`)


| Area         | Role                                                                                          |
| ------------ | --------------------------------------------------------------------------------------------- |
| `settings/`  | Pydantic settings — **platform (`plt_`) vs operator (`ocv_`)** 1Claw envs, vault path references, model defaults |
| `custody/`   | `SecretStore`, `SecretValue`, `OneClawHttpClient`, fakes for tests                            |
| `reasoning/` | Deep agent harness, factory                                                                   |
| `tools/`     | LangChain tool surfaces                                                                       |
| `graphs/`    | LangGraph subgraphs (EVM codecs, swaps, txs, checkpoints)                                     |
| `service/`   | FastAPI app, bootstrap, adapters, `**/v1/invoke`**                                            |
| `telegram/`  | Optional bot — shares `**AureyServiceState**` with HTTP                                       |


### Develop & test

```bash
uv run ruff check src tests
uv run pytest
```

For integration tests **without live models**, inject `state=` into `**create_fastapi_application`** or patch `**create_aurey_deep_agent**` (see codebase tests).

---

## 🏛️ Part of **Agentic Pantheon** 

**[Agentic Pantheon](https://github.com/agentic-pantheon)** is the security-first ecosystem for autonomous systems — where **LangChain**, **1Claw**, and opinionated repos meet so teams ship agents that behave in production.

- **THIS REPO (`aurey`)** — standalone **EVM agentic wallet** + service shell.  
- **Organization** → explore sibling projects (**Juno**, **Mercury**, **Fabietto** and more) under **[github.com/agentic-pantheon](https://github.com/agentic-pantheon)**.

If Aurey resonates, **follow the org**!!!

---

## 🗺 Aurey's Roadmap

High-signal priorities (intent, not a promise ledger):


| Horizon   | Themes                                                                                |
| --------- | ------------------------------------------------------------------------------------- |
| **Now**   | Harden onboarding (templates, presets), expand eval scenarios, docs for signing modes |
| **Next**  | Deeper composability packs (routing, risk checks), tighter observability dashboards   |
| **Later** | Hosted “secure agent wallet” playbook, audits, institutional deployment guides        |


👉 **Tell us what to build next:** open **[issues](https://github.com/agentic-pantheon/aurey/issues)** with protocol or infra requirements.

---

## 🤝 Contributing & contact

PRs welcome. Typical flow: **fork** → **branch** → `**ruff` + `pytest` green** → **PR** with motivation + test notes.

**Ways to engage**

- 💼 **Consulting / custom agents / protocol integrations**: reach out via the contact channel linked from your Pantheon landing page / org README (or DM the maintainers on your usual Social — point them at this repo).  
- 💬 **Bugs / features**: **[open an issue](https://github.com/agentic-pantheon/aurey/issues/new)**.  
- 🔐 **Responsible disclosure**: if you suspect a custody or signing integration bug, coordinate privately — **do not** file public PoCs against live keys.

If Aurey removes one entire class of “we almost shipped keys” incidents for your team — **⭐ star the repo** and tell another agent engineer. That signal keeps the roadmap sharp.