#!/usr/bin/env bash
# Напоминание в конце хода. Не блокирует остановку и не пишет в журнал сам.
if [ ! -t 0 ]; then
  cat >/dev/null || true
fi
echo "Если этап закончен, человек это подтвердил там, где нужна контрольная точка: python3 scripts/strat.py log и python3 scripts/strat.py advance. Сам по себе конец хода этап не закрывает."
exit 0
