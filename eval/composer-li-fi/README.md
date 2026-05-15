# LiFi Composer evaluation

Structured manual evaluation: run queries from repo root [`lifi-queries.md`](../../lifi-queries.md), save one artifact per scenario (terminal log and/or markdown stub), then paste or **@-attach** logs in a **separate Cursor chat** using [`JUDGE_PROMPT.md`](JUDGE_PROMPT.md).

## All `lifi-queries.md` bullets (tracked stubs)

Twelve prompts are listed verbatim under [`scenarios/`](scenarios/) (`01-…` through `12-…` `.stub.md`). The second “Discovery” line in `lifi-queries.md` (no leading hyphen) is **`03-discovery-top5-composer-usdc-apy`**.

**Sequential Telegram workflow:** from repo root run

```bash
./eval/composer-li-fi/scripts/telegram-eval-guided-loop.sh
```

Per step the script prints the **User prompt**, then starts `run-telegram-with-eval-log.sh` (which loads `.env`; see below). After the bot replies, **Ctrl+C** the poller, fix up the scenario stub’s **`terminal_log`** field with the tee path, Enter to continue.

### `.env` loading

`run-telegram-with-eval-log.sh` and `run-http-with-eval-log.sh` run, when **`.env` exists in the repository root** (after `cd` there),

```bash
set -a
source .env
set +a
```

(equivalent intent to `set -a && source .env && set +a` before `uv run`.)

## Folder layout

| Path | Purpose |
|------|---------|
| [`rubric.md`](rubric.md) | Checklists by scenario category; align judge scores to product expectations |
| [`JUDGE_PROMPT.md`](JUDGE_PROMPT.md) | Prompt to paste so Cursor acts as judge (copy scenario + transcript blocks) |
| [`scenarios/`](scenarios/) | One `.stub.md` per `lifi-queries.md` bullet with YAML + **User prompt** |
| [`scripts/`](scripts/) | Helpers that run the server with `tee` into `runs/*.log` |
| `runs/` | `*.log` from the terminal (primary eval artifact) plus optional `*.md`; local only by default |

## Terminal logs (no copy-paste of the whole chat)

Aurey logs to **stderr** via Rich (`configure_aurey_console_logging`). At **`--log-level info`** you get:

- **`aurey.turn`**: `incoming` (user text) and `complete` (message count + **reply preview**, length-capped).
- **`aurey.agent.trace`**: enabled only when **`AUREY_AGENT_TRACE`** is set — use **`AUREY_AGENT_TRACE=info`** for `tool_start` / `tool_end` lines (inputs/outputs clipped in code). Without this, terminal logs are thin for tool-use judging.

There is no separate `LOG=INFO` flag in this repo; use the runner’s **`--log-level info`** (see `run_http.py` / `run_telegram.py`).

### Capture to `runs/*.log`

From the **repository root**:

```bash
# HTTP API (then drive the agent via your client / Cursor against localhost)
./eval/composer-li-fi/scripts/run-http-with-eval-log.sh discovery-v1

# Telegram long polling
./eval/composer-li-fi/scripts/run-telegram-with-eval-log.sh morpho-deposit
```

Each script sets `AUREY_AGENT_TRACE=info` unless already exported, disables color for readable files, and **`tee`s** all output to `eval/composer-li-fi/runs/<timestamp>-<suffix>.log`.

Manual equivalent:

```bash
export AUREY_AGENT_TRACE=info
export NO_COLOR=1 AUREY_LOG_FORCE_COLOR=0
uv run python run_http.py --log-level info 2>&1 | tee eval/composer-li-fi/runs/my-run.log
```

### Limitations

- Terminal capture is best for **tool order, arguments, truncated tool results, errors, and retries**. The `complete` preview is not always the full assistant message; for strict UX scoring, add a short pasted reply in the optional markdown stub (`templates/run-log.template.md`).
- **`AUREY_AGENT_TRACE=debug`** logs more graph noise and per-token stream data (very verbose); reserve for deep debugging.

## Workflow

1. **Run server with logging**: Use a script above (or the one-liner). Keep that terminal open while you exercise one scenario.
2. **Exercise**: Send **one** query (HTTP `POST /v1/invoke`, Telegram message, etc.) that matches a bullet in `lifi-queries.md`.
3. **Record**: Note the new `runs/*.log` path. Optionally copy `templates/run-log.template.md` → `runs/<id>.md` with YAML `terminal_log: ...` and any chat-only supplement.
4. **Judge**: New Cursor chat → paste `JUDGE_PROMPT.md` → set scenario id/category → **@-reference the `.log` file** (or paste its contents) as the transcript.
5. **Track**: Rollout sheet keyed by log filename + judge JSON `overall_pass`.

Repeat runs change only the Agent chat seed/config; keep the judge prompt frozen for comparable scores.

## Tips

- One scenario per judge turn; do not batch unrelated transcripts.
- Redact addresses, API keys, and signed payloads in `runs/` before sharing.
- For numeric truth (APY, TVL), prefer judge checks that say “grounded in tool output” rather than “correct vs market” unless you attach a golden snapshot.
