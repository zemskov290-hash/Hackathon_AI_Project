#!/usr/bin/env bash
# Unix wrapper. On Windows run: python hooks/stage_end.py
if [ ! -t 0 ]; then
  cat >/dev/null || true
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python >/dev/null 2>&1; then
  exec python "$ROOT/hooks/stage_end.py"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/hooks/stage_end.py"
fi
exit 0
