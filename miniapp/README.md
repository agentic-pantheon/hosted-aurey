# Aurey Telegram portfolio Mini App (Phase 1)

Static SPA served by FastAPI at `/miniapp/` when `miniapp/dist` exists.

## Prerequisites

Node 18+ and npm.

## Build

From repository root:

```bash
cd miniapp
npm install
npm run build
```

Output: `miniapp/dist/`.

The HTTP service (`run_http.py`) mounts that directory automatically.

## Local development

Terminal A — FastAPI (`uv sync --extra api`; Alchemy/hosted deps as needed):

```bash
uv run python run_http.py --port 8000
```

Set `AUREY_TELEGRAM_MINIAPP_ENABLED=true` plus hosted + bot token envs so `POST /v1/miniapp/portfolio` works.

Terminal B — Vite proxies `/v1` to localhost:8000:

```bash
cd miniapp
npm run dev
```

Open `http://127.0.0.1:5173/miniapp/` in a browser — without Telegram `initData` the UI shows a fallback message.

For realistic auth, expose the SPA over HTTPS + tunnel (e.g. ngrok/cloudflared) and open it from Telegram alongside `AUREY_TELEGRAM_MINIAPP_PUBLIC_URL`.
