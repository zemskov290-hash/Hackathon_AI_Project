#!/usr/bin/env python3
"""On stop, append approximate input/output character counts. Never continues the turn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import read_payload  # noqa: E402
from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()
STRAT = ROOT / "scripts" / "strat.py"


def text_of(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return str(value["text"])
        if "content" in value:
            return text_of(value["content"])
        if "message" in value:
            return text_of(value["message"])
    return ""


def absorb(obj: object, totals: list[int]) -> bool:
    if not isinstance(obj, dict):
        return False
    role = str(obj.get("role") or obj.get("type") or obj.get("speaker") or "").lower()
    blob = ""
    if "message" in obj:
        blob = text_of(obj.get("message"))
    elif "content" in obj or "text" in obj:
        blob = text_of(obj.get("content", obj.get("text")))
    if not blob:
        return False
    if role in {"user", "human"}:
        totals[0] += len(blob)
        return True
    if role in {"assistant", "agent", "model"}:
        totals[1] += len(blob)
        return True
    return False


def count_transcript(path: Path) -> tuple[int, int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    totals = [0, 0]
    parsed = False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        for item in data:
            parsed = absorb(item, totals) or parsed
    elif isinstance(data, dict):
        messages = data.get("messages") or data.get("transcript")
        if isinstance(messages, list):
            for item in messages:
                parsed = absorb(item, totals) or parsed
        else:
            parsed = absorb(data, totals) or parsed
    if not parsed:
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed = absorb(item, totals) or parsed
    if not parsed:
        return len(raw), 0
    return totals[0], totals[1]


def strat(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STRAT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


def project_dir(state: dict) -> Path | None:
    project_id = str(state.get("id") or "")
    if not project_id:
        return None
    roots = []
    raw = os.environ.get("STRAT_PROJECTS_DIR")
    if raw:
        roots.append(Path(raw))
    roots.append(ROOT / "projects")
    for root in roots:
        candidate = root / project_id
        if (candidate / "log.jsonl").is_file():
            return candidate
    return None


def previous_hook_counts(state: dict) -> tuple[int, int]:
    folder = project_dir(state)
    if folder is None:
        return 0, 0
    cumulative_in = 0
    cumulative_out = 0
    for line in (folder / "log.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("model") != "hook/stop":
            continue
        cumulative_in += int(entry.get("input_chars") or 0)
        cumulative_out += int(entry.get("output_chars") or 0)
    return cumulative_in, cumulative_out


def write_log(stage: int, incoming: int, outgoing: int) -> None:
    message = Path(tempfile.mkstemp(suffix="-stop-message.txt")[1])
    grounds = Path(tempfile.mkstemp(suffix="-stop-grounds.txt")[1])
    try:
        message.write_text("Хук stop: приблизительный объём хода\n", encoding="utf-8")
        grounds.write_text(
            "Автозапись хука stop. Транскрипт накопительный, в журнал пишется прирост. Это не счёт провайдера.\n",
            encoding="utf-8",
        )
        result = strat(
            "log",
            str(stage),
            "--message-file",
            str(message),
            "--grounds-file",
            str(grounds),
            "--input-chars",
            str(incoming),
            "--output-chars",
            str(outgoing),
            "--model",
            "hook/stop",
            "--tier",
            "ide",
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout or result.stderr or "журнал stop не записан\n")
    finally:
        message.unlink(missing_ok=True)
        grounds.unlink(missing_ok=True)


def main() -> int:
    sys.stdout.write("{}\n")
    sys.stderr.write(
        "Если этап закончен и человек это подтвердил там, где нужна контрольная точка: "
        "python scripts/strat.py log и python scripts/strat.py advance. "
        "Сам по себе конец хода этап не закрывает.\n"
    )
    try:
        payload = read_payload()
    except Exception as exc:  # noqa: BLE001 — fail open, do not follow up
        sys.stderr.write(f"хук stop пропущен: {exc}\n")
        return 0
    try:
        raw_path = payload.get("transcript_path") or os.environ.get("CURSOR_TRANSCRIPT_PATH")
        if not raw_path:
            sys.stderr.write("нет транскрипта, объём хода не записан\n")
            return 0
        path = Path(str(raw_path))
        if not path.is_file():
            sys.stderr.write("нет транскрипта, объём хода не записан\n")
            return 0
        shown = strat("show", "state")
        if shown.returncode != 0:
            sys.stderr.write("нет боевого проекта, объём хода не записан\n")
            return 0
        state = json.loads(shown.stdout)
        stage = int(state.get("stage") or 0)
        incoming, outgoing = count_transcript(path)
        prev_in, prev_out = previous_hook_counts(state)
        delta_in = incoming - prev_in
        delta_out = outgoing - prev_out
        if delta_in < 0 or delta_out < 0:
            delta_in, delta_out = incoming, outgoing
        if delta_in == 0 and delta_out == 0:
            sys.stderr.write("объём хода уже записан\n")
            return 0
        write_log(stage, delta_in, delta_out)
    except Exception as exc:  # noqa: BLE001 — fail open
        sys.stderr.write(f"хук stop пропущен: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
