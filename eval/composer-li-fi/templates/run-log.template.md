---
scenario_id: "001"
category: "discovery"
source_query: "paste exact bullet from lifi-queries.md"
run_date: "YYYY-MM-DD"
agent_notes: "model / branch / env (optional)"
# Primary artifact for the judge when using tee’d server logs:
terminal_log: "eval/composer-li-fi/runs/20260513-153045-discovery-v1.log"
---

## Terminal log

- For Cursor judge: **@-attach** the file in `terminal_log` (or paste a grep slice: `aurey.turn` + `agent_trace`).

## User prompt

(paste if not obvious from `aurey.turn incoming` in the log)

## Assistant (final or full thread)

(optional—often the `aurey.turn` `complete` line includes a long **preview**; paste full reply here only if the log preview is too short for UX judging)

## Tool trace

_Order and names matter. Abbreviate large JSON._

| Step | Tool | Summary / key fields |
|------|------|-------------------------|
| 1 | | |

## Raw tool output (optional)

```
(paste redacted excerpts if judge needs grounding)
```

## Human notes

- Stall / retry / bugs observed:
