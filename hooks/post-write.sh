#!/usr/bin/env bash
# Unix wrapper. On Windows run: python hooks/post_write.py
# python3 on Windows is often the Microsoft Store stub, so python comes first.
if [ ! -t 0 ]; then
  cat >/dev/null || true
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python >/dev/null 2>&1; then
  exec python "$ROOT/hooks/post_write.py"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/hooks/post_write.py"
fi
exit 0
