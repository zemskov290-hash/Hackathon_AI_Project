#!/usr/bin/env python3
"""Full-case comparison: agent vs plain chat (and optionally an analyst), time and rubles.

Only measured numbers are used. Agent time and tokens come from the project
(journal timestamps and agent turn logs, as `cost` reads them). Chat and
analyst numbers come from a JSON file a person filled after a real run.
Rubles are computed only when the person passes a price per million tokens
(or an hourly rate); the price is printed as an assumption, never as a bill.
Anything not measured stays "—" with the reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import usage_log  # noqa: E402

DASH = "—"


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def agent_side(project: Path, agent_logs: list[str] | None = None) -> dict:
    side: dict = {"label": f"агент ({project.name})", "seconds": None, "input_tokens": None, "output_tokens": None, "check_errors": None, "notes": []}
    log_path = project / "log.jsonl"
    entries: list[dict] = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                entries.append(row)
    if entries:
        clock = usage_log.journal_minutes(entries)
        if clock.get("records", 0) > 1:
            side["seconds"] = round(float(clock["total"]) * 60.0, 1)
    else:
        side["notes"].append("журнала log.jsonl нет — время не измерено")
    report = usage_log.agent_usage(project, agent_logs)
    if report["turns"]:
        side["input_tokens"] = report["total"]["input"] + report["total"]["cache_read"]
        side["output_tokens"] = report["total"]["output"]
        if report["total"].get("duration_ms"):
            side["seconds"] = round(report["total"]["duration_ms"] / 1000.0, 1)
        if report["incomplete"]:
            side["notes"].append(f"оборванных журналов агента: {len(report['incomplete'])} — токены занижены")
    else:
        side["notes"].append("журналов агента с usage нет — токены не измерены")
    try:
        import strat

        findings = strat.check_project(project, strict=False)
        side["check_errors"] = len(strat.hard_findings(findings))
    except SystemExit:
        side["notes"].append("check не выполнился")
    except Exception as exc:  # noqa: BLE001 — the comparison must not crash on a broken project
        side["notes"].append(f"check не выполнился: {type(exc).__name__}")
    return side


def manual_side(payload: dict, label: str) -> dict:
    return {
        "label": payload.get("label") or label,
        "seconds": _num(payload.get("seconds")),
        "input_tokens": _num(payload.get("input_tokens")),
        "output_tokens": _num(payload.get("output_tokens")),
        "check_errors": _num(payload.get("check_errors")),
        "hours": _num(payload.get("hours")),
        "missed": payload.get("missed") or [],
        "notes": list(payload.get("notes") or []),
    }


def rubles(side: dict, rub_in: float | None, rub_out: float | None, rub_hour: float | None) -> float | None:
    if side.get("hours") is not None and rub_hour is not None:
        return round(side["hours"] * rub_hour, 2)
    if rub_in is None or rub_out is None:
        return None
    if side.get("input_tokens") is None or side.get("output_tokens") is None:
        return None
    return round(side["input_tokens"] / 1e6 * rub_in + side["output_tokens"] / 1e6 * rub_out, 2)


def _cell(value) -> str:
    if value is None:
        return DASH
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render(sides: list[dict], rub_in: float | None, rub_out: float | None, rub_hour: float | None) -> str:
    lines = [
        "| Сторона | Время, мин | Токены вход | Токены выход | Рубли (оценка) | Ошибки check |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for side in sides:
        minutes = round(side["seconds"] / 60.0, 1) if side.get("seconds") is not None else None
        lines.append(
            f"| {side['label']} | {_cell(minutes)} | {_cell(side.get('input_tokens'))} | {_cell(side.get('output_tokens'))} | "
            f"{_cell(rubles(side, rub_in, rub_out, rub_hour))} | {_cell(side.get('check_errors'))} |"
        )
    lines.append("")
    if rub_in is None or rub_out is None:
        lines.append("Рубли: «—», потому что цена за миллион токенов не задана (--rub-per-1m-in/--rub-per-1m-out). Счёт провайдера не подставляется.")
    else:
        lines.append(f"Рубли — оценка по цене, которую задал человек: {rub_in} ₽ за 1 млн входных и {rub_out} ₽ за 1 млн выходных токенов. Это допущение, не счёт.")
    if rub_hour is not None:
        lines.append(f"Ставка аналитика {rub_hour} ₽/ч — допущение человека.")
    for side in sides:
        for note in side.get("notes") or []:
            lines.append(f"- {side['label']}: {note}")
        if side.get("missed"):
            lines.append(f"- {side['label']} пропустил: " + "; ".join(str(item) for item in side["missed"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сравнение полного кейса: агент против обычного чата, время и рубли")
    parser.add_argument("project", help="папка проекта агента")
    parser.add_argument("--chat", help="JSON замера обычного чата: seconds, input_tokens, output_tokens, check_errors, missed")
    parser.add_argument("--analyst", help="JSON замера аналитика: hours, notes (только реальный замер)")
    parser.add_argument("--rub-per-1m-in", type=float)
    parser.add_argument("--rub-per-1m-out", type=float)
    parser.add_argument("--analyst-rub-hour", type=float)
    parser.add_argument("--agent-log", action="append")
    args = parser.parse_args(argv)
    project = Path(args.project)
    if not (project / "state.json").is_file():
        print(f"Не проект: {project}", file=sys.stderr)
        return 2
    sides = [agent_side(project.resolve(), args.agent_log)]
    for raw, label in ((args.chat, "обычный чат"), (args.analyst, "аналитик")):
        if not raw:
            sides.append({"label": label, "seconds": None, "input_tokens": None, "output_tokens": None, "check_errors": None, "notes": ["замера нет"]})
            continue
        try:
            payload = json.loads(Path(raw).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Не читается {raw}: {exc}", file=sys.stderr)
            return 2
        sides.append(manual_side(payload if isinstance(payload, dict) else {}, label))
    print(render(sides, args.rub_per_1m_in, args.rub_per_1m_out, args.analyst_rub_hour), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
