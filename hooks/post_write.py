#!/usr/bin/env python3
"""After an IDE edit: check the current project when one exists.

Run with the same interpreter that should execute the CLI:
`python hooks/post_write.py`. stdin from the IDE is drained and ignored.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()
STRAT = ROOT / "scripts" / "strat.py"


def main() -> int:
    if not sys.stdin.isatty():
        try:
            sys.stdin.read()
        except OSError:
            pass
    status = subprocess.run(
        [sys.executable, str(STRAT), "status"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if status.returncode != 0:
        return 0
    checked = subprocess.run([sys.executable, str(STRAT), "check"], cwd=ROOT)
    return checked.returncode


if __name__ == "__main__":
    sys.exit(main())
