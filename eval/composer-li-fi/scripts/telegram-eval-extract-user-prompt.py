"""Print the "## User prompt" body from a scenario stub (stdlib only)."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
	if len(sys.argv) != 2:
		sys.stderr.write("usage: telegram-eval-extract-user-prompt.py PATH.stub.md\n")
		sys.exit(2)
	raw = Path(sys.argv[1]).read_text(encoding="utf-8")
	m = re.search(
		r"^## User prompt\s*\n\s*\n([\s\S]+?)(?=^## |\Z)",
		raw,
		re.MULTILINE,
	)
	if not m:
		sys.stderr.write("no ## User prompt section found\n")
		sys.exit(1)
	sys.stdout.write(m.group(1).rstrip() + "\n")


if __name__ == "__main__":
	main()
