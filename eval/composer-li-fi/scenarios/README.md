# LiFi Composer eval scenarios (`lifi-queries.md`)

Each **`NN-slug.stub.md`** matches one numbered prompt from repo root [`lifi-queries.md`](../../../lifi-queries.md) (discovery section splits the two sentences on adjacent lines into **two** stubs).

Sequential Telegram capture:

1. `./eval/composer-li-fi/scripts/telegram-eval-guided-loop.sh` — prints prompts in order and runs `run-telegram-with-eval-log.sh` between pauses (`Ctrl+C` after each bot reply).

Or run `./eval/composer-li-fi/scripts/run-telegram-with-eval-log.sh <suffix>` manually; both runners load `.env` from the repo root via `set -a && source .env && set +a` when `.env` exists.

After each run, fill **`terminal_log`** in the stub YAML with the `eval/composer-li-fi/runs/*.log` path from the tee.
