#!/usr/bin/env bash
# После правки файлов агентом: проверить текущий проект, если он есть.
# Вход хука IDE — JSON в stdin. Из терминала stdin не читаем, чтобы не ждать ввода.
if [ ! -t 0 ]; then
  cat >/dev/null || true
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! python3 scripts/strat.py status >/dev/null 2>&1; then
  exit 0
fi
python3 scripts/strat.py check
