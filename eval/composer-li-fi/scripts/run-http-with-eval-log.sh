#!/usr/bin/env bash
# Run the HTTP stack and tee stderr+stdout into eval/composer-li-fi/runs/*.log for LLM-as-judge.
#
# Usage (repo root is detected automatically):
#   ./eval/composer-li-fi/scripts/run-http-with-eval-log.sh [suffix]
#
# Requires AUREY_AGENT_TRACE so tool payloads appear under logger aurey.agent.trace.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
	set -a
	# shellcheck disable=SC1091
	source .env
	set +a
fi

SUFFIX="${1:-http}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/eval/composer-li-fi/runs/${STAMP}-${SUFFIX}.log"
mkdir -p "$ROOT/eval/composer-li-fi/runs"

export AUREY_AGENT_TRACE="${AUREY_AGENT_TRACE:-info}"
export AUREY_LOG_FORCE_COLOR="${AUREY_LOG_FORCE_COLOR:-0}"
export NO_COLOR="${NO_COLOR:-1}"

echo "logging to: $OUT" >&2
echo "hint: POST /v1/invoke triggers aurey.turn + agent_trace lines" >&2

exec uv run python run_http.py --log-level info 2>&1 | tee "$OUT"
