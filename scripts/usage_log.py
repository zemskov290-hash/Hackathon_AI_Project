#!/usr/bin/env python3
"""Measured time and tokens, as opposed to what the agent writes about itself.

Two sources, both read-only:

* Timestamps of log.jsonl. Every CLI and MCP write stamps ``ts``. The gap
  between two neighbouring records is work time of the later record's stage,
  unless the gap is longer than ``IDLE_GAP_MINUTES`` (a pause between turns or
  a night). This needs no cooperation from the model, so a stage that was
  worked on never shows zero minutes.
* Turn logs of the agent client (``agent -p --output-format stream-json`` in
  Cursor, ``claude -p --output-format stream-json`` in Claude Code). Their
  ``result`` record carries the turn duration and token usage counted by the
  client. Under an IDE subscription this is still not a bill.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

IDLE_GAP_MINUTES = 30.0
AGENT_LOG_DIR = "agent-logs"
AGENT_LOG_ENV = "STRAT_AGENT_LOGS"


def parse_ts(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def journal_minutes(entries: list[dict], idle_gap: float = IDLE_GAP_MINUTES) -> dict:
    """Wall-clock minutes per stage from neighbouring log timestamps."""
    stamped = []
    for order, entry in enumerate(entries):
        stamp = parse_ts(entry.get("ts"))
        if stamp is None:
            continue
        try:
            stage = int(entry.get("stage") or 0)
        except (TypeError, ValueError):
            stage = 0
        stamped.append((stamp, order, stage))
    stamped.sort()
    by_stage: dict[int, float] = {}
    pauses = 0
    for (before, _o1, _s1), (after, _o2, stage) in zip(stamped, stamped[1:]):
        gap = (after - before).total_seconds() / 60.0
        if gap < 0:
            continue
        if gap > idle_gap:
            pauses += 1
            continue
        by_stage[stage] = by_stage.get(stage, 0.0) + gap
    for _stamp, _order, stage in stamped:
        by_stage.setdefault(stage, 0.0)
    return {
        "by_stage": by_stage,
        "total": sum(by_stage.values()),
        "pauses": pauses,
        "records": len(stamped),
        "idle_gap": idle_gap,
    }


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_numbers(usage: dict) -> dict[str, int]:
    """Cursor uses camelCase, Claude Code snake_case with cache_* names."""
    return {
        "input": _int(usage.get("inputTokens", usage.get("input_tokens"))),
        "output": _int(usage.get("outputTokens", usage.get("output_tokens"))),
        "cache_read": _int(usage.get("cacheReadTokens", usage.get("cache_read_input_tokens"))),
        "cache_write": _int(usage.get("cacheWriteTokens", usage.get("cache_creation_input_tokens"))),
    }


def read_turn_log(path: Path) -> list[dict]:
    """Every ``result`` record with usage in one stream-json file."""
    turns: list[dict] = []
    models: list[str] = []
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return turns
    with handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            model = record.get("model")
            if record.get("type") == "system" and isinstance(model, str) and model and model not in models:
                models.append(model)
            if record.get("type") != "result" or not isinstance(record.get("usage"), dict):
                continue
            numbers = _usage_numbers(record["usage"])
            turn = {
                "file": path.name,
                "duration_ms": _int(record.get("duration_ms")),
                "models": list(models),
                "is_error": bool(record.get("is_error")),
                **numbers,
            }
            cost = record.get("total_cost_usd")
            if isinstance(cost, (int, float)):
                turn["cost_usd"] = float(cost)
            turns.append(turn)
    return turns


def candidate_logs(project: Path | None, explicit: list[str] | None = None) -> list[Path]:
    """--agent-log paths, then STRAT_AGENT_LOGS, then projects/<id>/agent-logs/."""
    raw: list[str] = list(explicit or [])
    env = os.environ.get(AGENT_LOG_ENV)
    if env:
        raw.extend(part for part in env.split(os.pathsep) if part.strip())
    if project is not None and (project / AGENT_LOG_DIR).is_dir():
        raw.append(str(project / AGENT_LOG_DIR))
    found: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        path = Path(item).expanduser()
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for candidate in files:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen or not candidate.is_file():
                continue
            seen.add(key)
            found.append(candidate)
    return found


def agent_usage(project: Path | None, explicit: list[str] | None = None) -> dict:
    files = candidate_logs(project, explicit)
    turns: list[dict] = []
    for path in files:
        turns.extend(read_turn_log(path))
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "duration_ms": 0}
    cost = 0.0
    has_cost = False
    models: list[str] = []
    for turn in turns:
        for key in total:
            total[key] += _int(turn.get(key))
        if "cost_usd" in turn:
            has_cost = True
            cost += float(turn["cost_usd"])
        for model in turn.get("models") or []:
            if model not in models:
                models.append(model)
    return {
        "files": [str(path) for path in files],
        "turns": turns,
        "total": total,
        "cost_usd": cost if has_cost else None,
        "models": models,
    }


def fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def format_agent_usage(report: dict) -> list[str]:
    turns = report["turns"]
    if not turns:
        if report["files"]:
            return [
                "Журналы агента найдены, но в них нет записи result с usage: токены неизвестны. "
                "Нужен вывод agent -p --output-format stream-json (или json)."
            ]
        return [
            "Фактические токены: журналов агента нет, точное число неизвестно. "
            f"Передайте cost --agent-log <файл или папка stream-json>, переменную {AGENT_LOG_ENV} "
            f"или положите журналы ходов в {AGENT_LOG_DIR}/ проекта."
        ]
    total = report["total"]
    lines = [
        f"Фактический расход по журналам агента ({len(turns)} ход., файлов {len(report['files'])}): "
        f"вход {fmt(total['input'])}, выход {fmt(total['output'])}, "
        f"чтение кэша {fmt(total['cache_read'])}, запись кэша {fmt(total['cache_write'])} токенов.",
        f"Время ходов по журналам агента: {fmt(total['duration_ms'] / 60000)} мин.",
    ]
    if report["models"]:
        lines.append("Модели: " + ", ".join(report["models"]) + ".")
    if report["cost_usd"] is not None:
        lines.append(f"Стоимость, которую сообщил клиент агента: {report['cost_usd']:.4f} USD. Это не счёт провайдера.")
    else:
        lines.append("Числа посчитал клиент агента. Под подпиской IDE отдельного счёта за ход нет.")
    return lines
