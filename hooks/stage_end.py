#!/usr/bin/env python3
"""Reminder at the end of a turn. Does not close a stage and does not block."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()


def main() -> int:
    if not sys.stdin.isatty():
        try:
            sys.stdin.read()
        except OSError:
            pass
    print(
        "Если этап закончен и человек это подтвердил там, где нужна контрольная точка: "
        "python scripts/strat.py log и python scripts/strat.py advance. "
        "Сам по себе конец хода этап не закрывает."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
