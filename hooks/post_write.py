#!/usr/bin/env python3
"""After an edit under a project, run strat check and show the report.

The hook exits 0 even when check finds errors or this script fails.
A hook crash must not block the user.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, project_of, read_payload  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()

STRAT = ROOT / "scripts" / "strat.py"


def run_check(payload: dict, *, interactive: bool) -> str:
    file_path = None
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        file_path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(file_path, str):
        raw = payload.get("file_path") or payload.get("path")
        file_path = raw if isinstance(raw, str) else None
    project = None
    if file_path:
        project = project_of(file_path)
        if project is None:
            return ""
    elif not interactive:
        return ""
    command = [sys.executable, str(STRAT), "check"]
    if project is not None:
        command.append(str(project))
    checked = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=55,
    )
    report = ((checked.stdout or "") + (checked.stderr or "")).strip()
    if checked.returncode != 0:
        report += "\nОшибки проверки показаны выше. Ход не заблокирован."
    return report


def main() -> int:
    interactive = sys.stdin.isatty()
    try:
        payload = read_payload()
    except Exception as exc:  # noqa: BLE001 — fail open
        sys.stderr.write(f"хук post_write пропущен: {exc}\n")
        return 0
    try:
        report = run_check(payload, interactive=interactive)
    except Exception as exc:  # noqa: BLE001 — fail open
        sys.stderr.write(f"хук post_write пропущен: {exc}\n")
        return 0
    event = str(payload.get("hook_event_name") or "")
    if report:
        sys.stderr.write(report + "\n")
    if event == "PostToolUse":
        import json

        body = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": report if report and "ОШИБКИ" in report else "",
            }
        }
        sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        return 0
    if event == "postToolUse" or (payload.get("tool_name") and "edits" not in payload and "file_path" not in payload):
        import json

        body = {}
        if report and "ОШИБКИ" in report:
            body["additional_context"] = report
        sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        return 0
    if report:
        sys.stdout.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
