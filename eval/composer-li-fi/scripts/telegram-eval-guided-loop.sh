#!/usr/bin/env bash
# Walk every scenario stub once, sequentially. For each step: show the Telegram message to send,
# then start polling with tee’d logs until you Ctrl+C after the bot finishes replying.
#
# Usage (repo root):
#   ./eval/composer-li-fi/scripts/telegram-eval-guided-loop.sh
#
# Prerequisites: `.env` at repo root (sourced automatically by run-telegram-with-eval-log.sh);
# Telegram extra installed (`uv sync --group dev --extra telegram`).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCEN="$ROOT/eval/composer-li-fi/scenarios"
PY="$SCRIPTS/telegram-eval-extract-user-prompt.py"
RUNTG="$SCRIPTS/run-telegram-with-eval-log.sh"

shopt -s nullglob
matches=("$SCEN"/[0-9][0-9]-*.stub.md)
if [[ ${#matches[@]} -eq 0 ]]; then
	echo "No stubs matching $SCEN/[0-9][0-9]-*.stub.md" >&2
	exit 1
fi
STUBS=()
while IFS= read -r line; do
	[[ -n "$line" ]] && STUBS+=("$line")
done < <(printf '%s\n' "${matches[@]}" | LC_ALL=C sort)
echo "Found ${#STUBS[@]} scenarios. Telegram session is per-chat; Ctrl+C stops polling between steps." >&2
echo "Only one bot process may poll this token." >&2
echo >&2

for stub in "${STUBS[@]}"; do
	base="$(basename "${stub%.stub.md}")"
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
	echo "$base" >&2
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
	echo "Message to send in Telegram (after polling starts):" >&2
	python3 "$PY" "$stub" >&2
	echo >&2
	read -r -p "Press Enter to start polling (suffix: $base). Then send the message; Ctrl+C when the reply is done… " _
	set +e
	"$RUNTG" "$base"
	_=$?
	set -e
	echo >&2
	read -r -p "Next scenario: press Enter when ready (bot should be stopped)… " _
	echo >&2
done

echo "Done all ${#STUBS[@]} scenarios." >&2
